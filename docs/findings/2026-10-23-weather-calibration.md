# Kalshi daily-high weather brackets: are the prices exploitable on their own? (2026-10-23)

Status: closed for the price-only check. **Verdict: at the assumed 2c half-spread there is no edge
(pre-declared kill criterion met); the only candidate, selling longshots, would work only if the
half-spread is under about 1c, which is unmeasured.** This does not test a forecast-based edge
(no forecast data was used). Fees rechecked 2026-10-23 (see Costs).

Reproduce: `uv run python -m drc.research.studies weather`.

## Data
7 cities (NYC, Chicago, Miami, Austin, LA, Denver, Philadelphia), settled markets closing
2026-08-25 .. 2026-10-22: 2,503 bracket markets in 419 city-days (each city-day is one event with
about six brackets, exactly one of which wins). Price = last trade within 4 hours before each
moment; median price age 11 minutes at 24h and 3 minutes at 12h before close.

A first pass with a 120-second lookback produced only 1,692 samples (brackets trade rarely, and
the samples were biased to active moments) and was discarded; see `docs/gotchas.md`.

## Pre-declared design (written before looking)
Primary horizons 24h and 12h before close (by 6h and 2h the day's high has usually happened, so
prices near 0 or 1 reflect observed weather; reported as a check only). Price groups under 15c,
15-85c, over 85c. For each, buying Yes and buying No priced net of Kalshi's 7% x p(1-p) fee and an
assumed 2c half-spread (unmeasured). All combinations printed. Kill: no primary combination has a
confidence interval lower bound above zero net of costs.

## Results
- **Prices are good forecasts.** Brier 0.100 at 24h and 0.069 at 12h, against 0.145 for the constant
  base rate.
- **Calibrated in the middle.** At 24h and 12h every bin from 5% to 95% has an interval that
  includes zero (intervals roughly +-4 to 6 points wide, so gaps under ~4 points are invisible).
- **Longshots are slightly overpriced.** Contracts under 5c: priced 1.9c, won 0.4% (24h; -1.4 pts,
  CI [-1.8, -0.9]); priced 1.3c, won 0.2% (12h; -1.1, CI [-1.3, -0.8]). Real but tiny.
- **At the assumed 2c half-spread nothing survives costs.** Of 18 combinations (price group x
  side x horizon, including the check-only horizons), none has a lower bound above zero. The best
  primary one is buying Yes above 85c at 12h: +0.2c, CI [-3.9, +3.7]. Selling longshots (buy No
  under 15c) loses 0.9c at 24h (CI [-1.7, -0.1]) and 1.0c at 12h ([-1.5, -0.5]).
- **At zero spread, selling longshots is positive.** Same trade with no spread cost (fee only):
  +1.1c at 24h (CI [+0.3, +1.9]) and +1.0c at 12h ([+0.5, +1.5]); also positive at 6h and 2h. That
  is the 1.1-1.4 point longshot overpricing minus a fee of ~0.1c. Every other combination stays
  negative or indistinguishable from zero even at zero spread. Break-even half-spread for the
  longshot trade is therefore about 1.1c (as low as 0.3c at the interval's lower bound).
  This table was added after seeing the 2c results, so it is descriptive, not a pre-declared test.
- **Bracket-sum check is not an arbitrage.** Last-trade prices of one city-day's brackets sum to a
  median 1.05 at 24h (1.04 later). That mixes trade times and bid/ask sides, so it says nothing
  about executable prices.

## Costs (rechecked)
Kalshi's API lists all seven weather series with `fee_type: quadratic`, `fee_multiplier: 1`, so
the standard fee applies: 7% x p(1-p) (the 7% coefficient is from the schedule recorded in
`src/drc/backtest/fees.py`, PR #2). The study uses the unrounded fee; Kalshi rounds the fee up to
the cent per order, which is negligible for orders of ~100 contracts but ~1c on a one-contract
order, which would erase the longshot edge. The 2c half-spread is still an assumption; Kalshi's
minimum tick is 1c, so the smallest possible half-spread is 0.5c.

## What we can and cannot conclude
- Can: simple price-based rules do not pay at a 2c half-spread. Longshots (under 5c) are overpriced
  by ~1 point, a persistent pattern across all four horizons (they share markets, so the
  horizons are not independent evidence).
- Cannot: say longshot-selling fails or works. It needs a half-spread under ~1c, and it earns ~1c
  on ~98c risked per contract with rare large losses (one miss costs ~98 wins), at tiny capacity.
  It also cannot be excluded that a forecast-based model beats the crowd in the mid-price bins,
  which are too noisy here (intervals +-4-6 points). Sixty days is one season.

## Pre-declared forward test (written before the data exists)
**H2:** buying No on brackets priced under 15c at 24h or 12h before close earns a gross gap of at
least 1.0 point (win frequency below price by >= 1.0 point), on city-days settling after
2026-10-23 and pulled with the same procedure (4-hour lookback, 7 cities).
- Support: cluster-bootstrap CI for the gap is entirely below -0.5 points and the point estimate
  is <= -1.0, on at least 60 new city-days (~8 days of data per city).
- Kill: the interval includes zero or the point estimate is above -0.5. Then drop it.
- Even if H2 holds, trading it needs a measured half-spread under ~1c from live sessions.

## Decision
Per the plan's kill criterion (at 2c half-spread) the weather forecast pipeline stays on hold. The
price-only longshot pattern is parked as H2, to be tested on fresh days and with a live spread
measurement. Next in the plan: deadline-market time decay.
