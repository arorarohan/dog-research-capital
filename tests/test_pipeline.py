import os

import polars as pl

from drc.normalize.pipeline import normalize_all, normalize_hour, out_path
from drc.recorder.writer import NS_PER_HOUR, RawWriter

T0 = 1_760_000_000 * 1_000_000_000
T0 -= T0 % NS_PER_HOUR
DATE = "2025-10-09"


def _hour_of(ts):
    import time

    t = time.gmtime(ts // 1_000_000_000)
    return time.strftime("%Y-%m-%d", t), t.tm_hour


def _kraken_snapshot(price, qty):
    return (
        '{"channel":"book","type":"snapshot","data":[{"symbol":"BTC/USD",'
        f'"bids":[{{"price":{price},"qty":{qty}}}],"asks":[]}}]}}'
    )


def _write_raw(root):
    w = RawWriter(root)
    w.event("kraken", "connect", ts_ns=T0 + 1)
    w.write("kraken", "book", _kraken_snapshot(10.0, 1.0), ts_ns=T0 + 10)
    w.write(
        "kraken",
        "book",
        '{"channel":"book","type":"update","data":[{"symbol":"BTC/USD","bids":[{"price":10.0,"qty":0.0}],"asks":[],"timestamp":"1970-01-01T00:00:01Z"}]}',
        ts_ns=T0 + 20,
    )
    w.event("kraken", "disconnect", ts_ns=T0 + 30, reason="x")
    w.event("kraken", "connect", ts_ns=T0 + 40)
    w.write("kraken", "book", _kraken_snapshot(11.0, 2.0), ts_ns=T0 + 50)
    w.write("kraken", "book", "PONG", ts_ns=T0 + 55)  # valid JSON string, not a book
    w.write(
        "kraken",
        "trade",
        '{"channel":"trade","type":"update","data":[{"symbol":"BTC/USD","side":"buy","price":11.0,"qty":0.5,"trade_id":1,"timestamp":"1970-01-01T00:00:02Z"}]}',
        ts_ns=T0 + 60,
    )
    w.close()


def test_epochs_msg_ids_and_ordering(tmp_path):
    raw, norm = tmp_path / "raw", tmp_path / "norm"
    _write_raw(raw)
    date, hour = _hour_of(T0)
    counts = normalize_hour(raw, norm, "kraken", date, hour)
    assert counts == {"trades": 1, "book_events": 5}

    book = pl.read_parquet(out_path(norm, "book_events", "kraken", date, hour))
    assert book["epoch"].to_list() == [T0 + 1] * 3 + [T0 + 40] * 2
    assert book["kind"].to_list() == ["reset", "snapshot", "update", "reset", "snapshot"]
    assert book["msg_id"].to_list() == [1, 1, 2, 3, 3]
    assert book["ts_recv"].is_sorted()

    trades = pl.read_parquet(out_path(norm, "trades", "kraken", date, hour))
    assert trades["epoch"].to_list() == [T0 + 40]
    assert trades["side"].to_list() == ["buy"]


def test_idempotent_and_skips_up_to_date_hours(tmp_path):
    raw, norm = tmp_path / "raw", tmp_path / "norm"
    _write_raw(raw)
    first = normalize_all(raw, norm)
    assert len(first) == 1
    assert normalize_all(raw, norm) == {}  # outputs newer than inputs: skipped
    # touch an input so it is newer than the output: reprocessed
    f = next(raw.rglob("book/*/*.jsonl"))
    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 100))
    assert len(normalize_all(raw, norm)) == 1
