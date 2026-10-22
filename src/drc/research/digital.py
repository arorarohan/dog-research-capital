"""Fair value of "BTC up over the next 15 minutes" style contracts as digital options.

Kalshi's KXBTC15M resolves Yes if the average of the last 60 seconds of the index is at least
the 60-second average at the start (the strike, `floor_strike`). Treating log-price as a
driftless Brownian motion with volatility sigma per second, the probability of finishing at or
above the strike is  Phi( ln(S/K) / (sigma * sqrt(tau)) ).

Approximations (deliberate and flagged, not hidden):
- Spot S is the last *completed* Binance BTCUSDT 1m close at decision time, so it is stale by up
  to a minute. For the 90s and 30s offsets staleness dominates, so those are less trustworthy.
- tau accounts for the averaging window: time until the window starts plus a third of the
  window's own variance (the variance of a 60s Brownian average).
- Binance trades against USDT, Kalshi settles on a USD index; the basis is ignored. A
  basis-neutral variant measures the strike on Binance too (`strike_binance`).
- Drift is zero; sigma is the trailing 60-minute standard deviation of 1m log returns.
"""

from __future__ import annotations

import math

import polars as pl

AVG_WINDOW = 60.0


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def effective_tau(secs_to_close: float) -> float:
    """Seconds of Brownian variance remaining for the final-minute average."""
    return max(secs_to_close - AVG_WINDOW, 0.0) + AVG_WINDOW / 3.0


def fair_prob(spot: float, strike: float, sigma_per_sec: float, secs_to_close: float) -> float:
    if sigma_per_sec <= 0 or spot <= 0 or strike <= 0:
        return float("nan")
    tau = effective_tau(secs_to_close)
    return norm_cdf(math.log(spot / strike) / (sigma_per_sec * math.sqrt(tau)))


def prepare_klines(klines: pl.DataFrame, vol_window: int = 60) -> pl.DataFrame:
    """Add `avail_ts` (when the candle is complete) and trailing per-second volatility."""
    return (
        klines.sort("ts")
        .with_columns(
            (pl.col("ts") + 60 * 10**9).alias("avail_ts"),
            (pl.col("close") / pl.col("close").shift(1)).log().alias("ret"),
        )
        .with_columns((pl.col("ret").rolling_std(vol_window) / math.sqrt(60.0)).alias("sigma_sec"))
        .select("avail_ts", "close", "open", "sigma_sec")
    )


MAX_STALENESS_NS = 150 * 10**9  # spot candle must have completed within 2.5 minutes of use


def build_dataset(
    markets: pl.DataFrame,
    samples: pl.DataFrame,
    klines: pl.DataFrame,
    max_staleness_ns: int = MAX_STALENESS_NS,
) -> pl.DataFrame:
    """Join market outcomes + sampled Kalshi prices with spot/vol known at each sample time.

    Rows whose spot (or strike) candle is older than `max_staleness_ns` are dropped, so history
    that ends before a market does can never feed stale prices into the model.
    """
    k = prepare_klines(klines)
    s = samples.drop_nulls("price").filter(pl.col("secs_to_close") > 0)
    d = s.join(
        markets.select("venue", "market_id", "event_id", "strike", "open_ts", "result_value"),
        on=["venue", "market_id"],
    ).sort("target_ts")
    d = d.join_asof(
        k.rename({"close": "spot", "open": "spot_open"}),
        left_on="target_ts",
        right_on="avail_ts",
        strategy="backward",
    )
    # strike measured on Binance: the candle completed just before the market opened
    strikes = (
        d.select("market_id", "open_ts")
        .unique()
        .sort("open_ts")
        .join_asof(
            k.select(
                pl.col("avail_ts").alias("strike_avail_ts"),
                pl.col("avail_ts"),
                pl.col("close").alias("strike_binance"),
            ),
            left_on="open_ts",
            right_on="avail_ts",
            strategy="backward",
        )
        .select("market_id", "strike_binance", "strike_avail_ts")
    )
    d = d.join(strikes, on="market_id", how="left").drop_nulls(["spot", "sigma_sec", "strike"])
    d = d.filter(
        (pl.col("target_ts") - pl.col("avail_ts") <= max_staleness_ns)
        & (pl.col("open_ts") - pl.col("strike_avail_ts") <= max_staleness_ns)
    )
    return d.with_columns(
        pl.struct("spot", "strike", "sigma_sec", "secs_to_close")
        .map_elements(
            lambda r: fair_prob(r["spot"], r["strike"], r["sigma_sec"], r["secs_to_close"]),
            return_dtype=pl.Float64,
        )
        .alias("model_p"),
        pl.struct("spot", "strike_binance", "sigma_sec", "secs_to_close")
        .map_elements(
            lambda r: fair_prob(r["spot"], r["strike_binance"], r["sigma_sec"], r["secs_to_close"]),
            return_dtype=pl.Float64,
        )
        .alias("model_p_binance_strike"),
    )
