"""Venue fee models.

Coinbase and Kraken are still PLACEHOLDER taker-fee estimates, not verified against the
venues' current schedules. Kalshi and Polymarket follow their published formulas (Polymarket
verified against docs.polymarket.com/trading/fees on 2026-10-23). Check before trusting any
result near break-even.
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


# Taker fee rate by Polymarket market category (docs.polymarket.com/trading/fees, 2026-10-23).
# Makers pay 0 and receive a rebate funded by these fees (rebate not modelled).
POLYMARKET_TAKER_RATES: dict[str, float] = {
    "crypto": 0.07,
    "sports": 0.05,
    "finance": 0.04,
    "politics": 0.04,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "other": 0.05,
    "mentions": 0.04,
    "tech": 0.04,
    "geopolitics": 0.0,
    "combos": 0.05,
}


@dataclass(frozen=True)
class PolymarketFee:
    """Polymarket taker fee in USDC: rate x shares x p x (1 - p), rounded to 5 decimals.

    Same shape as Kalshi's but the rate depends on the market category (0 to 7%), the fee
    is not rounded up to the cent, and it is charged on every taker fill, buys and sells.
    Peak at p = 0.5: 1.75c per share for crypto, 1.25c sports/economics/other, 1.0c politics.
    Holding to resolution pays no exit fee. The default rate is the "other" category; use
    `PolymarketFee.for_category` when the market's category is known.
    """

    rate: float = POLYMARKET_TAKER_RATES["other"]

    @classmethod
    def for_category(cls, category: str) -> PolymarketFee:
        return cls(POLYMARKET_TAKER_RATES[category.strip().lower()])

    def fee(self, qty: float, price: float) -> float:
        return round(self.rate * abs(qty) * price * (1 - price), 5)


VENUE_FEES: dict[str, FeeModel] = {
    "coinbase": BpsFee(60),
    "kraken": BpsFee(40),
    "polymarket": PolymarketFee(),
    "kalshi": KalshiFee(),
}
