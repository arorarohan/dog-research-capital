"""Limit order book state, rebuilt from normalized book events."""

from __future__ import annotations

from sortedcontainers import SortedDict


class Book:
    """Aggregate size per price level on each side."""

    __slots__ = ("bids", "asks")

    def __init__(self) -> None:
        self.bids: SortedDict = SortedDict()  # ascending price; best bid is the last item
        self.asks: SortedDict = SortedDict()  # ascending price; best ask is the first item

    def clear(self) -> None:
        self.bids.clear()
        self.asks.clear()

    def set_level(self, side: str, price: float, size: float) -> None:
        levels = self.bids if side == "bid" else self.asks
        if size > 0:
            levels[price] = size
        else:
            levels.pop(price, None)

    def best_bid(self) -> tuple[float, float] | None:
        return self.bids.peekitem(-1) if self.bids else None

    def best_ask(self) -> tuple[float, float] | None:
        return self.asks.peekitem(0) if self.asks else None

    def depth(self, side: str, n: int) -> float:
        """Total size across the best `n` levels of a side."""
        if side == "bid":
            return sum(self.bids.values()[-n:])
        return sum(self.asks.values()[:n])

    def crossed(self) -> bool:
        bb, ba = self.best_bid(), self.best_ask()
        return bb is not None and ba is not None and bb[0] >= ba[0]

    def trim(self, n: int) -> None:
        """Keep only the best `n` levels per side (for venues that stream a depth-limited book)."""
        while len(self.bids) > n:
            self.bids.popitem(0)
        while len(self.asks) > n:
            self.asks.popitem(-1)
