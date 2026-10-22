# Aggressor (taker) side

Every trade has two parties: the **resting** order that was already in the book and the
**aggressor** that arrived and crossed the spread to match it. A trade is a "buy" if the
buyer was the aggressor (lifting the ask), a "sell" if the seller was (hitting the bid).

Why it matters: aggressor-labelled volume is the raw material of **order flow** signals
("net buying pressure"). Aggressors pay the spread to get immediacy, so their direction
carries information that resting orders don't.

Trap: venues disagree on what their `side` field means. Coinbase labels the *resting* side,
Kraken/Polymarket/Kalshi label the aggressor. We normalize everything to aggressor side and
verify it empirically (`python -m drc.validate.trade_side`): buys should print above mid.
