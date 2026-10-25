"""Polymarket CLOB public market websocket, with top-N-by-volume market discovery.

Discovery uses the Gamma API. The selected markets' full metadata (question, rules, end date,
token ids) is stored in the `_catalog` channel so outcomes can be joined to books later.
New markets found on refresh are added with a dynamic subscribe, so existing streams keep
flowing without a reconnect gap.

Two selection modes. Default: the top-N open markets by 24h volume. Deadline mode
(`deadline_max_days` set): every open market whose SCHEDULED end is within that many days and
whose volume to date is at least `min_volume`, i.e. a universe fixed by information available at
selection time, not by how a market later turned out. It is used to forward-test the
scheduled-end strategy (docs/findings/2026-10-23-scheduled-calibration.md). Subscriptions only
grow, so a selected market is recorded until it resolves; the volume-to-date at each refresh is
in the stored `_catalog` records.
"""

from __future__ import annotations

import datetime as dt
import logging

import httpx
import orjson

from drc.recorder.base import WsSource

log = logging.getLogger(__name__)

GAMMA_URL = "https://gamma-api.polymarket.com/markets"
GAMMA_KEYSET_URL = "https://gamma-api.polymarket.com/markets/keyset"


class PolymarketSource(WsSource):
    venue = "polymarket"
    url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    keepalive_interval = 10.0
    stale_timeout = 300.0

    def __init__(
        self,
        top_n: int = 50,
        refresh_secs: float = 3600.0,
        deadline_max_days: float | None = None,
        min_volume: float = 0.0,
        max_markets: int = 400,
        grace_days: float = 1.0,
    ) -> None:
        self.top_n = top_n
        self.deadline_max_days = deadline_max_days
        self.min_volume = min_volume
        self.max_markets = max_markets
        self.grace_days = grace_days  # keep markets whose scheduled end just passed but are open
        self.tick_interval = refresh_secs
        self.subscribed: set[str] = set()

    def keepalive_message(self) -> str:
        return "PING"

    async def discover(self, client: httpx.AsyncClient | None = None) -> list[dict]:
        if client is None:
            async with httpx.AsyncClient(timeout=20) as own:
                return await self.discover(own)
        if self.deadline_max_days is not None:
            markets = await self._deadline_markets(client)
        else:
            params = {
                "limit": self.top_n,
                "active": "true",
                "closed": "false",
                "order": "volume24hr",
                "ascending": "false",
            }
            resp = await client.get(GAMMA_URL, params=params)
            resp.raise_for_status()
            markets = resp.json()
        markets = [m for m in markets if m.get("enableOrderBook") and m.get("clobTokenIds")]
        self.writer.write(self.venue, "_catalog", orjson.dumps(markets))
        return markets

    async def _deadline_markets(self, client: httpx.AsyncClient) -> list[dict]:
        """Open markets scheduled to end within the window with enough volume so far."""
        now = dt.datetime.now(dt.UTC)

        def iso(days: float) -> str:
            return (now + dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "closed": "false", "active": "true", "limit": 100, "order": "endDate",
            "ascending": "true", "end_date_min": iso(-self.grace_days),
            "end_date_max": iso(self.deadline_max_days), "volume_num_min": self.min_volume,
        }  # fmt: skip
        out: list[dict] = []
        cursor = None
        while len(out) < self.max_markets:
            page_params = dict(params, **({"after_cursor": cursor} if cursor else {}))
            resp = await client.get(GAMMA_KEYSET_URL, params=page_params)
            resp.raise_for_status()
            body = resp.json()
            out += body.get("markets", [])
            cursor = body.get("next_cursor")
            if not cursor or not body.get("markets"):
                break
        return out[: self.max_markets]

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
