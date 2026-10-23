# Research plan: which edges to hunt next

Written 2026-10-22 after the first look (`docs/findings/2026-10-22-first-look.md`) found no edge
in the most contested corner (Kalshi/Polymarket 15-minute BTC). Scores are **my judgment**,
not measurements; treat tiers as meaningful and exact ordering as soft. Novelty cannot be
verified (private firms don't publish), and "novel" also means unproven: the prior that any
single idea works is low, and we expect most to fail.

## Selection principles
1. **Net, not gross.** A study is worth running only if a plausible gross edge clears the cost
   hurdle by a margin we can measure. Hurdles (held to resolution, so one-way): Kalshi fee is
   7% x p(1-p) (1.75c at 50c, 0.6c at 10c, ~0 near 0/100) plus half the spread; Polymarket fee
   is 0 on most markets (unverified placeholder) plus half the spread.
2. **Orphan edges.** Edges persist where they are too small or too labor-intensive for
   institutions to bother, not merely where they are obscure. We have no capacity problem, so
   thin, per-market, hand-built pipelines are our natural habitat. Contested, scalable edges
   (fast spot -> 15-minute contracts, plain arbitrage) are not.
3. **Power first.** Before running, write the minimum detectable edge. With ~300 independent
   trades we only see edges of ~8c (80% power); that rules out nothing realistic. Need
   thousands of independent events for a 1-2c net edge.
4. **Pre-declare** hypotheses, thresholds, and a kill criterion; report every combination.
5. **Anchor everything to what was knowable then**: scheduled end dates, ex-ante universes,
   point-in-time forecasts (see Research pitfalls in `docs/gotchas.md`).

## Scoring (1-5; higher is better)
P = prior plausibility of a net edge given competition. N = novelty / orphan-ness. C = low
cost hurdle. T = testable now with free data at adequate power. E = low effort.
Score = 2P + 2N + C + T + E (novelty weighted up per project priority).

| # | Study | P | N | C | T | E | Score | Tier |
|---|---|---|---|---|---|---|---|---|
| 1 | Deadline ("by date") markets: scheduled-end calibration + time-decay vs hazard rate | 3 | 3 | 5 | 4 | 4 | 25 | 1 |
| 2 | Polymarket wallet-flow: does flow from walk-forward-proven wallets predict beyond price? | 2 | 5 | 5 | 2 | 2 | 23 | 1 |
| 3 | Kalshi daily-high weather brackets vs archived-forecast fair value | 3 | 4 | 2 | 4 | 2 | 22 | 1 |
| 4 | Cross-venue same-question gaps with rule/oracle differences (fresh quotes only) | 2 | 4 | 3 | 3 | 3 | 21 | 2 |
| 5 | Near-certain contracts (95-99c) "bond" carry: realized upset rate vs price | 2 | 1 | 5 | 5 | 5 | 21 | 2 |
| 6 | Time-of-day / day-of-week structure in 15-minute contracts | 1 | 3 | 2 | 5 | 5 | 20 | 2 |
| 7 | Slow crypto: funding carry and trend (second book, diversifier) | 2 | 1 | 3 | 5 | 4 | 18 | 3 |
| 8 | Strike-ladder / complement consistency (needs live book data) | 2 | 2 | 3 | 2 | 3 | 16 | 3 |
| 9 | Digital-option model v2 on Kalshi 15-minute (fast spot) | 1 | 1 | 2 | 5 | 3 | 14 | 3 |
| 10 | Economic-release markets (CPI, jobs) | 2 | 2 | 2 | 1 | 3 | 14 | 3 |

Dropped: sports vs sharp sportsbook odds (competitive; historical odds are paid data).

## Tier 1 detail

### 1. Deadline markets (scheduled-end calibration and time decay)
- Idea: "Will X happen by D?" markets must lose value as D nears with no event, at a rate set by
  the event hazard. If prices decay too slowly (or too fast), buy the other side. Probability
  and survival-analysis machinery fits our strengths; the scheduled-end anchor fixes the bias
  that fooled us.
- Cost: Polymarket ~half-spread only. Data: 3,000 markets already listed; needs `end_ts`
  stored and a re-sampling pass (~25 min fetch) anchored to scheduled end.
- Power: ~1,000+ distinct events, so a ~3-4 point gap is detectable; smaller is not.
- Kill: pooled gap CI includes 0 in the "ran to schedule" subset after hazard adjustment.

### 2. Wallet flow on Polymarket
- Idea: the public trade feed carries wallet addresses. Score wallets walk-forward (only past
  outcomes), then test whether proven wallets' net flow predicts resolution beyond the
  prevailing price. High novelty, but heavy data and a real lag problem: price may move before
  we can see/act.
- Cost: ~0 fee. Power: millions of trades but ~hundreds of independent events per period.
- Kill: no out-of-sample lift over price alone. Respect rate limits; public data only.

### 3. Weather brackets on Kalshi
- Idea: daily-high-temperature markets (122 daily weather series; one city shows ~400 settled
  markets in ~2 months, median volume ~2k contracts, so thin and under-covered). Build a
  calibrated probability from archived model forecasts (Open-Meteo historical-forecast API,
  verified reachable) and compare to the crowd's price. External information the crowd must
  process by hand: the genuinely different data source we want.
- Cost: highest of Tier 1 (spreads in thin markets 2-4c plus fee at mid prices), so the
  forecast must beat the crowd by ~3-4c at mid prices; edges are likelier at the tails.
- Data caveats: archived forecasts must be point-in-time (lead-time leakage); markets settle on
  the NWS daily climate report while reanalysis/archive values can differ by ~1F, which
  matters for whole-degree thresholds. Capacity is tiny (tens of dollars per market).
- Kill: calibrated-forecast Brier no better than market price after the pre-declared
  calibration step.

## Sequencing (each step is bounded; none is a long unattended job)
1. Extend `markets` with scheduled `end_ts`; re-sample Polymarket general (study 1). ~25 min fetch.
2. Weather: pick 5-10 city series, pull forecasts + actuals, build the calibration pipeline.
3. Cheap scans while those run: 5 (carry) and 6 (time-of-day), with the multiple-testing
   count written down first.
4. Decide on wallet flow (2) after seeing how much data a one-sector pilot needs.

## Additional conceptual research (2026-10-23)

[Twelve structural-alpha proposals](research/2026-10-23-structural-alpha.md) develops process,
measurement, conditional-probability and wallet-behavior mechanisms beyond the baseline studies
above. These are unvalidated hypotheses with ranked feasibility, mathematical examples,
concrete trades, data gaps and kill criteria. Start candidates: procedural gate count,
rule-disagreement payoff baskets, and weather measurement channels. This memo does not change
the existing holdout hypothesis or authorize a broad implementation effort.
