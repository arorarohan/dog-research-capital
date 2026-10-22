# Binary contract (prediction markets)

Pays $1 if an event occurs and $0 otherwise (e.g. "ceasefire holds through Oct 22").
Price p in [0,1] is the market's implied probability. A "Yes" at p costs p and returns 1,
so the expected profit if your true probability is q is q - p per contract.

"No" is the complement: Yes at 0.30 is equivalent to No at 0.70 (on Polymarket these are
separate tokens with separate books; arbitrage keeps them consistent).

Evaluate forecasts with calibration, Brier score, and log score. Each market resolves once,
so independent samples are scarce: expect to need much stricter overfitting controls than
in crypto.
