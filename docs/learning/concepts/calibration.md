# Calibration

**Definition.** A forecaster is calibrated if P(event | forecast = p) = p. A prediction-market
price is a forecast: a Yes contract at 70c says "70%". To test it, bucket resolved contracts by
price and compare each bucket's average price to its realized Yes frequency.

    gap = freq_yes - avg_price        (profit per contract from buying that bucket, before costs)

**Worked example (toy numbers).** 200 contracts priced 60-70c, average price 0.65. 140 resolved
Yes, so freq_yes = 0.70 and gap = +0.05. Buying all 200 at ~65c pays $1 on 140 of them:
profit = (140 - 200 x 0.65) / 200 = +5c per contract, before fees and spread. Whether +5c is
signal or noise depends on the interval around it (see cluster bootstrap).

**Why it matters.** It is the cheapest test of whether a market is exploitable by a simple
rule. A flat gap near 0 in every bucket (what we see for 15-minute BTC) means "price is the
right probability" and any edge has to come from information the price lacks.

**Calibration is not skill.** A forecaster that always says the base rate is calibrated and
useless. Pair it with Brier score (does it separate likely from unlikely events?).

**Pitfalls.**
- Buckets must be judged on independent events: contracts from one underlying event move
  together, so resample whole events ([cluster bootstrap](cluster-bootstrap.md)).
- Many buckets means some look "significant" by luck ([multiple comparisons](multiple-comparisons.md)).
- *When* you read the price must be knowable then ([lookahead bias](lookahead-bias.md)); this
  one fooled us.

In this repo: `drc.research.evaluation.calibration`.
