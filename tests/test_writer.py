import orjson

from drc.recorder import writer as writer_mod
from drc.recorder.writer import NS_PER_HOUR, RawWriter, hour_path, read_lines

T0 = 1_760_000_000 * 1_000_000_000  # fixed instant, UTC hour boundary irrelevant
T0 -= T0 % NS_PER_HOUR


def test_writes_verbatim_and_rotates_hourly(tmp_path):
    w = RawWriter(tmp_path)
    w.write("kraken", "book", '{"a":1}', ts_ns=T0 + 5)
    w.write("kraken", "book", "[1,2]", ts_ns=T0 + 6)
    w.write("kraken", "book", '{"a":2}', ts_ns=T0 + NS_PER_HOUR + 1)
    w.close()

    first = hour_path(tmp_path, "kraken", "book", T0)
    second = hour_path(tmp_path, "kraken", "book", T0 + NS_PER_HOUR)
    assert first != second
    assert list(read_lines(first)) == [{"t": T0 + 5, "m": {"a": 1}}, {"t": T0 + 6, "m": [1, 2]}]
    assert list(read_lines(second)) == [{"t": T0 + NS_PER_HOUR + 1, "m": {"a": 2}}]


def test_non_json_payload_is_stored_as_string(tmp_path):
    w = RawWriter(tmp_path)
    w.write("polymarket", "book", "PONG", ts_ns=T0)
    w.close()
    (rec,) = read_lines(hour_path(tmp_path, "polymarket", "book", T0))
    assert rec == {"t": T0, "m": "PONG"}


def test_events_go_to_meta_channel(tmp_path):
    w = RawWriter(tmp_path)
    w.event("coinbase", "disconnect", ts_ns=T0, reason="boom")
    w.close()
    (rec,) = read_lines(hour_path(tmp_path, "coinbase", "_meta", T0))
    assert rec == {"t": T0, "ev": "disconnect", "reason": "boom"}


def test_compress_closed_skips_open_file_and_roundtrips(tmp_path):
    w = RawWriter(tmp_path)
    w.write("kraken", "trade", '{"x":1}', ts_ns=T0)
    w.write("kraken", "trade", '{"x":2}', ts_ns=T0 + NS_PER_HOUR)  # rotates; first now closed
    w.flush()
    assert w.compress_closed() == 1

    closed = hour_path(tmp_path, "kraken", "trade", T0)
    assert not closed.exists()
    zst = closed.with_name(closed.name + ".zst")
    assert [r["m"] for r in read_lines(zst)] == [{"x": 1}]
    live = hour_path(tmp_path, "kraken", "trade", T0 + NS_PER_HOUR)
    assert live.exists()
    w.close()
    assert orjson.loads(live.read_bytes())["m"] == {"x": 2}


def test_embedded_newlines_do_not_break_lines(tmp_path):
    w = RawWriter(tmp_path)
    w.write("kraken", "book", '{"a":\n1}', ts_ns=T0)
    w.close()
    assert [r["m"] for r in read_lines(hour_path(tmp_path, "kraken", "book", T0))] == [{"a": 1}]


def test_reader_skips_corrupt_lines(tmp_path):
    path = hour_path(tmp_path, "kraken", "book", T0)
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"t":1,"m":{"a":1}}\n{"t":2,"m":{"a":\n{"t":3,"m":{"a":3}}\n')
    assert [r["t"] for r in read_lines(path)] == [1, 3]


def test_receive_timestamps_stay_strictly_increasing_when_clock_steps_back(tmp_path, monkeypatch):
    clock = iter([1_000, 1_000, 5_000, 2_000, 2_001, 9_000])  # steps back, then repeats
    monkeypatch.setattr("drc.recorder.writer.time.time_ns", lambda: next(clock))
    w = RawWriter(tmp_path)
    stamps = [w.now_ns() for _ in range(6)]
    assert stamps == sorted(set(stamps)) and len(stamps) == 6
    assert stamps[:4] == [1_000, 1_001, 5_000, 5_001]


def test_compress_closed_idle_guard_leaves_recently_written_files(tmp_path):
    import os
    import time as _time

    old = hour_path(tmp_path, "kraken", "book", T0)  # an hour long gone
    old.parent.mkdir(parents=True)
    old.write_bytes(b'{"t":1,"m":{"a":1}}\n')
    fresh = hour_path(tmp_path, "kraken", "trade", T0)
    fresh.parent.mkdir(parents=True)
    fresh.write_bytes(b'{"t":2,"m":{"a":2}}\n')
    os.utime(old, (_time.time() - 3600, _time.time() - 3600))  # untouched for an hour
    w = RawWriter(tmp_path)
    assert w.compress_closed(min_idle_secs=900) == 1  # only the idle one
    assert not old.exists() and old.with_name(old.name + ".zst").exists()
    assert fresh.exists()  # written just now: someone may still be appending
    assert w.compress_closed() == 1  # without the guard it goes too


def test_compress_closed_keeps_the_original_if_the_compressed_copy_does_not_match(
    tmp_path, monkeypatch
):
    path = hour_path(tmp_path, "kraken", "book", T0)
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"t":1,"m":{"a":1}}\n')
    real = writer_mod._sha256
    monkeypatch.setattr(
        writer_mod, "_sha256", lambda p, compressed: "bad" if compressed else real(p, compressed)
    )
    assert RawWriter(tmp_path).compress_closed() == 0
    assert path.exists()  # the only copy is untouched
    assert not list(path.parent.glob("*.zst*"))  # and no stray compressed file is left behind
