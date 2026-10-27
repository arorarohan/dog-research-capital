import pytest

from drc.backtest.fees import BpsFee, PolymarketFee
from drc.forward.broker import BrokerConfig, SimBroker
from drc.forward.eventlog import EventLog
from drc.forward.fund import Fund
from drc.forward.risk import RiskLimits
from drc.forward.state import DAY_NS, FundState, MissingMark
from drc.forward.treasury import firm_report
from drc.forward.types import BookSnapshot, Level, Order

S = 1_000_000_000
LIMITS = RiskLimits(
    max_order_notional=100.0, max_event_exposure=150.0, max_fund_exposure=300.0, max_drawdown=0.2
)


def book(token="T", ts=10 * S, bids=((0.30, 1000),), asks=((0.32, 1000),)):
    return BookSnapshot(token, ts, tuple(Level(*b) for b in bids), tuple(Level(*a) for a in asks))


def order(oid="o1", token="T", event="E", qty=100, limit=0.33, side="buy", fund="f1"):
    return Order(oid, fund, token, event, side, qty, limit, 0)


FREE = BpsFee(0)


def make(tmp_path, fee=FREE, fund_id="f1", log=None, limits=LIMITS):
    log = log or EventLog(tmp_path / "e.sqlite")
    broker = SimBroker(BrokerConfig(depth_share=1.0))
    f = Fund(log, fund_id, broker, limits, lambda tok: fee, clock=lambda: 10 * S)
    f.create(fund_id, "hash-" + fund_id)
    f.allocate(1000.0)
    return f


def test_buy_fill_updates_cash_position_and_logs_everything(tmp_path):
    f = make(tmp_path)
    ex = f.submit(order(qty=100), book())
    assert ex.status == "filled"
    assert f.state.cash == pytest.approx(1000 - 32.0)
    assert f.state.positions() == {"T": (100, pytest.approx(0.32))}
    types = [e.type for e in f.log.events(fund_id="f1")]
    assert types == [
        "fund_created", "capital_allocated", "risk_check", "order_submitted", "fill"
    ]  # fmt: skip
    assert f.log.verify().ok


def test_nav_needs_a_bid_mark_and_is_liquidation_value(tmp_path):
    f = make(tmp_path)
    f.submit(order(qty=100), book())
    with pytest.raises(MissingMark):
        f.state.nav()
    f.mark("T", 0.30, ts=11 * S)  # bid, not mid 0.31
    assert f.state.nav() == pytest.approx(1000 - 32 + 30)
    assert f.state.pnl() == pytest.approx(-2.0)  # the half-spread we crossed shows up at once
    assert f.state.execution_cost == pytest.approx(100 * (0.32 - 0.31))


def test_settlement_win_lose_void(tmp_path):
    f = make(tmp_path)
    f.submit(order("a", "A", "EA", qty=100), book("A"))
    f.mark("A", 0.30, 11 * S)
    f.submit(order("b", "B", "EB", qty=100), book("B"))
    f.mark("B", 0.30, 11 * S)
    f.submit(order("c", "C", "EC", qty=100), book("C"))
    f.settle("A", "win", 20 * S)
    f.settle("B", "lose", 20 * S)
    f.settle("C", "void", 20 * S)
    # win +68, lose -32, void 0
    assert f.state.cash == pytest.approx(1000 + 68 - 32)
    assert f.state.positions() == {}
    assert f.state.nav() == pytest.approx(1036.0)
    assert f.reconcile() == []
    with pytest.raises(ValueError):
        f.settle("A", "win", 21 * S)


def test_fees_reduce_cash_and_are_tracked(tmp_path):
    fee = PolymarketFee(0.05)
    f = make(tmp_path, fee=fee)
    f.submit(order(qty=100), book())
    assert f.state.ledger.fees == pytest.approx(fee.fee(100, 0.32))
    assert f.state.cash == pytest.approx(1000 - 32 - fee.fee(100, 0.32))


def test_duplicate_order_id_is_a_noop(tmp_path):
    f = make(tmp_path)
    f.submit(order("dup"), book())
    n = len(f.log.events())
    ex = f.submit(order("dup"), book())
    assert ex.reason == "duplicate_order" and len(f.log.events()) == n
    assert f.state.positions()["T"][0] == 100


def test_restart_rebuilds_identical_state_and_remembers_orders(tmp_path):
    f = make(tmp_path)
    f.submit(order("o1"), book())
    f.mark("T", 0.30, 11 * S)
    g = Fund(f.log, "f1", f.broker, LIMITS, f.fee_for, clock=f.clock)
    assert g.state.cash == f.state.cash and g.state.positions() == f.state.positions()
    assert g.submit(order("o1"), book()).reason == "duplicate_order"
    g.create("f1", "hash-f1")  # idempotent
    with pytest.raises(ValueError, match="different charter"):
        g.create("f1", "other-hash")


@pytest.mark.parametrize(
    "kw,code",
    [
        (dict(qty=400), "order_notional"),  # 400 x .33 = 132 > 100
        (dict(sell=True), "would_short"),
    ],
)
def test_risk_rejections_do_not_reach_the_broker(tmp_path, kw, code):
    f = make(tmp_path)
    o = order(side="sell", qty=10) if kw.get("sell") else order(qty=kw["qty"])
    ex = f.submit(o, book())
    assert ex.status == "rejected" and code in ex.reason
    types = [e.type for e in f.log.events(fund_id="f1")]
    assert "order_submitted" not in types and types[-2:] == ["risk_check", "order_rejected"]
    assert f.state.positions() == {}


def test_event_and_fund_exposure_caps(tmp_path):
    f = make(tmp_path)
    assert f.submit(order("a", "T1", "E", qty=400, limit=0.33), book("T1")).reason == (
        "order_notional"
    )
    f.submit(order("b", "T1", "E", qty=100), book("T1"))  # 32 at risk on E
    f.mark("T1", 0.30, 11 * S)
    for i in range(1, 4):
        f.submit(order(f"c{i}", "T1", "E", qty=100), book("T1"))
    f.mark("T1", 0.30, 12 * S)
    ex = f.submit(order("d", "T1", "E", qty=100), book("T1"))  # 4 x 32 = 128 + 33 > 150
    assert "event_exposure" in ex.reason
    # different events up to the fund cap of 300
    for tok, ev, ids in (("T2", "E2", ("e", "e2")), ("T3", "E3", ("e3", "e4"))):
        for oid in ids:
            assert f.submit(order(oid, tok, ev, qty=100), book(tok)).status == "filled"
            f.mark(tok, 0.3, 13 * S)
    assert f.submit(order("e5", "T4", "E4", qty=100), book("T4")).status == "filled"  # 288
    f.mark("T4", 0.3, 14 * S)
    ex = f.submit(order("e6", "T5", "E5", qty=100), book("T5"))  # 288 + 33 > 300
    assert "fund_exposure" in ex.reason


def test_insufficient_cash(tmp_path):
    f = make(tmp_path)
    f.withdraw(980.0)
    ex = f.submit(order(qty=100), book())
    assert "insufficient_cash" in ex.reason


def test_missing_marks_block_new_buys(tmp_path):
    f = make(tmp_path)
    f.submit(order("a"), book())
    ex = f.submit(order("b", "T2", "E2"), book("T2"))
    assert "missing_marks" in ex.reason
    f.mark("T", 0.30, 11 * S)
    assert f.submit(order("b2", "T2", "E2"), book("T2")).status == "filled"


def test_drawdown_halts_new_entries_and_is_logged(tmp_path):
    f = make(tmp_path)
    f.withdraw(900.0)  # allocated now 100
    f.submit(order(qty=100), book())  # cost 32
    f.mark("T", 0.0, 11 * S)  # position worthless: nav 68 <= 100 x 0.8
    assert f.check_drawdown() is True and f.state.status == "halted"
    ex = f.submit(order("o2", "T2", "E2"), book("T2"))
    assert ex.reason == "fund_halted"
    halts = [e for e in f.log.events(types=["fund_halted"])]
    assert halts[0].payload["reason"] == "drawdown"


def test_non_filled_outcomes_are_logged(tmp_path):
    f = make(tmp_path)
    thin = book(asks=((0.32, 10),))  # min_size 5, depth_share 1 -> partial
    ex = f.submit(order("p", qty=50), thin)
    assert ex.status == "partial" and f.state.positions()["T"][0] == 10
    f.mark("T", 0.30, 11 * S)
    ex = f.submit(order("u", limit=0.31), book())
    assert ex.status == "unfilled"
    ex = f.submit(order("r"), book(ts=0))  # book at the decision instant: rejected by the broker
    assert ex.status == "rejected"
    types = [e.type for e in f.log.events(fund_id="f1")]
    assert "order_unfilled" in types and "order_rejected" in types


def test_reconcile_clean_then_detects_a_corrupted_state(tmp_path):
    f = make(tmp_path)
    f.submit(order(), book())
    f.mark("T", 0.30, 11 * S)
    assert f.reconcile() == []
    f.state.ledger.cash += 5  # simulate live state drifting from the log
    problems = f.reconcile()
    assert problems and f.state.status == "halted"
    recs = f.log.events(types=["reconciliation"])
    assert recs[0].payload["ok"] is True and recs[1].payload["ok"] is False


def test_flow_arithmetic_agrees_with_ledger_across_a_random_session(tmp_path):
    import random

    rng = random.Random(7)
    f = make(tmp_path, fee=PolymarketFee(0.05))
    f.allocate(9000.0)
    held = []
    for i in range(40):
        tok, ev = f"T{i}", f"E{i % 12}"
        ask = round(rng.choice([0.1, 0.2, 0.3, 0.4, 0.5]), 2)
        b = book(tok, bids=((ask - 0.01, 500),), asks=((ask, 500),))
        ex = f.submit(order(f"o{i}", tok, ev, qty=rng.choice([5, 20, 50]), limit=ask), b)
        if ex.qty:
            held.append(tok)
            f.mark(tok, ask - 0.01, 11 * S)
        if held and rng.random() < 0.4:
            f.settle(
                held.pop(rng.randrange(len(held))), rng.choice(["win", "lose", "void"]), 12 * S
            )
    assert f.state.flow_mismatches() == []
    assert f.reconcile() == []
    ledger = f.state.ledger
    marks = f.state.marks
    ident = 9000 + 1000 + ledger.realized_gross - ledger.fees + ledger.unrealized(marks)
    assert f.state.nav() == pytest.approx(ident)


def test_sessions_and_status_events(tmp_path):
    f = make(tmp_path)
    f.begin_session("s1", note="x")
    f.finish_session("s1", trades=0)
    f.miss_session("s2", "laptop asleep")
    f.set_status("paused", "manual test")
    assert f.state.status == "paused"
    assert f.submit(order(), book()).reason == "fund_paused"
    f.set_status("active", "resume")
    assert f.submit(order("o9"), book()).status == "filled"
    assert [e.session_id for e in f.log.events(session_id="s2")] == ["s2"]


def test_treasury_totals_include_closed_funds_and_count_hypotheses(tmp_path):
    log = EventLog(tmp_path / "e.sqlite")
    a = make(tmp_path, fund_id="a", log=log)
    b = make(tmp_path, fund_id="b", log=log)
    a.submit(order("x", fund="a"), book())
    a.mark("T", 0.30, 11 * S)  # a: -2 pnl
    b.set_status("closed", "killed: criterion met")
    rep = firm_report(log, as_of_ts=11 * S)
    assert rep.n_hypotheses == 2 and rep.allocated == pytest.approx(2000.0)
    assert rep.pnl == pytest.approx(-2.0) and rep.complete
    by = {s.fund_id: s for s in rep.funds}
    assert by["b"].status == "closed" and by["b"].allocated == 1000.0
    assert by["a"].deployed == pytest.approx(32.0) and by["a"].ret == pytest.approx(-0.002)


def test_treasury_flags_missing_marks_instead_of_guessing(tmp_path):
    log = EventLog(tmp_path / "e.sqlite")
    a = make(tmp_path, fund_id="a", log=log)
    a.submit(order("x", fund="a"), book())
    rep = firm_report(log, 11 * S)
    assert rep.funds[0].nav is None and rep.pnl is None and not rep.complete


def test_dollar_days_integrates_capital_tied_up(tmp_path):
    f = make(tmp_path, log=EventLog(tmp_path / "d.sqlite", clock=lambda: S))
    f.submit(order(qty=100), book(ts=10 * S))  # 32 deployed from t=10s
    f.mark("T", 0.30, 10 * S + 2 * DAY_NS)
    f.settle("T", "win", 10 * S + 3 * DAY_NS)
    assert f.state.dollar_days == pytest.approx(32 * 3)
    assert f.state.dollar_days_at(10 * S + 10 * DAY_NS) == pytest.approx(32 * 3)  # flat after


def test_rebuild_from_events_matches_after_many_actions(tmp_path):
    f = make(tmp_path)
    f.submit(order("a"), book())
    f.mark("T", 0.31, 11 * S)
    f.settle("T", "win", 12 * S)
    re = FundState.from_events("f1", f.log.events())
    assert re.cash == pytest.approx(f.state.cash) and re.positions() == f.state.positions()
