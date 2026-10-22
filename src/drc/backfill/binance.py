"""Binance public data (data.binance.vision): spot 1m klines and perpetual funding rates.

Monthly archives cover completed months; the current month is assembled from daily archives.
Spot timestamps are milliseconds before 2025 and microseconds from 2025-01-01, so units are
detected by magnitude. Parsed output is cached as Parquet; completed months are never refetched.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
import zipfile
from pathlib import Path

import polars as pl

from drc.backfill.http import Client
from drc.backfill.schemas import FUNDING_SCHEMA, KLINES_SCHEMA

log = logging.getLogger(__name__)
BASE = "https://data.binance.vision/data"


def to_ns(ts: int) -> int:
    """Binance timestamps: ms (13 digits) or us (16 digits)."""
    if ts > 10**15:
        return ts * 1_000
    return ts * 1_000_000


def parse_klines_csv(raw: bytes, symbol: str) -> pl.DataFrame:
    df = pl.read_csv(
        io.BytesIO(raw),
        has_header=False,
        new_columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "n_trades",
            "taker_buy_volume",
            "taker_buy_quote",
            "ignore",
        ],  # fmt: skip
        infer_schema_length=1000,
    )
    if df["open_time"].dtype == pl.Utf8:  # a header row slipped in
        df = df.filter(pl.col("open_time").str.contains(r"^\d+$")).with_columns(
            pl.col("open_time").cast(pl.Int64)
        )
    return df.select(
        pl.lit(symbol).alias("symbol"),
        pl.col("open_time").map_elements(to_ns, return_dtype=pl.Int64).alias("ts"),
        *(pl.col(c).cast(pl.Float64) for c in ("open", "high", "low", "close", "volume")),
        pl.col("n_trades").cast(pl.Int64),
        pl.col("taker_buy_volume").cast(pl.Float64),
    ).cast(KLINES_SCHEMA)


def parse_funding_csv(raw: bytes, symbol: str) -> pl.DataFrame:
    df = pl.read_csv(io.BytesIO(raw))
    return df.select(
        pl.lit(symbol).alias("symbol"),
        (pl.col("calc_time") * 1_000_000).alias("ts"),
        pl.col("last_funding_rate").cast(pl.Float64).alias("rate"),
        pl.col("funding_interval_hours").cast(pl.Int64).alias("interval_hours"),
    ).cast(FUNDING_SCHEMA)


def _unzip_csv(content: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return z.read(z.namelist()[0])


def _months(start: dt.date, end: dt.date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def load_klines(
    out_root: Path,
    symbol: str,
    start: dt.date,
    end: dt.date | None = None,
    client: Client | None = None,
) -> list[Path]:
    """Ensure monthly 1m kline Parquet files exist for [start, end]. Returns the file paths."""
    client = client or Client(min_interval=0.1)
    end = end or dt.date.today()
    today = dt.date.today()
    paths = []
    for y, m in _months(start, end):
        path = out_root / "klines_1m" / f"symbol={symbol}" / f"{y}-{m:02d}.parquet"
        current = (y, m) == (today.year, today.month)
        if path.exists() and not current:
            paths.append(path)
            continue
        frames = []
        if not current:
            url = f"{BASE}/spot/monthly/klines/{symbol}/1m/{symbol}-1m-{y}-{m:02d}.zip"
            if client.exists(url):
                frames.append(parse_klines_csv(_unzip_csv(client.get(url).content), symbol))
        else:  # current month: daily archives up to yesterday
            for d in range(1, today.day):
                day = dt.date(y, m, d)
                url = f"{BASE}/spot/daily/klines/{symbol}/1m/{symbol}-1m-{day}.zip"
                if client.exists(url):
                    frames.append(parse_klines_csv(_unzip_csv(client.get(url).content), symbol))
        if not frames:
            log.warning("no kline data for %s %d-%02d", symbol, y, m)
            continue
        df = pl.concat(frames).unique("ts").sort("ts")
        path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(path, compression="zstd")
        log.info("klines %s %d-%02d: %d rows", symbol, y, m, df.height)
        paths.append(path)
    return paths


def load_funding(
    out_root: Path,
    symbol: str,
    start: dt.date,
    end: dt.date | None = None,
    client: Client | None = None,
) -> Path | None:
    """Funding-rate settlements from monthly archives, merged into one Parquet file."""
    client = client or Client(min_interval=0.1)
    end = end or dt.date.today()
    frames = []
    for y, m in _months(start, end):
        url = f"{BASE}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{y}-{m:02d}.zip"
        if client.exists(url):
            frames.append(parse_funding_csv(_unzip_csv(client.get(url).content), symbol))
    if not frames:
        return None
    df = pl.concat(frames).unique("ts").sort("ts")
    path = out_root / "funding" / f"symbol={symbol}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path, compression="zstd")
    log.info("funding %s: %d rows", symbol, df.height)
    return path
