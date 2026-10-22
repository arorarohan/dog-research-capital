"""Portfolio ledger: cash, signed positions, realized PnL, fees, and an append-only fill log.

Accounting identity (checked in tests):
    equity = initial_cash + realized_gross - fees + unrealized
where unrealized is marked at the prices passed in. Positions may be short (negative qty).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Fill:
    ts: int
    symbol: str
    side: str  # buy | sell
    qty: float
    price: float
    fee: float
    order_id: int
    tag: str = ""
    mid: float | None = None  # mid at fill time, for cost attribution


@dataclass
class Position:
    qty: float = 0.0
    avg_cost: float = 0.0


@dataclass
class Ledger:
    initial_cash: float
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    realized_gross: float = 0.0
    fees: float = 0.0
    fills: list[Fill] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash = self.initial_cash

    def position(self, symbol: str) -> float:
        p = self.positions.get(symbol)
        return p.qty if p else 0.0

    def apply_fill(self, fill: Fill) -> None:
        signed = fill.qty if fill.side == "buy" else -fill.qty
        self.cash -= signed * fill.price + fill.fee
        self.fees += fill.fee
        pos = self.positions.setdefault(fill.symbol, Position())
        if pos.qty == 0 or (pos.qty > 0) == (signed > 0):
            total = pos.qty + signed
            pos.avg_cost = (pos.qty * pos.avg_cost + signed * fill.price) / total
            pos.qty = total
        else:
            closing = min(abs(signed), abs(pos.qty))
            direction = 1.0 if pos.qty > 0 else -1.0
            self.realized_gross += closing * (fill.price - pos.avg_cost) * direction
            pos.qty += signed
            if abs(pos.qty) < 1e-12:
                pos.qty, pos.avg_cost = 0.0, 0.0
            elif (pos.qty > 0) == (signed > 0):  # flipped through zero: remainder opens at price
                pos.avg_cost = fill.price
        self.fills.append(fill)

    def settle(self, symbol: str, value: float) -> float:
        """Close a position at a settlement value (e.g. 1.0 or 0.0 for a binary contract)."""
        pos = self.positions.get(symbol)
        if pos is None or pos.qty == 0:
            return 0.0
        pnl = pos.qty * (value - pos.avg_cost)
        self.cash += pos.qty * value
        self.realized_gross += pnl
        pos.qty, pos.avg_cost = 0.0, 0.0
        return pnl

    def unrealized(self, marks: dict[str, float]) -> float:
        return sum(p.qty * (marks[s] - p.avg_cost) for s, p in self.positions.items() if p.qty != 0)

    def equity(self, marks: dict[str, float]) -> float:
        return self.cash + sum(p.qty * marks[s] for s, p in self.positions.items() if p.qty != 0)

    def gross_exposure(self, marks: dict[str, float]) -> float:
        return sum(abs(p.qty) * marks[s] for s, p in self.positions.items() if p.qty != 0)
