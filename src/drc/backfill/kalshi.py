"""Kalshi history: settled markets (with how they resolved) and sampled trade prices.

Kalshi keeps recent data behind the live endpoints and moves older data to `/historical/*`
(see `/historical/cutoff`). We read both and tag rows with their tier.

Busy markets have tens of thousands of trades (a single 15-minute BTC market: ~34k), so we do
not download tick history. `sample_prices` asks, for each chosen moment before a market closes,
for the latest trades in a short window and records the last price seen.
"""

from __future__ import annotations

import calendar
import logging
import time
from pathlib import Path

import polars as pl

from drc.backfill.http import Client
from drc.backfill.schemas import MARKETS_SCHEMA, PRICE_SAMPLES_SCHEMA

log = logging.getLogger(__name__)
BASE = "https://api.elections.kalshi.com/trade-api/v2"
VENUE = "kalshi"


def _ns(iso: str | None) -> int | None:
    if not iso:
        return None
    secs = calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    return secs * 1_000_000_000


def market_row(m: dict, tier: str) -> dict:
    result = {"yes": 1.0, "no": 0.0}.get(m.get("result"))
    strike = m.get("floor_strike")
    return {
        "venue": VENUE,
        "market_id": m["ticker"],
        "token_id": None,
        "event_id": m.get("event_ticker"),
        "series": m["event_ticker"].split("-")[0] if m.get("event_ticker") else None,
        "title": m.get("title"),
        "outcome": "yes",
        "open_ts": _ns(m.get("open_time")),
        "close_ts": _ns(m.get("close_time")),
        "result_value": result,
        "strike": float(strike) if strike is not None else None,
        "strike_type": m.get("strike_type"),
        "volume": float(m["volume_fp"]) if m.get("volume_fp") else None,
        "rules": m.get("rules_primary"),
        "tier": tier,
    }


def fetch_markets(
    client: Client, series: str, min_close_ts: int | None = None, tiers=("historical", "live")
) -> pl.DataFrame:
    """All settled markets of a series closing at or after `min_close_ts` (epoch seconds)."""
    rows: list[dict] = []
    for tier in tiers:
        path = "/historical/markets" if tier == "historical" else "/markets"
        cursor = None
        while True:
            params = {"series_ticker": series, "limit": 1000}
            if tier == "live":
                params["status"] = "settled"
            if min_close_ts:
                params["min_close_ts"] = min_close_ts
            if cursor:
                params["cursor"] = cursor
            body = client.json(path, **params)
            rows += [market_row(m, tier) for m in body.get("markets", [])]
            cursor = body.get("cursor")
            if not cursor or not body.get("markets"):
                break
        log.info("kalshi %s %s: %d markets so far", series, tier, len(rows))
    df = pl.DataFrame(rows, schema=MARKETS_SCHEMA)
    df = df.unique(["venue", "market_id"], keep="first")  # a market lives in one tier
    if min_close_ts:  # in case the server ignored the filter
        df = df.filter(pl.col("close_ts") >= min_close_ts * 1_000_000_000)
    return df.sort("close_ts")


def sample_prices(
    client: Client,
    market: dict,
    offsets: list[int],
    lookback: int = 120,
    limit: int = 50,
) -> list[dict]:
    """Last trade price at each `close - offset` moment (seconds), looking back `lookback`s."""
    path = "/historical/trades" if market["tier"] == "historical" else "/markets/trades"
    out = []
    close_s = market["close_ts"] // 1_000_000_000
    for off in offsets:
        target = close_s - off
        body = client.json(
            path, ticker=market["market_id"], min_ts=target - lookback, max_ts=target, limit=limit
        )
        trades = body.get("trades", [])
        if not trades:
            continue
        latest = max(trades, key=lambda t: t["created_time"])
        out.append(
            {
                "venue": VENUE,
                "market_id": market["market_id"],
                "target_ts": target * 1_000_000_000,
                "secs_to_close": off,
                "price": float(latest["yes_price_dollars"]),
                "last_trade_ts": _ns(latest["created_time"]),
                "n_window": len(trades),
            }
        )
    return out


def sample_series(
    client: Client,
    out_root: Path,
    markets: pl.DataFrame,
    offsets: list[int],
    chunk: int = 25,
) -> int:
    """Sample every market not already sampled; append chunked Parquet. Returns rows written."""
    series = markets["series"][0]
    dest = out_root / "price_samples" / f"venue={VENUE}" / f"series={series}"
    dest.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    files = list(dest.glob("*.parquet"))
    if files:
        done = set(pl.read_parquet(files)["market_id"].unique().to_list())
    todo = [m for m in markets.iter_rows(named=True) if m["market_id"] not in done]
    log.info("kalshi %s: %d markets to sample (%d already done)", series, len(todo), len(done))
    written = 0
    for i in range(0, len(todo), chunk):
        rows = []
        for m in todo[i : i + chunk]:
            got = sample_prices(client, m, offsets)
            # a market with no trades at all still counts as done: record a sentinel-free skip
            rows += got or [
                {
                    "venue": VENUE, "market_id": m["market_id"], "target_ts": m["close_ts"],
                    "secs_to_close": 0, "price": None, "last_trade_ts": None, "n_window": 0,
                }
            ]  # fmt: skip
        pl.DataFrame(rows, schema=PRICE_SAMPLES_SCHEMA).write_parquet(
            dest / f"part-{time.time_ns()}.parquet", compression="zstd"
        )
        written += len(rows)
        log.info("kalshi %s: %d/%d markets sampled", series, min(i + chunk, len(todo)), len(todo))
    return written
