import polars as pl

from drc.backfill.catalog import coverage
from drc.backfill.cli import save_markets
from drc.backfill.kalshi import market_row


def _mk(t, result):
    m = {
        "ticker": t, "event_ticker": "KXBTC15M-X", "title": "t",
        "open_time": "2026-10-01T00:00:00Z", "close_time": "2026-10-01T00:15:00Z",
        "result": result, "volume_fp": "1",
    }  # fmt: skip
    return market_row(m, "live")


def test_save_markets_merges_and_catalog_reports(tmp_path):
    save_markets(tmp_path, pl.DataFrame([_mk("A", "")]), "kalshi", "KXBTC15M")
    save_markets(tmp_path, pl.DataFrame([_mk("A", "yes"), _mk("B", "no")]), "kalshi", "KXBTC15M")
    df = pl.read_parquet(tmp_path / "markets" / "venue=kalshi" / "series=KXBTC15M.parquet")
    assert sorted(df["market_id"].to_list()) == ["A", "B"]
    assert df.filter(pl.col("market_id") == "A")["result_value"][0] == 1.0  # newest fetch wins
    (line,) = coverage(tmp_path)
    assert "kalshi/KXBTC15M" in line and "2 markets" in line and "(2 resolved)" in line
