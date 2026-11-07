"""Polymarket market data for forward-test sessions, behind a small interface.

`MarketData` is everything a session needs from the venue: enumerate candidate markets
(Gamma), fetch one token's order book (CLOB `GET /book`), and look markets up by token for
settlement. `PolymarketHttp` is the only implementation that touches the network; tests use
an in-memory fake with recorded fixtures. All endpoints are public and read-only: no keys,
no order endpoint (design doc section 1).

Venue facts this relies on are in docs/gotchas.md ("Polymarket one-shot books and
resolution"), verified 2026-11-06. The two that bite: the CLOB lists the best price LAST, and
Gamma's token lookup hides closed markets unless asked with `closed=true`.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx
import orjson

from drc.backfill.http import Client
from drc.forward.types import BookSnapshot, Level

log = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


@dataclass(frozen=True)
class RawBook:
    """A book response exactly as received, plus our local receive time (ns).

    `body` is None when the venue has no book for the token (HTTP 404), which is a logged
    outcome, not an error.
    """

    token: str
    recv_ns: int
    body: dict | None
    status: int = 200


class MarketData(Protocol):
    def candidates(
        self, end_min: dt.datetime, end_max: dt.datetime, min_volume: float
    ) -> list[dict]:
        """Open markets whose scheduled end is in [end_min, end_max] with volume to date at
        least `min_volume`, as raw Gamma objects."""
        ...

    def book(self, token: str) -> RawBook: ...

    def markets_by_token(self, tokens: Sequence[str], closed: bool) -> list[dict]:
        """Gamma objects for the markets listing these tokens; open ones if `closed` is False,
        closed ones if True (Gamma never returns both at once)."""
        ...


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class PolymarketHttp:
    """The real thing: paced, retried GETs against Gamma and the CLOB."""

    def __init__(
        self,
        min_interval: float = 0.2,
        max_markets: int = 1000,
        clock: Callable[[], int] = time.time_ns,
        transport: httpx.BaseTransport | None = None,
    ):
        self.http = Client(min_interval=min_interval, transport=transport)
        self.max_markets = max_markets
        self.clock = clock

    def candidates(
        self, end_min: dt.datetime, end_max: dt.datetime, min_volume: float
    ) -> list[dict]:
        params = {
            "closed": "false", "active": "true", "limit": 100, "order": "endDate",
            "ascending": "true", "end_date_min": _iso(end_min), "end_date_max": _iso(end_max),
            "volume_num_min": min_volume,
        }  # fmt: skip
        out: list[dict] = []
        cursor = None
        while len(out) < self.max_markets:
            page = dict(params, **({"after_cursor": cursor} if cursor else {}))
            body = self.http.json(f"{GAMMA}/markets/keyset", **page)
            out += body.get("markets", [])
            cursor = body.get("next_cursor")
            if not cursor or not body.get("markets"):
                break
        if len(out) >= self.max_markets:
            log.warning("candidate list hit max_markets=%d; universe truncated", self.max_markets)
        return out[: self.max_markets]

    def book(self, token: str) -> RawBook:
        try:
            r = self.http.get(f"{CLOB}/book", token_id=token)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return RawBook(token, self.clock(), None, 404)
            raise
        return RawBook(token, self.clock(), orjson.loads(r.content), r.status_code)

    def markets_by_token(self, tokens: Sequence[str], closed: bool) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(tokens), 20):
            query = [("clob_token_ids", t) for t in tokens[i : i + 20]]
            query += [("closed", "true" if closed else "false"), ("limit", "100")]
            out += self.http.get_query(f"{GAMMA}/markets", query).json()
        return out


# ---- parsing --------------------------------------------------------------------------------


def parse_book(raw: RawBook) -> BookSnapshot | None:
    """A venue book as a `BookSnapshot` (best first), with the market's own tick and minimum
    order size. None if there is no book. The CLOB lists best prices LAST; we sort explicitly
    rather than reverse, so a future change of order cannot silently invert the book."""
    b = raw.body
    if b is None:
        return None

    def levels(side: str, best_first_desc: bool) -> tuple[Level, ...]:
        lv = [Level(float(x["price"]), float(x["size"])) for x in b.get(side) or []]
        lv = [x for x in lv if x.size > 0]
        return tuple(sorted(lv, key=lambda x: -x.price if best_first_desc else x.price))

    return BookSnapshot(
        token=str(b.get("asset_id") or raw.token),
        ts=raw.recv_ns,
        bids=levels("bids", True),
        asks=levels("asks", False),
        tick=float(b["tick_size"]),
        min_size=float(b["min_order_size"]),
    )


def yes_token(market: dict) -> str | None:
    """The Yes token of a binary Yes/No market, else None."""
    outcomes = json_list(market.get("outcomes"))
    tokens = json_list(market.get("clobTokenIds"))
    if outcomes != ["Yes", "No"] or len(tokens) != 2:
        return None
    return str(tokens[0])


def event_id(market: dict) -> str:
    """The real-world event a market belongs to; exposure is capped per event."""
    events = market.get("events") or []
    if events and events[0].get("id") is not None:
        return str(events[0]["id"])
    return f"market:{market.get('id')}"


def parse_ts(s: str | None) -> dt.datetime | None:
    """Gamma timestamps come as '2026-11-09T05:00:00Z' or '2026-11-04 10:53:59.942479+00'."""
    if not s:
        return None
    s = s.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    elif len(s) > 3 and s[-3] in "+-" and ":" not in s[-3:]:
        s += ":00"
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


@dataclass(frozen=True)
class Resolution:
    """How the venue resolved a token. kind: win | lose | split | pending."""

    kind: str
    value: float | None
    ts: dt.datetime | None
    detail: dict


def resolution(market: dict, token: str) -> Resolution:
    """Settle only on an explicit, final venue resolution (see gotchas): closed, UMA status
    `resolved`, and outcome prices exactly 1/0 (or 0.5/0.5, a split). Anything else is pending;
    we never settle on last-traded prices."""
    detail = {
        k: market.get(k)
        for k in ("id", "closed", "umaResolutionStatus", "umaResolutionStatuses", "outcomePrices",
                  "outcomes", "closedTime", "endDate")
    }  # fmt: skip
    tokens = [str(t) for t in json_list(market.get("clobTokenIds"))]
    prices = json_list(market.get("outcomePrices"))
    if (
        not market.get("closed")
        or market.get("umaResolutionStatus") != "resolved"
        or token not in tokens
        or len(prices) != len(tokens)
    ):
        return Resolution("pending", None, None, detail)
    p = prices[tokens.index(token)]
    ts = parse_ts(market.get("closedTime"))
    kind = {"1": "win", "0": "lose", "0.5": "split"}.get(str(p).strip())
    if kind is None:
        return Resolution("pending", None, None, detail)
    return Resolution(kind, {"win": 1.0, "lose": 0.0, "split": 0.5}[kind], ts, detail)


def tokens_of(market: dict) -> list[str]:
    return [str(t) for t in json_list(market.get("clobTokenIds"))]


def json_list(v) -> list:
    """Gamma list fields arrive as JSON strings ('["Yes", "No"]', even "[]"); accept both."""
    if v is None:
        return []
    return orjson.loads(v) if isinstance(v, str) else list(v)
