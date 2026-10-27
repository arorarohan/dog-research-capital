import pytest

from drc.backtest.fees import BpsFee, PolymarketFee
from drc.forward.broker import BrokerConfig, SimBroker
from drc.forward.types import BookSnapshot, Level, Order

S = 1_000_000_000  # one second in ns


def book(bids=((0.30, 100),), asks=((0.32, 100),), ts=10 * S, **kw):
    return BookSnapshot(
        "T",
        ts,
        tuple(Level(p, s) for p, s in bids),
        tuple(Level(p, s) for p, s in asks),
        **kw,
    )


def order(qty=10, limit=0.33, side="buy", decision_ts=0, **kw):
    return Order("o1", "f", "T", "ev", side, qty, limit, decision_ts, **kw)


BROKER = SimBroker(BrokerConfig(latency_ns=5 * S, max_book_age_ns=30 * S, depth_share=0.25))
FREE = BpsFee(0)


def run(o, b, now=None, fee=FREE, broker=BROKER):
    return broker.execute(o, b, b.ts if now is None else now, fee)


def test_simple_buy_fills_at_the_ask_not_the_mid():
    ex = run(order(qty=10), book())
    assert ex.status == "filled" and ex.legs == ((0.32, 10),)
    assert ex.mid == pytest.approx(0.31) and ex.avg_price == pytest.approx(0.32)


def test_walks_levels_and_pays_each_price():
    b = book(asks=((0.32, 20), (0.33, 20), (0.34, 400)))
    ex = run(order(qty=30, limit=0.34), b, broker=SimBroker(BrokerConfig(depth_share=1.0)))
    assert ex.legs == ((0.32, 20), (0.33, 10))
    assert ex.avg_price == pytest.approx((0.32 * 20 + 0.33 * 10) / 30)


def test_limit_price_bounds_the_levels_used():
    b = book(asks=((0.32, 20), (0.35, 500)))
    ex = run(order(qty=50, limit=0.33), b, broker=SimBroker(BrokerConfig(depth_share=1.0)))
    assert ex.legs == ((0.32, 20),) and ex.status == "partial"


def test_limit_below_the_ask_is_unfilled():
    ex = run(order(limit=0.31), book())
    assert ex.status == "unfilled" and ex.reason == "limit_not_marketable"


def test_depth_share_caps_the_fill():
    b = book(asks=((0.32, 40),))  # 25% of 40 = 10
    ex = run(order(qty=30), b)
    assert ex.status == "partial" and ex.qty == pytest.approx(10) and ex.reason == "depth_capped"


def test_fill_never_exceeds_displayed_depth_property():
    for size in (5, 7, 13, 100):
        for share in (0.1, 0.5, 1.0):
            br = SimBroker(BrokerConfig(depth_share=share))
            ex = br.execute(order(qty=1000, limit=0.9), book(asks=((0.32, size),)), 10 * S, FREE)
            assert ex.qty <= size * share + 1e-9


def test_same_instant_book_is_refused():
    ex = run(order(decision_ts=10 * S), book(ts=10 * S))
    assert ex.status == "rejected" and ex.reason == "book_too_early"
    ex = run(order(decision_ts=10 * S - 4 * S), book(ts=10 * S))  # only 4s < 5s latency
    assert ex.reason == "book_too_early"
    assert run(order(decision_ts=5 * S), book(ts=10 * S)).status == "filled"  # exactly 5s


def test_stale_book_is_refused():
    ex = run(order(), book(ts=10 * S), now=41 * S)
    assert ex.reason == "stale_book"
    assert run(order(), book(ts=10 * S), now=40 * S).status == "filled"


@pytest.mark.parametrize(
    "kw,reason",
    [
        (dict(bids=()), "one_sided_book"),
        (dict(asks=()), "one_sided_book"),
        (dict(bids=((0.32, 5),)), "locked_or_crossed_book"),
        (dict(bids=((0.40, 5),)), "locked_or_crossed_book"),
    ],
)
def test_degenerate_books_are_refused(kw, reason):
    assert run(order(), book(**kw)).reason == reason


def test_order_validation():
    assert run(order(qty=0), book()).reason == "bad_qty"
    assert run(order(qty=4), book()).reason == "below_min_size"
    assert run(order(limit=0.335), book()).reason == "off_tick"
    assert run(order(side="hold"), book()).reason == "bad_side"
    assert run(Order("o", "f", "OTHER", "e", "buy", 10, 0.33, 0), book()).reason == "wrong_book"


def test_fee_is_charged_per_level_walked():
    b = book(asks=((0.32, 20), (0.33, 20)))
    fee = PolymarketFee(0.05)
    br = SimBroker(BrokerConfig(depth_share=1.0))
    ex = run(order(qty=30, limit=0.33), b, fee=fee, broker=br)
    assert ex.fee == pytest.approx(fee.fee(20, 0.32) + fee.fee(10, 0.33))


def test_sell_hits_bids_best_first():
    b = book(bids=((0.30, 10), (0.29, 10), (0.20, 100)), asks=((0.32, 100),))
    br = SimBroker(BrokerConfig(depth_share=1.0))
    ex = run(order(qty=15, limit=0.29, side="sell"), b, broker=br)
    assert ex.legs == ((0.30, 10), (0.29, 5))


def test_size_step_floors_quantity():
    ex = run(order(qty=10.019), book(), broker=SimBroker(BrokerConfig(depth_share=1.0)))
    assert ex.qty == pytest.approx(10.01)
