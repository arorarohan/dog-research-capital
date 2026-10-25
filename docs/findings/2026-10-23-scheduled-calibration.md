# Polymarket general calibration, redone with scheduled-end anchoring (2026-10-23)

Status: **H1 (favorites overpriced) is dead. The 5-60c gap replicated on the holdout and, net of
each market's own fee and a 1c half-spread, still earns about +1.2c per contract (pre-declared
primary test passed, 2026-10-25). Spread is unmeasured and a hindsight volume filter remains, so
this is a candidate for forward testing, not a trading result.** Supersedes section 5
of `docs/findings/2026-10-22-first-look.md`; why that was wrong is in
`docs/findings/2026-10-23-early-close-lookahead.md`.

Reproduce: `uv run python -m drc.research.studies calibration_poly_sched` (discovery) and
`calibration_poly_sched_holdout`.

## Design (what changed)
- **Universe:** every resolved binary Yes/No market on Polymarket with total volume >= $50k and a
  *scheduled* end date in a window fixed in advance (2024-01-01 .. 2026-09-01). Not "the
  biggest N".
- **Horizons:** 60/30/14/7/3/1 days before the scheduled end (`end_ts`). A market enters a
  horizon only if it was open then. Price = last trade at or before that moment (staleness
  <= 24h).
- **Discovery set:** markets scheduled to end before 2026-02-09 (about 19,250 markets, 1,100 to
  6,500 events per horizon). Later markets are reserved as a holdout and have not been looked at.
- Known residual hindsight: the $50k floor uses total volume, which is only known at the end.
  Volume-tier splits are reported as a check.

## Results (discovery set)
Pooled gap (win frequency minus price) for contracts priced 5-60c, one row per pre-declared horizon:

| Horizon | Contracts | Events | Gap | 95% CI |
|---|---|---|---|---|
| 60d | 3,427 | 1,098 | -1.8 pts | [-3.8, +0.2] |
| 30d | 5,104 | 1,824 | -1.4 | [-3.1, +0.3] |
| 14d | 6,614 | 2,731 | +0.6 | [-0.8, +2.1] |
| 7d | 9,647 | 4,173 | +1.7 | [+0.7, +2.7] |
| 3d | 15,565 | 6,036 | +2.2 | [+1.3, +3.0] |
| 1d | 16,024 | 6,468 | +2.5 | [+1.6, +3.3] |

(n is all contracts at that horizon; the gap uses the 5-60c subset: 1,276 to 7,203.)

1. **The earlier +3 to +8 point "anomaly" shrinks to 0 to +2.5 points** and is absent at long
   horizons. It was mostly our anchoring bug. A small positive gap remains at 7d, 3d and 1d.
2. **Prices beat the base rate everywhere** (Brier 0.066 to 0.116 vs 0.139 to 0.198).
3. **Favorites look overpriced at 3 days** (hypothesis H1 below). Contracts priced 80-90%
   resolved Yes 76.7% of the time (gap -7.9 pts, CI [-12.0, -4.2], 464 contracts); 90-95%: -6.1
   [-10.1, -2.3]; 95-100%: -2.4 [-4.2, -0.7]. Longshots at 10-20c: +4.3 [+2.6, +5.9]. This is the
   classic favorite-longshot pattern. At 7 days only the 80-90% bin has the same sign (-4.5 pts, CI [-10.7, +1.0]); the 90-100% bins
   do not show it (+0.8 and +1.5), so the pattern is specific to the 3-day horizon so far.
4. The 5-60c gap at 3 days grows with market size (+1.7 pts for $50-250k, +5.0 for >= $1M), so
   the volume-floor hindsight filter does not look like the source.

## What we cannot conclude
- **Not net of costs.** These are last-trade prices. Polymarket taker fees are set per market (rate x p(1-p); 0 on `zero_fees` markets, 3-7% elsewhere, up to 1.75c per share at 50c; see `fees.py` and gotchas), and the stored markets table predates fee capture, so these markets' rates are unknown until `drc-backfill polymarket-general` is re-run. We also
  have not measured the spread on these markets (history has no quotes; only live sessions can).
  A spread of a cent or two would eat most of a 2-pt average gap.
- **Many cells.** 6 horizons x 9 bins x 3 tiers were looked at; the pre-declared headline was the
  pooled 5-60c row, and the favorite result in item 3 was found by looking at the tables, so it is
  a hypothesis, not a finding. Horizons reuse the same markets, so they are not independent.
- **Not a trading result.** Nothing here is simulated as trades yet.

## Pre-declared test on the holdout (written before the holdout was looked at)
**H1:** at the 3-day horizon, contracts priced 80-95% resolve Yes at least 3 points less often
than their price (gap <= -3 pts) in markets scheduled to end on or after 2026-02-09.
- Support: the cluster-bootstrap 95% interval for the gap is entirely below 0 and the point
  estimate is <= -3 pts.
- Kill: the interval includes 0 or the point estimate is above -3 pts. Then we drop it.
- Only if H1 survives do we price a trade: buy No at 1 - p, with a half-spread haircut, and check
  the net result at several spread assumptions.

## Holdout results (run 2026-10-23, after sampling finished)
Discovery numbers above were from the first ~19,250 markets. Sampling later completed, so the
discovery set (scheduled ends before 2026-02-09) grew to 25,705 markets and the holdout (ends
2026-02-09 .. 2026-09-01) is 21,803 markets. The holdout was not looked at before this run.

**H1 fails.** 3 days before the scheduled end, contracts priced 80-95c:

| Set | Contracts | Events | Avg price | Won | Gap | 95% CI |
|---|---|---|---|---|---|---|
| Discovery (grown) | 1,021 | 861 | 87.8% | 80.2% | -7.6 pts | [-10.3, -4.9] |
| Holdout | 649 | 577 | 87.9% | 87.1% | -0.8 pts | [-3.4, +1.7] |

The kill rule (interval includes 0 or estimate above -3) is met, so we drop it. The discovery effect
was strong even with more data, so the likeliest explanations are chance among the many cells we
looked at, or a change in the market after early 2026 (for example Polymarket's 2026 fee
introduction); the second is untested and we should not chase it on the holdout.

**The pooled 5-60c gap replicates.** Primary pre-declared pooled row, gap in points (95% CI):

| Horizon | Discovery | Holdout |
|---|---|---|
| 60d | -2.9 [-4.4, -1.3] | -0.4 [-2.4, +1.7] |
| 30d | -1.0 [-2.5, +0.6] | -1.4 [-2.7, 0.0] |
| 14d | +0.6 [-0.6, +1.9] | -0.6 [-1.6, +0.5] |
| 7d | +1.7 [+0.9, +2.6] | +2.3 [+1.5, +3.0] |
| 3d | +1.9 [+1.2, +2.6] | +2.8 [+2.1, +3.5] |
| 1d | +2.4 [+1.7, +3.1] | +3.8 [+3.1, +4.5] |

Contracts priced 5-60c within 7 days of the scheduled end resolve Yes 2-4 points more often than
their price, in both periods, and (checked at 3d and 7d) in all three volume tiers. The 10-40c bins carry most of it (holdout, 3d:
10-20c priced 14.5%, won 19.3%, +4.7 [+3.1, +6.3]). The pooled row was pre-declared as the
headline but no numeric holdout threshold was written, so this counts as replication, not as a
pass of a pre-declared test.

## Net of costs (run 2026-10-25)
Rule written and committed before running (`scheduled_net_study`, `poly_net`): buy Yes on every
contract priced 5-60c at the horizon and hold to resolution. Cost = the market's own stated fee
(rate x (p(1-p))^exponent; 0 where fees are not enabled; 53,778 markets now carry fee data) plus
an assumed half-spread. **Primary: holdout, 3-day horizon, 1c half-spread; survives only if the
95% CI lower bound is above 0.**

**Primary result: passed.** Net +1.2c per contract, CI [+0.5, +1.9] (9,613 contracts, 4,987 events).

Holdout, net per contract (95% CI) by half-spread:

| Horizon | Gross | Fee | 0c | 0.5c | 1c | 2c | Break-even half-spread |
|---|---|---|---|---|---|---|---|
| 7d | +2.3 | 0.5 | +1.7 [0.9, 2.5] | +1.2 [0.4, 2.0] | +0.7 [-0.1, 1.5] | -0.3 [-1.1, 0.5] | 1.7c |
| 3d | +2.8 | 0.6 | +2.2 [1.5, 2.9] | +1.7 [1.0, 2.4] | **+1.2 [0.5, 1.9]** | +0.2 [-0.5, 0.9] | 2.2c |
| 1d | +3.8 | 0.6 | +3.2 [2.5, 3.9] | +2.7 [2.0, 3.4] | +2.2 [1.5, 2.9] | +1.2 [0.5, 1.9] | 3.2c |

(Gross, fee and costs in cents per contract.) Discovery set is similar (3d: +0.9c at 1c, CI
[+0.2, +1.6]; negative at 2c). The edge disappears at a 2c half-spread at 7d and 3d.

Where it lives (holdout, 1c half-spread, net with CI): the 10-20c band (+3.4c at 3d, +4.7c at 1d)
and the 20-40c band (+1.9c, +3.8c) carry it. The 40-60c band has none (-0.6c at 3d, -1.6c at 1d),
and 5-10c only at 1d. Fee-free markets net +2.4c at 3d; fee-bearing markets still +0.9c [+0.1, +1.6].
By scheduled-end month the 3d net at 1c was positive in 6 of 7 months (Feb +3.4c, Mar +1.8,
Apr +0.6, May +0.8, Jun -0.8, Jul +2.9, Aug +1.0): mostly individually insignificant, but not one
lucky month.

## What could still kill it (not yet addressed)
1. **Spread, unmeasured.** The 3d break-even is 2.2c, but its 95% lower bound is about 1.5c, so
   the edge needs a half-spread under about 1.5c on contracts that trade little 1-3 days before
   the deadline. The live Polymarket books in `data/raw` (top 50 markets by volume) can give an
   estimate, biased toward liquid markets.
2. **Hindsight volume filter.** The universe requires total volume >= $50k, known only at the
   end; markets that attract late volume may differ. The gap is positive in all three volume tiers,
   which argues against it, but only a universe fixed ex ante (live capture, or volume as of the
   horizon) removes it.
3. **Last trade is not the ask.** Fills are assumed at last price plus half-spread.
4. **Fee history.** Fees use the market's current schedule, so the cost is overstated where fees
   came in mid-life (conservative).
5. **Capacity.** These are markets with $50k-$1M total volume; position sizes would be small.
6. **Mechanism unexplained.** Contracts priced 10-40c within a few days of a deadline win more
   often than their price, the opposite of the classic overpriced-longshot pattern. We have no
   story for why, which raises the bar for believing it persists.

## Next step
Forward test: capture markets live at the horizon with an ex-ante universe (fixes 2) and measure
spreads (fixes 1), then compare realized results to this prediction.
