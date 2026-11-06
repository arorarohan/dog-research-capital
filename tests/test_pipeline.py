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


def test_parquet_is_written_at_the_configured_zstd_level(tmp_path, monkeypatch):
    from drc.normalize import pipeline

    seen = []
    real = pl.DataFrame.write_parquet

    def spy(self, file, **kw):
        seen.append((kw.get("compression"), kw.get("compression_level")))
        return real(self, file, **kw)

    monkeypatch.setattr(pl.DataFrame, "write_parquet", spy)
    raw, norm = tmp_path / "raw", tmp_path / "norm"
    _write_raw(raw)
    normalize_all(raw, norm)
    assert seen and set(seen) == {("zstd", pipeline.PARQUET_ZSTD_LEVEL)}
    assert pipeline.PARQUET_ZSTD_LEVEL == 19
    # the tables still read back
    assert all(pl.read_parquet(f).height > 0 for f in norm.rglob("hour=*.parquet"))


def test_closed_only_skips_the_hour_still_being_recorded(tmp_path):
    raw, norm = tmp_path / "raw", tmp_path / "norm"
    _write_raw(raw)  # data in the hour starting at T0
    t0 = T0 / 1e9
    inside = t0 + 1800  # half way through that hour: the recorder may still be writing
    assert normalize_all(raw, norm, closed_only=True, now=inside) == {}
    assert not list(norm.rglob("*.parquet"))
    after = t0 + 3600 + 1  # the hour has ended
    assert len(normalize_all(raw, norm, closed_only=True, now=after)) == 1
    assert list(norm.rglob("*.parquet"))
    # without the flag the open hour is still processed (existing behaviour)
    norm2 = tmp_path / "norm2"
    assert len(normalize_all(raw, norm2, now=inside)) == 1


def _read_all(norm_root):
    return {
        p.relative_to(norm_root): pl.read_parquet(p)
        for p in sorted(norm_root.rglob("hour=*.parquet"))
    }


def test_chunked_output_equals_unchunked_output(tmp_path, monkeypatch):
    """The memory fix builds tables in bounded chunks; the result must not depend on chunk size."""
    from drc.normalize import pipeline

    raw = tmp_path / "raw"
    _write_raw(raw)
    normalize_all(raw, tmp_path / "big")
    monkeypatch.setattr(pipeline, "CHUNK_ROWS", 1)  # a flush after every message
    normalize_all(raw, tmp_path / "tiny")
    a, b = _read_all(tmp_path / "big"), _read_all(tmp_path / "tiny")
    assert a.keys() == b.keys() and a
    assert all(a[k].equals(b[k]) and a[k].schema == b[k].schema for k in a)


def test_unsorted_channel_file_is_sorted_in_memory(tmp_path, caplog):
    """Kalshi books are stamped with explicit times and can go back in time within a file; a
    streaming merge would then differ from a sort, so the pipeline must sort instead."""
    raw, norm = tmp_path / "raw", tmp_path / "norm"
    w = RawWriter(raw)
    w.event("kraken", "connect", ts_ns=T0 + 1)
    w.write("kraken", "book", _kraken_snapshot(11.0, 2.0), ts_ns=T0 + 50)  # written first...
    w.write("kraken", "book", _kraken_snapshot(10.0, 1.0), ts_ns=T0 + 20)  # ...but earlier
    w.close()
    with caplog.at_level("WARNING"):
        normalize_all(raw, norm)
    assert "sorting channel book in memory" in caplog.text
    df = pl.read_parquet(next(norm.rglob("hour=*.parquet")))
    assert df["ts_recv"].to_list() == sorted(df["ts_recv"].to_list())
    assert df["price"].drop_nulls().to_list()[0] == 10.0  # the earlier snapshot comes first


def test_sorted_files_do_not_trigger_the_fallback(tmp_path, caplog):
    raw = tmp_path / "raw"
    _write_raw(raw)
    with caplog.at_level("WARNING"):
        normalize_all(raw, tmp_path / "norm")
    assert "falling back" not in caplog.text


def test_only_the_unsorted_channel_is_sorted_and_output_equals_a_global_sort(tmp_path, caplog):
    from drc.normalize import pipeline
    from drc.normalize.pipeline import _build, _channel_stream, _hour_files, load_epochs

    raw, norm = tmp_path / "raw", tmp_path / "norm"
    w = RawWriter(raw)
    w.event("kraken", "connect", ts_ns=T0 + 1)
    w.write("kraken", "book", _kraken_snapshot(11.0, 2.0), ts_ns=T0 + 50)  # out of order
    w.write("kraken", "book", _kraken_snapshot(10.0, 1.0), ts_ns=T0 + 20)
    trade = (
        '{"channel":"trade","type":"update","data":[{"symbol":"BTC/USD","side":"buy",'
        '"price":%s,"qty":0.5,"trade_id":%d,"timestamp":"1970-01-01T00:00:02Z"}]}'
    )
    for i, t in enumerate((30, 40, 60)):  # sorted channel, interleaved with the book times
        w.write("kraken", "trade", trade % (10 + i, i), ts_ns=T0 + t)
    w.close()
    date, hour = _hour_of(T0)
    with caplog.at_level("WARNING"):
        normalize_hour(raw, norm, "kraken", date, hour)
    assert "sorting channel book in memory" in caplog.text
    assert "sorting channel trade" not in caplog.text

    files = _hour_files(raw, "kraken", date, hour)
    expected = sorted(
        (r for ch, p in files.items() for r in _channel_stream("kraken", ch, p, strict=False)),
        key=pipeline._sort_key,
    )
    trades_df, book_df = _build(expected, "kraken", load_epochs(raw, "kraken"))
    got_t = pl.read_parquet(out_path(norm, "trades", "kraken", date, hour))
    got_b = pl.read_parquet(out_path(norm, "book_events", "kraken", date, hour))
    assert got_t.equals(trades_df) and got_b.equals(book_df)
    assert got_t.height == 3


def _pm_book(asset, price):
    return (
        f'[{{"asset_id":"{asset}","timestamp":"1500",'
        f'"bids":[{{"price":"{price}","size":"10"}}],"asks":[]}}]'
    )


def _pm_change(*assets):
    changes = ",".join(
        f'{{"asset_id":"{a}","price":"0.4","size":"5","side":"BUY"}}' for a in assets
    )
    return f'{{"timestamp":"2000","price_changes":[{changes}]}}'


def test_sharded_connects_give_each_symbol_its_own_epoch(tmp_path):
    """Shard 1 (A, B) reconnecting must not move the epoch of C on shard 2; a dynamic
    subscribe starts an epoch for just the added token."""
    raw, norm = tmp_path / "raw", tmp_path / "norm"
    w = RawWriter(raw)
    w.event("polymarket", "connect", ts_ns=T0 + 1, shard=0, assets=["A", "B"])
    w.event("polymarket", "connect", ts_ns=T0 + 2, shard=1, assets=["C"])
    w.write("polymarket", "book", _pm_book("A", "0.4"), ts_ns=T0 + 10)
    w.write("polymarket", "book", _pm_book("C", "0.4"), ts_ns=T0 + 11)
    w.write("polymarket", "price_change", _pm_change("A", "C"), ts_ns=T0 + 20)
    w.event("polymarket", "disconnect", ts_ns=T0 + 30, shard=0, reason="1013")
    w.event("polymarket", "connect", ts_ns=T0 + 40, shard=0, assets=["A", "B"])
    w.event("polymarket", "subscribe", ts_ns=T0 + 45, shard=1, assets=["D"])
    w.write("polymarket", "price_change", _pm_change("A", "C", "D"), ts_ns=T0 + 50)
    w.close()

    date, hour = _hour_of(T0)
    normalize_hour(raw, norm, "polymarket", date, hour)
    book = pl.read_parquet(out_path(norm, "book_events", "polymarket", date, hour))
    first = {
        s: e for s, e in book.filter(pl.col("ts_recv") <= T0 + 20).select("symbol", "epoch").rows()
    }
    last = {
        s: e for s, e in book.filter(pl.col("ts_recv") == T0 + 50).select("symbol", "epoch").rows()
    }
    assert first == {"A": T0 + 1, "C": T0 + 2}
    assert last == {"A": T0 + 40, "C": T0 + 2, "D": T0 + 45}


def test_epochs_without_assets_stay_venue_wide():
    from drc.normalize.pipeline import Epochs

    e = Epochs([10, 40], {"A": [25]})
    assert [e.at("B", t) for t in (5, 10, 39, 40, 99)] == [0, 10, 10, 40, 40]
    assert [e.at("A", t) for t in (5, 10, 24, 25, 39, 40, 99)] == [0, 10, 10, 25, 25, 40, 40]
    assert e.at("A", 30) == 25  # cached lookups going back in time are still correct
    assert e.at("A", 12) == 10
