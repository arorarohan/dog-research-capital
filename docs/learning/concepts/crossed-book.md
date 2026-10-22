# Crossed (and locked) book

A real order book can never have best bid >= best ask: such orders would simply match.
If our reconstructed book is crossed, **our reconstruction is wrong** (a missed message or
mis-ordered events), not the market. We use "crossed state rate" as a built-in health check
on replay: it should be 0%.

(Early on it read 56-78% for Coinbase and Kraken, which is how we caught the clock bug
described in CLAUDE.md.)
