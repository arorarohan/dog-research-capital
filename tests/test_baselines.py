import random

import polars as pl
import pytest

from drc.backtest.engine import BAR_NS, Config, run_backtest
from drc.backtest.fees import BpsFee
from drc.backtest.metrics import summarize
from drc.backtest.strategies import BuyAndHold, ImbalanceFollower, RandomTrader
from drc.backtest.strategy import Context


def market(n, drift=0.0, noise=0.0, mid0=100.0, half_spread=0.5, seed=1):
    rng = random.Random(seed)
    mid, rows = mid0, []
    for i in range(n):
        mid += drift + rng.gauss(0, noise)
        rows.append(
            {
                "venue": "coinbase", "symbol": "X", "epoch": 1, "ts": i * BAR_NS,
                "bid": mid - half_spread, "ask": mid + half_spread, "bid_sz": 1e6, "ask_sz": 1e6,
                "mid": mid, "crossed": False, "imbalance": 0.0,
            }
        )  # fmt: skip
    return pl.DataFrame(rows)


def test_random_trader_loses_money_in_a_driftless_market():
    bars = market(20_000, noise=0.05)
    cfg = Config(slippage_bps=1.0, fee_model=BpsFee(40))
    losses = []
    for seed in range(5):
        res = run_backtest(bars, RandomTrader("X", seed=seed, prob=0.01), cfg)
        losses.append(res.equity["equity"][-1] - cfg.initial_cash)
    assert all(loss < 0 for loss in losses), losses


def test_random_trader_loss_matches_the_cost_model_on_a_flat_market():
    # With a perfectly flat mid, every dollar lost is spread + slippage + fees.
    bars = market(5_000, noise=0.0)
    cfg = Config(slippage_bps=2.0, fee_model=BpsFee(40))
    res = run_backtest(bars, RandomTrader("X", seed=3, prob=0.02), cfg)
    f = res.fills
    assert f.height > 20
    cost = ((f["price"] - f["mid"]).abs() * f["qty"]).sum() + f["fee"].sum()
    held = res.ledger.position("X")
    end_penalty = held * 0.5  # an open long is marked at the bid, half a spread under mid
    assert cfg.initial_cash - res.equity["equity"][-1] == pytest.approx(
        cost + end_penalty, rel=1e-6
    )


def test_buy_and_hold_tracks_the_market_minus_entry_costs():
    bars = market(300, drift=0.1)
    res = run_backtest(bars, BuyAndHold("X"), Config(slippage_bps=0.0, fee_model=BpsFee(0)))
    qty = res.fills["qty"][0]
    assert res.equity["equity"][-1] == pytest.approx(
        10_000 + qty * (bars["bid"][-1] - res.fills["price"][0])
    )


def test_imbalance_follower_enters_and_exits_on_signal():
    s = ImbalanceFollower("X", enter=0.5, exit_=-0.1, notional=1000.0)

    def ctx(imb, pos):
        bar = {"imbalance": imb, "ask": 100.0}
        return Context(0, {"X": bar}, {}, {"X": pos}, 0.0, 0.0)

    assert s.on_bar(ctx(0.9, 0.0))[0].side == "buy"
    assert s.on_bar(ctx(0.2, 0.0)) == []
    assert s.on_bar(ctx(-0.5, 3.0))[0].side == "sell"
    assert s.on_bar(ctx(0.3, 3.0)) == []


def test_summary_flags_short_samples_and_reports_costs():
    bars = market(500)
    res = run_backtest(bars, RandomTrader("X", seed=1, prob=0.05), Config(fee_model=BpsFee(40)))
    s = summarize(res, bars)
    assert any("under 24h" in w for w in s["warnings"])
    assert s["cost_bps_of_notional"]["fees"] == pytest.approx(40.0, abs=0.01)
    assert s["max_drawdown_pct"] <= 0
