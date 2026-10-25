# NAV, blotter, and the shop's core views

**NAV (net asset value)** is what a fund is worth right now: cash plus positions marked to
market, net of fees. Per-share NAV is the same idea for funds with investors; ours has none,
so NAV is just equity. Think of it as the running value of a portfolio-sized random variable
you are tracking over time. We mark at liquidation value (see [mark to market](mark-to-market.md)).

**Blotter**: the chronological list of orders and fills (time, instrument, side, qty, price,
fee). It is the "request log" of trading; every other number should be derivable from it.

**Positions and exposure**: what we hold, and how much we lose if a given event goes against
us. For prediction markets, exposure is per event (all contracts tied to one outcome are
correlated), not per contract.

**PnL attribution**: splitting profit into gross edge, spread paid, fees, and slippage so we
can see whether a strategy's edge or its costs drove the result.

Why it matters here: Phase 4 is built around an append-only event log from which NAV,
blotter, positions and attribution are all projections. See `docs/phase4-scoping.md`.
