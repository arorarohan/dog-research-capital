"""Venue adapter and the sched-gap rule, against real recorded Polymarket responses
(tests/fixtures/polymarket, fetched 2026-11-06 10:13 UTC) and HTTP mocks. No network."""

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest

from drc.forward.polymarket import (
    PolymarketHttp,
    RawBook,
    event_id,
    json_list,
    parse_book,
    parse_ts,
    resolution,
    yes_token,
)
from drc.forward.sched_gap import decide, screen
from drc.forward.types import BookSnapshot, Level

FIX = Path(__file__).parent / "fixtures" / "polymarket"
BOOK = json.loads((FIX / "book_btc_dip_74k.json").read_text())
MARKET = json.loads((FIX / "gamma_btc_dip_74k.json").read_text())
RESOLVED = json.loads((FIX / "gamma_resolved_yes.json").read_text())
FETCHED = dt.datetime(2026, 11, 6, 10, 13, 43, tzinfo=dt.UTC)
YES = json.loads(MARKET["clobTokenIds"])[0]

UNIVERSE = {"min_hours_to_end": 60, "max_hours_to_end": 84, "min_volume_to_date": 50000}
ENTRY = {"min_ask": 0.05, "max_ask": 0.60}
SIZING = {"dollars_per_trade": 12.0, "depth_share": 0.25}


# ---- book parsing ---------------------------------------------------------------------------


def test_real_book_lists_best_last_and_parse_book_puts_best_first():
    assert float(BOOK["bids"][-1]["price"]) > float(BOOK["bids"][0]["price"])  # venue order
    assert float(BOOK["asks"][-1]["price"]) < float(BOOK["asks"][0]["price"])
    b = parse_book(RawBook(YES, 123, BOOK))
    assert b.best_bid == 0.343 and b.best_ask == 0.35
    assert [lv.price for lv in b.bids] == sorted((lv.price for lv in b.bids), reverse=True)
    assert [lv.price for lv in b.asks] == sorted(lv.price for lv in b.asks)
    assert b.tick == 0.001 and b.min_size == 5.0  # from the venue, not the placeholder
    assert b.ts == 123 and b.token == YES
    assert b.asks[0] == Level(0.35, 4115.97)


def test_parse_book_without_a_book_is_none_and_drops_empty_levels():
    assert parse_book(RawBook("T", 1, None, 404)) is None
    body = {"asset_id": "T", "tick_size": "0.01", "min_order_size": "5",
            "bids": [{"price": "0.30", "size": "0"}], "asks": []}  # fmt: skip
    b = parse_book(RawBook("T", 1, body))
    assert b.bids == () and b.asks == () and b.tick == 0.01


# ---- market fields --------------------------------------------------------------------------


def test_market_helpers_on_real_gamma_objects():
    assert yes_token(MARKET) == YES == BOOK["asset_id"]
    assert event_id(MARKET) == "1299077"
    assert event_id({"id": 9}) == "market:9"
    assert yes_token({**MARKET, "outcomes": '["Up", "Down"]'}) is None
    assert json_list("[]") == [] and json_list(None) == [] and json_list(["a"]) == ["a"]
    assert MARKET["umaResolutionStatuses"] == "[]"  # a JSON string, truthy as a str


def test_parse_ts_handles_both_gamma_formats():
    assert parse_ts("2026-11-09T05:00:00Z") == dt.datetime(2026, 11, 9, 5, tzinfo=dt.UTC)
    t = parse_ts("2026-11-04 10:53:59.942479+00")
    assert t == dt.datetime(2026, 11, 4, 10, 53, 59, 942479, tzinfo=dt.UTC)
    assert parse_ts(None) is None


def test_resolution_of_a_real_resolved_market():
    yes, no = json.loads(RESOLVED["clobTokenIds"])
    r = resolution(RESOLVED, yes)
    assert (r.kind, r.value) == ("win", 1.0)
    assert r.ts == parse_ts(RESOLVED["closedTime"])
    assert r.detail["umaResolutionStatus"] == "resolved"
    assert resolution(RESOLVED, no).kind == "lose"


@pytest.mark.parametrize(
    "change",
    [
        {"closed": False},
        {"umaResolutionStatus": "proposed"},
        {"umaResolutionStatus": None},
        {"outcomePrices": '["0.9995", "0.0005"]'},  # last prices, not a resolution
    ],
)
def test_anything_short_of_a_final_resolution_is_pending(change):
    yes = json.loads(RESOLVED["clobTokenIds"])[0]
    assert resolution({**RESOLVED, **change}, yes).kind == "pending"


def test_split_resolution_and_unknown_token():
    yes = json.loads(RESOLVED["clobTokenIds"])[0]
    r = resolution({**RESOLVED, "outcomePrices": '["0.5", "0.5"]'}, yes)
    assert (r.kind, r.value) == ("split", 0.5)
    assert resolution(RESOLVED, "nope").kind == "pending"


# ---- HTTP client (mocked transport) ---------------------------------------------------------


def test_http_client_book_404_markets_by_token_and_paging():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/book":
            if req.url.params["token_id"] == YES:
                return httpx.Response(200, json=BOOK)
            return httpx.Response(404, json={"error": "No orderbook exists"})
        if req.url.path == "/markets/keyset":
            if "after_cursor" not in req.url.params:
                return httpx.Response(200, json={"markets": [MARKET], "next_cursor": "c1"})
            return httpx.Response(200, json={"markets": [RESOLVED]})
        if req.url.path == "/markets":
            return httpx.Response(200, json=[RESOLVED])
        return httpx.Response(500)

    md = PolymarketHttp(min_interval=0, clock=lambda: 7, transport=httpx.MockTransport(handler))
    rb = md.book(YES)
    assert rb.status == 200 and rb.body["hash"] == BOOK["hash"] and rb.recv_ns == 7
    gone = md.book("123")
    assert gone.status == 404 and gone.body is None

    got = md.candidates(FETCHED, FETCHED + dt.timedelta(days=1), 50000)
    assert [m["id"] for m in got] == [MARKET["id"], RESOLVED["id"]]
    first = [r for r in seen if r.url.path == "/markets/keyset"][0].url.params
    assert first["volume_num_min"] == "50000" and first["closed"] == "false"

    md.markets_by_token(["a", "b"], closed=True)
    q = [r for r in seen if r.url.path == "/markets"][-1].url.params
    assert q.get_list("clob_token_ids") == ["a", "b"] and q["closed"] == "true"


# ---- the rule: universe screen --------------------------------------------------------------


def test_screen_accepts_the_real_market_at_fetch_time():
    s = screen(MARKET, UNIVERSE, FETCHED)
    assert s.ok and s.reason == "" and s.token == YES and s.event_id == "1299077"
    assert 66 < s.hours_to_end < 67 and s.volume > 50000


@pytest.mark.parametrize(
    "change,now_shift_h,reason",
    [
        ({"outcomes": '["A", "B"]'}, 0, "not_binary_yes_no"),
        ({"enableOrderBook": False}, 0, "no_order_book"),
        ({"closed": True}, 0, "closed_or_inactive"),
        ({"acceptingOrders": False}, 0, "not_accepting_orders"),
        ({"umaResolutionStatus": "proposed"}, 0, "resolution_in_progress"),
        ({"umaResolutionStatuses": '["proposed"]'}, 0, "resolution_in_progress"),
        ({"endDate": None}, 0, "no_scheduled_end"),
        ({}, -20, "outside_window"),  # 86.8 h to the scheduled end
        ({}, 10, "outside_window"),  # 56.8 h
        ({"volumeNum": 49999.0}, 0, "below_volume_floor"),
    ],
)
def test_screen_reasons(change, now_shift_h, reason):
    now = FETCHED + dt.timedelta(hours=now_shift_h)
    assert screen({**MARKET, **change}, UNIVERSE, now).reason == reason


def test_screen_once_per_token():
    assert screen(MARKET, UNIVERSE, FETCHED, {YES}).reason == "already_traded"


# ---- the rule: entry decision ---------------------------------------------------------------


def _book(bids, asks, tick=0.01, min_size=5.0):
    return BookSnapshot(
        "T", 0, tuple(Level(*b) for b in bids), tuple(Level(*a) for a in asks), tick, min_size
    )


def test_decide_on_the_real_book_buys_twelve_dollars_at_the_ask():
    d = decide(parse_book(RawBook(YES, 0, BOOK)), ENTRY, SIZING)
    assert d.action == "trade" and d.limit == 0.35
    assert d.qty == pytest.approx(34.28)  # $12 / 0.35, floored to 0.01; depth not binding
    assert d.ask_depth == pytest.approx(4115.97) and d.best_bid == 0.343


def test_decide_caps_at_a_quarter_of_depth_and_skips_below_min_size():
    d = decide(_book([(0.10, 100)], [(0.12, 40)]), ENTRY, SIZING)
    assert d.action == "trade" and d.qty == 10.0  # 25% of 40, not $12/0.12 = 100
    d = decide(_book([(0.10, 100)], [(0.12, 16)]), ENTRY, SIZING)
    assert (d.action, d.reason, d.qty) == ("skip", "depth_below_min_size", 4.0)


@pytest.mark.parametrize(
    "book,reason",
    [
        (None, "no_book"),
        (_book([(0.1, 10)], []), "no_ask"),
        (_book([], [(0.2, 10)]), "one_sided_book"),
        (_book([(0.2, 10)], [(0.2, 10)]), "locked_or_crossed_book"),
        (_book([(0.01, 10)], [(0.04, 1000)]), "out_of_band_low"),
        (_book([(0.6, 10)], [(0.61, 1000)]), "out_of_band_high"),
    ],
)
def test_decide_skip_reasons(book, reason):
    d = decide(book, ENTRY, SIZING)
    assert (d.action, d.reason) == ("skip", reason)


def test_band_edges_are_inclusive():
    assert decide(_book([(0.04, 10)], [(0.05, 10000)]), ENTRY, SIZING).action == "trade"
    assert decide(_book([(0.59, 10)], [(0.60, 10000)]), ENTRY, SIZING).action == "trade"
