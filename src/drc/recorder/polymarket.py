"""Polymarket CLOB public market websocket, with top-N-by-volume market discovery.

Discovery uses the Gamma API. The selected markets' full metadata (question, rules, end date,
token ids) is stored in the `_catalog` channel so outcomes can be joined to books later.
New markets found on refresh are added with a dynamic subscribe, so existing streams keep
flowing without a reconnect gap.
"""

from __future__ import annotations

import logging

import httpx
import orjson

from drc.recorder.base import WsSource

log = logging.getLogger(__name__)

GAMMA_URL = "https://gamma-api.polymarket.com/markets"


class PolymarketSource(WsSource):
    venue = "polymarket"
    url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    keepalive_interval = 10.0
    stale_timeout = 300.0

    def __init__(self, top_n: int = 50, refresh_secs: float = 3600.0) -> None:
        self.top_n = top_n
        self.tick_interval = refresh_secs
        self.subscribed: set[str] = set()

    def keepalive_message(self) -> str:
        return "PING"

    async def discover(self) -> list[dict]:
        params = {
            "limit": self.top_n,
            "active": "true",
            "closed": "false",
            "order": "volume24hr",
            "ascending": "false",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(GAMMA_URL, params=params)
            resp.raise_for_status()
        markets = [m for m in resp.json() if m.get("enableOrderBook") and m.get("clobTokenIds")]
        self.writer.write(self.venue, "_catalog", orjson.dumps(markets))
        return markets

    @staticmethod
    def token_ids(markets: list[dict]) -> list[str]:
        return [t for m in markets for t in orjson.loads(m["clobTokenIds"])]

    async def subscriptions(self) -> list[dict]:
        tokens = self.token_ids(await self.discover())
        self.subscribed = set(tokens)
        log.info("polymarket subscribing to %d tokens", len(tokens))
        return [{"assets_ids": tokens, "type": "market"}]

    async def on_tick(self, ws) -> None:
        new = [t for t in self.token_ids(await self.discover()) if t not in self.subscribed]
        if new:
            self.subscribed.update(new)
            log.info("polymarket adding %d tokens", len(new))
            await ws.send(orjson.dumps({"assets_ids": new, "operation": "subscribe"}).decode())

    def channel_of(self, raw: str | bytes) -> str | None:
        try:
            msg = orjson.loads(raw)
        except orjson.JSONDecodeError:
            return None  # PONG
        if isinstance(msg, list):  # initial book snapshots arrive as an array
            return "book"
        if not isinstance(msg, dict):
            return None
        if "price_changes" in msg:
            return "price_change"
        return msg.get("event_type") or "other"
