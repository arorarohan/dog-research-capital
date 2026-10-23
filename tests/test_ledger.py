import random

import pytest

from drc.backtest.fees import BpsFee, KalshiFee, PolymarketFee
from drc.backtest.ledger import Fill, Ledger


def fill(side, qty, price, fee=0.0, sym="X"):
    return Fill(0, sym, side, qty, price, fee, 0)


def test_round_trip_realizes_pnl_and_fees_separately():
    led = Ledger(1000.0)
    led.apply_fill(fill("buy", 10, 10.0, fee=1.0))
    led.apply_fill(fill("sell", 10, 12.0, fee=1.2))
    assert led.position("X") == 0
    assert led.realized_gross == pytest.approx(20.0)
    assert led.fees == pytest.approx(2.2)
    assert led.cash == pytest.approx(1000 + 20 - 2.2)


def test_average_cost_and_partial_close():
    led = Ledger(1000.0)
    led.apply_fill(fill("buy", 10, 10.0))
    led.apply_fill(fill("buy", 10, 20.0))
    assert led.positions["X"].avg_cost == pytest.approx(15.0)
    led.apply_fill(fill("sell", 5, 25.0))
    assert led.realized_gross == pytest.approx(50.0)
    assert led.position("X") == 15


def test_short_and_flip_through_zero():
    led = Ledger(1000.0)
    led.apply_fill(fill("sell", 10, 10.0))
    assert led.position("X") == -10
    led.apply_fill(fill("buy", 15, 8.0))  # covers 10 at a +20 gain, opens long 5 at 8
    assert led.realized_gross == pytest.approx(20.0)
    assert led.position("X") == 5 and led.positions["X"].avg_cost == pytest.approx(8.0)


def test_equity_identity_holds_over_random_trading():
    rng = random.Random(7)
    led = Ledger(10_000.0)
    for _ in range(300):
        led.apply_fill(
            fill(
                rng.choice(["buy", "sell"]),
                rng.uniform(0.1, 5),
                rng.uniform(9, 11),
                rng.uniform(0, 0.2),
            )
        )
    marks = {"X": 10.3}
    expected = led.initial_cash + led.realized_gross - led.fees + led.unrealized(marks)
    assert led.equity(marks) == pytest.approx(expected)


def test_settle_binary_contract():
    led = Ledger(100.0)
    led.apply_fill(fill("buy", 10, 0.40, sym="MKT"))
    assert led.settle("MKT", 1.0) == pytest.approx(6.0)
    assert led.cash == pytest.approx(100 + 6.0) and led.position("MKT") == 0
    led.apply_fill(fill("buy", 10, 0.40, sym="MKT"))
    assert led.settle("MKT", 0.0) == pytest.approx(-4.0)


def test_fee_models():
    assert BpsFee(60).fee(2, 100.0) == pytest.approx(1.2)
    assert KalshiFee().fee(100, 0.5) == pytest.approx(1.75)  # 7% x 100 x 0.25, already a cent
    assert KalshiFee().fee(1, 0.99) == pytest.approx(0.01)  # rounds up to the cent


def test_polymarket_fee_matches_published_table():
    # docs.polymarket.com/trading/fees, 100 shares
    assert PolymarketFee.for_category("crypto").fee(100, 0.5) == pytest.approx(1.75)
    assert PolymarketFee.for_category("Crypto").fee(100, 0.3) == pytest.approx(1.47)
    assert PolymarketFee.for_category("politics").fee(100, 0.5) == pytest.approx(1.00)
    assert PolymarketFee().fee(100, 0.1) == pytest.approx(0.45)  # default = "other", 5%
    assert PolymarketFee.for_category("geopolitics").fee(100, 0.5) == 0.0
    assert PolymarketFee.for_category("crypto").fee(1, 0.01) == pytest.approx(0.00069)
    assert PolymarketFee().fee(-100, 0.5) == PolymarketFee().fee(100, 0.5)
