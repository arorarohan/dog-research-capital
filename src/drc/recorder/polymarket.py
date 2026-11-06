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

Sharding (#23): one connection carrying ~800 tokens was closed by Polymarket with 1013 "slow
consumer: send buffer full" ~18 times a day, each a gap. Tokens are now spread over connections
of at most `shard_size` (`PolymarketShard`); one shard reconnecting leaves the others streaming,
and its `connect` event lists its tokens so only their books resync (see `Epochs` in
normalize/pipeline.py). A token that leaves the universe stays on its live connection and is
left out when that shard next reconnects, as it was before when every reconnect re-ran discovery.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

import httpx
import orjson
import websockets

from drc.recorder.base import MAX_BACKOFF, WsSource
from drc.recorder.writer import RawWriter

log = logging.getLogger(__name__)

GAMMA_URL = "https://gamma-api.polymarket.com/markets"
GAMMA_KEYSET_URL = "https://gamma-api.polymarket.com/markets/keyset"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def channel_of(raw: str | bytes) -> str | None:
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


class PolymarketSource:
    """Market discovery plus the shard connections that stream the chosen tokens."""

    venue = "polymarket"
    writer: RawWriter

    def __init__(
        self,
        top_n: int = 50,
        refresh_secs: float = 3600.0,
        deadline_max_days: float | None = None,
        min_volume: float = 0.0,
        max_markets: int = 400,
        grace_days: float = 1.0,
        shard_size: int = 200,
    ) -> None:
        self.top_n = top_n
        self.deadline_max_days = deadline_max_days
        self.min_volume = min_volume
        self.max_markets = max_markets
        self.grace_days = grace_days  # keep markets whose scheduled end just passed but are open
        self.refresh_secs = refresh_secs
        self.shard_size = shard_size
        self.subscribed: set[str] = set()
        self.shards: list[PolymarketShard] = []
        self._tg: asyncio.TaskGroup | None = None

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

    channel_of = staticmethod(channel_of)

    async def run(self, writer: RawWriter) -> None:
        """Discover, start one shard per `shard_size` tokens, then refresh every `refresh_secs`.
        Any shard failing (beyond the reconnects it handles itself) stops them all."""
        self.writer = writer
        tokens = list(dict.fromkeys(await self._initial_tokens()))  # one connection per token
        self.subscribed = set(tokens)
        log.info("polymarket subscribing to %d tokens", len(tokens))
        async with asyncio.TaskGroup() as tg:
            self._tg = tg
            for i in range(0, len(tokens), self.shard_size):
                self._start_shard(tokens[i : i + self.shard_size])
            tg.create_task(self._refresh_loop())

    async def _initial_tokens(self) -> list[str]:
        backoff = 1.0
        while True:
            try:
                if tokens := self.token_ids(await self.discover()):
                    return tokens
                log.warning("polymarket discovery found no markets; retry in %.0fs", backoff)
            except Exception as e:  # noqa: BLE001 - the venue API flaking must not end the run
                log.warning("polymarket discovery failed: %r; retry in %.0fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

    def _start_shard(self, tokens: list[str]) -> PolymarketShard:
        shard = PolymarketShard(len(self.shards), tokens, self.writer)
        self.shards.append(shard)
        self._tg.create_task(shard.run(self.writer), name=shard.name)
        return shard

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self.refresh_secs)
            try:
                tokens = self.token_ids(await self.discover())
            except Exception as e:  # noqa: BLE001 - keep streaming; try again next refresh
                log.warning("polymarket refresh failed: %r; keeping current subscriptions", e)
                continue
            await self.apply_universe(tokens)

    async def apply_universe(self, tokens: list[str]) -> None:
        """Subscribe new tokens (least-loaded shard with room, else a new shard). Tokens that
        left the universe are dropped from their shard's next reconnect but not unsubscribed
        from a live connection."""
        if not tokens:
            log.warning("polymarket refresh found no markets; keeping current subscriptions")
            return
        current = set(tokens)
        gone = self.subscribed - current
        if gone:
            for shard in self.shards:
                shard.drop(gone)
            log.info("polymarket: %d tokens left the universe", len(gone))
        new = [t for t in dict.fromkeys(tokens) if t not in self.subscribed]
        self.subscribed = (self.subscribed - gone) | set(new)
        for shard in self.shards:  # back before its old shard reconnected: still streaming there,
            new = shard.readopt(new)  # so subscribing elsewhere would record it twice
        while new:
            open_shards = [s for s in self.shards if len(s.tokens) < self.shard_size]
            if not open_shards:
                self._start_shard(new[: self.shard_size])
                new = new[self.shard_size :]
                continue
            shard = min(open_shards, key=lambda s: len(s.tokens))
            take, new = (
                new[: self.shard_size - len(shard.tokens)],
                new[self.shard_size - len(shard.tokens) :],
            )
            await shard.add(take)
        log.info(
            "polymarket: %d tokens on %d connections %s",
            len(self.subscribed),
            len(self.shards),
            [len(s.tokens) for s in self.shards],
        )


class PolymarketShard(WsSource):
    """One market-channel connection carrying a slice of the tokens.

    Every connect event lists exactly the tokens its subscription covered, and a token added
    to a live connection gets a `subscribe` event, so replay resets only this shard's books."""

    venue = "polymarket"
    url = WS_URL
    keepalive_interval = 10.0
    stale_timeout = 300.0
    channel_of = staticmethod(channel_of)

    def __init__(self, shard_id: int, tokens: list[str], writer: RawWriter) -> None:
        self.shard_id = shard_id
        self.tokens = list(tokens)
        self.writer = writer
        self._sent: list[str] = []  # tokens in the subscription of the current connection
        self._ws = None
        self._has_tokens = asyncio.Event()
        if self.tokens:
            self._has_tokens.set()

    @property
    def name(self) -> str:
        return f"polymarket[{self.shard_id}]"

    def keepalive_message(self) -> str:
        return "PING"

    def event_fields(self) -> dict:
        return {"shard": self.shard_id}

    async def subscriptions(self) -> list[dict]:
        await self._has_tokens.wait()  # a shard emptied by refreshes waits for new tokens
        self._ws = None
        self._sent = list(self.tokens)
        return [{"assets_ids": self._sent, "type": "market"}]

    def connect_assets(self) -> list[str]:
        return self._sent

    async def on_connected(self, ws) -> None:
        self._ws = ws
        sent = set(self._sent)
        if late := [t for t in self.tokens if t not in sent]:  # added while connecting
            await self._subscribe(late)

    async def add(self, tokens: list[str]) -> None:
        self.tokens += tokens
        self._has_tokens.set()
        if self._ws is not None:
            await self._subscribe(tokens)

    def drop(self, gone: set[str]) -> None:
        self.tokens = [t for t in self.tokens if t not in gone]
        if not self.tokens:
            self._has_tokens.clear()

    def readopt(self, tokens: list[str]) -> list[str]:
        """Take back tokens this live connection still streams; return the others."""
        live = set(self._sent) if self._ws is not None else set()
        mine = [t for t in tokens if t in live and t not in self.tokens]
        if mine:
            self.tokens += mine
            self._has_tokens.set()
        return [t for t in tokens if t not in set(mine)]

    async def _subscribe(self, tokens: list[str]) -> None:
        try:
            await self._ws.send(
                orjson.dumps({"assets_ids": tokens, "operation": "subscribe"}).decode()
            )
        except websockets.ConnectionClosed:
            return  # the reconnect subscribes to everything in self.tokens
        self._sent += tokens
        self.writer.event(self.venue, "subscribe", shard=self.shard_id, assets=tokens)
        log.info("%s adding %d tokens", self.name, len(tokens))
