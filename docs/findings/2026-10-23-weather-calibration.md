# Kalshi daily-high weather brackets: are the prices exploitable on their own? (2026-10-23)

Status: complete for the price-only check. **Verdict: no edge found; the pre-declared kill
criterion is met.** This does not test a forecast-based edge (no forecast data was used).

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
- **Nothing survives costs.** Of the 18 net-of-cost combinations (price group x side x horizon,
  including the check-only horizons), none has a lower bound above zero. The best primary one is
  buying Yes above 85c at 12h: +0.2c, CI [-3.9, +3.7]. Selling longshots (buy No under 15c)
  loses 0.9c at 24h (CI [-1.7, -0.1]) and 1.0c at 12h ([-1.5, -0.5]).
- **Bracket-sum check is not an arbitrage.** Last-trade prices of one city-day's brackets sum to a
  median 1.05 at 24h (1.04 later). That mixes trade times and bid/ask sides, so it says nothing
  about executable prices.

## What we can and cannot conclude
- Can: simple price-based rules on these markets do not pay after costs. The one visible inefficiency
  (overpriced longshots, ~1 point) is smaller than the fee plus any realistic spread.
- Cannot: say a forecast-based model would also fail. The mid-price bins are too noisy to rule out a
  3-4 point advantage, and the 2c spread is an assumption. Sixty days is one season. 
- The 2c half-spread is the largest unknown; live sessions could measure it.

## Decision
Per `docs/research-plan.md`, this puts the weather forecast pipeline (Open-Meteo archive, point-in-time
forecasts, station rounding) on hold: the prior was already low (3/5) and the cheap test gave no
encouragement. Revisit if live spread measurements come in far below 2c, or after better candidates
are exhausted. Next in the plan: deadline-market time decay.
