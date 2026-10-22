# Statistical arbitrage (stat arb)

Not true arbitrage (which is riskless). It is betting that a *relationship between prices*
that usually holds will reassert itself after a deviation. Define a spread
`s = price_A - beta * price_B` (or contract price minus a model's fair value). If `s` is
**mean-reverting** (stationary; for two price series this is called **cointegration**), trade
against large deviations and exit when it reverts. Statistically it is a bet on a
stationary process, so unit-root tests and half-life estimates are the usual tools.

Risks: the relationship breaks (regime change) and you are left holding a trend; costs are
multiplied by the number of legs (a pair trade pays fees on four fills per round trip).

Prediction-market versions, all "price vs something that should equal it":
- complement/exclusive sets whose prices should sum to 1;
- threshold ladders that must be monotone (P(BTC>100k) >= P(BTC>110k));
- the same event on two venues;
- a contract vs a model: a "BTC above K at time T" contract is a **binary (digital) option**,
  so its fair value comes from spot price, time left, and volatility.
