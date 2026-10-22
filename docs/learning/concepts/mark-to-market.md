# Mark to market, and liquidation value

Marking a position means valuing it at a current price. **Mid** marking (halfway between bid
and ask) is the usual convention but flatters you: you could not actually sell at the mid.
**Liquidation value** marks longs at the bid and shorts at the ask, i.e. what closing the
position would really fetch. Our backtester uses liquidation marking, so opening a position
immediately shows a small loss equal to the half-spread you crossed.
