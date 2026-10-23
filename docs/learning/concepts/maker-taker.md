# Maker and taker

- **Maker**: places a resting order that adds liquidity to the book (waits to be hit).
- **Taker**: places an order that matches immediately, removing liquidity (crosses the spread).

Exchanges usually charge takers more (and sometimes pay or discount makers). Makers earn
the spread but are not guaranteed a fill; takers are guaranteed a fill but pay for it.
Our fill simulator must model both honestly.

## Example: Polymarket (verified 2026-10-23)
Only takers pay: fee = shares x rate x p x (1 - p), where the rate is stated on each market
(0 to 7%: crypto 7%, sports 3-5%, politics/finance 4%, many markets 0). Makers pay nothing and
split a rebate (15-25% of the fees) paid daily. Holding to resolution costs no exit fee.
On Coinbase and Kraken the maker/taker gap is a flat percentage that falls with 30-day volume
(entry tiers: Coinbase 0.50% / 0.90%, Kraken 0.40% / 0.80%).
