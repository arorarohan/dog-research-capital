"""Regression tests for the independent review of PR #33 (drc-session). Each test reproduces
one confirmed finding; the fakes are shared with test_forward_session.py."""

import re

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
)

from drc.forward import session as sess
from drc.forward.charter import Charter, CharterError
from drc.forward.eventlog import EventLog
from drc.forward.session import run_session

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
