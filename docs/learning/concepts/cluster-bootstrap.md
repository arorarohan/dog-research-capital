# Cluster bootstrap and exact bounds for all-win bins

**Bootstrap.** To see how much a statistic would wobble across datasets, resample your data with
replacement many times, recompute the statistic each time, and read an interval off the spread
(2.5th to 97.5th percentile gives a 95% interval).

**Why cluster.** Ordinary bootstrap assumes rows are independent. Ours aren't: a Polymarket
"Who wins the election?" event has dozens of candidate markets whose outcomes are tied together
(exactly one is Yes); a Kalshi daily-temperature event has six brackets, one of which wins.
Treating each as an independent observation overstates the sample size and makes intervals too
narrow. A *cluster* bootstrap resamples whole events (draw k events with replacement, keep all
their rows), so the interval reflects the real number of independent units.

**Worked example.** 300 contracts from 20 events. Plain bootstrap treats n = 300; the cluster
bootstrap effectively has n = 20 draws, and the interval is roughly sqrt(300/20) = 3.9 times
wider when rows within an event are strongly dependent. Same data, very different conclusion.

**The all-win failure.** If every contract in a bin won, every resample also has 100% wins, so
the bootstrap interval has zero width and looks "significant" for any price below 100c. We saw
94/94 wins in the 95-100c bin of the 15-minute data. The fix is an exact bound: if the true win
rate were q, P(k wins in k tries) = q^k, and the 95% two-sided lower bound solves q^k = 0.025,
q = 0.025^(1/k). With k = 94 independent events that gives q >= 0.961, which includes the
97.5c price; so 94/94 is consistent with fair pricing. (Clopper-Pearson interval, one end.)

In this repo: `drc.research.evaluation.cluster_bootstrap_mean`, `_boundary_gap_ci`.
