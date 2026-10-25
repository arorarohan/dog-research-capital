"""Firm-level totals: how much went in, how each fund is doing, overall PnL to date.

Everything is derived from the event log. Closed and halted funds stay in the totals forever
(no survivorship): a dead fund's loss is part of what finding the live one cost. The hypothesis
count is reported next to the PnL so a winner among many tries is read for what it is.
"""

from __future__ import annotations

from dataclasses import dataclass

from drc.forward.eventlog import EventLog
from drc.forward.state import FundState, MissingMark


@dataclass(frozen=True)
class FundSummary:
    fund_id: str
    name: str
    status: str
    allocated: float
    cash: float
    deployed: float
    nav: float | None  # None if an open position has no mark
    pnl: float | None
    realized_gross: float
    fees: float
    unrealized: float | None
    execution_cost: float  # spread/slippage paid vs mid, already inside the numbers above
    n_fills: int
    ret: float | None  # pnl / allocated
    ret_per_dollar_day: float | None  # pnl / dollar-days deployed
    dollar_days: float


@dataclass(frozen=True)
class FirmReport:
    funds: tuple[FundSummary, ...]
    allocated: float
    nav: float | None
    pnl: float | None
    n_hypotheses: int
    complete: bool  # False if any fund lacked marks, so nav/pnl cover marked funds only


def summarize(state: FundState, as_of_ts: int) -> FundSummary:
    try:
        nav, unrealized = state.nav(), state.unrealized()
    except MissingMark:
        nav = unrealized = None
    pnl = None if nav is None else nav - state.allocated
    dd = state.dollar_days_at(as_of_ts)
    return FundSummary(
        fund_id=state.fund_id,
        name=state.name,
        status=state.status,
        allocated=state.allocated,
        cash=state.cash,
        deployed=state.deployed(),
        nav=nav,
        pnl=pnl,
        realized_gross=state.ledger.realized_gross,
        fees=state.ledger.fees,
        unrealized=unrealized,
        execution_cost=state.execution_cost,
        n_fills=state.n_fills,
        ret=None if pnl is None or state.allocated <= 0 else pnl / state.allocated,
        ret_per_dollar_day=None if pnl is None or dd <= 0 else pnl / dd,
        dollar_days=dd,
    )


def firm_report(log: EventLog, as_of_ts: int) -> FirmReport:
    events = log.events()
    summaries = tuple(
        summarize(FundState.from_events(fid, events), as_of_ts) for fid in log.fund_ids()
    )
    marked = [s for s in summaries if s.nav is not None]
    complete = len(marked) == len(summaries)
    return FirmReport(
        funds=summaries,
        allocated=sum(s.allocated for s in summaries),
        nav=sum(s.nav for s in marked) if marked else None,
        pnl=sum(s.pnl for s in marked) if marked else None,
        n_hypotheses=len(summaries),
        complete=complete,
    )
