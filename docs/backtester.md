# Backtester design and limits

Code: `src/drc/backtest/`. Run: `uv run drc-backtest --venue kraken --symbol BTC-USD --strategy random`.

## Model
- Decisions are made on 1-second bars; the strategy sees only the past and present.
- Market orders only, filled one bar later at the worse of the decision-time and fill-time
  book (ask for buys, bid for sells) plus slippage, capped by displayed top-of-book size,
  immediate-or-cancel. Taker fees per venue (`fees.py`).
- Equity is marked at liquidation value (longs at bid, shorts at ask).
- A recorder reconnect (new `epoch`) cancels pending orders; positions are carried at the last
  mark and the gap is logged.
- Ledger: signed positions, average cost, realized PnL, fees; the identity
  `equity = cash0 + realized - fees + unrealized` is tested.

## Tests that guard honesty
- No same-bar fills; strategy history excludes the current bar.
- Flat market: a random trader's loss equals spread + slippage + fees to 6 digits.
- Random trader loses money in a driftless noisy market across seeds.

## Known limits (do not forget when reading results)
1. **Fee schedules are placeholders** (Coinbase 60 bp, Kraken 40 bp taker). Verify before
   trusting anything near break-even. At these levels fees are 100-1000x the quoted spread on
   BTC/ETH, so short-horizon taker strategies cannot work; edge must come from lower fee
   tiers, maker rebates, or longer horizons.
2. **Top-of-book size only.** Bars carry the best level's size, so a $9.5k buy-and-hold on
   Kraken BTC fills ~$480. Real orders would walk the book. Fix: carry a depth ladder in bars
   or drive fills from replayed book states.
3. **No passive (maker) orders.** No defensible queue-position model yet.
4. **Latency is "one bar" (1s)**, coarse. Fine for slow strategies, wrong for fast ones.
5. **Samples so far are minutes long.** Nothing here is evidence; it is plumbing.
6. Prediction-market settlement exists in the ledger (`settle`) but nothing feeds outcomes yet.
