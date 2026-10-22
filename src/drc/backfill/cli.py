"""`drc-backfill`: refresh historical data. Everything is resumable and safe to re-run."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time
from pathlib import Path

import polars as pl

from drc.backfill import binance, kalshi
from drc.backfill.catalog import coverage
from drc.backfill.http import Client
from drc.backfill.schemas import MARKETS_SCHEMA


def save_markets(root: Path, df: pl.DataFrame, venue: str, series: str) -> Path:
    path = root / "markets" / f"venue={venue}" / f"series={series}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df.cast(MARKETS_SCHEMA)  # all-null columns can arrive typed Null
    if path.exists():  # merge with what we had; newest fetch wins
        df = pl.concat([df, pl.read_parquet(path)]).unique(["venue", "market_id"], keep="first")
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

    sub.add_parser("catalog", help="show what we hold")
    args = ap.parse_args()
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")

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
    print("\n".join(coverage(args.root)))
