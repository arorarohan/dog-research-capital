# Paper trading and the forward test

**Paper trading** means running a strategy against live prices with simulated fills and no
real money. A **forward test** is paper trading with a pre-declared rule and evaluation
horizon, used as out-of-sample evidence (see [out-of-sample testing](out-of-sample.md)): the
future has not happened yet, so it cannot have leaked into the rule.

Its key product is the **decision-time snapshot**: the prices, spread and size available at
the instant the rule fired. Historical backfills only have sampled prices, so this record cannot
be recreated later. The honest comparison is realized per-trade edge against the backtest's
predicted distribution; the gap is itself a result (usually the backtest was optimistic).

Caveat: paper fills still flatter (no queue, no market impact), so fill models stay
conservative, and a forward test of a few trades a day needs weeks to see a 1c edge.
