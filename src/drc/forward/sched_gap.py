"""The `pm-sched-gap` rule: buy Yes on binary Polymarket markets whose best ask is in a band,
about 3 days before their scheduled end, and hold to resolution.

Pure functions of (market metadata, book, charter, decision time): no network, no clock, no
access to outcomes, so the rule can only use what was known at decision time. Every verdict
carries a reason code; the session logs skips as carefully as trades (design doc 1.4).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from drc.forward.polymarket import event_id, json_list, parse_ts, yes_token
from drc.forward.types import BookSnapshot

_EPS = 1e-9


@dataclass(frozen=True)
class Screen:
    """Universe screen of one market, before any book is fetched."""

    ok: bool
    reason: str  # "" when ok
    token: str | None
    event_id: str
    hours_to_end: float | None
    volume: float | None


@dataclass(frozen=True)
class Decision:
    """Entry verdict on one token's decision-time book."""

    action: str  # trade | skip
    reason: str  # "" for trade; out_of_band reasons are logged-not-traded by charter
    qty: float = 0.0
    limit: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    ask_depth: float = 0.0  # shares at the best ask
    eligible_depth: float = 0.0  # shares at or under the limit


def screen(
    market: dict, universe: dict, now: dt.datetime, traded_tokens: frozenset | set = frozenset()
) -> Screen:
    token = yes_token(market)
    ev = event_id(market)
    end = parse_ts(market.get("endDate"))
    hours = None if end is None else (end - now).total_seconds() / 3600
    vol = market.get("volumeNum")
    vol = None if vol is None else float(vol)

    def out(reason: str) -> Screen:
        return Screen(reason == "", reason, token, ev, hours, vol)

    if universe.get("binary_yes_no_only", True) and token is None:
        return out("not_binary_yes_no")
    if not market.get("enableOrderBook"):
        return out("no_order_book")
    if market.get("closed") or not market.get("active", True):
        return out("closed_or_inactive")
    if universe.get("require_accepting_orders", True) and market.get("acceptingOrders") is False:
        return out("not_accepting_orders")
    if universe.get("exclude_resolution_in_progress", True) and (
        market.get("umaResolutionStatus") or json_list(market.get("umaResolutionStatuses"))
    ):  # Gamma sends the history as a JSON string: "[]" is truthy, so parse it
        return out("resolution_in_progress")
    if hours is None:
        return out("no_scheduled_end")
    if not universe["min_hours_to_end"] <= hours <= universe["max_hours_to_end"]:
        return out("outside_window")
    if vol is None or vol < universe["min_volume_to_date"]:
        return out("below_volume_floor")
    if token in traded_tokens:
        return out("already_traded")
    return out("")


def _floor_to(x: float, step: float) -> float:
    return round(math.floor(x / step + _EPS) * step, 8)


def decide(
    book: BookSnapshot | None, entry: dict, sizing: dict, size_step: float = 0.01
) -> Decision:
    """Ask-based entry. Size = fixed dollars at the decision ask, capped at `depth_share` of
    displayed shares at or under the limit, floored to the size step; below the venue's
    minimum order size the trade is skipped (we could not have sent it)."""
    if book is None:
        return Decision("skip", "no_book")
    bb, ba = book.best_bid, book.best_ask
    base = {"best_bid": bb, "best_ask": ba}
    if ba is None:
        return Decision("skip", "no_ask", **base)
    ask_depth = sum(lv.size for lv in book.asks if abs(lv.price - ba) < _EPS)
    base["ask_depth"] = ask_depth
    if bb is None:
        return Decision("skip", "one_sided_book", **base)
    if bb >= ba:
        return Decision("skip", "locked_or_crossed_book", **base)
    if ba < entry["min_ask"] - _EPS:
        return Decision("skip", "out_of_band_low", **base)
    if ba > entry["max_ask"] + _EPS:
        return Decision("skip", "out_of_band_high", **base)

    limit = ba  # decision_best_ask: never chase
    eligible = sum(lv.size for lv in book.asks if lv.price <= limit + _EPS)
    want = float(sizing["dollars_per_trade"]) / limit
    cap = float(sizing["depth_share"]) * eligible
    qty = _floor_to(min(want, cap), size_step)
    base["eligible_depth"] = eligible
    if qty < book.min_size - _EPS:
        return Decision("skip", "depth_below_min_size", qty, limit, **base)
    return Decision("trade", "", qty, limit, **base)
