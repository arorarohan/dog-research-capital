# Calibration

A forecaster is **calibrated** if events it prices at X% happen X% of the time. For a
prediction market: bucket contracts by price, then compare each bucket's average price to the
realized frequency of Yes outcomes. The difference (**gap** = frequency - price) is the
profit per contract from buying that bucket, before costs.

Pitfalls: the buckets must be judged on independent events (markets from one underlying event
move together, so we **cluster-bootstrap**: resample whole events); and with many buckets,
some will look "significant" by chance (see multiple comparisons).

In this repo: `drc.research.evaluation.calibration`.
