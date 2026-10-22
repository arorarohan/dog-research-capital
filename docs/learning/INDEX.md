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
