# Our studies after multiple-testing correction (2026-11-06)

Status: re-reading of numbers already published in `docs/findings/`; no new data, no study
re-run, no holdout touched. **One result survives every correction: the out-of-sample
replication of the Polymarket scheduled-end gap (gross, 1-7 days out). Its net-of-cost version
survives the confirmatory family but not the pessimistic all-looks view, and the
weather-longshot overpricing is statistically solid but economically untested.** Nothing changes
status. Reproduce: `uv run drc-registry table` (method: `hypotheses/README.md`).

## What was counted
The registry (`hypotheses/`) holds 31 hypotheses: 9 that were tested or measured, plus the 10
research-plan studies and the 12 structural-alpha ideas (proposed, untested). Over the 8 tested
edge hypotheses there are **49 recorded tests, of which 43 have a CI and so a p-value, and about
525 more cells we looked at but never wrote down individually: 574 looks in all**. 20 were
pre-declared primary tests. No finding reported a p-value. Every p below is derived from the
reported estimate and 95% interval (normal approximation, using the interval bound facing zero,
so p < 0.05 exactly when the interval excludes zero).

## Corrected view of the results that matter
Two-sided p; Holm and BH over the 43 tests with a p; Bonferroni over all 574 looks; Bonferroni
over the 20 primary tests; DSR = Phi(z - 3.09), where 3.09 is the expected best z among 574 tries
by luck. "Survives" means the adjusted p is at most 0.05 and the sign is the predicted one.

| Hypothesis | Test | Estimate [95% CI] | p | Holm | BH q | Bonf. m=574 | Bonf. primary m=20 | DSR |
|---|---|---|---|---|---|---|---|---|
| pm-sched-gap | holdout gross gap 3d, pts | +2.8 [+2.1, +3.5] | <1e-4 | <1e-4 | <1e-4 | <1e-4 | - | 1.00 |
| pm-sched-gap | holdout gross gap 7d / 1d | +2.3 / +3.8 | <1e-4 | <1e-4 | <1e-4 | <1e-4 | - | 0.99 / 1.00 |
| pm-sched-gap | **primary: holdout net 3d at 1c half-spread, c** | +1.2 [+0.5, +1.9] | 0.0008 | 0.016 | 0.0014 | 0.45 | 0.016 | 0.60 |
| pm-sched-gap | holdout net 3d at 0.5c half-spread | +1.7 [+1.0, +2.4] | <1e-4 | <1e-4 | <1e-4 | 0.0011 | - | 0.95 |
| pm-sched-gap | holdout net 7d at 1c | +0.7 [-0.1, +1.5] | 0.086 | 0.95 | 0.11 | 1 | - | 0.08 |
| pm-sched-gap | discovery net 3d at 1c | +0.9 [+0.2, +1.6] | 0.012 | 0.18 | 0.017 | 1 | - | 0.28 |
| pm-sched-gap | holdout net 3d at 1c, fee-bearing only | +0.9 [+0.1, +1.6] | 0.027 | 0.38 | 0.038 | 1 | - | 0.19 |
| H2 weather longshots | discovery gap < 5c at 12h, pts | -1.1 [-1.3, -0.8] | <1e-4 | <1e-4 | <1e-4 | <1e-4 | - | 1.00 |
| H2 weather longshots | discovery net at ZERO spread, 12h, c | +1.0 [+0.5, +1.5] | <1e-4 | 0.0021 | 0.0002 | 0.051 | - | 0.80 |
| H2 weather longshots | discovery net at zero spread, 24h | +1.1 [+0.3, +1.9] | 0.0070 | 0.11 | 0.011 | 1 | - | 0.35 |
| H1 favorites (killed) | discovery 3d 80-95c, pts | -7.6 [-10.3, -4.9] | <1e-4 | <1e-4 | <1e-4 | <1e-4 | - | 0.99 |
| H1 favorites (killed) | **primary: holdout 3d 80-95c** | -0.8 [-3.4, +1.7] | 0.53 | 1 | 0.57 | 1 | 1 | 0.01 |
| lookahead (killed) | closed-early subset 7d, pts | +26 [+17.7, +36.8] | <1e-4 | <1e-4 | <1e-4 | <1e-4 | - | 1.00 |

The full table (all 43 rows) is in `hypotheses/SUMMARY.md`.

## What survives
1. **The scheduled-end gap replicated out of sample, and that survives everything.** On the
   holdout, 5-60c contracts 1-7 days before the scheduled end won 2.3-3.8 points more often than
   their price, with z around 6-10. Even treated as the best of all 574 looks it stands out.
   This is the only out-of-sample, statistically robust result we have.
2. **The net trade survives the right family, narrowly, and not the harshest one.** The
   pre-declared primary (+1.2c at a 1c half-spread) has adjusted p 0.016 within the 20 primary
   tests and under Holm over all recorded tests, but 0.45 under Bonferroni over every look and a
   DSR of 0.60. The confirmatory family is the appropriate one for a pre-declared test on
   unseen data, so it passes, but with less margin than "CI excludes zero" suggests. **Caveat we
   had not written down before:** the net rule was frozen on 2026-10-25, two days after the
   holdout gross gap had been seen, and net = gross - fee - assumed spread. So the primary is not
   an independent confirmation; it restates the holdout replication under a cost assumption.
   Whether the edge is real after costs is a question about spreads and depth, not statistics:
   at a 0.5c half-spread it clears even the all-looks bar (0.0011, DSR 0.95), at 1c it does not,
   and forward session 1 measured a 1.13c mean half-spread with ~$45 median depth.
3. **Weather longshots are overpriced (statistically), but no tradeable version survives.** The
   ~1-point gross overpricing under 5c survives every view. The zero-spread net trade (12h)
   survives Holm and BH but just misses all-looks Bonferroni (0.051), the 24h version fails Holm,
   and zero spread is impossible (Kalshi's minimum half-spread is 0.5c). H2 stays an untested,
   pre-declared forward test; at 2c half-spread both longshot rows were significantly *negative*.

## What does not survive
- Discovery-set net result (+0.9c, Holm 0.18), the fee-bearing-only split (Holm 0.38) and
  the 7-day net trade at 1c: individually "CI excludes zero" (or nearly) but not after correction.
  Do not quote them as independent support.
- H1's 95-100c discovery cell (Holm 0.096) and the 24h zero-spread weather row (Holm 0.11).
- Everything already killed stays killed; no correction resurrects a failed primary.

## What correction cannot do (the most useful lesson here)
- **H1's discovery cell survived every correction, including Bonferroni over all 574 looks
  (z about -5.5), and then failed on the holdout** (-0.8 [-3.4, +1.7]). Multiple-testing
  correction only removes *chance* among many tests and assumes each p-value is right. If our
  intervals are too narrow (we cluster by `event_id`, but markets also co-move by news day and
  category), or the market changed (Polymarket's 2026 fee rollout), every p is too small and no
  correction helps. Only the holdout caught it. The same may apply to the surviving gap, which
  is why its replication across two periods matters more than its z-score.
- **The lookahead artifact (+26 points) survives every correction too.** Correction does
  nothing against bias in what we measure.
- So: correction is necessary, not sufficient. Holdouts, ex-ante universes and design checks
  (`docs/gotchas.md`, Research pitfalls) did the real work so far.

## Honest caveats on these numbers
- p-values are derived from intervals rounded to 0.1 and assume a roughly normal bootstrap
  distribution; treat them to about one significant figure.
- Unrecorded-look counts are reconstructed from the findings' descriptions and counted
  generously (for example all holdout calibration bins, all printed weather combinations). The
  weather finding says 18 combinations while the study code prints 24; we used 24.
- Looks within a study reuse the same markets (horizons, spreads), so they are positively
  dependent; Bonferroni and DSR then over-penalize, and the all-looks view is a pessimistic
  bound, not the expected truth.
- Two tests with no CI (the digital model's two significantly negative combinations) count as
  looks but have no p.

## Decision
No status changes: `pm-sched-gap` stays **testing** (forward test is the decider), H2 stays
**testing**, everything killed stays killed. Going forward (written into
`docs/research-plan.md`): every study registers its hypothesis and its look count before it
runs, reports a CI or p for every cell it prints, and a forward-test verdict is judged inside the
primary family. Open for Rohan: whether fund approval should additionally require DSR >= 0.95
over all looks (the scheduled-end gap would not meet that at a 1c half-spread).
