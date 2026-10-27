"""Fund state as a projection of the event log.

`FundState.apply(event)` is the only way state changes, whether we are replaying history or
absorbing a just-written event, so a rebuilt state and a live one cannot diverge by code path.
Two independent calculations run side by side so reconciliation means something:
- `ledger`: the backtest `Ledger` (average-cost accounting), and
- `flow_cash` / `flow_qty`: plain cash-flow arithmetic with no cost-basis logic.
`flow_mismatches()` asserts they agree; `compare()` asserts live state equals a fresh rebuild.

Valuation is liquidation value: longs are marked at the bid (a `mark` event's price), never
the mid. A position with no mark has no honest value, so `nav()` raises rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from drc.backtest.ledger import Fill, Ledger
from drc.forward.eventlog import Event

DAY_NS = 86_400 * 1_000_000_000


class MissingMark(Exception):
    pass


@dataclass
class FundState:
    fund_id: str
    ledger: Ledger = field(default_factory=lambda: Ledger(0.0))
    name: str = ""
    charter_hash: str = ""
    status: str = "new"  # new | active | paused | halted | closed
    allocated: float = 0.0  # net: allocations minus withdrawals
    marks: dict[str, float] = field(default_factory=dict)
    token_event: dict[str, str] = field(default_factory=dict)
    order_ids: set[str] = field(default_factory=set)
    execution_cost: float = 0.0  # sum over fills of (price - mid) x qty, sign by side
    flow_cash: float = 0.0
    flow_qty: dict[str, float] = field(default_factory=dict)
    n_fills: int = 0
    dollar_days: float = 0.0
    _last_ts: int = 0
    seen_types: set[str] = field(default_factory=set)

    # ---- projection -------------------------------------------------------------------
    @classmethod
    def from_events(cls, fund_id: str, events: list[Event]) -> FundState:
        st = cls(fund_id)
        for e in events:
            if e.fund_id == fund_id:
                st.apply(e)
        return st

    def apply(self, e: Event) -> None:
        p = e.payload
        self._advance_clock(e.ts_event)
        t = e.type
        self.seen_types.add(t)
        if t == "fund_created":
            self.name, self.charter_hash, self.status = (
                p.get("name", ""),
                p["charter_hash"],
                "active",
            )
        elif t == "capital_allocated":
            self._cash(+p["amount"])
            self.allocated += p["amount"]
        elif t == "capital_withdrawn":
            self._cash(-p["amount"])
            self.allocated -= p["amount"]
        elif t in ("order_submitted", "order_rejected"):
            self.order_ids.add(p["order_id"])
        elif t == "fill":
            self._apply_fill(e)
        elif t == "mark":
            self.marks[p["token"]] = p["price"]
        elif t == "settlement":
            self._apply_settlement(p)
        elif t == "fund_paused":
            self.status = "paused"
        elif t == "fund_resumed":
            self.status = "active"
        elif t == "fund_halted":
            self.status = "halted"
        elif t == "fund_closed":
            self.status = "closed"

    def _cash(self, delta: float) -> None:
        self.ledger.cash += delta
        self.flow_cash += delta

    def _apply_fill(self, e: Event) -> None:
        p = e.payload
        side, qty, price, fee, token = p["side"], p["qty"], p["price"], p["fee"], p["token"]
        self.ledger.apply_fill(
            Fill(e.ts_event, token, side, qty, price, fee, e.seq, p.get("tag", ""), p.get("mid"))
        )
        signed = qty if side == "buy" else -qty
        self.flow_cash += -signed * price - fee
        self.flow_qty[token] = self.flow_qty.get(token, 0.0) + signed
        self.token_event[token] = p["event_id"]
        self.n_fills += 1
        if p.get("mid") is not None:
            self.execution_cost += signed * (price - p["mid"])

    def _apply_settlement(self, p: dict) -> None:
        token, value = p["token"], p["value"]
        self.ledger.settle(token, value)
        self.flow_cash += self.flow_qty.get(token, 0.0) * value
        self.flow_qty[token] = 0.0
        self.marks.pop(token, None)

    def _advance_clock(self, ts: int) -> None:
        # dollar-days: integrate the capital tied up in open positions over time
        if self._last_ts and ts > self._last_ts:
            self.dollar_days += self.deployed() * (ts - self._last_ts) / DAY_NS
        self._last_ts = max(self._last_ts, ts)

    # ---- views ------------------------------------------------------------------------
    def positions(self) -> dict[str, tuple[float, float]]:
        """token -> (qty, average cost) for open positions."""
        return {t: (p.qty, p.avg_cost) for t, p in self.ledger.positions.items() if p.qty != 0}

    @property
    def cash(self) -> float:
        return self.ledger.cash

    def deployed(self) -> float:
        """Cost basis of open positions: what is tied up until resolution."""
        return sum(q * c for q, c in self.positions().values())

    def event_exposure(self, event_id: str) -> float:
        """Cost basis of open positions on one real-world event (max loss for long-only)."""
        return sum(
            q * c for t, (q, c) in self.positions().items() if self.token_event.get(t) == event_id
        )

    def missing_marks(self) -> list[str]:
        return [t for t in self.positions() if t not in self.marks]

    def nav(self) -> float:
        if m := self.missing_marks():
            raise MissingMark(f"no mark for open positions: {m}")
        return self.ledger.equity(self.marks)

    def unrealized(self) -> float:
        if m := self.missing_marks():
            raise MissingMark(f"no mark for open positions: {m}")
        return self.ledger.unrealized(self.marks)

    def pnl(self) -> float:
        return self.nav() - self.allocated

    def dollar_days_at(self, as_of_ts: int) -> float:
        tail = max(0, as_of_ts - self._last_ts) / DAY_NS
        return self.dollar_days + self.deployed() * tail

    # ---- integrity --------------------------------------------------------------------
    def flow_mismatches(self, tol: float = 1e-6) -> list[str]:
        out = []
        if abs(self.flow_cash - self.ledger.cash) > tol:
            out.append(f"cash: flows {self.flow_cash:.6f} vs ledger {self.ledger.cash:.6f}")
        tokens = set(self.flow_qty) | {t for t, p in self.ledger.positions.items()}
        for t in sorted(tokens):
            a, b = self.flow_qty.get(t, 0.0), self.ledger.position(t)
            if abs(a - b) > tol:
                out.append(f"position {t}: flows {a} vs ledger {b}")
        return out


def compare(live: FundState, rebuilt: FundState, tol: float = 1e-6) -> list[str]:
    """Differences between an incrementally maintained state and a fresh rebuild."""
    out = []
    for name in ("cash", "allocated", "n_fills"):
        a, b = getattr(live, name), getattr(rebuilt, name)
        if abs(a - b) > tol:
            out.append(f"{name}: live {a} vs rebuilt {b}")
    if abs(live.ledger.fees - rebuilt.ledger.fees) > tol:
        out.append(f"fees: live {live.ledger.fees} vs rebuilt {rebuilt.ledger.fees}")
    if live.status != rebuilt.status:
        out.append(f"status: live {live.status} vs rebuilt {rebuilt.status}")
    lp, rp = live.positions(), rebuilt.positions()
    for t in sorted(set(lp) | set(rp)):
        (q1, c1), (q2, c2) = lp.get(t, (0.0, 0.0)), rp.get(t, (0.0, 0.0))
        if abs(q1 - q2) > tol or abs(c1 - c2) > tol:
            out.append(f"position {t}: live {(q1, c1)} vs rebuilt {(q2, c2)}")
    return out
