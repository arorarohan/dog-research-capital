"""Regression tests for the independent review of PR #33 (drc-session). Each test reproduces
one confirmed finding; the fakes are shared with test_forward_session.py."""

import datetime as dt
import re
import sqlite3
import threading
import time

import pytest
from test_forward_session import (
    CHARTER,
    T0,
    Clock,
    FakeVenue,
    S,
    clob_book,
    frozen,  # noqa: F401  (pytest fixture)
    market,
    standard_day,
)

from drc.forward import session as sess
from drc.forward.charter import Charter, CharterError
from drc.forward.eventlog import EventLog
from drc.forward.session import format_summary, run_session

# ---- 1. latency gate vs a wall clock that steps backwards ----------------------------------


class SteppingClock(Clock):
    """A wall clock that steps BACK `step_back` seconds during the first sleep, as the WSL2
    clock does (docs/gotchas.md), while the monotonic clock keeps going forward."""

    def __init__(self, t=T0, step_back=1.0):
        super().__init__(t)
        self.step_back, self.mono, self.sleeps = step_back, 0, []

    def sleep(self, secs):
        self.sleeps.append(secs)
        self.ns += int(secs * S)
        self.mono += int(secs * S)
        if self.step_back:
            self.ns -= int(self.step_back * S)
            self.step_back = 0

    def monotonic(self):
        self.mono += 10_000_000
        return self.mono


def _one_trade_session(charter, tmp_path, clock):
    venue = FakeVenue(clock)
    log = EventLog(tmp_path / "e.sqlite", clock=clock)
    venue.markets = [market("A", T0)]
    venue.books = {"A": [clob_book("A", [(0.28, 500)], [(0.30, 400)])]}
    r = run_session(charter, log, venue, clock=clock, sleep=clock.sleep, monotonic=clock.monotonic)
    fb = [e.payload for e in log.events(types=["book_snapshot"]) if e.payload["purpose"] == "fill"]
    return r, log, fb


def test_wall_clock_step_back_during_latency_sleep_still_fills(frozen, tmp_path):  # noqa: F811
    clock = SteppingClock(step_back=1.0)
    r, log, fb = _one_trade_session(frozen, tmp_path, clock)
    assert r.orders[0]["status"] == "filled", r.orders
    fill = log.events(types=["fill"])[0].payload
    assert fill["book_ts"] - fill["decision_ts"] >= 5 * S
    assert len(clock.sleeps) >= 2  # the session waited again for the wall clock
    w = fb[0]["latency_wait"]  # both clocks are logged with the fill book
    assert w["mono_waited_ns"] >= 5 * S and w["wall_ready"] is True
    assert w["wall_ready_ns"] >= w["decision_ns"] + 5 * S


def test_wall_clock_wait_is_capped(frozen, tmp_path):  # noqa: F811
    clock = SteppingClock(step_back=3600.0)  # a huge step: never wait an hour
    r, log, fb = _one_trade_session(frozen, tmp_path, clock)
    assert sum(clock.sleeps) <= 5 + sess.MAX_WALL_WAIT_SECS + 1
    assert r.orders[0]["reason"] == "book_too_early"  # an honest, logged rejection
    assert fb[0]["latency_wait"]["wall_ready"] is False


# ---- 2. the freeze covers the code, and unread charter fields are validated ---------------


def _frozen_funds_dir(tmp_path, edit=lambda t: t):
    d = tmp_path / "funds"
    d.mkdir(exist_ok=True)
    text = re.sub(r'(?m)^status = "draft"', 'status = "frozen"', CHARTER.read_text())
    (d / "pm-sched-gap-v1.toml").write_text(edit(text))
    return d


def _fake_git(dirty: str):
    def git(*args):
        if args[:1] == ("ls-files",):
            return args[-1]  # tracked
        if args[:2] == ("status", "--porcelain"):
            return dirty if any("src" in a for a in args) else ""
        if args[:1] == ("rev-parse",):
            return "true" if "--is-inside-work-tree" in args else "f" * 40
        return ""

    return git


def test_cli_refuses_a_real_run_with_uncommitted_code(tmp_path, capsys, monkeypatch):
    funds = _frozen_funds_dir(tmp_path)
    monkeypatch.setattr(sess, "_git", _fake_git(" M src/drc/forward/broker.py"))
    monkeypatch.setattr(sess, "PolymarketHttp", lambda: pytest.fail("must not reach the venue"))
    rc = sess.main(["--fund", "pm-sched-gap-v1", "--funds-dir", str(funds),
                    "--ledger", str(tmp_path / "e.sqlite")])  # fmt: skip
    err = capsys.readouterr().err
    assert rc == 2 and "uncommitted" in err and "broker.py" in err
    assert not (tmp_path / "e.sqlite").exists()


def test_dirty_paths_checks_code_charters_and_lockfiles():
    seen = []

    def git(*args):
        seen.append(args)
        return ""

    assert sess.dirty_paths(git) == []
    status = [a for a in seen if a[:2] == ("status", "--porcelain")][0]
    for p in ("src", "funds", "pyproject.toml", "uv.lock"):
        assert any(a.endswith(p) for a in status), status


def test_session_started_logs_a_hash_of_the_session_code(frozen, tmp_path):  # noqa: F811
    clock = Clock()
    log = EventLog(tmp_path / "e.sqlite", clock=clock)
    run_session(frozen, log, FakeVenue(clock), clock=clock, sleep=clock.sleep,
                code_tree="abc123")  # fmt: skip
    p = log.events(types=["session_started"])[0].payload
    assert p["code_sha256"] == sess.code_fingerprint() and len(p["code_sha256"]) == 64
    assert p["forward_tree_sha"] == "abc123"


@pytest.mark.parametrize(
    "old,new,field",
    [
        ('side = "buy_yes"', 'side = "buy_no"', "entry.side"),
        ("once_per_token = true", "once_per_token = false", "entry.once_per_token"),
        ("log_out_of_band = true", "log_out_of_band = false", "entry.log_out_of_band"),
        ('limit = "decision_best_ask"', 'limit = "mid"', "entry.limit"),
        ('rule = "hold_to_resolution"', 'rule = "stop_loss"', "exit.rule"),
        ('cadence = "daily"', 'cadence = "hourly"', "schedule.cadence"),
        ('venue = "polymarket"', 'venue = "kalshi"', "universe.venue"),
        ("once_per_token = true", "", "entry.once_per_token"),  # missing is not "default"
    ],
)
def test_unsupported_charter_values_fail_loudly(tmp_path, old, new, field):
    funds = _frozen_funds_dir(tmp_path, lambda t: t.replace(old, new, 1))
    c = Charter.load(funds / "pm-sched-gap-v1.toml")
    clock = Clock()
    log = EventLog(":memory:", clock=clock)
    with pytest.raises(CharterError, match=re.escape(field)):
        run_session(c, log, FakeVenue(clock), clock=clock, sleep=clock.sleep, dry_run=True)
    assert log.events() == []


def test_the_real_charter_validates():
    assert Charter.load(CHARTER).problems() == []


# ---- 3. two overlapping sessions on one ledger --------------------------------------------


def test_two_overlapping_sessions_on_one_ledger_do_not_double_allocate(frozen, tmp_path):  # noqa: F811
    """Session B starts while A sleeps through its latency (A holds the write lock). B must
    wait, then see A's finished session: one fund, one allocation, one finished session."""
    path = tmp_path / "e.sqlite"
    EventLog(path).close()
    out: dict = {}

    def run_b():
        clock = Clock()
        venue = FakeVenue(clock)
        standard_day(venue, T0)
        log = EventLog(path, clock=clock)
        try:
            out["b"] = run_session(frozen, log, venue, clock=clock, sleep=clock.sleep)
        except Exception as e:  # noqa: BLE001
            out["b_error"] = e
        finally:
            log.close()

    tb = threading.Thread(target=run_b)
    clock = Clock()
    venue = FakeVenue(clock)
    standard_day(venue, T0)

    def sleep_a(secs):
        clock.sleep(secs)
        if not tb.is_alive() and "b" not in out and "b_error" not in out:
            tb.start()
            time.sleep(0.5)  # let B get as far as it can while A is mid-session

    log = EventLog(path, clock=clock)
    a = run_session(frozen, log, venue, clock=clock, sleep=sleep_a)
    tb.join(timeout=30)
    assert a.status == "finished"
    assert "b_error" not in out, out.get("b_error")
    assert out["b"].status == "already_ran"
    kinds = ("fund_created", "capital_allocated", "session_started", "session_finished")
    count = {t: len(log.events(types=[t])) for t in kinds}
    assert count == dict.fromkeys(kinds, 1)
    assert log.verify().ok


def test_ledger_refuses_a_second_fund_created_or_session_finished(tmp_path):
    log = EventLog(tmp_path / "e.sqlite")
    log.append("fund_created", {"charter_hash": "x"}, fund_id="f")
    with pytest.raises(sqlite3.IntegrityError):
        log.append("fund_created", {"charter_hash": "x"}, fund_id="f")
    log.append("session_finished", {}, fund_id="f", session_id="f:1")
    with pytest.raises(sqlite3.IntegrityError):
        log.append("session_finished", {}, fund_id="f", session_id="f:1")
    log.append("session_finished", {}, fund_id="f", session_id="f:2")  # another day is fine
    assert log.verify().ok


def _log_with_live_fund(charter, tmp_path):
    clock = Clock()
    venue = FakeVenue(clock)
    standard_day(venue, T0)
    log = EventLog(tmp_path / "ledger" / "events.sqlite", clock=clock)
    run_session(charter, log, venue, clock=clock, sleep=clock.sleep)
    return clock, venue, log


# ---- 4. no silent dry-run peeks at a live fund ---------------------------------------------


def test_dry_run_against_a_ledger_holding_the_fund_is_refused(frozen, tmp_path):  # noqa: F811
    clock, venue, log = _log_with_live_fund(frozen, tmp_path)
    n = len(log.events())
    clock.set(T0.replace(day=7))
    standard_day(venue, T0.replace(day=7))
    with pytest.raises(CharterError, match="scratch"):
        run_session(frozen, log, venue, clock=clock, sleep=clock.sleep, dry_run=True)
    assert len(log.events()) == n


def test_cli_dry_run_refuses_the_live_ledger(frozen, tmp_path, capsys, monkeypatch):  # noqa: F811
    _, _, log = _log_with_live_fund(frozen, tmp_path)
    log.close()
    funds = _frozen_funds_dir(tmp_path)
    monkeypatch.setattr(sess, "PolymarketHttp", lambda: FakeVenue(Clock()))
    rc = sess.main(["--fund", "pm-sched-gap-v1", "--funds-dir", str(funds), "--dry-run",
                    "--ledger", str(tmp_path / "ledger" / "events.sqlite")])  # fmt: skip
    assert rc == 2 and "scratch" in capsys.readouterr().err


# ---- 5. lateness is enforced, and the universe window is anchored to the schedule ---------


def _env(tmp_path, t):
    clock = Clock(t)
    venue = FakeVenue(clock)
    log = EventLog(tmp_path / "e.sqlite", clock=clock)
    return clock, venue, log


def test_a_run_later_than_the_tolerance_is_logged_missed_not_run(frozen, tmp_path):  # noqa: F811
    clock, venue, log = _env(tmp_path, T0 + dt.timedelta(hours=3))
    standard_day(venue, T0)
    r = run_session(frozen, log, venue, clock=clock, sleep=clock.sleep)
    assert r.status == "missed"
    assert not [c for c in venue.calls if c[0] == "candidates"]  # never looked at the market
    (m,) = log.events(types=["session_missed"])
    assert m.session_id == "pm-sched-gap-v1:2026-11-06" and m.payload["date"] == "2026-11-06"
    assert "late" in m.payload["reason"] and m.payload["late_secs"] == 3 * 3600
    assert m.payload["max_late_secs"] == 7200
    assert log.events(types=["session_started", "market_observed"]) == []
    # a re-run the same day is a no-op, and the next on-time day does not re-log the miss
    assert run_session(frozen, log, venue, clock=clock, sleep=clock.sleep).status == "missed"
    clock.set(T0 + dt.timedelta(days=1))
    r2 = run_session(frozen, log, venue, clock=clock, sleep=clock.sleep)
    assert r2.status == "finished" and r2.missed == []
    assert len(log.events(types=["session_missed"])) == 1 and log.verify().ok


def test_late_tolerance_comes_from_the_charter(tmp_path):
    funds = _frozen_funds_dir(
        tmp_path, lambda t: t.replace("max_late_secs = 7200", "max_late_secs = 600")
    )
    c = Charter.load(funds / "pm-sched-gap-v1.toml")
    clock, venue, log = _env(tmp_path, T0 + dt.timedelta(minutes=15))
    assert run_session(c, log, venue, clock=clock, sleep=clock.sleep).status == "missed"


def test_a_catch_up_run_uses_the_scheduled_window_not_now(frozen, tmp_path):  # noqa: F811
    late = T0 + dt.timedelta(minutes=90)  # within the 2 h tolerance
    clock, venue, log = _env(tmp_path, late)
    venue.markets = [market("A", T0, hours=61)]  # 61 h after 12:00, only 59.5 h after now
    venue.books = {"A": [clob_book("A", [(0.28, 500)], [(0.30, 400)])]}
    r = run_session(frozen, log, venue, clock=clock, sleep=clock.sleep)
    assert r.status == "finished" and r.observed == {"trade": 1}
    (_, end_min, end_max, _) = [c for c in venue.calls if c[0] == "candidates"][0]
    assert end_min == T0 + dt.timedelta(hours=60) and end_max == T0 + dt.timedelta(hours=84)
    p = log.events(types=["session_started"])[0].payload
    assert p["late_secs"] == 90 * 60 and p["anchor_utc"] == T0.isoformat()


def test_a_run_before_the_scheduled_time_does_nothing(frozen, tmp_path):  # noqa: F811
    clock, venue, log = _env(tmp_path, T0 - dt.timedelta(hours=9))  # 03:00, e.g. a boot catch-up
    standard_day(venue, T0)
    r = run_session(frozen, log, venue, clock=clock, sleep=clock.sleep)
    assert r.status == "too_early" and log.events() == [] and venue.calls == []
    assert "too early" in format_summary(r)


def test_dry_run_is_not_held_to_the_schedule(tmp_path):
    clock, venue, log = _env(tmp_path, T0 - dt.timedelta(hours=9))
    standard_day(venue, T0)
    r = run_session(Charter.load(CHARTER), log, venue, clock=clock, sleep=clock.sleep,
                    dry_run=True)  # fmt: skip
    assert r.status == "finished" and r.observed["trade"] == 1


def test_cli_ledger_lock_refuses_a_concurrent_run(tmp_path):
    p = tmp_path / "e.sqlite"
    with sess.ledger_lock(p):
        with pytest.raises(sess.LedgerBusy):
            with sess.ledger_lock(p):
                pass
    with sess.ledger_lock(p):  # released
        pass
