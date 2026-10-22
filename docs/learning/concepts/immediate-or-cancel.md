# Immediate-or-cancel (IOC) and market orders

A **market order** buys or sells now at the best available prices, walking up (buy) or down
(sell) the book until filled. **IOC** means any part not filled immediately is cancelled
rather than left resting.

Our simulator fills market orders as IOC against the *top-of-book size only*, which is
conservative for large orders (a real order would walk deeper levels at worse prices). See
docs/backtester.md for the planned improvement.
