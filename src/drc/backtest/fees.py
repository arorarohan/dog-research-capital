"""Venue fee models.

All four venues now follow published schedules, checked 2026-10-23:
- Polymarket: docs.polymarket.com/trading/fees, plus the per-market `feeSchedule` that Gamma
  returns (preferred: the docs table is a summary and already disagrees with live markets,
  e.g. `sports_fees_v2` markets charge 3%, not the table's 5%).
- Kraken: support.kraken.com fee schedule (primary source, spot tier table).
- Coinbase: Advanced spot tiers. Coinbase blocks scripted access to its own fee pages, so the
  table is from two independent 2026-10 secondary copies of the Advanced VIP page that agree.
  Re-check against a signed-in fee page when convenient.
- Kalshi: 7% x p(1-p) rounded up to the cent.
Defaults assume the entry tier (a small account), which is also the most conservative one.
The engine charges a static tier; it does not roll 30-day volume forward. Passive (maker)
fills are not modelled yet, so only taker fees are charged.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
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


# ---- Volume-tiered spot venues (Coinbase, Kraken) -------------------------------------------

# (30-day volume in USD at which the tier starts, maker bp, taker bp)
Tier = tuple[float, float, float]

COINBASE_TIERS: tuple[Tier, ...] = (  # Advanced spot, US, after the 2026-09-16 restructure
    (0, 50, 90),  # Intro
    (10e3, 35, 75),  # Advanced 1
    (50e3, 15, 30),  # Advanced 2
    (250e3, 10, 15),  # Advanced 3
    (1e6, 6, 10),  # VIP 1
    (5e6, 4, 6),  # VIP 2
    (20e6, 1, 5),  # VIP 3
    (50e6, 0, 4),  # VIP 4
    (100e6, 0, 3),  # VIP 5
    (200e6, 0, 2.5),  # VIP 6
    (500e6, 0, 2.25),  # VIP 7
    (1e9, 0, 2),  # VIP 8
)

KRAKEN_TIERS: tuple[Tier, ...] = (  # Kraken Pro spot crypto; stablecoin/FX pairs differ
    (0, 40, 80),  # Tier 1
    (2.5e3, 30, 60),
    (10e3, 22, 38),
    (25e3, 20, 35),
    (50e3, 15, 30),
    (100e3, 12, 25),
    (250e3, 10, 22),
    (500e3, 8, 20),
    (1e6, 6, 18),
    (2.5e6, 4, 15),
    (5e6, 2, 12),
    (10e6, 0, 10),  # Tier 12
    (50e6, 0, 9),  # Pro 1
    (100e6, 0, 8),
    (250e6, 0, 7),
    (400e6, 0, 6),
    (500e6, 0, 5),  # Pro 5
)


@dataclass(frozen=True)
class TieredBpsFee:
    """Spot fee that depends on trailing 30-day volume; taker side is what the engine charges.

    Both venues' public APIs expose no per-pair fee data (Kraken's AssetPairs `fees` arrays
    come back empty), so the tier table is the only source. Defaults to the entry tier.
    """

    tiers: tuple[Tier, ...]
    volume_30d: float = 0.0

    @property
    def _tier(self) -> Tier:
        return [t for t in self.tiers if t[0] <= self.volume_30d][-1]

    @property
    def maker_bps(self) -> float:
        return self._tier[1]

    @property
    def taker_bps(self) -> float:
        return self._tier[2]

    def fee(self, qty: float, price: float) -> float:
        return abs(qty) * price * self.taker_bps / 1e4


def CoinbaseFee(volume_30d: float = 0.0) -> TieredBpsFee:  # noqa: N802 (reads as a class)
    return TieredBpsFee(COINBASE_TIERS, volume_30d)


def KrakenFee(volume_30d: float = 0.0) -> TieredBpsFee:  # noqa: N802
    return TieredBpsFee(KRAKEN_TIERS, volume_30d)


# ---- Polymarket -----------------------------------------------------------------------------

# Taker rate by category, from the docs fee page. FALLBACK ONLY: used when a market carries no
# fee data of its own. Gamma's `category` field is null on every market we have seen, so in
# practice a market's own `feeSchedule` (below) is what decides.
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
POLYMARKET_DEFAULT_CATEGORY = "other"


@dataclass(frozen=True)
class PolymarketFee:
    """Polymarket taker fee in USDC: rate x shares x (p x (1 - p))^exponent, 5 decimals.

    Every market observed so far has exponent 1 (the docs formula: rate x p x (1 - p)). Makers
    pay nothing; only takers are charged, on every fill, buys and sells. Holding to resolution
    pays no exit fee. Peak at p = 0.5 is 1.75c per share at a 7% rate, 1.25c at 5%, 0.75c at 3%.
    """

    rate: float = POLYMARKET_TAKER_RATES[POLYMARKET_DEFAULT_CATEGORY]
    exponent: float = 1.0

    @classmethod
    def for_category(cls, category: str) -> PolymarketFee:
        return cls(POLYMARKET_TAKER_RATES[category.strip().lower()])

    @classmethod
    def from_gamma(cls, market: Mapping) -> PolymarketFee | None:
        """The fee a Gamma market object states for itself, or None if it states none.

        `feesEnabled: false` with no schedule means no fee (all pre-fee-era markets look like
        this). Caveat: the schedule is the market's CURRENT one; if a market's fees were
        switched on mid-life, earlier fills would not have paid it.
        """
        sched = market.get("feeSchedule") or {}
        if sched.get("rate") is not None:
            return cls(float(sched["rate"]), float(sched.get("exponent") or 1.0))
        if market.get("feesEnabled") is False:
            return cls(0.0)
        return None

    @classmethod
    def from_row(cls, row: Mapping) -> PolymarketFee | None:
        """Same, from a backfilled markets-table row (`fee_rate`, `fees_enabled`, ...)."""
        if row.get("fee_rate") is not None:
            return cls(float(row["fee_rate"]), float(row.get("fee_exponent") or 1.0))
        if row.get("fees_enabled") is False:
            return cls(0.0)
        return None

    def fee(self, qty: float, price: float) -> float:
        return round(self.rate * abs(qty) * (price * (1 - price)) ** self.exponent, 5)


def resolve_polymarket_fee(
    market: Mapping | None = None, category: str | None = None
) -> tuple[PolymarketFee, str]:
    """Best available fee for a market and where it came from.

    Order: the market's own fee data, then its category's published rate, then the default
    category's rate. The source string says which, so a result never silently rests on the
    default.
    """
    if market:
        if (m := PolymarketFee.from_gamma(market) or PolymarketFee.from_row(market)) is not None:
            return m, "market fee data"
        category = category or market.get("category")
    if category and category.strip().lower() in POLYMARKET_TAKER_RATES:
        return PolymarketFee.for_category(category), f"category '{category.strip().lower()}'"
    return PolymarketFee(), f"default ('{POLYMARKET_DEFAULT_CATEGORY}' rate; no market data)"


VENUE_FEES: dict[str, FeeModel] = {
    "coinbase": CoinbaseFee(),
    "kraken": KrakenFee(),
    "polymarket": PolymarketFee(),
    "kalshi": KalshiFee(),
}
