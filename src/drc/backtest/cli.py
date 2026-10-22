"""`drc-backtest`: run a baseline strategy over recorded bars and save the run."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import polars as pl

from drc.backtest.engine import Config, run_backtest
from drc.backtest.metrics import summarize
from drc.backtest.strategies import STRATEGIES


def main() -> None:
    ap = argparse.ArgumentParser(prog="drc-backtest")
    ap.add_argument("--venue", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--strategy", choices=sorted(STRATEGIES), required=True)
    ap.add_argument("--bars", default="data/norm/bars_1s", type=Path)
    ap.add_argument("--out", default="data/runs", type=Path)
    ap.add_argument("--cash", type=float, default=10_000.0)
    ap.add_argument("--slippage-bps", type=float, default=1.0)
    ap.add_argument("--latency-bars", type=int, default=1)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    bars = pl.read_parquet(args.bars / f"venue={args.venue}.parquet").filter(
        pl.col("symbol") == args.symbol
    )
    if bars.is_empty():
        raise SystemExit(f"no bars for {args.venue} {args.symbol}")
    cls = STRATEGIES[args.strategy]
    strat = cls(args.symbol, seed=args.seed) if args.strategy == "random" else cls(args.symbol)
    cfg = Config(
        initial_cash=args.cash, slippage_bps=args.slippage_bps, latency_bars=args.latency_bars
    )
    result = run_backtest(bars, strat, cfg)
    summary = summarize(result, bars)

    run_dir = (
        args.out / f"{time.strftime('%Y%m%dT%H%M%S')}-{args.strategy}-{args.venue}-{args.symbol}"
    )
    run_dir.mkdir(parents=True)
    result.equity.write_parquet(run_dir / "equity.parquet")
    result.fills.write_parquet(run_dir / "fills.parquet")
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (run_dir / "events.json").write_text(json.dumps(result.events[:1000]))
    print(json.dumps(summary, indent=2))
    print(f"saved to {run_dir}")
