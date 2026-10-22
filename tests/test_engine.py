import polars as pl
import pytest

from drc.backtest.engine import BAR_NS, Config, run_backtest
from drc.backtest.fees import BpsFee
from drc.backtest.strategy import Order, Strategy


def bar(i, bid, ask, bid_sz=100.0, ask_sz=100.0, epoch=1, symbol="X", venue="coinbase"):
    return {
        "venue": venue,
        "symbol": symbol,
        "epoch": epoch,
        "ts": i * BAR_NS,
        "bid": bid,
        "ask": ask,
        "bid_sz": bid_sz,
        "ask_sz": ask_sz,
        "mid": None if bid is None or ask is None else (bid + ask) / 2,
        "crossed": False if bid is not None else None,
    }


def frame(rows):
    return pl.DataFrame(
        rows,
        schema={
            "venue": pl.Utf8,
            "symbol": pl.Utf8,
            "epoch": pl.Int64,
            "ts": pl.Int64,
            "bid": pl.Float64,
            "ask": pl.Float64,
            "bid_sz": pl.Float64,
            "ask_sz": pl.Float64,
            "mid": pl.Float64,
            "crossed": pl.Boolean,
        },
    )


class Scripted(Strategy):
    """Submit given orders at given bar indices; record what it can see."""

    def __init__(self, plan):
        self.plan = plan
        self.seen_history_lens = []

    def on_bar(self, ctx):
        self.seen_history_lens.append(len(ctx.history.get("X", [])))
        return self.plan.get(ctx.ts // BAR_NS, [])


CFG = dict(slippage_bps=0.0, fee_model=BpsFee(0))


def test_fills_one_bar_later_at_the_worse_of_decision_and_fill_book():
    bars = frame([bar(0, 9, 10), bar(1, 10, 11), bar(2, 10, 11)])
    res = run_backtest(bars, Scripted({0: [Order("X", "buy", 1)]}), Config(**CFG))
    (f,) = res.fills.to_dicts()
    assert f["ts"] == 1 * BAR_NS and f["price"] == 11  # ask moved up: pay the higher price

    bars = frame([bar(0, 9, 10), bar(1, 8, 9), bar(2, 8, 9)])
    (f,) = run_backtest(bars, Scripted({0: [Order("X", "buy", 1)]}), Config(**CFG)).fills.to_dicts()
    assert f["price"] == 10  # ask fell: still pay the price we saw, no free improvement


def test_sells_get_the_lower_bid():
    bars = frame([bar(0, 10, 11), bar(1, 9, 10), bar(2, 9, 10), bar(3, 9, 10)])
    plan = {0: [Order("X", "buy", 1)], 1: [Order("X", "sell", 1)]}
    res = run_backtest(bars, Scripted(plan), Config(**CFG))
    sell = res.fills.filter(pl.col("side") == "sell").to_dicts()[0]
    assert sell["ts"] == 2 * BAR_NS and sell["price"] == 9


def test_strategy_never_sees_the_current_bar_in_history_and_no_same_bar_fill():
    bars = frame([bar(i, 9, 10) for i in range(4)])
    strat = Scripted({1: [Order("X", "buy", 1)]})
    res = run_backtest(bars, strat, Config(**CFG))
    assert strat.seen_history_lens == [0, 1, 2, 3]
    assert res.fills["ts"].to_list() == [2 * BAR_NS]  # decided at bar 1, filled at bar 2


def test_participation_and_cash_caps():
    bars = frame([bar(0, 9, 10, ask_sz=5.0), bar(1, 9, 10, ask_sz=5.0), bar(2, 9, 10)])
    (f,) = run_backtest(
        bars, Scripted({0: [Order("X", "buy", 100)]}), Config(**CFG)
    ).fills.to_dicts()
    assert f["qty"] == 5.0

    bars = frame([bar(0, 9, 10), bar(1, 9, 10), bar(2, 9, 10)])
    cfg = Config(initial_cash=100.0, **CFG)
    (f,) = run_backtest(bars, Scripted({0: [Order("X", "buy", 50)]}), cfg).fills.to_dicts()
    assert f["qty"] == pytest.approx(10.0)  # $100 / $10


def test_short_selling_rejected_by_default_and_recorded():
    bars = frame([bar(i, 9, 10) for i in range(3)])
    res = run_backtest(bars, Scripted({0: [Order("X", "sell", 1)]}), Config(**CFG))
    assert res.fills.height == 0
    assert any(e["event"] == "reject" and "short" in e["reason"] for e in res.events)


def test_reconnect_gap_cancels_pending_orders():
    bars = frame([bar(0, 9, 10, epoch=1), bar(1, 9, 10, epoch=2), bar(2, 9, 10, epoch=2)])
    res = run_backtest(bars, Scripted({0: [Order("X", "buy", 1)]}), Config(**CFG))
    assert res.fills.height == 0
    assert [e for e in res.events if e["event"] == "gap"][0]["cancelled_orders"] == 1


def test_round_trip_loses_exactly_spread_slippage_and_fees():
    flat = [bar(i, 99.0, 101.0) for i in range(5)]
    plan = {0: [Order("X", "buy", 10)], 2: [Order("X", "sell", 10)]}
    cfg = Config(initial_cash=10_000.0, slippage_bps=10.0, fee_model=BpsFee(50))
    res = run_backtest(frame(flat), Scripted(plan), cfg)
    buy_px, sell_px = 101.0 * 1.001, 99.0 * 0.999
    fees = (10 * buy_px + 10 * sell_px) * 0.005
    expected = 10_000 + 10 * (sell_px - buy_px) - fees
    assert res.ledger.cash == pytest.approx(expected)
    assert res.ledger.position("X") == 0
    assert res.equity["equity"][-1] == pytest.approx(expected)


def test_equity_is_marked_at_liquidation_value():
    bars = frame([bar(0, 99, 101), bar(1, 99, 101), bar(2, 99, 101)])
    res = run_backtest(bars, Scripted({0: [Order("X", "buy", 10)]}), Config(**CFG))
    # bought at the 101 ask, but a long is valued at the 99 bid
    assert res.equity["equity"][-1] == pytest.approx(10_000 - 10 * 101 + 10 * 99)


def test_short_leverage_cap_when_shorting_allowed():
    bars = frame([bar(i, 99, 101) for i in range(4)])
    cfg = Config(initial_cash=1_000.0, allow_short=True, max_leverage=1.0, **CFG)
    res = run_backtest(bars, Scripted({0: [Order("X", "sell", 1000)]}), cfg)
    (f,) = res.fills.to_dicts()
    assert f["qty"] * f["price"] <= 1_000.0 * 1.0 + 1e-6  # gross exposure <= equity x leverage
    assert f["qty"] > 5  # but it did short a meaningful amount


def test_no_trade_when_book_is_missing():
    bars = frame([bar(0, None, None), bar(1, 9, 10), bar(2, 9, 10)])
    res = run_backtest(
        bars, Scripted({0: [Order("X", "buy", 1)], 1: [Order("X", "buy", 1)]}), Config(**CFG)
    )
    assert res.fills["ts"].to_list() == [2 * BAR_NS]
    assert any("no tradeable book" in e["reason"] for e in res.events)
