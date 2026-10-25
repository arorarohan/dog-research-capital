# Walking the book, depth, and fill share

A displayed book is a ladder: 100 shares offered at 32c, 400 at 33c, and so on. A buy order of
150 shares does not get 32c for all of it: it **walks the book**, taking 100 at 32c and 50 at
33c, so its average price is 32.33c. The bigger the order relative to displayed depth, the
worse the average price (this is the visible part of market impact).

Our simulated broker adds two pessimistic rules a backtest on top-of-book skips: it only walks
levels at or better than the order's **limit price**, and it takes at most a fixed **share of
the displayed depth** (default 25%), because displayed size can vanish when you try to hit it
and because real orders move the book.

Why it matters here: a forward-test fill that ignores depth flatters the strategy exactly where
our candidate trades, thin markets days before a deadline. See `src/drc/forward/broker.py`.
