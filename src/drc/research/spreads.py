"""Bid/ask spreads on recorded Polymarket books, for judging whether a gross edge survives.

Uses the 1-second top-of-book bars (`drc-bars`) joined to the recorded `_catalog` (scheduled end
date, tick size, fee type). Only seconds with a two-sided, strictly positive spread count; locked
books (bid == ask, seen on near-resolved markets sitting at 99.8c / 0.2c) and one-sided books are
excluded and counted. Caveat: the recorder subscribes to the top-N markets by 24h volume, so
these books are more liquid than the quiet markets a deadline strategy would trade.

Run: `uv run python -m drc.research.spreads`.
"""

from __future__ import annotations

from pathlib import Path

import orjson
import polars as pl

from drc.recorder.writer import read_lines

ROOT = Path("data")
BAND = (0.05, 0.60)  # the price band of the scheduled-end strategy
DAY_NS = 86_400 * 1_000_000_000


def load_catalog(raw_root: Path) -> pl.DataFrame:
    """One row per recorded token (newest catalog record wins): market, scheduled end, tick."""
    rows: dict[str, dict] = {}
    files = sorted((raw_root / "polymarket" / "_catalog").rglob("*.jsonl*"))
    for path in files:
        for rec in read_lines(path):
            for m in rec.get("m", []):
                tokens = m.get("clobTokenIds") or "[]"
                tokens = orjson.loads(tokens) if isinstance(tokens, str) else tokens
                end = m.get("endDate")
                for tok in tokens:
                    rows[tok] = {
                        "symbol": tok,
                        "question": m.get("question"),
                        "end_ts": _ns(end),
                        "tick": m.get("orderPriceMinTickSize"),
                        "fee_type": m.get("feeType"),
                        "volume": m.get("volumeNum"),
                    }
    schema = {
        "symbol": pl.Utf8, "question": pl.Utf8, "end_ts": pl.Int64,
        "tick": pl.Float64, "fee_type": pl.Utf8, "volume": pl.Float64,
    }  # fmt: skip
    return pl.DataFrame(list(rows.values()), schema=schema)


def _ns(iso: str | None) -> int | None:
    import calendar
    import time

    if not iso:
        return None
    return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) * 1_000_000_000


def usable_seconds(bars: pl.DataFrame, catalog: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    """Two-sided, strictly positive-spread bars with catalog fields, plus exclusion counts."""
    both = bars.drop_nulls(["bid", "ask"])
    good = both.filter(pl.col("ask") > pl.col("bid"))
    counts = {
        "bars": bars.height,
        "one_sided": bars.height - both.height,
        "locked_or_crossed": both.height - good.height,
        "tokens": bars["symbol"].n_unique(),
    }
    d = good.join(catalog, on="symbol", how="left").with_columns(
        ((pl.col("end_ts") - pl.col("ts")) / DAY_NS).alias("days_to_end"),
        ((pl.col("bid") + pl.col("ask")) / 2).alias("mid_px"),
        (pl.col("ask") - pl.col("bid")).alias("spr"),
        (pl.col("ask_sz") * pl.col("ask")).alias("ask_usd"),
    )
    return d, counts


def summarize(d: pl.DataFrame, by: str, edges: list[float] | None = None) -> pl.DataFrame:
    """Per-token median spread first (so busy tokens don't dominate), then across tokens."""
    if edges:
        labels = [f"{a:g}-{b:g}" for a, b in zip(edges[:-1], edges[1:], strict=True)]
        d = d.with_columns(
            pl.col(by).cut(edges[1:-1], labels=labels, left_closed=True).cast(pl.Utf8).alias("grp")
        )
    else:
        d = d.with_columns(pl.col(by).cast(pl.Utf8).alias("grp"))
    per_token = d.group_by("grp", "symbol").agg(
        pl.col("spr").median().alias("tok_spread"),
        pl.col("ask_usd").median().alias("tok_ask_usd"),
        pl.len().alias("seconds"),
    )
    return (
        per_token.group_by("grp")
        .agg(
            pl.len().alias("tokens"),
            pl.col("seconds").sum().alias("seconds"),
            pl.col("tok_spread").median().alias("median_spread"),
            (pl.col("tok_spread") <= 0.0105).mean().alias("share_tokens_le_1c"),
            (pl.col("tok_spread") <= 0.0205).mean().alias("share_tokens_le_2c"),
            pl.col("tok_ask_usd").median().alias("median_ask_depth_usd"),
        )
        .sort("grp")
        .with_columns(
            (pl.col("median_spread") / 2).round(4).alias("median_half_spread"),
            pl.col("median_spread").round(4),
            pl.col("share_tokens_le_1c").round(2),
            pl.col("share_tokens_le_2c").round(2),
            pl.col("median_ask_depth_usd").round(0),
        )
    )


def main() -> None:
    bars = pl.read_parquet(ROOT / "norm" / "bars_1s" / "venue=polymarket.parquet")
    d, counts = usable_seconds(bars, load_catalog(ROOT / "raw"))
    span_h = (bars["ts"].max() - bars["ts"].min()) / 1e9 / 3600
    print(
        f"Polymarket recorded books: {counts['tokens']} tokens, {span_h:.2f} h, {counts['bars']:,} "
        f"bars; excluded {counts['one_sided']:,} one-sided and {counts['locked_or_crossed']:,} "
        "locked/crossed"
    )
    inband = d.filter((pl.col("mid_px") >= BAND[0]) & (pl.col("mid_px") < BAND[1]))
    print(f"\nIn the strategy band (mid {BAND[0]:.2f}-{BAND[1]:.2f}): {inband.height:,} seconds")
    print("\nAll in-band tokens, spread in $ per share (0.01 = 1 cent):")
    print(summarize(inband.with_columns(pl.lit("all").alias("all")), "all"))
    print("\nBy days to scheduled end:")
    print(summarize(inband, "days_to_end", [-1000, 1, 3, 7, 30, 10_000]))
    print("\nBy price (mid):")
    print(summarize(inband, "mid_px", [0.05, 0.10, 0.20, 0.40, 0.60]))
    print("\nBy tick size:")
    print(summarize(inband, "tick"))
    print("\nBy fee type:")
    print(summarize(inband, "fee_type"))


if __name__ == "__main__":
    main()
