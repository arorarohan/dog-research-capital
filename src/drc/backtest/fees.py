"""Venue fee models.

PLACEHOLDERS: these are conservative taker-fee estimates for a small account, not verified
against each venue's current fee schedule. Check before trusting any result near break-even.
Passive (maker) fills are not modelled yet, so only taker fees are needed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol


class FeeModel(Protocol):
    def fee(self, qty: float, price: float) -> float: ...


@dataclass(frozen=True)
class BpsFee:
    """Fee as basis points (1 bp = 0.01%) of notional."""

    bps: float

    def fee(self, qty: float, price: float) -> float:
        return abs(qty) * price * self.bps / 1e4


@dataclass(frozen=True)
class KalshiFee:
    """Kalshi taker fee: 7% x contracts x p x (1 - p), rounded up to the cent.

    Largest at p = 0.5 and vanishing near 0 or 1, so cheap longshots and near-certainties
    cost almost nothing to trade while coin-flip contracts cost the most.
    """

    rate: float = 0.07

    def fee(self, qty: float, price: float) -> float:
        return math.ceil(self.rate * abs(qty) * price * (1 - price) * 100 - 1e-9) / 100


VENUE_FEES: dict[str, FeeModel] = {
    "coinbase": BpsFee(60),
    "kraken": BpsFee(40),
    "polymarket": BpsFee(0),
    "kalshi": KalshiFee(),
}
