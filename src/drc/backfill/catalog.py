"""Coverage report: what historical data we hold, its date range, and how complete it is."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl

from drc.backfill.schemas import MARKETS_SCHEMA


def _d(ns: int | None) -> str:
    return dt.datetime.fromtimestamp(ns / 1e9, dt.UTC).strftime("%Y-%m-%d") if ns else "-"


def read_markets(files: list) -> pl.DataFrame:
    """Concatenate markets tables, adding columns that older files were written without."""
    frames = []
    for f in files:
        df = pl.read_parquet(f)
        missing = [c for c in MARKETS_SCHEMA if c not in df.columns]
        df = df.with_columns([pl.lit(None, MARKETS_SCHEMA[c]).alias(c) for c in missing])
        frames.append(df.select(list(MARKETS_SCHEMA)).cast(MARKETS_SCHEMA))
    return pl.concat(frames)


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
        m = read_markets(mfiles)
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
