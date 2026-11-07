# Validation log

How we know the data pipeline is right, and what we have **not** verified. Re-run these whenever a
parser, replay, or recorder changes. Raw expires from the archive after 15 days (`docs/sync.md`),
and several checks need raw, so a result here is the evidence that outlives it.

Verified on: laptop recording 2026-10-22 18:44-20:25 UTC (all four venues, 313k Coinbase, 2M Kraken,
1.2M Polymarket, 871k Kalshi messages), laptop forward session 2026-10-25, and VM forward-config
data from 2026-11-04 (Polymarket hours 00-01, Kalshi full day) and 2026-11-06 (Kalshi 00-10h).
Numbers below are from the PR for #20 (2026-11-06).

## What is verified, per venue and channel

"Fields" = every raw field is accounted for (`drc.normalize.fieldmap`, enforced by
`drc.validate.fields`). "Accounted" = every raw message is parsed or counted (`accounting.py`).
"Truth" = checked against something the exchange tells us independently of our parser.

| Venue / channel | Fields | Accounted | Truth check | Not verified |
|---|---|---|---|---|
| Coinbase `l2_data` | yes | yes | `sequence_num` continuous: 313,911 msgs (all channels), 0 gaps. Crossed-book 0% | book depth vs REST snapshot; sequence only exists in raw |
| Coinbase `market_trades` | yes | yes | trade ids contiguous: 59,122 trades, 0 missing (replay snapshots included). Side checked vs mid (maker label, flipped) | trade size/price vs REST |
| Kraken `book` | yes | yes | CRC32 checksum 100% match on 1.95M updates (BTC 706,627, ETH 922,039, SOL 325,192) | checksum is verified by `validate.kraken`'s own replay, not by `drc.market.replay`; depth > 100 untested |
| Kraken `trade` | yes | yes | ids contiguous except 29 missing, all across the one recorded reconnect (2.2 s). Side = aggressor | |
| Polymarket `book` + `price_change` | yes | yes | replay vs exchange best bid/ask: 99.79% of 2,405,032 items (10-22), 99.79% of 4,058,234 (10-25), 99.75% of 16.6M (VM 11-04). Replay vs full `book` snapshots: 99.86%, 99.93%, 99.85% | the ~0.2% mismatches are transient (see below); sharded-connection data (after #23) has no ground-truth run yet; no REST book comparison (needs live data) |
| Polymarket `last_trade_price` | yes | yes | side vs mid: 10,129 of 10,526 buys print above mid, 1,307 of 1,452 sells below | completeness (no sequential id, no history API check); `fee_rate_bps` dropped |
| Polymarket `tick_size_change` | yes | ignored | none | **not normalized at all** (#36) |
| Kalshi `orderbook` | yes | yes | crossed-book 0% | REST samples every 15-60 s of current state; there is no history to compare with. Empty books (10% of samples) are valid, not errors |
| Kalshi `trades` | yes | yes | vs Kalshi REST history over our window: **2.8% missing** (10-22, 429 bursts), **6.7%** (VM 11-04), **1.3%** (VM 11-06, after #24). 0 field mismatches on the rest | the remaining loss is unexplained (#35); `is_block_trade` false on every trade seen |

Re-run commands (all read raw, none write):

```
uv run python -m drc.validate.fields    data/raw data/raw_forward   # undeclared fields/channels
uv run python -m drc.validate.kraken    data/raw                    # CRC32 checksums
uv run python -m drc.validate.polymarket data/raw                   # replay vs exchange best bid/ask
uv run python -m drc.validate.continuity data/raw                   # Coinbase sequence, trade-id gaps
uv run python -m drc.validate.timing    data/raw                    # clock steps, skew, gaps
uv run python -m drc.validate.kalshi    data/raw 12                 # trades vs REST (network, slow)
uv run python -m drc.validate.trade_side data/norm                  # side label vs mid
uv run drc-normalize --raw data/raw --norm data/norm                # fails (exit 1) on unexplained loss
```

## 1. Field accounting

`src/drc/normalize/fieldmap.py` lists, for every (venue, channel), each raw field as kept,
dropped (with why that is safe), or routing, plus the channels no parser reads. `validate.fields`
exits 1 on any field or channel not listed, so an exchange adding a field cannot vanish silently.

Answering the issue's example: **Polymarket `price_change` carries `best_bid` and `best_ask`** (and
a `hash`); we drop them. They are redundant with the replayed book (checked above), and we use them as
ground truth on raw only. Drops that need a decision before raw expires (#36): `best_bid/best_ask`,
Kraken `checksum`, Coinbase `sequence_num`, Polymarket `tick_size_change` and `tick_size`, and the
per-trade `fee_rate_bps` (`'0'` on every message seen; fees come from the catalog).
Kalshi trade fields we drop were checked on all raw: `no_price_dollars == 1 - yes_price_dollars`,
`taker_outcome_side == taker_side`, `taker_book_side` follows from the side.

## 2. Nothing silently skipped

Every raw line of every parsed channel is counted per hour into
`<norm>/_accounting/venue=V/date=D/hour=HH.json` as `parsed` (rows produced), `empty` (valid message
with no rows, e.g. `events: []`), `unparseable` (parser raised), or `bad_lines` (not JSON); the
sidecar also lists raw channels with data that no parser reads and nobody declared. `unparseable`,
`bad_lines`, undeclared channels, and a mismatch between rows parsed and rows written make the hour
**fail loudly**: logged as `UNEXPLAINED LOSS`, `normalize_all` raises `UnexplainedLoss` after the other
hours finish, `drc-normalize` exits 1, and a later run re-reports it from the sidecar. Hours
normalized before this change have no sidecar and are redone once.

Parsers no longer guess: an unknown enum (side, snapshot/update type) or a Kalshi response that is
not an order book raises instead of becoming a default. Before, `side != BUY` meant ask, any
non-`bid` side meant ask, and a Kalshi error body became an empty book.

Result: 12 laptop venue-hours and 26 VM venue-hours, **0 unparseable, 0 invalid lines, every raw
message balanced**.

## 3. Ground truth: results and what they mean

- **Polymarket replay vs the exchange.** About 0.2% of `price_change` items show a replayed top of
  book one tick off the exchange's `best_bid/best_ask`, and 0.07-0.15% of full `book` snapshots differ at
  a level. On 2026-10-22, 35 snapshots differed (39 levels, always a level size, never a missing level);
  each of the 39 levels was overwritten by a later update, 36 within 5 s, so the error does not
  accumulate. Likely cause: the snapshot (sent after a trade) and its `price_change` arrive
  slightly out of order. Not proven. Consequence: top-of-book at a given instant can be off by a tick
  for a few seconds about 1 time in 500.
- **Coinbase.** `sequence_num` is one counter per connection shared by every channel; checking one
  channel alone shows 27,566 false gaps. Merged, it is continuous.
- **Kalshi trades** lost trades through a recorder bug (fixed, see Bugs) and still lose ~1.3% for a
  reason we do not know (#35). Anything that counts Kalshi trades (volume, flow) is biased low until
  then; 10-22 data in particular is missing 2.8%, mostly during the 20:00-20:06 UTC burst.

## 4. Timestamps

`drc.validate.timing` on 2026-10-22: receive time never goes backwards on any channel except Kalshi
`orderbook` (28 of 21,157; fixed). Receive minus exchange timestamp has a median of 1.0-1.2 s on Coinbase,
Kraken and Polymarket (p01 0.14-0.22 s, p99 1.9-6.8 s, under 0.05% negative): the laptop clock runs
about 1.1 s ahead of all three venues, consistently, so cross-venue alignment must use `ts_exch`.
Kalshi trades arrive a median 8 s after `created_time` (REST polling): use `ts_exch`, not `ts_recv`.
Gaps of 2 s or more on busy channels: Coinbase 1, Kraken 2 (one is the recorded reconnect), Polymarket 2.
The Coinbase, Kraken, and Polymarket gaps starting at unix 1792697289.7 coincide (3.5 s on each): a
**local stall**, not a venue problem. Coinbase sequence numbers are continuous across it, so the
messages were delayed (stamped late), not lost. Gaps are reported, never filled.

## 5. Determinism and restarts

Re-running `drc-normalize` on the same raw gives **byte-identical Parquet** (24 files, SHA-256 equal across
two runs; also a unit test on the fixtures). An hour that spans a restart is split into epochs
(Kraken hour 20 on 2026-10-22: two epochs, and in the second every symbol starts with a `reset`).
Replay needs contiguous hours: hour files begin mid-stream (updates before that hour's snapshot), so
a single hour loaded alone starts unsynced; replay drops those updates and counts them.

## 6. Fixtures

`tests/fixtures/raw/` holds real raw messages per venue and channel (first 3-4 of each, plus an
array-form Polymarket book and an empty Kalshi book; books trimmed to 12 levels). `tests/test_fixtures.py`
checks their normalized output against values derived independently from the raw JSON. Regenerate
with `tests/fixtures/make_fixtures.py`. This is the seed of the verbatim sample archive from #15.

## Bugs these checks caught
1. **Backward-stepping clock**: sorting by wall-clock receive time scrambled events (crossed
   rate 56-78%). Fixed by strictly increasing `ts_recv` in the writer.
2. **Kraken depth**: levels outside the subscribed depth are never removed by the exchange.
   Fixed by trimming in replay.
3. **Coinbase trade side inverted** relative to every other venue. Fixed in the parser.
4. **Validator formatting**: tiny quantities arrive as `4.5e-07`; checksum strings need
   fixed-point.
5. **Kalshi books used the raw wall clock** instead of the writer's monotonic stamp (28 of 21,157
   going back in time), which also forced the normalizer to sort that channel in memory. Fixed.
6. **Kalshi trade polling lost its window on any failed poll** (the window advanced before the
   request): 5,917 of 211,724 trades missing in the 429 burst on 2026-10-22. Fixed; a poll that hits the
   page cap now records `trades_truncated`. Residual loss: #35.
7. **Parsers silently defaulted** on unknown sides/types and on non-book Kalshi responses, and raw
   channels with no parser were skipped without a trace. Now loud (section 2).

## Known limits
- Kalshi books are REST samples every 15-60 s, not a full event stream.
- Bars are timestamped by exchange time where available; Kalshi books fall back to receive time.
- Ground-truth fields are dropped from Parquet (#36), so replay checks need raw: run them within 15 days.
- Not run on Polymarket data recorded with the sharded connections (#23, from 2026-11-06).
- Samples are short (1.7 h laptop, 2 h VM Polymarket); rare message types may be missing. The field
  check fails if one appears later, which is the point.
