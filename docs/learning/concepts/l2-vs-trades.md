# L2 data vs trades

- **Trades** (the tape): executed transactions only, each with price, size, time, side.
- **L2** ("level 2"): aggregate quantity resting at each price level of the book.
  **L3** would list individual orders.

Trades tell you what happened; L2 tells you what could happen next (visible supply and
demand). L2 is much bigger, and for most venues free history doesn't exist, which is why we
record it ourselves.
