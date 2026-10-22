"""Polymarket history for the short-horizon crypto "Up or Down" markets.

Markets are named `<asset>-updown-<kind>-<start epoch>` (kind: 5m or 15m, start aligned to
the interval), so we enumerate them by constructing slugs instead of paging every market.
They resolve "Up" if the Chainlink TWAP over the interval is >= the price at its start (a
different oracle from Kalshi's BRTI average, so the two venues can disagree on the outcome).

Prices come from the CLOB `prices-history` endpoint queried with an explicit time window
(`startTs`/`endTs`, fidelity=1 minute). The `interval=max` form returns nothing for old
resolved markets. Points exist only for minutes with activity.
"""

from __future__ import annotations

import calendar
import logging
import time
from pathlib import Path

import orjson
import polars as pl

from drc.backfill.http import Client
from drc.backfill.schemas import MARKETS_SCHEMA, PRICE_SAMPLES_SCHEMA

log = logging.getLogger(__name__)
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
VENUE = "polymarket"
KIND_SECS = {"5m": 300, "15m": 900}


def _ns(iso: str | None) -> int | None:
    if not iso:
        return None
    return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) * 1_000_000_000


def slugs(asset: str, kind: str, start_ts: int, end_ts: int) -> list[tuple[str, int]]:
    """(slug, interval start) for every interval starting in [start_ts, end_ts)."""
    step = KIND_SECS[kind]
    first = start_ts - start_ts % step
    return [(f"{asset}-updown-{kind}-{t}", t) for t in range(first, end_ts, step)]


def market_row(event: dict, series: str, start_s: int, kind: str) -> dict | None:
    """The 'Up' contract of an up/down event, or None if the event has no usable market."""
    if not event.get("markets"):
        return None
    m = event["markets"][0]
    outcomes = orjson.loads(m["outcomes"]) if isinstance(m["outcomes"], str) else m["outcomes"]
    tokens = (
        orjson.loads(m["clobTokenIds"]) if isinstance(m["clobTokenIds"], str) else m["clobTokenIds"]
    )
    if "Up" not in outcomes:
        return None
    i = outcomes.index("Up")
    prices = m.get("outcomePrices")
    prices = orjson.loads(prices) if isinstance(prices, str) else prices
    result = None
    if event.get("closed") and prices and float(prices[i]) in (0.0, 1.0):
        result = float(prices[i])
    return {
        "venue": VENUE,
        "market_id": m["conditionId"],
        "token_id": tokens[i],
        "event_id": event.get("slug"),
        "series": series,
        "title": event.get("title"),
        "outcome": "Up",
        "open_ts": start_s * 1_000_000_000,
        "close_ts": (start_s + KIND_SECS[kind]) * 1_000_000_000,
        "result_value": result,
        "strike": None,
        "strike_type": None,
        "volume": float(m["volumeNum"]) if m.get("volumeNum") is not None else None,
        "rules": m.get("description"),
        "tier": "gamma",
    }


def fetch_updown_markets(
    client: Client, asset: str, kind: str, start_ts: int, end_ts: int
) -> pl.DataFrame:
    series = f"{asset}-updown-{kind}"
    rows = []
    for slug, start_s in slugs(asset, kind, start_ts, end_ts):
        events = client.json(f"{GAMMA}/events", slug=slug)
        if events and (row := market_row(events[0], series, start_s, kind)):
            rows.append(row)
    log.info("polymarket %s: %d markets", series, len(rows))
    return pl.DataFrame(rows, schema=MARKETS_SCHEMA).sort("close_ts")


def samples_from_history(
    history: list[dict], market: dict, offsets: list[int], lookback: int = 120
) -> list[dict]:
    """Last price at or before each `close - offset` moment, within `lookback` seconds."""
    close_s = market["close_ts"] // 1_000_000_000
    out = []
    for off in offsets:
        target = close_s - off
        window = [h for h in history if target - lookback <= h["t"] <= target]
        if not window:
            continue
        last = max(window, key=lambda h: h["t"])
        out.append(
            {
                "venue": VENUE,
                "market_id": market["market_id"],
                "target_ts": target * 1_000_000_000,
                "secs_to_close": off,
                "price": float(last["p"]),
                "last_trade_ts": last["t"] * 1_000_000_000,
                "n_window": len(window),
            }
        )
    return out


def sample_series(
    client: Client, out_root: Path, markets: pl.DataFrame, offsets: list[int], chunk: int = 50
) -> int:
    series = markets["series"][0]
    dest = out_root / "price_samples" / f"venue={VENUE}" / f"series={series}"
    dest.mkdir(parents=True, exist_ok=True)
    files = list(dest.glob("*.parquet"))
    done = set(pl.read_parquet(files)["market_id"].unique().to_list()) if files else set()
    todo = [m for m in markets.iter_rows(named=True) if m["market_id"] not in done]
    log.info("polymarket %s: %d markets to sample (%d done)", series, len(todo), len(done))
    written = 0
    for i in range(0, len(todo), chunk):
        rows = []
        for m in todo[i : i + chunk]:
            body = client.json(
                f"{CLOB}/prices-history",
                market=m["token_id"],
                startTs=m["open_ts"] // 10**9,
                endTs=m["close_ts"] // 10**9,
                fidelity=1,
            )
            got = samples_from_history(body.get("history", []), m, offsets)
            rows += got or [  # sentinel so empty markets are not refetched forever
                {
                    "venue": VENUE, "market_id": m["market_id"], "target_ts": m["close_ts"],
                    "secs_to_close": 0, "price": None, "last_trade_ts": None, "n_window": 0,
                }
            ]  # fmt: skip
        pl.DataFrame(rows, schema=PRICE_SAMPLES_SCHEMA).write_parquet(
            dest / f"part-{time.time_ns()}.parquet", compression="zstd"
        )
        written += len(rows)
        log.info("polymarket %s: %d/%d sampled", series, min(i + chunk, len(todo)), len(todo))
    return written


# ---- General resolved markets (broad calibration studies) ----------------------------------

GENERAL_SERIES = "general"
FIDELITY_LADDER = (60, 360, 720, 1440)  # minutes; old markets only return data at coarse fidelity
DAY = 86400
DEFAULT_OFFSETS_DAYS = (30, 14, 7, 3, 1)


def _closed_ns(m: dict) -> int | None:
    """Close time as epoch ns. closedTime looks like '2024-11-06 15:17:41+00'."""
    raw = m.get("closedTime") or m.get("endDate")
    if not raw:
        return None
    return _ns(raw.replace(" ", "T"))


def general_market_row(m: dict) -> dict | None:
    """The 'Yes' token of a cleanly resolved binary market, else None."""
    if not m.get("closed") or "updown" in (m.get("slug") or ""):
        return None
    outcomes = orjson.loads(m["outcomes"]) if isinstance(m["outcomes"], str) else m["outcomes"]
    if outcomes != ["Yes", "No"] or not m.get("clobTokenIds") or not m.get("outcomePrices"):
        return None
    prices = orjson.loads(m["outcomePrices"])
    if float(prices[0]) not in (0.0, 1.0) or float(prices[0]) + float(prices[1]) != 1.0:
        return None  # void, 50/50, or unresolved
    tokens = orjson.loads(m["clobTokenIds"])
    events = m.get("events") or [{}]
    return {
        "venue": VENUE,
        "market_id": m["conditionId"],
        "token_id": tokens[0],
        "event_id": str(events[0].get("id") or events[0].get("slug") or m["conditionId"]),
        "series": GENERAL_SERIES,
        "title": m.get("question"),
        "outcome": "Yes",
        "open_ts": _ns(m["startDate"]) if m.get("startDate") else None,
        "close_ts": _closed_ns(m),
        "result_value": float(prices[0]),
        "strike": None,
        "strike_type": m.get("category"),
        "volume": float(m["volumeNum"]) if m.get("volumeNum") is not None else None,
        "rules": m.get("description"),
        "tier": "gamma",
    }


def fetch_general_markets(
    client: Client, min_volume: float = 50_000, max_markets: int = 2000
) -> pl.DataFrame:
    """Resolved Yes/No markets, largest volume first, until volume or count runs out."""
    rows: list[dict] = []
    cursor = None
    while len(rows) < max_markets:
        params = {"closed": "true", "limit": 100, "order": "volumeNum", "ascending": "false"}
        if cursor:
            params["after_cursor"] = cursor
        body = client.json(f"{GAMMA}/markets/keyset", **params)
        page = body.get("markets", [])
        if not page:
            break
        for m in page:
            if float(m.get("volumeNum") or 0) < min_volume:
                return _general_frame(rows[:max_markets])
            if row := general_market_row(m):
                rows.append(row)
        cursor = body.get("next_cursor")
        if not cursor:
            break
        log.info("polymarket general: %d resolved markets so far", len(rows))
    return _general_frame(rows[:max_markets])


def _general_frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=MARKETS_SCHEMA).drop_nulls("close_ts").sort("close_ts")


def fetch_history(client: Client, token_id: str) -> tuple[list[dict], int | None]:
    """Whole-life price path at the finest fidelity available: (points, fidelity)."""
    for fidelity in FIDELITY_LADDER:
        body = client.json(
            f"{CLOB}/prices-history", market=token_id, interval="max", fidelity=fidelity
        )
        if body.get("history"):
            return body["history"], fidelity
    return [], None


def sample_general(
    client: Client, out_root: Path, markets: pl.DataFrame, offsets: list[int], chunk: int = 50
) -> int:
    dest = out_root / "price_samples" / f"venue={VENUE}" / f"series={GENERAL_SERIES}"
    dest.mkdir(parents=True, exist_ok=True)
    files = list(dest.glob("*.parquet"))
    done = set(pl.read_parquet(files)["market_id"].unique().to_list()) if files else set()
    todo = [m for m in markets.iter_rows(named=True) if m["market_id"] not in done]
    log.info("polymarket general: %d markets to sample (%d done)", len(todo), len(done))
    written = 0
    for i in range(0, len(todo), chunk):
        rows = []
        for m in todo[i : i + chunk]:
            history, fidelity = fetch_history(client, m["token_id"])
            got = samples_from_history(history, m, offsets, lookback=2 * (fidelity or 720) * 60)
            rows += got or [  # sentinel so markets without history are not refetched forever
                {
                    "venue": VENUE, "market_id": m["market_id"], "target_ts": m["close_ts"],
                    "secs_to_close": 0, "price": None, "last_trade_ts": None, "n_window": 0,
                }
            ]  # fmt: skip
        pl.DataFrame(rows, schema=PRICE_SAMPLES_SCHEMA).write_parquet(
            dest / f"part-{time.time_ns()}.parquet", compression="zstd"
        )
        written += len(rows)
        log.info("polymarket general: %d/%d sampled", min(i + chunk, len(todo)), len(todo))
    return written
