# Post-mortem: the "mispricing" that was our own lookahead (found 2026-10-22)

Status: closed. The corrected study is in `docs/findings/2026-10-23-scheduled-calibration.md`
(or its pending note in the roadmap). This note exists so we recognise the pattern next time.

## What we saw
Calibration study on 3,000 Polymarket Yes/No markets (largest by volume), prices sampled
1/3/7/14/30 days "before close". Pooled over price bins, contracts priced 5-60c resolved Yes
3-8 points more often than their price implied, with 95% intervals excluding zero at all five
horizons, shrinking by year (+12.6 pts in 2024, +8.8 in 2025, +7.2 in 2026). That looks like
exactly what we are hunting: a persistent, decaying, systematic mispricing.

## What was wrong
`close_ts` is when trading **actually stopped**. Many markets stop early *because the event
happened* ("Will X happen by June?" closes in March when X happens). So "7 days before
close_ts" is, for those markets, a moment chosen by the outcome: we only look at the price at
a time that depends on when (and whether) the event occurred. The exact mechanism of how this
tilts prices was not worked out; the split below shows where the effect lives, which is enough.

Splitting by whether a market ran to its scheduled end date made it obvious:

| Subset (7d horizon) | Gap | 95% CI |
|---|---|---|
| Ran to its scheduled end | +1.5 pts | [-2.1, +5.0] |
| Closed > 2 days early | +26 pts | [+17.7, +36.8] |

About 35% of markets closed early. The entire "edge" lived in them.

## Why it was convincing
- Intervals were cluster-bootstrapped and excluded zero at every horizon (each horizon reused
  the same flawed anchor, so agreement across horizons was not independent evidence).
- It shrank over time, which pattern-matches to "arbitraged away".
- Nothing crashed or looked odd; the bug was in what we *chose to measure*, not in the code.

## How we caught it
Splitting the result by whether each market ran to its scheduled end date. The effect lived
entirely in the early-closing markets, which pointed at the anchor.

## Fix
- `markets.end_ts` stores the scheduled end (Polymarket `endDate`, Kalshi `close_time`).
- `price_samples` for the `general-sched` series are anchored to `end_ts`, and a moment counts
  only if the market was open then (nothing is sampled after an early close).
- The universe is every resolved binary market with a scheduled end inside a fixed date window,
  not "the biggest ones" (that is picked after the fact).
- Old series `general` is kept only so `calibration_poly_general_flawed` can reproduce this.

## Checklist for any future study
1. For each variable that defines *when* or *which* rows are used, ask: could a trader have
   known it at the time of the decision?
2. Anything tied to how a market ended (close time, final volume, resolution date) is suspect.
3. Before believing a large effect, split on the variables most likely to be tied to outcome.
4. Agreement across horizons or intervals is only independent evidence if the anchor is clean.

Related: `docs/gotchas.md` (Research pitfalls), `docs/learning/concepts/lookahead-bias.md`.
