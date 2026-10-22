import orjson

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
