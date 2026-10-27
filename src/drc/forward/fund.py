"""A forward-test fund: the only path from a strategy's order to the (simulated) broker.

Every action writes its events in one transaction and updates in-memory state only after the
commit, so a crash mid-action leaves the log and the state consistent. Orders are idempotent
by `order_id`: resubmitting one after a restart does nothing.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict

from drc.backtest.fees import FeeModel
from drc.forward.broker import Broker
from drc.forward.eventlog import Event, EventLog
from drc.forward.risk import RiskLimits, check_order, drawdown_breached
from drc.forward.state import FundState, compare
from drc.forward.types import BookSnapshot, Execution, Order


class Fund:
    def __init__(
        self,
        log: EventLog,
        fund_id: str,
        broker: Broker,
        limits: RiskLimits,
        fee_for: Callable[[str], FeeModel],
        clock: Callable[[], int] = time.time_ns,
    ):
        self.log, self.fund_id = log, fund_id
        self.broker, self.limits, self.fee_for, self.clock = broker, limits, fee_for, clock
        self.state = FundState.from_events(fund_id, log.events(fund_id=fund_id))

    # ---- plumbing ---------------------------------------------------------------------
    def _write(self, specs: list[tuple], session_id: str = "") -> list[Event]:
        """specs: (type, payload) or (type, payload, ts_event). Atomic, then apply to state."""
        events: list[Event] = []
        with self.log.transaction():
            for s in specs:
                ts = s[2] if len(s) > 2 else None
                events.append(
                    self.log.append(
                        s[0], s[1], fund_id=self.fund_id, session_id=session_id, ts_event=ts
                    )
                )
        for e in events:
            self.state.apply(e)
        return events

    # ---- lifecycle --------------------------------------------------------------------
    def create(self, name: str, charter_hash: str, git_sha: str = "") -> None:
        if "fund_created" in self.state.seen_types:
            if self.state.charter_hash != charter_hash:
                raise ValueError("fund exists with a different charter; changed rules = new fund")
            return
        self._write(
            [("fund_created", {"name": name, "charter_hash": charter_hash, "git_sha": git_sha})]
        )

    def allocate(self, amount: float, note: str = "") -> None:
        if amount <= 0:
            raise ValueError("amount must be positive")
        self._write([("capital_allocated", {"amount": amount, "note": note})])

    def withdraw(self, amount: float, note: str = "") -> None:
        if amount <= 0 or amount > self.state.cash + 1e-9:
            raise ValueError("withdrawal must be positive and no more than free cash")
        self._write([("capital_withdrawn", {"amount": amount, "note": note})])

    def set_status(self, status: str, reason: str) -> None:
        """Manual or automatic state change; always logged with a reason."""
        ev = {"paused": "fund_paused", "active": "fund_resumed", "closed": "fund_closed"}
        if status not in ev:
            raise ValueError(f"cannot set status {status!r}")
        self._write([(ev[status], {"reason": reason})])

    def begin_session(self, session_id: str, **info) -> None:
        self._write([("session_started", info)], session_id)

    def finish_session(self, session_id: str, **summary) -> None:
        self._write([("session_finished", summary)], session_id)

    def miss_session(self, session_id: str, reason: str) -> None:
        self._write([("session_missed", {"reason": reason})], session_id)

    # ---- trading ----------------------------------------------------------------------
    def mark(self, token: str, bid_price: float, ts: int, session_id: str = "") -> None:
        """Record the liquidation value of a position (best bid, never the mid)."""
        self._write([("mark", {"token": token, "price": bid_price}, ts)], session_id)

    def submit(
        self, order: Order, fill_book: BookSnapshot, now: int | None = None, session_id: str = ""
    ) -> Execution:
        if order.fund_id != self.fund_id:
            raise ValueError("order belongs to another fund")
        if order.order_id in self.state.order_ids:
            return Execution("rejected", "duplicate_order")
        now = self.clock() if now is None else now
        fee_model = self.fee_for(order.token)
        od = asdict(order)
        specs: list[tuple] = []

        violations = check_order(order, self.state, self.limits, fee_model)
        specs.append(
            ("risk_check", {"order_id": order.order_id, "ok": not violations, "v": violations})
        )
        if violations:
            ex = Execution("rejected", ";".join(violations))
            specs.append(
                ("order_rejected", {**od, "order_id": order.order_id, "reason": ex.reason})
            )
            self._write(specs, session_id)
            return ex

        specs.append(("order_submitted", od))
        ex = self.broker.execute(order, fill_book, now, fee_model)
        if ex.status == "rejected":
            specs.append(("order_rejected", {**od, "reason": ex.reason}))
        elif ex.status == "unfilled":
            specs.append(("order_unfilled", {"order_id": order.order_id, "reason": ex.reason}))
        else:
            specs.append(
                (
                    "fill",
                    {
                        "order_id": order.order_id,
                        "token": order.token,
                        "event_id": order.event_id,
                        "side": order.side,
                        "qty": ex.qty,
                        "price": ex.avg_price,
                        "fee": ex.fee,
                        "legs": [list(leg) for leg in ex.legs],
                        "mid": ex.mid,
                        "book_ts": ex.book_ts,
                        "decision_ts": order.decision_ts,
                        "status": ex.status,
                        "tag": order.tag,
                    },
                    ex.book_ts,
                )
            )
        self._write(specs, session_id)
        return ex

    def settle(self, token: str, outcome: str, ts: int, session_id: str = "") -> None:
        """Settle an open position. outcome: win (pays 1), lose (pays 0), void (cost returned)."""
        if outcome not in ("win", "lose", "void"):
            raise ValueError("outcome must be win | lose | void")
        pos = self.state.ledger.positions.get(token)
        if pos is None or pos.qty == 0:
            raise ValueError(f"no open position in {token}")
        value = {"win": 1.0, "lose": 0.0, "void": pos.avg_cost}[outcome]
        self._write(
            [("settlement", {"token": token, "value": value, "kind": outcome}, ts)], session_id
        )

    # ---- controls ---------------------------------------------------------------------
    def check_drawdown(self, session_id: str = "") -> bool:
        """Halt new entries if NAV breached the drawdown limit. Returns True if halted."""
        if self.state.status == "active" and drawdown_breached(self.state, self.limits):
            self._write([("fund_halted", {"reason": "drawdown"})], session_id)
        return self.state.status == "halted"

    def reconcile(self, session_id: str = "") -> list[str]:
        """Compare live state with a fresh rebuild and with plain cash-flow arithmetic.
        Any mismatch halts the fund; the result is logged either way."""
        rebuilt = FundState.from_events(self.fund_id, self.log.events(fund_id=self.fund_id))
        problems = compare(self.state, rebuilt) + self.state.flow_mismatches()
        specs: list[tuple] = [("reconciliation", {"ok": not problems, "problems": problems})]
        if problems and self.state.status != "halted":
            specs.append(("fund_halted", {"reason": "reconciliation"}))
        self._write(specs, session_id)
        return problems
