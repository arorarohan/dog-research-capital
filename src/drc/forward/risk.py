"""Pre-trade risk checks: the same gate a live system would need before sending an order.

`check_order` returns violation codes (empty = allowed). Checks reject, never silently resize,
and a strategy cannot skip them because `Fund.submit` is the only path to the broker.
Exposure is cost basis of open long positions, which is the most a long-only binary position
can lose.
"""

from __future__ import annotations

from dataclasses import dataclass

from drc.backtest.fees import FeeModel
from drc.forward.state import FundState
from drc.forward.types import Order


@dataclass(frozen=True)
class RiskLimits:
    max_order_notional: float  # dollars, at the limit price
    max_event_exposure: float  # dollars at risk on any one real-world event
    max_fund_exposure: float  # dollars at risk across the fund
    max_drawdown: float  # fraction of net allocated capital; breach halts new entries


def drawdown_breached(state: FundState, limits: RiskLimits) -> bool:
    """True if marked NAV is at or below allocated x (1 - max_drawdown). Needs marks."""
    return state.allocated > 0 and state.nav() <= state.allocated * (1 - limits.max_drawdown)


def check_order(
    order: Order, state: FundState, limits: RiskLimits, fee_model: FeeModel
) -> list[str]:
    v: list[str] = []
    if state.status != "active":
        return [f"fund_{state.status}"]
    if order.side == "sell":
        held = state.ledger.position(order.token)
        if order.qty > held + 1e-9:
            v.append("would_short")
        return v  # selling reduces risk; no exposure checks

    if state.missing_marks():
        v.append("missing_marks")
    elif drawdown_breached(state, limits):
        v.append("drawdown_halt")

    notional = order.qty * order.limit_price
    if notional > limits.max_order_notional + 1e-9:
        v.append("order_notional")
    if state.event_exposure(order.event_id) + notional > limits.max_event_exposure + 1e-9:
        v.append("event_exposure")
    if state.deployed() + notional > limits.max_fund_exposure + 1e-9:
        v.append("fund_exposure")
    if notional + fee_model.fee(order.qty, order.limit_price) > state.cash + 1e-9:
        v.append("insufficient_cash")
    return v
