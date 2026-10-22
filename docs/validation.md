# Validation log

How we know the data pipeline is right. Re-run these whenever a parser or replay changes.

| Check | Command | Result (2026-10-22, ~15 min of live data) |
|---|---|---|
| Kraken book vs exchange CRC32 checksums | `python -m drc.validate.kraken data/raw` | 100% match on BTC/ETH/SOL (36k+ updates), after depth-100 trim |
| Crossed-book rate after replay | `drc-bars` warns if > 0 | 0% on Coinbase, Kraken, Polymarket, Kalshi |
| Trade side vs prevailing mid | `python -m drc.validate.trade_side` | Kraken, Polymarket, Kalshi labels are aggressor side; Coinbase's is maker side (flipped in parser) |

## Bugs these checks caught
1. **Backward-stepping clock**: sorting by wall-clock receive time scrambled events (crossed
   rate 56-78%). Fixed by strictly increasing `ts_recv` in the writer.
2. **Kraken depth**: levels outside the subscribed depth are never removed by the exchange.
   Fixed by trimming in replay.
3. **Coinbase trade side inverted** relative to every other venue. Fixed in the parser.
4. **Validator formatting**: tiny quantities arrive as `4.5e-07`; checksum strings need
   fixed-point.

## Known limits
- Kalshi books are REST samples every 15s, not a full event stream.
- Checksum validation only covers Kraken; Coinbase and Polymarket rely on the crossed-book
  invariant and the structure of their snapshots.
- Bars are timestamped by exchange time where available; Kalshi books fall back to receive time.
