"""Regression tests for the independent review of PR #33 (drc-session). Each test reproduces
one confirmed finding; the fakes are shared with test_forward_session.py."""

from test_forward_session import (
    T0,
    Clock,
    FakeVenue,
    S,
    clob_book,
    frozen,  # noqa: F401  (pytest fixture)
    market,
)

from drc.forward import session as sess
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
