"""drc-session end to end, offline: a fake venue serving CLOB-format books (best LAST, as the
real one does) and Gamma-format markets built from the real recorded fixture."""

import datetime as dt
import json
import re
from pathlib import Path

import pytest

from drc.forward import session as sess
from drc.forward.charter import Charter, min_detectable_edge
from drc.forward.eventlog import EventLog
from drc.forward.polymarket import RawBook
from drc.forward.session import CharterError, format_summary, run_session

ROOT = Path(__file__).parent.parent
CHARTER = ROOT / "funds" / "pm-sched-gap-v1.toml"
FIX = Path(__file__).parent / "fixtures" / "polymarket"
REAL_MARKET = json.loads((FIX / "gamma_btc_dip_74k.json").read_text())
REAL_BOOK = json.loads((FIX / "book_btc_dip_74k.json").read_text())
S = 1_000_000_000
T0 = dt.datetime(2026, 11, 6, 12, 0, tzinfo=dt.UTC)


class Clock:
    def __init__(self, t: dt.datetime = T0):
        self.ns = int(t.timestamp() * S)

    def __call__(self) -> int:
        self.ns += 10_000_000  # every read moves time on by 10 ms
        return self.ns

    def sleep(self, secs: float) -> None:
        self.ns += int(secs * S)

    def set(self, t: dt.datetime) -> None:
        self.ns = int(t.timestamp() * S)


def clob_book(token, bids, asks, tick="0.01"):
    """A CLOB /book body in venue order: bids ascending, asks descending (best last)."""
    lv = lambda xs: [{"price": str(p), "size": str(s)} for p, s in xs]  # noqa: E731
    return {"market": "0xm", "asset_id": token, "timestamp": "1", "hash": "h",
            "bids": lv(sorted(bids)), "asks": lv(sorted(asks, reverse=True)),
            "min_order_size": "5", "tick_size": tick, "neg_risk": False}  # fmt: skip


def market(token, now, hours=66.0, volume=100_000.0, event="E1", **kw):
    end = now + dt.timedelta(hours=hours)
    m = {**REAL_MARKET, "id": f"m-{token}", "clobTokenIds": json.dumps([token, token + "-no"]),
         "endDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "volumeNum": volume,
         "events": [{"id": event}], "question": f"Q {token}?"}  # fmt: skip
    m.update(kw)
    return m


class FakeVenue:
    def __init__(self, clock: Clock):
        self.clock = clock
        self.markets: list[dict] = []
        self.books: dict[str, list[dict | None]] = {}  # token -> successive responses
        self.closed: dict[str, dict] = {}  # token -> closed Gamma market
        self.calls: list[tuple] = []
        self.fail_on_book: str | None = None

    def candidates(self, end_min, end_max, min_volume):
        self.calls.append(("candidates", end_min, end_max, min_volume))
        return list(self.markets)

    def book(self, token):
        self.calls.append(("book", token))
        if token == self.fail_on_book:
            raise RuntimeError("network down")
        seq = self.books.get(token, [None])
        body = seq.pop(0) if len(seq) > 1 else seq[0]
        return RawBook(token, self.clock(), body, 200 if body else 404)

    def markets_by_token(self, tokens, closed):
        self.calls.append(("markets_by_token", tuple(tokens), closed))
        assert closed, "settlement must ask Gamma for closed markets"
        return [m for t, m in self.closed.items() if t in tokens]


@pytest.fixture
def frozen(tmp_path):
    text = re.sub(r'(?m)^status = "draft"', 'status = "frozen"', CHARTER.read_text())
    p = tmp_path / "pm-sched-gap-v1.toml"
    p.write_text(text)
    return Charter.load(p)


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    venue = FakeVenue(clock)
    log = EventLog(tmp_path / "ledger" / "events.sqlite", clock=clock)
    return clock, venue, log


def run(charter, env, **kw):
    clock, venue, log = env
    return run_session(charter, log, venue, clock=clock, sleep=clock.sleep, **kw)


def types(log, **kw):
    return [e.type for e in log.events(**kw)]


def standard_day(venue, now):
    """Five candidates: one trade, one out of band, one one-sided, one already outside the
    volume floor, one without a book."""
    venue.markets = [
        market("A", now, event="EA"),
        market("B", now, event="EB"),
        market("C", now, event="EC"),
        market("D", now, volume=10.0, event="ED"),
        market("N", now, event="EN"),
    ]
    venue.books = {
        "A": [clob_book("A", [(0.28, 500)], [(0.30, 400), (0.31, 1000)])],
        "B": [clob_book("B", [(0.70, 500)], [(0.72, 400)])],
        "C": [clob_book("C", [], [(0.20, 400)])],
        "N": [None],
    }


# ---- charter --------------------------------------------------------------------------------


def test_real_charter_is_a_draft_and_its_mde_matches_the_formula():
    c = Charter.load(CHARTER)
    assert c.status == "draft" and not c.frozen and c.fund_id == "pm-sched-gap-v1"
    assert c.computed_mde() * 100 == pytest.approx(c["evaluation"]["mde_cents"], abs=0.05)
    assert min_detectable_edge(540, 0.464) == pytest.approx(0.0559, abs=1e-3)
    lim, br = c.risk_limits(), c.broker_config()
    assert lim.max_order_notional >= c["sizing"]["dollars_per_trade"]
    assert br.latency_ns == 5 * S and br.depth_share == 0.25
    assert len(c.sha256) == 64


def test_draft_charter_refuses_a_real_session(env):
    with pytest.raises(CharterError, match="draft"):
        run(Charter.load(CHARTER), env)
    assert env[2].events() == []


# ---- a full session -------------------------------------------------------------------------


def test_first_session_trades_logs_every_candidate_and_reconciles(frozen, env):
    clock, venue, log = env
    standard_day(venue, T0)
    r = run(frozen, env, git_sha="abc")
    assert r.status == "finished" and not r.reconciliation and not r.halted
    assert r.observed == {"trade": 1, "out_of_band_high": 1, "one_sided_book": 1,
                          "below_volume_floor": 1, "no_book": 1}  # fmt: skip

    obs = log.events(types=["market_observed"])
    assert len(obs) == 5  # every candidate, skipped ones included
    assert {e.payload["token"] for e in obs} == {"A", "B", "C", "D", "N"}
    assert all(e.payload["reason"] or e.payload["verdict"] == "trade" for e in obs)

    created = log.events(types=["fund_created"])[0].payload
    assert created["charter_hash"] == frozen.sha256 and created["git_sha"] == "abc"
    assert log.events(types=["capital_allocated"])[0].payload["amount"] == 10000.0

    fill = log.events(types=["fill"])[0].payload
    # $12 at 0.30 = 40 shares; 25% of 400 displayed = 100, so not depth-bound
    assert fill["token"] == "A" and fill["qty"] == 40.0 and fill["price"] == 0.30
    assert fill["fee"] == pytest.approx(40 * 0.07 * 0.3 * 0.7, abs=1e-5)  # fixture: 7% crypto
    assert fill["book_ts"] - fill["decision_ts"] >= 5 * S  # filled on a LATER book

    purposes = [e.payload["purpose"] for e in log.events(types=["book_snapshot"])]
    assert purposes.count("decision") == 4 and purposes.count("fill") == 1
    raw = log.events(types=["book_snapshot"])[0].payload
    assert raw["raw"]["asks"][-1]["price"] == "0.3"  # stored verbatim, venue order
    # marked at liquidation value: 40 shares into the 0.28 bid, minus the 7% exit taker fee
    exit_fee = round(0.07 * 40 * 0.28 * 0.72, 5)
    mark = log.events(types=["mark"])[-1].payload
    assert mark["token"] == "A" and mark["price"] == pytest.approx(0.28 - exit_fee / 40)

    t = types(log)
    assert t[:3] == ["fund_created", "capital_allocated", "session_started"]
    assert t[-2:] == ["reconciliation", "session_finished"]
    assert log.verify().ok
    assert r.nav == pytest.approx(10000 - 40 * 0.30 - fill["fee"] + 40 * 0.28 - exit_fee)
    assert "trade 1" in format_summary(r) and "reconciliation: ok" in format_summary(r)


def test_dry_run_writes_nothing_even_on_a_draft(env):
    clock, venue, log = env
    standard_day(venue, T0)
    r = run(Charter.load(CHARTER), env, dry_run=True)
    assert r.dry_run and r.observed["trade"] == 1 and len(r.orders) == 1
    assert log.events() == []
    assert "DRY RUN" in format_summary(r)


def test_rerun_same_day_is_a_no_op(frozen, env):
    clock, venue, log = env
    standard_day(venue, T0)
    run(frozen, env)
    n = len(log.events())
    calls = len(venue.calls)
    r = run(frozen, env)
    assert r.status == "already_ran" and len(log.events()) == n
    assert len(venue.calls) == calls  # did not even touch the venue
    assert "already finished" in format_summary(r)


def test_crash_rolls_back_the_whole_session_and_a_rerun_starts_clean(frozen, env):
    clock, venue, log = env
    standard_day(venue, T0)
    venue.fail_on_book = "B"
    with pytest.raises(RuntimeError):
        run(frozen, env)
    assert log.events() == []  # not even fund_created survives
    venue.fail_on_book = None
    assert run(frozen, env).status == "finished" and log.verify().ok


def test_changed_charter_after_creation_is_refused(frozen, env, tmp_path):
    clock, venue, log = env
    standard_day(venue, T0)
    run(frozen, env)
    clock.set(T0 + dt.timedelta(days=1))
    p = tmp_path / "changed.toml"
    p.write_text(
        frozen.path.read_text().replace("dollars_per_trade = 12.0", "dollars_per_trade = 50.0")
    )
    with pytest.raises(CharterError, match="new fund"):
        run(Charter.load(p), env)


# ---- across days ----------------------------------------------------------------------------


def test_missed_days_are_logged_never_backfilled_and_tokens_are_bought_once(frozen, env):
    clock, venue, log = env
    standard_day(venue, T0)
    run(frozen, env)
    day4 = T0 + dt.timedelta(days=3)
    clock.set(day4)
    standard_day(venue, day4)  # same tokens still in the window (moved end dates)
    venue.books["A"] = [clob_book("A", [(0.25, 500)], [(0.27, 400)])]
    r = run(frozen, env)
    assert r.missed == ["2026-11-07", "2026-11-08"]
    missed = log.events(types=["session_missed"])
    assert [e.payload["date"] for e in missed] == ["2026-11-07", "2026-11-08"]
    assert {e.session_id for e in missed} == {
        "pm-sched-gap-v1:2026-11-07",
        "pm-sched-gap-v1:2026-11-08",
    }
    assert r.observed["already_traded"] == 1 and "trade" not in r.observed
    assert len(log.events(types=["fill"])) == 1
    # the open position was marked at the new bid before anything else
    marks = log.events(types=["mark"], session_id="pm-sched-gap-v1:2026-11-09")
    exit_fee = round(0.07 * 40 * 0.25 * 0.75, 5)
    assert marks[0].payload["token"] == "A" and marks[0].payload["best_bid"] == 0.25
    assert marks[0].payload["price"] == pytest.approx(0.25 - exit_fee / 40)


def test_settles_resolved_positions_and_marks_unresolved_closed_ones_at_zero(frozen, env):
    clock, venue, log = env
    standard_day(venue, T0)
    venue.markets.append(market("Z", T0, event="EZ"))
    venue.books["Z"] = [clob_book("Z", [(0.10, 500)], [(0.12, 400)])]
    run(frozen, env)
    assert set(r.payload["token"] for r in log.events(types=["fill"])) == {"A", "Z"}

    clock.set(T0 + dt.timedelta(days=1))
    venue.markets = []
    resolved = {**REAL_MARKET, "clobTokenIds": json.dumps(["A", "A-no"]), "closed": True,
                "umaResolutionStatus": "resolved", "outcomePrices": '["1", "0"]',
                "closedTime": "2026-11-07 06:00:00+00"}  # fmt: skip
    disputed = {**resolved, "clobTokenIds": json.dumps(["Z", "Z-no"]),
                "umaResolutionStatus": "disputed"}  # fmt: skip
    venue.closed = {"A": resolved, "Z": disputed}
    venue.books = {"Z": [None]}  # closed market: no book
    r = run(frozen, env)
    assert r.settlements == [{"token": "A", "kind": "win", "value": 1.0}]
    assert r.pending == ["Z"]
    st = log.events(types=["settlement"])[0]
    assert st.ts_event == int(dt.datetime(2026, 11, 7, 6, tzinfo=dt.UTC).timestamp() * S)
    assert st.payload["venue"]["umaResolutionStatus"] == "resolved"
    zmark = log.events(types=["mark"])[-1].payload
    assert zmark == {"token": "Z", "price": 0.0, "reason": "no_book"}
    assert r.open_positions == 1 and not r.reconciliation and log.verify().ok


def test_book_gone_between_decision_and_fill_is_a_logged_rejection(frozen, env):
    clock, venue, log = env
    venue.markets = [market("A", T0)]
    venue.books = {"A": [clob_book("A", [(0.28, 500)], [(0.30, 400)]), None]}
    r = run(frozen, env)
    assert r.orders[0]["status"] == "rejected" and r.orders[0]["reason"] == "one_sided_book"
    assert types(log, types=["order_rejected"]) == ["order_rejected"]
    assert log.events(types=["fill"]) == []


def test_price_moved_up_after_decision_is_unfilled_not_chased(frozen, env):
    clock, venue, log = env
    venue.markets = [market("A", T0)]
    venue.books = {"A": [clob_book("A", [(0.28, 500)], [(0.30, 400)]),
                         clob_book("A", [(0.30, 500)], [(0.32, 400)])]}  # fmt: skip
    r = run(frozen, env)
    assert (r.orders[0]["status"], r.orders[0]["reason"]) == ("unfilled", "limit_not_marketable")


def test_per_event_cap_rejects_the_fourth_leg_of_one_event(frozen, env):
    clock, venue, log = env
    venue.markets = [market(t, T0, event="SAME") for t in "PQRS"]
    venue.books = {t: [clob_book(t, [(0.28, 5000)], [(0.30, 5000)])] for t in "PQRS"}
    r = run(frozen, env)
    status = [(o["token"], o["status"], o["reason"]) for o in r.orders]
    assert [s[1] for s in status[:3]] == ["filled"] * 3  # 3 x $12 = $36 = the event cap
    assert status[3][1:] == ("rejected", "event_exposure")


def test_universe_query_uses_the_charter_window_and_floor(frozen, env):
    clock, venue, log = env
    run(frozen, env)
    (_, end_min, end_max, vol) = [c for c in venue.calls if c[0] == "candidates"][0]
    assert vol == 50000.0
    assert (end_max - end_min) == dt.timedelta(hours=24)
    assert dt.timedelta(hours=59.9) < end_min - T0 < dt.timedelta(hours=60.1)


# ---- CLI ------------------------------------------------------------------------------------


def test_cli_refuses_a_draft_without_dry_run(tmp_path, capsys):
    rc = sess.main(["--fund", "pm-sched-gap-v1", "--funds-dir", str(ROOT / "funds"),
                    "--ledger", str(tmp_path / "e.sqlite")])  # fmt: skip
    assert rc == 2 and "DRAFT" in capsys.readouterr().err
    assert not (tmp_path / "e.sqlite").exists()


def test_cli_dry_run_uses_the_venue_interface_and_creates_no_ledger(tmp_path, capsys, monkeypatch):
    clock = Clock(dt.datetime.now(dt.UTC))
    venue = FakeVenue(clock)
    standard_day(venue, dt.datetime.now(dt.UTC))
    real = sess.run_session
    monkeypatch.setattr(sess, "PolymarketHttp", lambda: venue)
    monkeypatch.setattr(
        sess, "run_session", lambda *a, **kw: real(*a, **kw, clock=clock, sleep=clock.sleep)
    )
    rc = sess.main(["--fund", "pm-sched-gap-v1", "--funds-dir", str(ROOT / "funds"),
                    "--ledger", str(tmp_path / "e.sqlite"), "--dry-run"])  # fmt: skip
    out = capsys.readouterr().out
    assert rc == 0 and "DRY RUN" in out and "candidates 5" in out
    assert not (tmp_path / "e.sqlite").exists()


def test_check_committed_flags_untracked_files(tmp_path):
    p = tmp_path / "x.toml"
    p.write_text("x = 1")
    assert "not tracked" in sess.check_committed(p)
