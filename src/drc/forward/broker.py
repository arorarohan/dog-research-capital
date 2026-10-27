"""Broker interface and the simulated, deliberately pessimistic implementation.

The strategy-facing contract is `Broker.execute`. A live broker would implement the same
method; only `SimBroker` exists, so nothing here can reach a real exchange.

SimBroker is stricter than the backtest's top-of-book fill:
- fills only against a book at least `latency_ns` AFTER the decision (never the same instant);
- refuses stale, one-sided, locked or crossed books;
- walks the displayed levels at or better than the limit, paying each level's price;
- takes at most `depth_share` of the displayed eligible depth;
- floors the quantity to the size step; partial fills are allowed and the rest is cancelled;
- charges the venue fee on every level walked.
It still cannot model market impact beyond displayed depth, adverse selection, or API failure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from drc.backtest.fees import FeeModel
from drc.forward.types import BookSnapshot, Execution, Order

_EPS = 1e-9


class Broker(Protocol):
    def execute(
        self, order: Order, book: BookSnapshot, now: int, fee_model: FeeModel
    ) -> Execution: ...


@dataclass(frozen=True)
class BrokerConfig:
    latency_ns: int = 5_000_000_000  # decision -> earliest book we may fill against
    max_book_age_ns: int = 30_000_000_000  # book must be this fresh at execution time
    depth_share: float = 0.25  # never take more than this share of eligible displayed depth
    size_step: float = 0.01


def _floor_to(x: float, step: float) -> float:
    return round(math.floor(x / step + _EPS) * step, 8)


def _on_tick(price: float, tick: float) -> bool:
    return abs(price / tick - round(price / tick)) < 1e-6


class SimBroker:
    def __init__(self, config: BrokerConfig | None = None):
        self.cfg = config or BrokerConfig()

    def execute(self, order: Order, book: BookSnapshot, now: int, fee_model: FeeModel) -> Execution:
        cfg = self.cfg

        def reject(reason: str) -> Execution:
            return Execution("rejected", reason, book_ts=book.ts)

        def unfilled(reason: str, mid: float | None = None) -> Execution:
            return Execution("unfilled", reason, mid=mid, book_ts=book.ts)

        if order.side not in ("buy", "sell"):
            return reject("bad_side")
        if order.qty <= 0:
            return reject("bad_qty")
        if order.qty < book.min_size - _EPS:
            return reject("below_min_size")
        if not _on_tick(order.limit_price, book.tick):
            return reject("off_tick")
        if book.token != order.token:
            return reject("wrong_book")
        if book.ts < order.decision_ts + cfg.latency_ns:
            return reject("book_too_early")  # would be filling at (or before) the decision
        if now - book.ts > cfg.max_book_age_ns:
            return reject("stale_book")
        if not book.bids or not book.asks:
            return reject("one_sided_book")
        bb, ba = book.bids[0].price, book.asks[0].price
        if bb >= ba:
            return reject("locked_or_crossed_book")
        mid = (bb + ba) / 2

        if order.side == "buy":
            levels = [lv for lv in book.asks if lv.price <= order.limit_price + _EPS]
            levels.sort(key=lambda lv: lv.price)
        else:
            levels = [lv for lv in book.bids if lv.price >= order.limit_price - _EPS]
            levels.sort(key=lambda lv: -lv.price)
        if not levels:
            return unfilled("limit_not_marketable", mid)

        cap = cfg.depth_share * sum(lv.size for lv in levels)
        target = _floor_to(min(order.qty, cap), cfg.size_step)
        if target <= 0:
            return unfilled("no_depth", mid)

        legs: list[tuple[float, float]] = []
        left = target
        for lv in levels:
            take = _floor_to(min(left, lv.size), cfg.size_step)
            if take <= 0:
                continue
            legs.append((lv.price, take))
            left = round(left - take, 8)
            if left <= 0:
                break
        if not legs:
            return unfilled("no_depth", mid)

        fee = sum(fee_model.fee(q, p) for p, q in legs)
        filled = sum(q for _, q in legs)
        status = "filled" if filled >= order.qty - _EPS else "partial"
        reason = "" if status == "filled" else "depth_capped"
        return Execution(status, reason, tuple(legs), fee, mid, book.ts)
