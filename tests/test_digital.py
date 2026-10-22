import math

import polars as pl

from drc.research.digital import build_dataset, effective_tau, fair_prob, norm_cdf

S = 10**9


def test_norm_cdf_and_atm_is_a_coin_flip():
    assert abs(norm_cdf(0.0) - 0.5) < 1e-12
    assert abs(norm_cdf(1.96) - 0.975) < 1e-3
    assert abs(fair_prob(100.0, 100.0, 0.001, 600) - 0.5) < 1e-12


def test_direction_and_time_behaviour():
    up = fair_prob(100.0 * math.exp(0.002), 100.0, 0.0005, 300)
    down = fair_prob(100.0 * math.exp(-0.002), 100.0, 0.0005, 300)
    assert up > 0.5 > down and abs(up + down - 1.0) < 1e-9  # mirrored log distances
    # more time left -> more uncertainty -> price closer to 0.5
    assert fair_prob(101.0, 100.0, 0.0005, 840) < fair_prob(101.0, 100.0, 0.0005, 120)
    # tau includes a third of the averaging window even at the very end
    assert effective_tau(30) == 20.0 and effective_tau(660) == 600.0 + 20.0
    assert math.isnan(fair_prob(100.0, 100.0, 0.0, 300))


def test_build_dataset_uses_only_completed_candles():
    # one candle per minute with a steady rise
    n = 130
    kl = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"] * n,
            "ts": [i * 60 * S for i in range(n)],
            "open": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i + (0.1 if i % 2 else 0.0) for i in range(n)],
            "volume": [1.0] * n,
            "n_trades": [1] * n,
            "taker_buy_volume": [0.5] * n,
        }
    )
    close_ts = 125 * 60 * S
    markets = pl.DataFrame(
        {
            "venue": ["kalshi"], "market_id": ["M"], "event_id": ["E"], "strike": [120.0],
            "open_ts": [close_ts - 900 * S], "result_value": [1.0],
        }
    )  # fmt: skip
    samples = pl.DataFrame(
        {
            "venue": ["kalshi"], "market_id": ["M"], "target_ts": [close_ts - 300 * S],
            "secs_to_close": [300], "price": [0.6], "last_trade_ts": [0], "n_window": [5],
        }
    )  # fmt: skip
    d = build_dataset(markets, samples, kl)
    assert d.height == 1
    # at t = 120 min the last completed candle is minute 119 (ts=119 min, ends at 120 min)
    assert d["spot"][0] == kl.filter(pl.col("ts") == 119 * 60 * S)["close"][0]
    assert 0.0 <= d["model_p"][0] <= 1.0


def test_stale_spot_rows_are_dropped_not_silently_used():
    n = 130
    kl = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"] * n, "ts": [i * 60 * S for i in range(n)],
            "open": [100.0 + i for i in range(n)], "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)], "close": [100.5 + i for i in range(n)],
            "volume": [1.0] * n, "n_trades": [1] * n, "taker_buy_volume": [0.5] * n,
        }
    )  # fmt: skip
    last_end = n * 60 * S  # klines stop here

    def one(close_ts):
        markets = pl.DataFrame(
            {"venue": ["kalshi"], "market_id": ["M"], "event_id": ["E"], "strike": [120.0],
             "open_ts": [close_ts - 900 * S], "result_value": [1.0]}
        )  # fmt: skip
        samples = pl.DataFrame(
            {"venue": ["kalshi"], "market_id": ["M"], "target_ts": [close_ts - 300 * S],
             "secs_to_close": [300], "price": [0.6], "last_trade_ts": [0], "n_window": [5]}
        )  # fmt: skip
        return build_dataset(markets, samples, kl)

    assert one(last_end + 300 * S).height == 1  # target right at the end of history: fresh enough
    assert one(last_end + 3600 * S).height == 0  # an hour past the data: dropped
