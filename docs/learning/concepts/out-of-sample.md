# Out-of-sample testing (holdout and forward tests)

A pattern found by browsing data is a hypothesis, because with enough cells some will look
significant by chance. It becomes evidence only on data you did not use to find it.

- **Holdout:** reserve part of the existing data (we reserved markets scheduled to end on or
  after 2026-02-09), look at none of it, write the pass/kill rule, then run once.
- **Forward test:** data that did not exist when the hypothesis was written (weather days after
  2026-10-23). The strongest version: nothing could have leaked.

**Worked example (ours).** Favorites priced 80-95c won 7.6 points less than priced on discovery
data (interval [-10.3, -4.9], 1,021 contracts). On the holdout: -0.8 points ([-3.4, +1.7]). The
discovery effect looked solid and was not. The pooled 5-60c gap went the other way: +1.9 on
discovery, +2.8 on the holdout, so it counts as replicated.

**Rules we follow.** Write the threshold and kill rule before running; run the holdout once;
report every cell; do not retune on the holdout (that burns it).
