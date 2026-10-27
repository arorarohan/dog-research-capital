import sqlite3

import pytest

from drc.forward.eventlog import GENESIS, EventLog


def make(tmp_path):
    clock = iter(range(1_000, 10_000_000, 10))
    return EventLog(tmp_path / "ledger" / "events.sqlite", clock=lambda: next(clock))


def test_append_chains_hashes_and_verifies(tmp_path):
    log = make(tmp_path)
    a = log.append("fund_created", {"x": 1}, fund_id="f")
    b = log.append("capital_allocated", {"amount": 10.0}, fund_id="f")
    assert a.prev_hash == GENESIS and b.prev_hash == a.hash and a.hash != b.hash
    assert (a.seq, b.seq) == (1, 2)
    res = log.verify()
    assert res.ok and res.n_events == 2


def test_empty_log_verifies(tmp_path):
    assert make(tmp_path).verify().ok


def test_unknown_event_type_rejected(tmp_path):
    with pytest.raises(ValueError):
        make(tmp_path).append("made_up", {})


def test_update_and_delete_are_refused(tmp_path):
    log = make(tmp_path)
    log.append("fund_created", {}, fund_id="f")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        log._db.execute("UPDATE events SET payload = '{}'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        log._db.execute("DELETE FROM events")


def tamper(log, sql):
    """Bypass the triggers the way someone with file access could."""
    log._db.execute("DROP TRIGGER events_no_update")
    log._db.execute("DROP TRIGGER events_no_delete")
    log._db.execute(sql)


def test_edited_row_is_detected(tmp_path):
    log = make(tmp_path)
    for i in range(3):
        log.append("mark", {"i": i}, fund_id="f")
    tamper(log, "UPDATE events SET payload = '{\"i\": 99}' WHERE seq = 2")
    res = log.verify()
    assert not res.ok and res.bad_seq == 2 and "altered" in res.reason


def test_removed_middle_row_is_detected(tmp_path):
    log = make(tmp_path)
    for i in range(3):
        log.append("mark", {"i": i}, fund_id="f")
    tamper(log, "DELETE FROM events WHERE seq = 2")
    res = log.verify()
    assert not res.ok and res.bad_seq == 3


def test_edit_that_recomputes_one_hash_still_breaks_the_next_link(tmp_path):
    log = make(tmp_path)
    for i in range(3):
        log.append("mark", {"i": i}, fund_id="f")
    # an attacker who rewrites row 2's payload AND its own hash is caught at row 3
    from drc.forward.eventlog import row_hash

    r = log._db.execute("SELECT * FROM events WHERE seq = 2").fetchone()
    new_h = row_hash(r[7], 2, r[1], r[2], r[3], r[4], r[5], '{"i": 99}')
    tamper(log, f"UPDATE events SET payload='{{\"i\": 99}}', hash='{new_h}' WHERE seq = 2")
    res = log.verify()
    assert not res.ok and res.bad_seq == 3 and "prev_hash" in res.reason


def test_transaction_is_atomic(tmp_path):
    log = make(tmp_path)
    log.append("fund_created", {}, fund_id="f")
    with pytest.raises(RuntimeError):
        with log.transaction():
            log.append("mark", {"i": 1}, fund_id="f")
            log.append("mark", {"i": 2}, fund_id="f")
            raise RuntimeError("session crashed")
    assert [e.type for e in log.events()] == ["fund_created"]
    log.append("mark", {"i": 3}, fund_id="f")  # chain continues cleanly after rollback
    assert log.verify().ok and len(log.events()) == 2


def test_nested_transactions_join_the_outer_one(tmp_path):
    log = make(tmp_path)
    with pytest.raises(RuntimeError):
        with log.transaction():
            with log.transaction():
                log.append("mark", {}, fund_id="f")
            raise RuntimeError
    assert log.events() == []


def test_filters_and_reopen(tmp_path):
    path = tmp_path / "e.sqlite"
    log = EventLog(path)
    log.append("fund_created", {"n": "a"}, fund_id="a")
    log.append("fund_created", {"n": "b"}, fund_id="b")
    log.append("mark", {}, fund_id="a", session_id="s1")
    log.close()
    log = EventLog(path)
    assert log.fund_ids() == ["a", "b"]
    assert [e.type for e in log.events(fund_id="a")] == ["fund_created", "mark"]
    assert len(log.events(types=["fund_created"])) == 2
    assert len(log.events(session_id="s1")) == 1
    log.append("mark", {}, fund_id="b")
    assert log.verify().ok and log.verify().n_events == 4
