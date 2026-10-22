# Basis between price sources

The "same" asset has different prices on different sources. Binance quotes BTC against USDT
(Tether); Kalshi settles on a USD index (CF Benchmarks BRTI). In our data the Kalshi strike
sits a steady ~13 bp below Binance BTCUSDT. A model that mixes the two without correcting for
this is biased by a constant, which against a typical 15-minute move of ~25 bp is huge.
Fix: measure both the strike and the spot on the same source (basis-neutral).
