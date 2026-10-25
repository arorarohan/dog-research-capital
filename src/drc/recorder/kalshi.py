"""Kalshi public REST poller: order books for the top-N markets, plus the global trade feed.

Kalshi's websocket requires API-key auth, so for now we poll the public endpoints. Books are
therefore sampled snapshots (every `poll_secs`), not a full event stream; we can upgrade to
the websocket once we have a key. The books endpoint does not echo the ticker, so each stored
record is wrapped as `{"ticker": ..., "resp": <response>}`.

Two modes. Default: the top-N open markets by 24h volume across Kalshi. Series mode
(`series=("KXHIGHNY", ...)`): every open market of just those series, and only their trades;
used to measure spreads in thin niche markets (e.g. daily-high weather brackets) that a
global top-N would never pick.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict

import httpx
import orjson

from drc.recorder.writer import RawWriter

log = logging.getLogger(__name__)

BASE = "https://api.elections.kalshi.com/trade-api/v2"
MAX_CATALOG_PAGES = 200
MAX_TRADE_PAGES = 20
SEEN_TRADES_CAP = 50_000


class KalshiPoller:
    venue = "kalshi"

    def __init__(
        self,
        top_n: int = 50,
        poll_secs: float = 15.0,
        catalog_secs: float = 3600.0,
        concurrency: int = 5,
        exclude_trade_prefixes: tuple[str, ...] = ("KXMVE",),
        series: tuple[str, ...] = (),
    ) -> None:
        self.exclude_trade_prefixes = exclude_trade_prefixes
        self.series = tuple(series)
        self.top_n = top_n
        self.poll_secs = poll_secs
        self.catalog_secs = catalog_secs
        self.sem = asyncio.Semaphore(concurrency)
        self.tickers: list[str] = []
        self._seen_trades: OrderedDict[str, None] = OrderedDict()
        self._last_trade_ts = int(time.time())

    async def _open_markets(self, client: httpx.AsyncClient, **filters: str) -> list[dict]:
        markets: list[dict] = []
        cursor = None
        for _ in range(MAX_CATALOG_PAGES):
            params = {"limit": 1000, "status": "open", **filters}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get(f"{BASE}/markets", params=params)
            resp.raise_for_status()
            body = resp.json()
            markets += body["markets"]
            cursor = body.get("cursor")
            if not cursor:
                break
        return markets

    async def refresh_catalog(self, client: httpx.AsyncClient, writer: RawWriter) -> None:
        """Pick the markets to poll: all open markets of `series`, else the top N by 24h volume."""
        if self.series:
            top = []
            for name in self.series:
                top += await self._open_markets(client, series_ticker=name)
            self.tickers = [m["ticker"] for m in top]
            writer.write(self.venue, "_catalog", orjson.dumps(top))
            log.info("kalshi tracking %d open markets in %s", len(top), ",".join(self.series))
            return
        markets = await self._open_markets(client, mve_filter="exclude")
        markets.sort(key=lambda m: float(m.get("volume_24h_fp") or 0), reverse=True)
        top = markets[: self.top_n]
        self.tickers = [m["ticker"] for m in top]
        writer.write(self.venue, "_catalog", orjson.dumps(top))
        log.info("kalshi tracking %d of %d open markets", len(top), len(markets))

    async def poll_book(self, client: httpx.AsyncClient, writer: RawWriter, ticker: str) -> None:
        async with self.sem:
            resp = await client.get(f"{BASE}/markets/{ticker}/orderbook")
        resp.raise_for_status()
        t = time.time_ns()
        writer.write(
            self.venue,
            "orderbook",
            b'{"ticker":"%s","resp":%s}' % (ticker.encode(), resp.content),
            t,
        )

    async def poll_trades(self, client: httpx.AsyncClient, writer: RawWriter) -> None:
        """Global trade feed since the last poll. Overlapping windows are de-duplicated by id."""
        since = self._last_trade_ts - 2
        self._last_trade_ts = int(time.time())
        cursor = None
        for _ in range(MAX_TRADE_PAGES):
            params = {"limit": 1000, "min_ts": since}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get(f"{BASE}/markets/trades", params=params)
            resp.raise_for_status()
            body = resp.json()
            for trade in body["trades"]:
                tid = trade["trade_id"]
                if trade["ticker"].startswith(self.exclude_trade_prefixes):
                    continue  # multivariate parlays: ~20% of the feed, not our focus
                if self.series and not trade["ticker"].startswith(
                    tuple(f"{name}-" for name in self.series)
                ):
                    continue
                if tid in self._seen_trades:
                    continue
                self._seen_trades[tid] = None
                writer.write(self.venue, "trades", orjson.dumps(trade))
            while len(self._seen_trades) > SEEN_TRADES_CAP:
                self._seen_trades.popitem(last=False)
            cursor = body.get("cursor")
            if not cursor or not body["trades"]:
                break

    async def _catalog_loop(self, client: httpx.AsyncClient, writer: RawWriter) -> None:
        """Hourly full scan (~160 requests, about a minute); runs beside book polling."""
        while True:
            try:
                await self.refresh_catalog(client, writer)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - keep the previous ticker list, retry soon
                writer.event(self.venue, "catalog_error", reason=repr(e))
                log.warning("kalshi catalog error: %r", e)
                await asyncio.sleep(60)
                continue
            await asyncio.sleep(self.catalog_secs)

    async def run(self, writer: RawWriter) -> None:
        async with httpx.AsyncClient(timeout=20) as client:
            catalog_task = asyncio.create_task(self._catalog_loop(client, writer))
            writer.event(self.venue, "connect", mode="rest-poll")
            try:
                while True:
                    started = time.monotonic()
                    results = await asyncio.gather(
                        self.poll_trades(client, writer),
                        *(self.poll_book(client, writer, t) for t in list(self.tickers)),
                        return_exceptions=True,
                    )
                    errors = [r for r in results if isinstance(r, Exception)]
                    if errors:
                        first = repr(errors[0])
                        writer.event(self.venue, "poll_errors", n=len(errors), first=first)
                        log.warning("kalshi %d poll errors, first: %s", len(errors), first)
                    await asyncio.sleep(max(0.0, self.poll_secs - (time.monotonic() - started)))
            except asyncio.CancelledError:
                writer.event(self.venue, "shutdown")
                raise
            finally:
                catalog_task.cancel()
