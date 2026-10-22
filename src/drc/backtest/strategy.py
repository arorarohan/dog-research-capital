"""Strategy interface.

A strategy sees one bar at a time (never the future) and returns market orders. Orders are
executed no earlier than the *next* bar (see engine.py), so a decision can never fill at the
price that triggered it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Order:
    symbol: str
    side: str  # buy | sell
    qty: float  # base units (crypto) or contracts (prediction markets); always positive
    tag: str = ""


@dataclass
class Context:
    ts: int
    bars: dict[str, dict]  # symbol -> current bar row (only symbols with a bar at ts)
    history: dict[str, deque]  # symbol -> recent past bars, oldest first, excluding current
    positions: dict[str, float]
    cash: float
    equity: float
    state: dict = field(default_factory=dict)


class Strategy:
    """Subclass and implement `on_bar`. Keep per-run state on `self`."""

    def on_bar(self, ctx: Context) -> list[Order]:
        raise NotImplementedError
