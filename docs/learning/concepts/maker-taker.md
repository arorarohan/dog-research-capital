# Maker and taker

- **Maker**: places a resting order that adds liquidity to the book (waits to be hit).
- **Taker**: places an order that matches immediately, removing liquidity (crosses the spread).

Exchanges usually charge takers more (and sometimes pay or discount makers). Makers earn
the spread but are not guaranteed a fill; takers are guaranteed a fill but pay for it.
Our fill simulator must model both honestly.
