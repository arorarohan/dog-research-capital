# Brier score and log loss

Scores for probability forecasts p against 0/1 outcomes y.

    Brier    = mean (p - y)^2           (mean squared error; 0 = perfect)
    log loss = -mean [y ln p + (1-y) ln(1-p)]

Both are *proper scoring rules*: expected score is optimized by reporting your true belief, so
you cannot game them by shading probabilities.

**Worked example.** Four contracts, forecasts 0.9, 0.8, 0.3, 0.6; outcomes 1, 1, 0, 0.
Squared errors: 0.01, 0.04, 0.09, 0.36 -> Brier = 0.50 / 4 = 0.125.
Always saying 0.5 gives 0.25 on any data. Always saying the base rate (here 0.5) also gives
0.25. So 0.125 beats both: the forecasts carry information.

**Skill means beating the constant base-rate forecast**, not beating 0.25 (if 90% of outcomes
are Yes, the base-rate forecast scores 0.09 and a Brier of 0.20 is bad).

**Log loss** punishes confident misses brutally: saying 99% and being wrong costs ln(100) = 4.6,
saying 70% and being wrong costs ln(3.3) = 1.2. Brier caps the same two mistakes at 0.98 vs 0.49.
Log loss is the right lens for tail probabilities; Brier is more robust to a few bad misses.

**Use here.** Compare two forecasters on the same contracts (market price vs our model). The
one with lower Brier is the better forecaster; only if the model wins can it find edge. So far
the market price wins every time (`docs/findings/2026-10-22-first-look.md`).

In this repo: `drc.research.evaluation.brier`, `log_loss`, `score_summary`.
