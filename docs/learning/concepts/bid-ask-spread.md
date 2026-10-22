# Bid, ask, spread

- **Bid**: highest price anyone is currently willing to pay.
- **Ask** (or offer): lowest price anyone is currently willing to sell at.
- **Spread** = ask - bid. **Mid** = (bid + ask) / 2, the usual "fair price" proxy.

Buying instantly costs the ask, selling instantly gets the bid, so a round trip loses about
one spread. Any strategy that trades often must earn more than the spread to survive.

Prediction markets: on a $0-$1 contract, bid 0.69 / ask 0.70 means the spread is 1 cent,
and mid 0.695 reads as a ~69.5% probability.
