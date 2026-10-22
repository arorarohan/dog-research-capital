# Concept index

Status: `taught` = explained to Rohan; `new` = not yet explained (explain on first use).

| Concept | Status | Gist |
|---|---|---|
| [order book](concepts/order-book.md) | taught | Standing buy/sell orders at each price; the market's state |
| [bid, ask, spread](concepts/bid-ask-spread.md) | taught | Best buy price, best sell price, and the gap between them |
| [L2 data vs trades](concepts/l2-vs-trades.md) | taught | Full book depth versus only executed transactions |
| [snapshot and diff](concepts/snapshot-and-diff.md) | taught | How exchanges stream a book: one full copy, then changes |
| [maker and taker](concepts/maker-taker.md) | taught | Resting orders versus orders that cross the spread |
| [market making](concepts/market-making.md) | taught | Quoting both sides to earn the spread; main risk is adverse selection |
| [binary contract](concepts/binary-contract.md) | taught | Pays $1 if an event happens, else $0; price is a probability |
| [slippage and fill realism](concepts/slippage-and-fills.md) | taught | Why paper PnL flatters and how we stay conservative |
| [arbitrage](concepts/arbitrage.md) | taught | Locking in profit from inconsistent prices |
| [funding rate, basis, carry](concepts/carry.md) | new | Perpetual-futures payments and cash-and-carry trades |
| [adverse selection](concepts/adverse-selection.md) | new | Your fills skew toward trades that then go against you |
| [parquet](concepts/parquet.md) | taught | Columnar, typed, compressed table files; SQL-able with DuckDB but not SQL itself |
| [aggressor side](concepts/aggressor-side.md) | taught | Which party crossed the spread; basis of order-flow signals; venues label it inconsistently |
| [crossed book](concepts/crossed-book.md) | taught | Bid >= ask is impossible; seeing it means our replay is broken |
| [basis point](concepts/basis-point.md) | taught | 0.01%; the unit costs and edges are quoted in |
| [mark to market](concepts/mark-to-market.md) | taught | Valuing positions at current prices; liquidation value (bid for longs) is the honest version |
| [lookahead bias](concepts/lookahead-bias.md) | taught | Using future information in a backtest; how our engine prevents it |
| [immediate-or-cancel](concepts/immediate-or-cancel.md) | taught | Market orders that fill now or are cancelled; our fill model uses top-of-book size only |
| [strategy families](concepts/strategy-families.md) | taught | Taxonomy of strategies by edge source and holding period; costs set the horizon |
| [statistical arbitrage](concepts/statistical-arbitrage.md) | taught | Betting a price relationship reverts; cointegration; PM versions incl. digital-option fair value |
| [calibration](concepts/calibration.md) | taught | Do 70% contracts win 70% of the time? Gap = frequency - price; judge on clustered events |
| [Brier score and log loss](concepts/brier-score.md) | taught | Proper scores for probability forecasts; compare to the constant base-rate forecast |
| [basis between price sources](concepts/index-basis.md) | taught | USDT vs USD index offset (~13 bp) that breaks naive cross-source models |
| [multiple comparisons](concepts/multiple-comparisons.md) | taught | Many tests => some false positives; pre-declare, correct, confirm out of sample |
