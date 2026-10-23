"""`drc-backfill`: refresh historical data. Everything is resumable and safe to re-run."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time
from pathlib import Path

import polars as pl

from drc.backfill import binance, kalshi, polymarket
from drc.backfill.catalog import coverage
from drc.backfill.http import Client
from drc.backfill.schemas import MARKETS_SCHEMA


def save_markets(root: Path, df: pl.DataFrame, venue: str, series: str) -> Path:
    path = root / "markets" / f"venue={venue}" / f"series={series}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df.cast(MARKETS_SCHEMA)  # all-null columns can arrive typed Null
    if path.exists():  # merge with what we had; newest fetch wins
        old = pl.read_parquet(path)
        old = old.with_columns(  # tables written before a column existed
            [
                pl.lit(None, MARKETS_SCHEMA[c]).alias(c)
                for c in MARKETS_SCHEMA
                if c not in old.columns
            ]
        ).select(list(MARKETS_SCHEMA))
        df = pl.concat([df, old]).unique(["venue", "market_id"], keep="first")
    df.sort("close_ts").write_parquet(path, compression="zstd")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(prog="drc-backfill")
    ap.add_argument("--root", default="data/hist", type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("binance", help="1m klines and funding rates")
    b.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    b.add_argument("--since", default="2024-12-01", help="YYYY-MM-DD")

    k = sub.add_parser("kalshi", help="settled markets and sampled prices")
    k.add_argument("--series", default="KXBTC15M,KXETH15M")
    k.add_argument("--days", type=int, default=30)
    k.add_argument(
        "--offsets", default="840,600,420,300,180,90,30", help="seconds before close to sample"
    )
    k.add_argument("--no-sample", action="store_true", help="only fetch markets and outcomes")

    p = sub.add_parser("polymarket", help="crypto up/down markets and sampled prices")
    p.add_argument("--assets", default="btc,eth")
    p.add_argument("--kinds", default="15m", help="5m and/or 15m, comma-separated")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--offsets", default="840,600,420,300,180,90,30")
    p.add_argument("--no-sample", action="store_true")

    g = sub.add_parser("polymarket-general", help="resolved Yes/No markets, largest first")
    g.add_argument("--end-min", default="2024-01-01", help="scheduled end >= (YYYY-MM-DD)")
    g.add_argument("--end-max", default="2026-09-01", help="scheduled end < (YYYY-MM-DD)")
    g.add_argument("--min-volume", type=float, default=50_000, help="USD volume floor")
    g.add_argument("--max-markets", type=int, default=20_000)
    g.add_argument("--offsets-days", default="30,14,7,3,1", help="days before close to sample")
    g.add_argument("--no-sample", action="store_true")

    sub.add_parser("catalog", help="show what we hold")
    args = ap.parse_args()
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.cmd == "binance":
        since = dt.date.fromisoformat(args.since)
        for sym in args.symbols.split(","):
            binance.load_klines(args.root, sym, since)
            binance.load_funding(args.root, sym, since)
    elif args.cmd == "kalshi":
        client = Client(kalshi.BASE, min_interval=0.25)
        offsets = [int(x) for x in args.offsets.split(",")]
        min_close = int(time.time()) - args.days * 86400
        for series in args.series.split(","):
            markets = kalshi.fetch_markets(client, series, min_close)
            save_markets(args.root, markets, "kalshi", series)
            if not args.no_sample and markets.height:
                kalshi.sample_series(client, args.root, markets, offsets)
    elif args.cmd == "polymarket":
        client = Client(min_interval=0.15)
        offsets = [int(x) for x in args.offsets.split(",")]
        end = int(time.time()) - 1800  # leave time for the latest markets to resolve
        start = end - args.days * 86400
        for asset in args.assets.split(","):
            for kind in args.kinds.split(","):
                markets = polymarket.fetch_updown_markets(client, asset, kind, start, end)
                if not markets.height:
                    continue
                save_markets(args.root, markets, "polymarket", f"{asset}-updown-{kind}")
                if not args.no_sample:
                    polymarket.sample_series(client, args.root, markets, offsets)
    elif args.cmd == "polymarket-general":
        client = Client(min_interval=0.15)
        markets = polymarket.fetch_general_markets(
            client,
            f"{args.end_min}T00:00:00Z",
            f"{args.end_max}T00:00:00Z",
            args.min_volume,
            args.max_markets,
        )
        if markets.height:
            save_markets(args.root, markets, "polymarket", polymarket.SCHEDULED_SERIES)
            if not args.no_sample:
                offsets = [int(float(d) * 86400) for d in args.offsets_days.split(",")]
                polymarket.sample_general(client, args.root, markets, offsets)
    print("\n".join(coverage(args.root)))
