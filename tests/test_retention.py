import time

from drc.recorder.retention import Rule, apply_retention

NOW = time.mktime(time.strptime("2026-10-22", "%Y-%m-%d")) - time.timezone + 12 * 3600


def _day(root, venue, channel, day, nbytes=100):
    d = root / venue / channel / day
    d.mkdir(parents=True)
    (d / "00.jsonl.zst").write_bytes(b"x" * nbytes)
    return d


def test_age_based_pruning_keeps_recent_and_unruled_channels(tmp_path):
    old = _day(tmp_path, "coinbase", "l2_data", "2026-10-10")
    recent = _day(tmp_path, "coinbase", "l2_data", "2026-10-20")
    trades = _day(tmp_path, "coinbase", "market_trades", "2026-01-01")
    removed = apply_retention(tmp_path, [Rule("coinbase", "l2_data", 7)], now=NOW)
    assert removed == [old]
    assert recent.exists() and trades.exists()


def test_size_cap_removes_oldest_prunable_first(tmp_path):
    a = _day(tmp_path, "kraken", "book", "2026-10-20", 600)
    b = _day(tmp_path, "kraken", "book", "2026-10-21", 600)
    keep = _day(tmp_path, "kraken", "trade", "2026-10-01", 600)
    removed = apply_retention(
        tmp_path, [Rule("kraken", "book", 30)], max_total_gb=1300 / 1e9, now=NOW
    )
    assert removed == [a]
    assert b.exists() and keep.exists()
