import polars as pl

from drc.research.spreads import summarize, usable_seconds

DAY = 86_400 * 10**9


def _bars(rows):
    return pl.DataFrame(
        rows,
        schema={
            "symbol": pl.Utf8, "ts": pl.Int64, "bid": pl.Float64, "ask": pl.Float64,
            "ask_sz": pl.Float64,
        },
        orient="row",
    )  # fmt: skip


def _cat():
    return pl.DataFrame(
        {
            "symbol": ["A", "B"], "question": ["qa", "qb"], "end_ts": [10 * DAY, 10 * DAY],
            "tick": [0.01, 0.001], "fee_type": ["x", "y"], "volume": [1.0, 1.0],
        }
    )  # fmt: skip


def test_usable_seconds_drops_one_sided_and_locked_and_counts_them():
    bars = _bars(
        [
            ("A", 9 * DAY, 0.20, 0.22, 100.0),
            ("A", 9 * DAY + 10**9, None, 0.22, 100.0),  # one-sided
            ("B", 9 * DAY, 0.998, 0.998, 5.0),  # locked
        ]
    )
    d, counts = usable_seconds(bars, _cat())
    assert counts == {"bars": 3, "one_sided": 1, "locked_or_crossed": 1, "tokens": 2}
    assert d.height == 1 and abs(d["spr"][0] - 0.02) < 1e-9
    assert abs(d["days_to_end"][0] - 1.0) < 1e-9 and abs(d["ask_usd"][0] - 22.0) < 1e-9


def test_summarize_weights_tokens_not_seconds():
    # token A is quoted 1000 seconds at 1c, token B 1 second at 5c: the median across tokens
    # is the average of the two, not 1c
    rows = [("A", 9 * DAY + i, 0.20, 0.21, 10.0) for i in range(1000)]
    rows += [("B", 9 * DAY, 0.20, 0.25, 10.0)]
    d, _ = usable_seconds(_bars(rows), _cat())
    out = summarize(d.with_columns(pl.lit("all").alias("all")), "all")
    assert out["tokens"][0] == 2 and out["seconds"][0] == 1001
    assert abs(out["median_spread"][0] - 0.03) < 1e-9
    assert out["share_tokens_le_1c"][0] == 0.5
