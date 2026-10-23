"""Coverage report: what historical data we hold, its date range, and how complete it is."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl


def _d(ns: int | None) -> str:
    return dt.datetime.fromtimestamp(ns / 1e9, dt.UTC).strftime("%Y-%m-%d") if ns else "-"


def coverage(root: Path) -> list[str]:
    lines: list[str] = []
    for kind, label in (("klines_1m", "klines 1m"), ("funding", "funding")):
        files = list((root / kind).rglob("*.parquet"))
        if not files:
            continue
        df = pl.read_parquet(files)
        for (sym,), g in df.group_by("symbol", maintain_order=True):
            span = f"{_d(g['ts'].min())} .. {_d(g['ts'].max())}"
            lines.append(f"{label:11} {sym:10} {g.height:>9,} rows  {span}")
    mfiles = list((root / "markets").rglob("*.parquet"))
    if mfiles:
        m = pl.scan_parquet(mfiles, missing_columns="insert").collect()
        samples = None
        sfiles = list((root / "price_samples").rglob("*.parquet"))
        if sfiles:
            samples = pl.read_parquet(sfiles)
        for (venue, series), g in m.group_by("venue", "series", maintain_order=True):
            resolved = g["result_value"].is_not_null().sum()
            line = (
                f"markets     {venue}/{series:12} {g.height:>7,} markets "
                f"({resolved:,} resolved)  {_d(g['close_ts'].min())} .. {_d(g['close_ts'].max())}"
            )
            if samples is not None:
                ids = g["market_id"]
                s = samples.filter(pl.col("market_id").is_in(ids))
                line += f"  | sampled {s['market_id'].n_unique():,}"
            lines.append(line)
    return lines or ["(no historical data yet)"]
