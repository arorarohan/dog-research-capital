# Brier score and log loss

Scores for probability forecasts against 0/1 outcomes. **Brier** = mean (p - y)^2; 0 is
perfect, 0.25 is what always saying "50%" earns, and a forecaster must beat the constant
base-rate forecast to have any skill. **Log loss** = -mean(y ln p + (1-y) ln(1-p)) punishes
confident wrong answers much harder. Both are "proper": the best strategy is to report your
true belief. Used to compare a model against the market price as a forecaster.
