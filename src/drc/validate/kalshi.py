"""Check Kalshi trade capture against Kalshi's own REST history (needs network).

    uv run python -m drc.validate.kalshi data/raw [n_tickers]

Our `trades` channel is a REST poll of the global feed with a small overlap and a page cap, so
it can lose trades silently when a poll window holds more than the cap. Ground truth: ask the
same REST endpoint per ticker, over the exact window we recorded, and compare trade ids and the
fields we keep. Tickers are the busiest ones in the recording plus a few random others.

Not covered here: order books (REST samples of current state; there is no history to compare).
"""

from __future__ import annotations

import random
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from drc.normalize.timeutil import iso_to_ns
from drc.recorder.writer import read_lines

BASE = "https://api.elections.kalshi.com/trade-api/v2"
KEPT = ("yes_price_dollars", "count_fp", "taker_side", "created_time")


@dataclass
class Result:
    tickers: int = 0
    raw: int = 0
    rest: int = 0
    missing_from_raw: int = 0  # REST has it inside our recorded window, we do not
    not_in_rest: int = 0  # we have it, REST does not
    field_mismatch: int = 0
    examples: list[str] = field(default_factory=list)
    missing_times: list[str] = field(default_factory=list)  # created_time of each missing trade


def fetch_rest(ticker: str, lo_s: int, hi_s: int, client: httpx.Client | None = None) -> list[dict]:
    own = client is None
    client = client or httpx.Client(timeout=30)
    out: list[dict] = []
    try:
        cursor = None
        while True:
            params = {"ticker": ticker, "min_ts": lo_s, "max_ts": hi_s, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            for attempt in range(8):  # be polite: the API rate-limits bursts
                time.sleep(0.4)
                r = client.get(f"{BASE}/markets/trades", params=params)
                if r.status_code != 429:
                    break
                time.sleep(min(30.0, 2.0 * 2**attempt))
            r.raise_for_status()
            body = r.json()
            out += body["trades"]
            cursor = body.get("cursor")
            if not cursor or not body["trades"]:
                return out
    finally:
        if own:
            client.close()


def raw_trades(raw_root: Path) -> dict[str, dict[str, dict]]:
    by_ticker: dict[str, dict[str, dict]] = {}
    for f in sorted((raw_root / "kalshi" / "trades").glob("*/*.jsonl*")):
        for rec in read_lines(f):
            m = rec["m"]
            by_ticker.setdefault(m["ticker"], {})[m["trade_id"]] = m
    return by_ticker


def check(
    raw_root: Path,
    n_tickers: int = 10,
    fetch: Callable[[str, int, int], list[dict]] = fetch_rest,
    seed: int = 0,
    margin_s: int = 30,
) -> Result:
    """Compare per ticker over the window between the first and last trade we recorded for it,
    shrunk by `margin_s` at both ends (poll windows overlap and the clocks differ)."""
    by_ticker = raw_trades(raw_root)
    busiest = [
        t for t, _ in Counter({t: len(v) for t, v in by_ticker.items()}).most_common(n_tickers)
    ]
    rng = random.Random(seed)
    rest_of = [t for t in by_ticker if t not in busiest]
    picks = busiest[: (n_tickers + 1) // 2] + rng.sample(rest_of, min(len(rest_of), n_tickers // 2))
    res = Result()
    # the recording's own time span (all tickers): a ticker's first/last trade is not its coverage
    span_lo = min(iso_to_ns(m["created_time"]) for v in by_ticker.values() for m in v.values())
    span_hi = max(iso_to_ns(m["created_time"]) for v in by_ticker.values() for m in v.values())
    lo_s, hi_s = span_lo // 10**9 + margin_s, span_hi // 10**9 - margin_s
    for ticker in picks:
        mine = {
            i: m
            for i, m in by_ticker[ticker].items()
            if lo_s <= iso_to_ns(m["created_time"]) // 10**9 <= hi_s
        }
        theirs = {t["trade_id"]: t for t in fetch(ticker, lo_s, hi_s)}
        res.tickers += 1
        res.raw += len(mine)
        res.rest += len(theirs)
        for i in theirs.keys() - mine.keys():
            res.missing_from_raw += 1
            res.missing_times.append(theirs[i]["created_time"])
            if len(res.examples) < 5:
                res.examples.append(f"{ticker}: REST has {i} at {theirs[i]['created_time']}")
        res.not_in_rest += len(mine.keys() - theirs.keys())
        for i in mine.keys() & theirs.keys():
            if any(mine[i][k] != theirs[i][k] for k in KEPT):
                res.field_mismatch += 1
    return res


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw")
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    r = check(root, n)
    print(
        f"{r.tickers} tickers: raw {r.raw} trades, REST {r.rest}; missing from raw "
        f"{r.missing_from_raw}, not in REST {r.not_in_rest}, field mismatches {r.field_mismatch}"
    )
    print("\n".join(r.examples))
    by_minute = Counter(t[:16] for t in r.missing_times)
    print("missing by minute (UTC):", dict(sorted(by_minute.items())))


if __name__ == "__main__":
    main()
