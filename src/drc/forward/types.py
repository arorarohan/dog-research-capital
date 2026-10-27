"""Plain data types shared by the broker, risk checks and fund."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Level:
    price: float
    size: float  # shares/contracts displayed at this price


@dataclass(frozen=True)
class BookSnapshot:
    """One venue's order book for one token at one instant (our local receive time, ns).

    `bids` are sorted best (highest) first, `asks` best (lowest) first. `tick` and `min_size`
    come from the venue per market; the defaults are placeholders until Slice 1 reads them
    from the venue's book response.
    """

    token: str
    ts: int
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    tick: float = 0.01
    min_size: float = 5.0

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None


@dataclass(frozen=True)
class Order:
    """A marketable limit order (immediate-or-cancel): fills what it can at or better than
    `limit_price` and cancels the rest. `order_id` is client-chosen and must be unique per
    fund; resubmitting the same id is a no-op (idempotent restarts)."""

    order_id: str
    fund_id: str
    token: str
    event_id: str  # the real-world event this contract is about (exposure is grouped by it)
    side: str  # buy | sell
    qty: float
    limit_price: float
    decision_ts: int  # when the strategy decided, ns
    tag: str = ""


@dataclass(frozen=True)
class Execution:
    """What the broker did with an order. `status` is one of
    filled | partial | unfilled | rejected."""

    status: str
    reason: str = ""
    legs: tuple[tuple[float, float], ...] = ()  # (price, qty) per book level walked
    fee: float = 0.0
    mid: float | None = None  # mid of the book the fill used, for cost attribution
    book_ts: int | None = None

    @property
    def qty(self) -> float:
        return sum(q for _, q in self.legs)

    @property
    def avg_price(self) -> float | None:
        q = self.qty
        return sum(p * q_ for p, q_ in self.legs) / q if q else None
