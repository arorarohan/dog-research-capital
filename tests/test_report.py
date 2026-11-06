import sqlite3

import pytest

from drc.backtest.fees import BpsFee
from drc.forward.broker import BrokerConfig, SimBroker
from drc.forward.eventlog import EventLog
from drc.forward.fund import Fund
from drc.forward.risk import RiskLimits
from drc.forward.state import DAY_NS
from drc.forward.types import BookSnapshot, Level, Order
from drc.report.cli import main, write_report
from drc.report.model import build_firm, load_prediction
from drc.report.render import render_firm, render_fund
from drc.report.sample import GAP_ID, KILLED_ID, build_sample

S = 1_000_000_000
LIMITS = RiskLimits(100.0, 150.0, 300.0, 0.5)


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    d = tmp_path_factory.mktemp("sample")
    log = build_sample(d / "events.sqlite", d / "funds")
    view = build_firm(log, charters=d / "funds")
    log.close()
    return d, view


def fund(view, fid):
    return next(f for f in view.funds if f.fund_id == fid)


# ---- snapshot of the synthetic sample ------------------------------------------------------
def test_firm_totals_include_the_killed_fund(sample):
    _, v = sample
    assert v.verify.ok and v.n_events == 60
    assert v.firm.allocated == pytest.approx(10_500.0)
    assert v.firm.nav == pytest.approx(10_530.9125)
    assert v.firm.pnl == pytest.approx(30.9125)  # +154.66 live fund, -123.75 killed fund
    assert v.firm.n_hypotheses == 2 and v.firm.complete


def test_gap_fund_numbers(sample):
    _, v = sample
    f = fund(v, GAP_ID)
    s = f.summary
    assert s.status == "active"
    assert s.nav == pytest.approx(10_154.6625)
    assert s.fees == pytest.approx(7.3375)
    assert s.n_fills == 4 and f.contracts_bought == pytest.approx(900)
    c = f.costs
    assert c.spread == pytest.approx(4.5)
    assert c.edge_vs_mid == pytest.approx(166.5)
    assert c.edge_vs_mid - c.spread - c.fees == pytest.approx(c.net)
    assert c.realized_gross + c.unrealized - c.fees == pytest.approx(c.net)
    assert [b.status for b in f.blotter] == ["filled", "partial", "filled", "filled"]
    assert [(x.kind, round(x.net, 4)) for x in f.settlements] == [
        ("win", 206.85),
        ("lose", -46.2375),
        ("void", -1.6),
    ]
    # open position valued at the bid (0.09), not the mid (0.095)
    (p,) = f.positions
    assert (p.token, p.qty, p.mark, p.value) == ("E1", 300, 0.09, pytest.approx(27.0))
    assert [(x.event_id, x.cost_basis) for x in f.exposure] == [("EE", pytest.approx(30.0))]


def test_gap_fund_edge_and_power_note(sample):
    _, v = sample
    f = fund(v, GAP_ID)
    assert f.prediction is not None and f.prediction.edge == pytest.approx(0.012)
    ed = f.edge
    assert (ed.n_positions, ed.n_events, ed.n_voids) == (2, 2, 1)
    assert ed.realized == pytest.approx((206.85 - 46.2375) / 400)
    assert ed.needed_events == 8721  # ((1.96 + 0.84) x 0.40 / 0.012)^2
    assert ed.mde == pytest.approx((1.959964 + 0.841621) * 0.40 / 2**0.5, rel=1e-5)
    assert ed.verdict.startswith("inconclusive by design")


def test_gap_fund_logs_skips_sessions_and_flags(sample):
    _, v = sample
    f = fund(v, GAP_ID)
    assert ("skip", "outside_band", 4) in f.candidates
    assert ("unfilled", "limit_not_marketable", 1) in f.order_outcomes
    assert any(o == "rejected" and "order_notional" in r for o, r, _ in f.order_outcomes)
    assert [m.session_id for m in f.sessions.missed] == ["2026-11-03"]
    assert len(f.sessions.finished) == 3 and f.sessions.unfinished == ()
    assert [x.type for x in f.flags] == ["fund_paused", "fund_resumed"]
    assert f.reconciliations_ok == 3 and f.reconciliation_failures == ()


def test_nav_curve_is_marked_and_ends_at_nav(sample):
    _, v = sample
    f = fund(v, GAP_ID)
    assert all(p.nav is not None for p in f.nav_points)  # fill->mark blips are dropped
    assert f.nav_points[-1].nav == pytest.approx(f.summary.nav)
    ts = [p.ts for p in f.nav_points]
    assert ts == sorted(ts)


def test_killed_fund_flags_and_stale_mark(sample):
    _, v = sample
    f = fund(v, KILLED_ID)
    assert f.summary.status == "closed"
    assert [x.type for x in f.flags] == ["fund_halted", "fund_closed"]
    assert f.flags[0].reason == "drawdown"
    assert f.stale_or_missing_marks and f.stale_or_missing_marks[0].stale
    assert f.prediction is None and "no [prediction]" in f.prediction_note


def test_pages_contain_the_essentials(sample):
    _, v = sample
    firm = render_firm(v)
    assert "hash chain verified: 60 events" in firm
    assert "What this cannot test" in firm and "Adverse selection" in firm
    assert "fund-demo-killed-v1.html" in firm and "+$30.91" in firm
    page = render_fund(v, fund(v, GAP_ID))
    for s in ("NAV over time", "Predicted vs realized", "Minimum detectable edge", "Blotter"):
        assert s in page
    assert "Missed session" in page and "Fund paused" in page
    assert "never mid" in page and "http" not in page.split("<main>")[1].split("</main>")[0]


# ---- CLI -----------------------------------------------------------------------------------
def test_cli_is_deterministic(sample, tmp_path):
    d, _ = sample
    args = ["--ledger", str(d / "events.sqlite"), "--charters", str(d / "funds")]
    assert main([*args, "--out", str(tmp_path / "a")]) == 0
    assert main([*args, "--out", str(tmp_path / "b")]) == 0
    names = sorted(p.name for p in (tmp_path / "a").iterdir())
    assert names == ["fund-demo-gap-v1.html", "fund-demo-killed-v1.html", "index.html"]
    for n in names:
        assert (tmp_path / "a" / n).read_bytes() == (tmp_path / "b" / n).read_bytes()


def test_cli_missing_ledger_does_not_create_one(tmp_path):
    ledger = tmp_path / "nope.sqlite"
    assert main(["--ledger", str(ledger), "--out", str(tmp_path / "o")]) == 2
    assert not ledger.exists()


def test_cli_sample(tmp_path):
    assert main(["--sample", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "fund-demo-gap-v1.html").exists()


def test_empty_ledger_renders(tmp_path):
    log = EventLog(tmp_path / "e.sqlite")
    v = build_firm(log)
    assert v.funds == () and v.verify.ok and v.n_events == 0
    files = write_report(v, tmp_path / "out")
    assert [f.name for f in files] == ["index.html"]
    assert "No funds yet" in files[0].read_text()


def test_tampered_log_shows_a_broken_chain(tmp_path):
    path = tmp_path / "e.sqlite"
    build_sample(path).close()
    db = sqlite3.connect(path)
    db.execute("DROP TRIGGER events_no_update")
    db.execute('UPDATE events SET payload = \'{"amount":99999.0,"note":""}\' WHERE seq = 2')
    db.commit()
    db.close()
    assert main(["--ledger", str(path), "--out", str(tmp_path / "o")]) == 1
    assert "hash chain BROKEN at event 2" in (tmp_path / "o" / "index.html").read_text()


# ---- edge cases ----------------------------------------------------------------------------
def small_fund(tmp_path, clock_ns=S):
    log = EventLog(tmp_path / "s.sqlite", clock=lambda: clock_ns)
    f = Fund(log, "f", SimBroker(BrokerConfig(depth_share=1.0)), LIMITS, lambda _: BpsFee(0))
    f.create("f", "h")
    f.allocate(1000.0)
    return log, f


def buy(f, oid="o1", token="T", reason_tag=""):
    book = BookSnapshot(token, 10 * S, (Level(0.30, 1000),), (Level(0.32, 1000),))
    return f.submit(Order(oid, "f", token, "E", "buy", 100, 0.33, 0, reason_tag), book, now=11 * S)


def test_unmarked_position_means_no_nav_and_incomplete_firm(tmp_path):
    log, f = small_fund(tmp_path)
    buy(f)
    v = build_firm(log)
    assert v.funds[0].summary.nav is None and v.firm.pnl is None and not v.firm.complete
    assert v.funds[0].nav_points[-1].nav is None  # stays a gap: no later mark
    assert v.funds[0].costs.edge_vs_mid is None
    assert "NAV unknown" in render_fund(v, v.funds[0])
    assert "Incomplete totals" in render_firm(v)


def test_long_unmarked_stretch_is_a_gap_not_interpolated(tmp_path):
    log, f = small_fund(tmp_path)
    buy(f)
    f.mark("T", 0.30, 10 * S + DAY_NS)
    pts = build_firm(log).funds[0].nav_points
    assert [p.nav is None for p in pts] == [False, True, False]


def test_log_text_is_escaped(tmp_path):
    log, f = small_fund(tmp_path)
    f.set_status("paused", "<script>alert(1)</script>")
    page = render_fund(build_firm(log), build_firm(log).funds[0])
    assert "<script>alert" not in page and "&lt;script&gt;alert" in page


def test_prediction_sources(tmp_path):
    created = {"prediction": {"edge_per_contract": 0.02}, "evaluation": {"alpha": 0.1}}
    p, _ = load_prediction("x", created, None)
    assert p.edge == 0.02 and p.alpha == 0.1 and p.source == "event log"
    (tmp_path / "x.toml").write_text("[prediction]\nedge_per_contract = 0.5\n")
    p, note = load_prediction("x", {"charter_hash": "not-the-hash"}, tmp_path)
    assert p is None and "does not match" in note  # an edited charter is not a prediction
