"""Report data: everything a fund or firm page shows, derived from the event log only.

Rules this module enforces (docs/design/forward-test-platform.md section 8):
- Valuation is liquidation value (from `mark` events: the position walked down the displayed
  bids, net of the exit taker fee; `drc.forward.broker.liquidation_value`). The mid is used for
  one thing only: attributing how much spread we paid on each fill. No headline number is
  mid-marked.
- A position without a mark has no value. NAV and PnL are then None, never a guess, and the NAV
  curve has a gap there instead of a line drawn across it.
- Closed, halted and killed funds stay in the firm totals.
- `as_of` defaults to the latest event time in the log, so the same log always renders the same
  report (no hidden dependence on the wall clock).
"""

from __future__ import annotations

import hashlib
import math
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import NormalDist

from drc.forward.eventlog import Event, EventLog, VerifyResult
from drc.forward.state import DAY_NS, FundState, MissingMark
from drc.forward.treasury import FirmReport, FundSummary, firm_report

# Per-contract standard deviation of a binary payoff when the charter states none. A contract
# paying 0 or 1 at a price near 0.2-0.5 has sd sqrt(p(1-p)) of about 0.40 (scoping doc Q11).
DEFAULT_SD = 0.40
MARK_STALE_NS = 36 * 3600 * 1_000_000_000  # a mark older than this at as_of is flagged
BLIP_NS = 5 * 60 * 1_000_000_000  # an unmarked fill marked within this does not break the curve

# Types that are flagged on the fund page because a human or a safety rule changed the fund.
FLAG_TYPES = ("fund_paused", "fund_resumed", "fund_halted", "fund_closed")
NAV_TYPES = ("capital_allocated", "capital_withdrawn", "fill", "mark", "settlement")


# ---- rows ----------------------------------------------------------------------------------
@dataclass(frozen=True)
class NavPoint:
    ts: int
    nav: float | None  # None = some open position had no mark: a gap, not a value
    allocated: float
    cause: str  # event type that produced the point


@dataclass(frozen=True)
class PositionRow:
    token: str
    event_id: str
    qty: float
    avg_cost: float
    mark: float | None
    mark_ts: int | None
    stale: bool

    @property
    def cost_basis(self) -> float:
        return self.qty * self.avg_cost

    @property
    def value(self) -> float | None:
        return None if self.mark is None else self.qty * self.mark

    @property
    def unrealized(self) -> float | None:
        return None if self.mark is None else self.qty * (self.mark - self.avg_cost)


@dataclass(frozen=True)
class BlotterRow:
    seq: int
    ts: int
    session_id: str
    order_id: str
    token: str
    event_id: str
    side: str
    qty: float
    price: float
    fee: float
    mid: float | None
    spread_cost: float | None  # (price - mid) x qty, signed so paying away is positive
    n_levels: int
    status: str  # filled | partial
    tag: str


@dataclass(frozen=True)
class SettlementRow:
    seq: int
    ts: int
    token: str
    event_id: str
    kind: str  # win | lose | split | void
    value: float
    qty: float
    avg_cost: float
    fees: float  # fees paid on this position's fills

    @property
    def gross(self) -> float:
        return self.qty * (self.value - self.avg_cost)

    @property
    def net(self) -> float:
        return self.gross - self.fees


@dataclass(frozen=True)
class ExposureRow:
    event_id: str
    n_tokens: int
    cost_basis: float  # most a long binary position can lose
    value: float | None  # at the bid


@dataclass(frozen=True)
class FlagRow:
    seq: int
    ts: int
    type: str
    reason: str
    session_id: str


@dataclass(frozen=True)
class CostAttribution:
    """net PnL = edge vs mid - spread paid - fees. `edge_vs_mid` is what the trades would have
    made at the mid with no fees; spread and fees are what crossing the book and the venue cost.
    All None when an open position is unmarked."""

    edge_vs_mid: float | None
    spread: float
    fees: float
    net: float | None
    realized_gross: float
    unrealized: float | None


@dataclass(frozen=True)
class Prediction:
    edge: float  # predicted net edge per contract, dollars
    ci_low: float | None
    ci_high: float | None
    sd: float
    sd_source: str
    alpha: float
    power: float
    min_events: int | None
    source: str  # where it came from: event log or a hash-verified charter file


@dataclass(frozen=True)
class EdgeStats:
    """Realized per-contract net edge on settled (win/lose/split) positions, with the power note.
    A split (Polymarket 50/50 resolution, pays 0.5) is a real outcome, so it is in the sample.

    Independent unit = distinct real-world event (legs of one event are dependent). Voids are
    excluded from the edge sample (no outcome) but their fees are in PnL.
    """

    n_positions: int
    n_events: int
    n_contracts: float
    n_voids: int
    realized: float | None  # contract-weighted net edge per contract
    ci_low: float | None
    ci_high: float | None
    sd: float
    mde: float | None  # minimum detectable edge at this n, alpha, power
    needed_events: int | None  # events needed to detect the predicted edge
    verdict: str


@dataclass(frozen=True)
class SessionInfo:
    started: tuple[str, ...]
    finished: tuple[str, ...]
    unfinished: tuple[str, ...]  # started but never finished: a crash or a kill mid-session
    missed: tuple[FlagRow, ...]


@dataclass(frozen=True)
class FundView:
    summary: FundSummary
    created_ts: int | None
    charter_hash: str
    git_sha: str
    nav_points: tuple[NavPoint, ...]
    positions: tuple[PositionRow, ...]
    exposure: tuple[ExposureRow, ...]
    blotter: tuple[BlotterRow, ...]
    settlements: tuple[SettlementRow, ...]
    costs: CostAttribution
    contracts_bought: float
    days_live: float
    candidates: tuple[tuple[str, str, int], ...]  # (verdict, reason, count) from market_observed
    order_outcomes: tuple[tuple[str, str, int], ...]  # (outcome, reason, count)
    sessions: SessionInfo
    flags: tuple[FlagRow, ...]
    reconciliations_ok: int
    reconciliation_failures: tuple[FlagRow, ...]
    prediction: Prediction | None
    prediction_note: str
    edge: EdgeStats

    @property
    def fund_id(self) -> str:
        return self.summary.fund_id

    @property
    def stale_or_missing_marks(self) -> tuple[PositionRow, ...]:
        return tuple(p for p in self.positions if p.mark is None or p.stale)

    @property
    def ret_per_contract(self) -> float | None:
        s = self.summary
        if s.pnl is None or self.contracts_bought <= 0:
            return None
        return s.pnl / self.contracts_bought


@dataclass(frozen=True)
class FirmView:
    as_of: int
    ledger: str
    n_events: int
    first_ts: int | None
    verify: VerifyResult
    firm: FirmReport
    funds: tuple[FundView, ...] = field(default=())


# ---- prediction ----------------------------------------------------------------------------
def _normalize_prediction(d: dict, ev: dict) -> dict:
    """Accept both charter spellings: dollars (`edge_per_contract`, `ci`, `sd_per_contract`)
    and the Slice 1 charter's cents (`net_per_contract_cents`, `ci95_cents`, with the sd in
    `[evaluation] per_contract_sd`)."""
    d = dict(d or {})
    if d.get("edge_per_contract") is None and d.get("net_per_contract_cents") is not None:
        d["edge_per_contract"] = float(d["net_per_contract_cents"]) / 100
    if not d.get("ci") and d.get("ci95_cents"):
        d["ci"] = [None if c is None else float(c) / 100 for c in d["ci95_cents"]]
    if d.get("sd_per_contract") is None and ev.get("per_contract_sd") is not None:
        d["sd_per_contract"] = ev["per_contract_sd"]
    return d


def _prediction_from(d: dict, ev: dict, source: str) -> Prediction | None:
    d = _normalize_prediction(d, ev or {})
    if d.get("edge_per_contract") is None:
        return None
    ci = d.get("ci") or [None, None]
    sd = d.get("sd_per_contract")
    return Prediction(
        edge=float(d["edge_per_contract"]),
        ci_low=None if ci[0] is None else float(ci[0]),
        ci_high=None if ci[1] is None else float(ci[1]),
        sd=float(sd) if sd is not None else DEFAULT_SD,
        sd_source="charter" if sd is not None else "default (binary payoff, ~0.40)",
        alpha=float(ev.get("alpha", 0.05)),
        power=float(ev.get("power", 0.80)),
        min_events=None if ev.get("min_events") is None else int(ev["min_events"]),
        source=source,
    )


def load_prediction(
    fund_id: str, created: dict, charters: Path | None
) -> tuple[Prediction | None, str]:
    """The fund's pre-registered prediction, from the `fund_created` payload or the charter.

    A charter file is used only if its sha256 equals the `charter_hash` logged at creation: a
    prediction edited after launch is not a prediction.
    """
    if created.get("prediction"):
        p = _prediction_from(created["prediction"], created.get("evaluation") or {}, "event log")
        if p:
            return p, ""
    if charters is None:
        return None, "no prediction in the event log and no charter directory given"
    path = charters / f"{fund_id}.toml"
    if not path.exists():
        return None, f"no charter file {path.name}"
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != created.get("charter_hash"):
        return None, f"{path.name} does not match the charter hash logged at creation; ignored"
    doc = tomllib.loads(raw.decode())
    p = _prediction_from(doc.get("prediction") or {}, doc.get("evaluation") or {}, path.name)
    return p, "" if p else f"{path.name} has no [prediction] edge_per_contract"


def edge_stats(settlements: list[SettlementRow], pred: Prediction | None) -> EdgeStats:
    decided = [s for s in settlements if s.kind in ("win", "lose", "split")]
    n_voids = sum(1 for s in settlements if s.kind == "void")
    n_events = len({s.event_id for s in decided})
    contracts = sum(s.qty for s in decided)
    alpha = pred.alpha if pred else 0.05
    power = pred.power if pred else 0.80
    sd = pred.sd if pred else DEFAULT_SD
    z = NormalDist().inv_cdf(1 - alpha / 2)
    zp = NormalDist().inv_cdf(power)
    needed = None
    if pred and pred.edge > 0:
        needed = math.ceil(((z + zp) * sd / pred.edge) ** 2)
    if not decided or contracts <= 0:
        return EdgeStats(
            0, 0, 0.0, n_voids, None, None, None, sd, None, needed, "no settled trades"
        )
    realized = sum(s.net for s in decided) / contracts
    se = sd / math.sqrt(n_events)
    lo, hi = realized - z * se, realized + z * se
    mde = (z + zp) * se
    if needed is not None and n_events < needed:
        verdict = (
            f"inconclusive by design: {n_events:,} of ~{needed:,} independent events needed "
            "to detect the predicted edge"
        )
    elif lo > 0:
        verdict = "realized edge is above zero at this sample size"
    elif hi < 0:
        verdict = "realized edge is below zero at this sample size"
    else:
        verdict = "realized edge is not distinguishable from zero"
    return EdgeStats(
        len(decided), n_events, contracts, n_voids, realized, lo, hi, sd, mde, needed, verdict
    )


# ---- one fund ------------------------------------------------------------------------------
def build_fund(
    events: list[Event], summary: FundSummary, as_of: int, charters: Path | None = None
) -> FundView:
    fid = summary.fund_id
    st = FundState(fid)
    nav_points: dict[int, NavPoint] = {}
    clock = 0
    mark_ts: dict[str, int] = {}
    token_fees: dict[str, float] = defaultdict(float)
    blotter: list[BlotterRow] = []
    settlements: list[SettlementRow] = []
    candidates: Counter = Counter()
    outcomes: Counter = Counter()
    started: list[str] = []
    finished: list[str] = []
    missed: list[FlagRow] = []
    flags: list[FlagRow] = []
    rec_ok, rec_bad = 0, []
    created: dict = {}
    created_ts: int | None = None
    contracts_bought = 0.0

    for e in events:
        p, t = e.payload, e.type
        if t == "settlement":  # read the position before the settlement closes it
            pos = st.ledger.positions.get(p["token"])
            qty, cost = (pos.qty, pos.avg_cost) if pos else (0.0, 0.0)
            settlements.append(
                SettlementRow(
                    e.seq, e.ts_event, p["token"], st.token_event.get(p["token"], ""),
                    p.get("kind", ""), p["value"], qty, cost, token_fees.pop(p["token"], 0.0),
                )
            )  # fmt: skip
        st.apply(e)
        clock = max(clock, e.ts_event)
        if t == "fund_created":
            created, created_ts = p, e.ts_event
        elif t == "fill":
            token_fees[p["token"]] += p["fee"]
            mid = p.get("mid")
            sign = 1.0 if p["side"] == "buy" else -1.0
            if p["side"] == "buy":
                contracts_bought += p["qty"]
            blotter.append(
                BlotterRow(
                    e.seq, e.ts_event, e.session_id, p["order_id"], p["token"],
                    p.get("event_id", ""), p["side"], p["qty"], p["price"], p["fee"], mid,
                    None if mid is None else sign * (p["price"] - mid) * p["qty"],
                    len(p.get("legs") or []), p.get("status", "filled"), p.get("tag", ""),
                )
            )  # fmt: skip
            outcomes[(p.get("status", "filled"), "")] += 1
        elif t == "mark":
            mark_ts[p["token"]] = e.ts_event
        elif t == "order_unfilled":
            outcomes[("unfilled", p.get("reason", ""))] += 1
        elif t == "order_rejected":
            outcomes[("rejected", p.get("reason", ""))] += 1
        elif t == "market_observed":
            candidates[(str(p.get("verdict", "")), str(p.get("reason", "")))] += 1
        elif t == "session_started":
            started.append(e.session_id)
        elif t == "session_finished":
            finished.append(e.session_id)
        elif t == "session_missed":
            missed.append(FlagRow(e.seq, e.ts_event, t, p.get("reason", ""), e.session_id))
        elif t in FLAG_TYPES:
            flags.append(FlagRow(e.seq, e.ts_event, t, p.get("reason", ""), e.session_id))
        elif t == "reconciliation":
            if p.get("ok"):
                rec_ok += 1
            else:
                reason = "; ".join(p.get("problems") or []) or "mismatch"
                rec_bad.append(FlagRow(e.seq, e.ts_event, t, reason, e.session_id))
        if t in NAV_TYPES:
            try:
                nav = st.nav()
            except MissingMark:
                nav = None
            nav_points[clock] = NavPoint(clock, nav, st.allocated, t)

    positions = []
    curve = _drop_blips([nav_points[k] for k in sorted(nav_points)])
    for tok, (qty, cost) in sorted(st.positions().items()):
        m = st.marks.get(tok)
        mts = mark_ts.get(tok) if m is not None else None
        stale = mts is not None and as_of - mts > MARK_STALE_NS
        positions.append(PositionRow(tok, st.token_event.get(tok, ""), qty, cost, m, mts, stale))

    by_event: dict[str, list[PositionRow]] = defaultdict(list)
    for pr in positions:
        by_event[pr.event_id].append(pr)
    exposure = sorted(
        (
            ExposureRow(
                ev,
                len(rows),
                sum(r.cost_basis for r in rows),
                None if any(r.value is None for r in rows) else sum(r.value for r in rows),
            )
            for ev, rows in by_event.items()
        ),
        key=lambda x: (-x.cost_basis, x.event_id),
    )

    s = summary
    costs = CostAttribution(
        edge_vs_mid=None if s.pnl is None else s.pnl + s.fees + s.execution_cost,
        spread=s.execution_cost,
        fees=s.fees,
        net=s.pnl,
        realized_gross=s.realized_gross,
        unrealized=s.unrealized,
    )
    pred, note = load_prediction(fid, created, charters)
    unfinished = tuple(x for x in dict.fromkeys(started) if x not in set(finished))
    return FundView(
        summary=s,
        created_ts=created_ts,
        charter_hash=created.get("charter_hash", ""),
        git_sha=created.get("git_sha", ""),
        nav_points=curve,
        positions=tuple(positions),
        exposure=tuple(exposure),
        blotter=tuple(blotter),
        settlements=tuple(settlements),
        costs=costs,
        contracts_bought=contracts_bought,
        days_live=0.0 if created_ts is None else max(0, as_of - created_ts) / DAY_NS,
        candidates=tuple((v, r, n) for (v, r), n in sorted(candidates.items())),
        order_outcomes=tuple((o, r, n) for (o, r), n in sorted(outcomes.items())),
        sessions=SessionInfo(tuple(started), tuple(finished), unfinished, tuple(missed)),
        flags=tuple(flags),
        reconciliations_ok=rec_ok,
        reconciliation_failures=tuple(rec_bad),
        prediction=pred,
        prediction_note=note,
        edge=edge_stats(settlements, pred),
    )


def _drop_blips(points: list[NavPoint]) -> tuple[NavPoint, ...]:
    """Drop an unknown-NAV point when a marked one follows within BLIP_NS (a fill, then its
    mark in the same session). A longer unmarked stretch stays in the curve as a gap."""
    out = []
    for i, pt in enumerate(points):
        nxt = points[i + 1] if i + 1 < len(points) else None
        if pt.nav is None and nxt is not None and nxt.nav is not None:
            if nxt.ts - pt.ts <= BLIP_NS:
                continue
        out.append(pt)
    return tuple(out)


# ---- the firm ------------------------------------------------------------------------------
def build_firm(
    log: EventLog, as_of: int | None = None, charters: Path | None = None, ledger: str = ""
) -> FirmView:
    events = log.events()
    if as_of is None:
        as_of = max((e.ts_event for e in events), default=0)
    firm = firm_report(log, as_of)
    by_fund: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        by_fund[e.fund_id].append(e)
    funds = tuple(build_fund(by_fund[s.fund_id], s, as_of, charters) for s in firm.funds)
    return FirmView(
        as_of=as_of,
        ledger=ledger,
        n_events=len(events),
        first_ts=events[0].ts_event if events else None,
        verify=log.verify(),
        firm=firm,
        funds=funds,
    )


def avg_deployed(view: FundView) -> float | None:
    """Average capital tied up in open positions over the fund's life."""
    return None if view.days_live <= 0 else view.summary.dollar_days / view.days_live
