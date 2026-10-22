"""Schemas for historical (backfilled) tables. Stored under data/hist/.

Unlike live recordings these have no receive time or epochs: history is what the venue says
happened, timestamped in exchange time (UTC nanoseconds).

- markets:        one row per tradable contract with how it resolved (the label for studies)
- price_samples:  last traded price at chosen moments of a market's life (full tick history of
                  busy markets is far too large; we sample)
- klines_1m:      Binance spot 1-minute candles
- funding:        Binance USDT-margined perpetual funding-rate settlements
"""

from __future__ import annotations

import polars as pl

MARKETS_SCHEMA = {
    "venue": pl.Utf8,
    "market_id": pl.Utf8,  # Kalshi ticker; Polymarket condition id
    "token_id": pl.Utf8,  # Polymarket outcome token (null on Kalshi)
    "event_id": pl.Utf8,  # groups markets resolving off the same underlying event
    "series": pl.Utf8,
    "title": pl.Utf8,
    "outcome": pl.Utf8,  # what a unit of this contract pays on ('yes', 'Up', ...)
    "open_ts": pl.Int64,
    "close_ts": pl.Int64,
    "result_value": pl.Float64,  # payoff per unit: 1.0, 0.0, or null if unresolved/void
    "strike": pl.Float64,  # reference price for threshold/up-down crypto markets
    "strike_type": pl.Utf8,
    "volume": pl.Float64,
    "rules": pl.Utf8,
    "tier": pl.Utf8,  # kalshi: live | historical
}

PRICE_SAMPLES_SCHEMA = {
    "venue": pl.Utf8,
    "market_id": pl.Utf8,
    "target_ts": pl.Int64,
    "secs_to_close": pl.Int64,
    "price": pl.Float64,  # yes-price of the last trade at or before target_ts (in the window)
    "last_trade_ts": pl.Int64,
    "n_window": pl.Int64,  # trades seen in the lookback window (liquidity proxy)
}

KLINES_SCHEMA = {
    "symbol": pl.Utf8,
    "ts": pl.Int64,  # candle open time, UTC ns
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,  # base asset
    "n_trades": pl.Int64,
    "taker_buy_volume": pl.Float64,
}

FUNDING_SCHEMA = {"symbol": pl.Utf8, "ts": pl.Int64, "rate": pl.Float64, "interval_hours": pl.Int64}
