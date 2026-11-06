"""A deterministic synthetic event log, written through the real Fund / SimBroker / risk code.

Used by the tests (snapshot numbers) and by `drc-report --sample` (a demo report with no real
data). Nothing here is market data: tokens, events and books are invented. The story covers
every report feature: fills (one partial), an unfilled and a risk-rejected order, skipped
candidates, marks, a win, a loss and a void settlement, a missed session, a manual pause and
resume, a drawdown halt, and a killed fund that stays in the firm totals.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from drc.backtest.fees import PolymarketFee
from drc.forward.broker import BrokerConfig, SimBroker
from drc.forward.eventlog import EventLog
from drc.forward.fund import Fund
from drc.forward.risk import RiskLimits
from drc.forward.types import BookSnapshot, Level, Order

S = 1_000_000_000
H = 3600 * S
DAY = 24 * H
T0 = 1_793_577_600 * S  # 2026-11-02 00:00 UTC

GAP_ID = "demo-gap-v1"
KILLED_ID = "demo-killed-v1"

GAP_CHARTER = """\
# Synthetic demo charter (not a real fund).
[hypothesis]
id = "demo-gap"
claim = "Demo only: buy Yes at 5-60c three days before the scheduled end."

[prediction]
edge_per_contract = 0.012   # dollars per contract, net of costs
ci = [0.005, 0.019]
sd_per_contract = 0.40

[evaluation]
alpha = 0.05
power = 0.80
min_events = 9000
"""

KILLED_CHARTER = """\
# Synthetic demo charter (not a real fund). No prediction on purpose.
[hypothesis]
id = "demo-killed"
claim = "Demo only: a fund that hits its drawdown halt and is killed."
"""


class _Clock:
    def __init__(self, t: int):
        self.t = t

    def __call__(self) -> int:
        return self.t


def charter_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _book(token: str, ts: int, bid: float, ask: float, ask_size: float = 4000.0) -> BookSnapshot:
    return BookSnapshot(token, ts, (Level(bid, 1000.0),), (Level(ask, ask_size),))


def build_sample(ledger: Path, charters: Path | None = None) -> EventLog:
    """Write the sample log to `ledger` (must not exist) and, if given, the two charters."""
    if ledger.exists():
        raise FileExistsError(ledger)
    if charters is not None:
        charters.mkdir(parents=True, exist_ok=True)
        (charters / f"{GAP_ID}.toml").write_text(GAP_CHARTER)
        (charters / f"{KILLED_ID}.toml").write_text(KILLED_CHARTER)

    clock = _Clock(T0)
    log = EventLog(ledger, clock=clock)
    fee = PolymarketFee(0.05)
    broker = SimBroker(BrokerConfig())  # default: 5 s latency, 25% depth share
    gap = Fund(
        log,
        GAP_ID,
        broker,
        RiskLimits(100.0, 150.0, 2000.0, 0.2),
        lambda _tok: fee,
        clock=clock,
    )
    killed = Fund(
        log, KILLED_ID, broker, RiskLimits(200.0, 200.0, 400.0, 0.2), lambda _: fee, clock
    )

    gap.create("Demo scheduled-end gap", charter_hash(GAP_CHARTER), git_sha="0000000")
    gap.allocate(10_000.0, "treasury: initial allocation")
    killed.create("Demo killed fund", charter_hash(KILLED_CHARTER), git_sha="0000000")
    killed.allocate(500.0, "treasury: initial allocation")

    def trade(fund, oid, token, event, qty, limit, bid, ask, ask_size=4000.0, sid=""):
        decided = clock.t
        o = Order(oid, fund.fund_id, token, event, "buy", qty, limit, decided, tag="demo")
        book = _book(token, decided + 6 * S, bid, ask, ask_size)
        clock.t = decided + 7 * S
        ex = fund.submit(o, book, session_id=sid)
        clock.t += S
        return ex

    def observe(fund, sid, token, verdict, reason):
        payload = {"token": token, "verdict": verdict, "reason": reason}
        fund.log.append("market_observed", payload, fund_id=fund.fund_id, session_id=sid)

    # ---- day 0: first session ---------------------------------------------------------------
    clock.t = T0 + 9 * H
    sid = "2026-11-02"
    gap.begin_session(sid, note="demo")
    observe(gap, sid, "A1", "trade", "in_band")
    observe(gap, sid, "B1", "trade", "in_band")
    observe(gap, sid, "C1", "trade", "in_band")
    observe(gap, sid, "D1", "trade", "in_band")
    observe(gap, sid, "X1", "skip", "outside_band")
    observe(gap, sid, "X2", "skip", "outside_band")
    observe(gap, sid, "X3", "skip", "outside_band")
    observe(gap, sid, "X4", "skip", "volume_floor")
    observe(gap, sid, "X5", "skip", "no_book")
    trade(gap, "o-A1", "A1", "EA", 300, 0.30, 0.29, 0.30, sid=sid)  # filled
    gap.mark("A1", 0.29, clock.t, sid)
    trade(gap, "o-B1", "B1", "EB", 200, 0.45, 0.44, 0.45, ask_size=400, sid=sid)  # partial 100
    gap.mark("B1", 0.44, clock.t, sid)
    trade(gap, "o-C1", "C1", "EC", 100, 0.20, 0.21, 0.22, sid=sid)  # unfilled: limit below ask
    trade(gap, "o-D1", "D1", "EC", 200, 0.20, 0.19, 0.20, sid=sid)  # filled, same event as C1
    gap.mark("D1", 0.19, clock.t, sid)
    trade(gap, "o-big", "Z1", "EZ", 500, 0.40, 0.39, 0.40, sid=sid)  # risk: order notional
    gap.finish_session(sid, trades=3)
    gap.reconcile(sid)

    # the killed fund buys once and its position collapses -> drawdown halt -> killed
    clock.t = T0 + 10 * H
    trade(killed, "k-1", "K1", "EK", 300, 0.50, 0.49, 0.50, sid="k-2026-11-02")
    killed.mark("K1", 0.10, clock.t, "k-2026-11-02")
    killed.check_drawdown("k-2026-11-02")
    clock.t += H
    killed.set_status("closed", "killed: drawdown halt hit; kill criterion met (demo)")

    # ---- day 1: the job did not run ---------------------------------------------------------
    clock.t = T0 + DAY + 9 * H
    gap.miss_session("2026-11-03", "laptop asleep (demo)")

    # ---- day 2: marks move, A1 wins, manual pause and resume --------------------------------
    clock.t = T0 + 2 * DAY + 9 * H
    sid = "2026-11-04"
    gap.begin_session(sid, note="demo")
    gap.mark("A1", 0.35, clock.t, sid)
    gap.mark("B1", 0.40, clock.t, sid)
    gap.mark("D1", 0.15, clock.t, sid)
    clock.t += H
    gap.settle("A1", "win", clock.t, sid)
    clock.t += H
    gap.set_status("paused", "manual: checking the fill model after a partial fill (demo)")
    clock.t += H
    gap.set_status("active", "manual: fill model checked, resuming (demo)")
    gap.finish_session(sid, trades=0)
    gap.reconcile(sid)

    # ---- day 3: B1 loses, D1's market is voided, one new trade ------------------------------
    clock.t = T0 + 3 * DAY + 9 * H
    sid = "2026-11-05"
    gap.begin_session(sid, note="demo")
    gap.settle("B1", "lose", clock.t, sid)
    gap.settle("D1", "void", clock.t, sid)
    observe(gap, sid, "E1", "trade", "in_band")
    observe(gap, sid, "X6", "skip", "outside_band")
    trade(gap, "o-E1", "E1", "EE", 300, 0.10, 0.09, 0.10, sid=sid)
    gap.mark("E1", 0.09, clock.t, sid)
    gap.finish_session(sid, trades=1)
    gap.reconcile(sid)
    return log
