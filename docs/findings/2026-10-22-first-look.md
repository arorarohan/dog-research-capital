# First look at the historical data (2026-10-22)

Status: exploratory. Nothing here is a tradeable result. The most useful outcome of this
session is a list of ways the analysis fooled us, which are recorded in `docs/gotchas.md`.

Reproduce: `uv run python -m drc.research.studies [calibration_kalshi15 | calibration_poly15 |
calibration_poly_general | digital | cross_venue]`.

## Data
| Set | Size | Span |
|---|---|---|
| Kalshi KXBTC15M ("BTC up in 15 min?") | 664 resolved markets, prices at 7 moments each | 2026-10-15 .. 10-22 |
| Polymarket btc-updown-15m | 673 resolved markets (673/673 sampled) | 2026-10-15 .. 10-22 |
| Polymarket general resolved Yes/No | 3,000 largest by volume (>= $2.4M), 5 horizons each | 2023-03 .. 2026-10 |
| Binance BTCUSDT/ETHUSDT 1m candles, funding | 993,600 candles each | 2024-12 .. 2026-10-21 |

## 1. 15-minute BTC contracts are well calibrated on both venues
Prices beat the constant base-rate forecast at every horizon (Brier 0.231 at 14 min out down
to 0.014 at 30 s on Kalshi; Polymarket 0.242 down to 0.028). With 50-100 contracts per price
bin the intervals are +-10 points wide, so mispricings smaller than that are invisible here.
The "significant" gap in the 95-100% bins was an artifact: every contract in the bin won, and
resampling identical outcomes gives a falsely tight interval. The tool now uses an exact bound
for such bins (with it, 94/94 wins at a 97.5% price is consistent with fair pricing).

## 2. Kalshi's index sits ~13 bp below Binance BTCUSDT
The strike (CF Benchmarks BRTI) is a steady -12.8 bp (median, ~$107) from Binance's price at
market open. Models mixing the two are biased: using Kalshi's strike with Binance spot scored
Brier 0.31-0.35 (worse than a coin flip); measuring the strike on Binance too (basis-neutral)
fixed it.

## 3. A simple digital-option model does not beat the market
Model: lognormal with trailing 60-minute volatility, Binance 1m spot, basis-neutral strike.
- Kalshi's price is the better forecaster at every horizon (e.g. Brier 0.0987 vs 0.117 at
  180 s, 0.0147 vs 0.0605 at 30 s).
- Trading the model's disagreement with the market, net of an assumed 0.5c half-spread and
  Kalshi's fee: 0 of 6 pre-declared (threshold x horizon) combinations positive with an
  interval excluding zero; two significantly negative (-4.7 and -6.8 cents per contract).
- Not evidence that no edge exists: spot was up to a minute stale, volatility is crude, and
  Binance history ended 2026-10-21 so the 69 most recent markets were excluded. A fair test
  needs second-level spot data.

## 4. Kalshi vs Polymarket on the same 15-minute question
- Outcomes agreed in 98.3% of 663 matched intervals (11 disagreements): they settle on
  different oracles (BRTI average vs Chainlink TWAP).
- Prices differ by 5-9 cents on average (19-65% of pairs differ by more than 5 cents,
  shrinking toward close) with no systematic bias. Polymarket's prices are sparse (1-2 points
  per 2-minute window) and score worse (Brier 0.028 vs 0.014 at 30 s). Kalshi is the better
  price. Not an arbitrage finding: these are last-trade samples, not executable quotes.

## 5. A "mispricing" in Polymarket's general markets that was our own lookahead
Pooled over horizons, contracts priced 5-60% resolved Yes 3-8 points more often than their
price implied, with intervals excluding zero at all five horizons, shrinking over time
(+12.6 pts in 2024, +8.8 in 2025, +7.2 in 2026). It looked like a real, decaying anomaly.

It was not. We measured "N days before close" from each market's *actual* close time, but
35% of markets close early precisely because their event happened, so the realized close
time leaks the outcome. Splitting by whether a market ran to its scheduled end date:

| Subset | Gap (7d out) |
|---|---|
| Ran to schedule | +1.5 pts, CI [-2.1, +5.0] |
| Closed > 2 days early | +26 pts, CI [+17.7, +36.8] |

(Other horizons: 14d -3.2 / +24.4, 3d +2.2 / +17.6.) The whole effect lives in early-closing
markets. A valid calibration study must anchor horizons to the scheduled end date, known in
advance, and use a universe defined before the outcomes.

## What we can and cannot say
- Can: the 15-minute contracts are efficiently priced to within our resolution; the venue
  basis and settlement-oracle differences are real and measurable; the infrastructure
  (backfill, evaluation with event clustering, honest costs) works.
- Cannot: claim any tradeable edge. The only apparent one was a bias in our design.

## Next studies
1. Redo the Polymarket general study anchored to scheduled end dates, with an ex-ante universe
   (needs `end_ts` stored in `markets` and a re-sampling pass).
2. Second-level spot (Binance 1s klines) for the digital model; refresh klines past 10-21.
3. Hourly/daily Kalshi crypto ladders: monotonicity and digital-option consistency.
4. More days of the 15-minute series to narrow the intervals (the series has ~24k markets).
