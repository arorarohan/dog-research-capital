# Gotchas

Hard-won lessons about this project's data, tooling, and environment. Read before touching
the recorder, parsers, replay, or anything that depends on timestamps or trade direction.
When we get bitten by something non-obvious, add it here (newest at the bottom of its
section) with the symptom, the cause, and the fix. Keep CLAUDE.md brief; this file can grow.

## Data and timing
- **The WSL2 wall clock is unreliable**: it runs fast (~6% against Coinbase's server clock)
  and steps backwards by over a second roughly every minute. `RawWriter.now_ns()` therefore
  guarantees strictly increasing `ts_recv` so arrival order is preserved, but absolute
  `ts_recv` can be off by ~1s and must not be used for latency or cross-venue alignment. Use
  exchange timestamps (`ts_exch`) for that. Sorting by anything other than arrival order once
  produced 56-78% crossed books.
- **Trade `side` in normalized tables is the aggressor side.** Coinbase labels the maker side
  and is flipped in the parser. Re-verify with `python -m drc.validate.trade_side` if a
  parser changes.
- **Depth-limited books (Kraken, depth 100) must be trimmed client-side**; the exchange never
  sends removals for levels that fall out of range.
- Validation tools live in `src/drc/validate/`; see `docs/validation.md`. A crossed-book rate
  above 0% means reconstruction is broken.

## Fees
- **Polymarket fees are per market, and the docs table is only a summary.** Gamma returns
  `feeType` and `feeSchedule` {rate, exponent, takerOnly, rebateRate} on every market; trust
  those over the category table. Seen 2026-10-23: `zero_fees` 0, `sports_fees_v2` 3% (docs
  table says sports 5%), `sports_fees_v3` 5%, `sports_fees_nfl_cfb_oct26` 3%, `crypto_fees_v2`
  7%, `finance_prices_fees` and `politics_fees` 4%, `economics_fees` 5%, `general_fees` 5%.
  Gamma's `category` field is null on every market, so the category table is a fallback only.
- **Gamma's fee schedule is the market's current one, not its history.** Fees were switched on
  for many market types during 2026; old closed markets read `feesEnabled: false`. A market
  whose fees were enabled mid-life reads as fee-bearing for its whole life, which overstates
  cost for early fills. Be conservative, and say so, when a study spans the switch-on.
- **Old markets-table rows have null fee columns** (written before capture). Null means
  unknown, not zero: `fee_source` falls to the category/default rate. Re-run
  `drc-backfill polymarket-general` (newest fetch wins) to fill them.
- **Neither Coinbase nor Kraken exposes fees per pair in public data** (Kraken `AssetPairs`
  `fees` arrays are empty), and Coinbase's fee pages block scripts. Their schedules are
  hand-copied tier tables in `fees.py`; recheck them when a "fees lowered" banner shows up
  (per secondary reports, Coinbase restructured its tiers on 2026-09-16 and Kraken on 2026-07-09).

## Historical APIs
- **Kalshi 429s at ~4 requests/sec sustained.** `backfill/http.py` spaces requests and
  retries with backoff; keep `min_interval` >= 0.25s.
- **Kalshi busy markets have huge trade histories** (one 15-minute BTC market: ~34k trades,
  34 pages, ~9s). Never download tick history of busy series; sample prices at chosen moments
  with `min_ts`/`max_ts` windows (`backfill/kalshi.py`).
- **Kalshi splits data across tiers**: recent in live endpoints, older under `/historical/*`
  (boundary at `/historical/cutoff`). Loaders read both and tag rows with `tier`.
- **Polymarket `prices-history` returns nothing for resolved markets with `interval=max`**;
  use explicit `startTs`/`endTs` with `fidelity=1`. Points exist only for minutes with activity.
- **Polymarket `prices-history` for old resolved markets only returns data at coarse fidelity**
  (12h points worked; 10-240 min returned empty, not an error) with `interval=max`; explicit
  `startTs`/`endTs` windows are limited to short spans. The general loader walks a fidelity
  ladder. Gamma keyset pages cap at 100 markets and continue with `after_cursor=<next_cursor>`.
- **Selection bias in "largest resolved markets"**: the top of the list is dominated by
  multi-outcome events (elections, tournaments) where most Yes/No legs resolve No and legs of
  one event are strongly dependent. Cluster by `event_id` before trusting any calibration number.
- **Polymarket and Kalshi resolve similar crypto markets with different oracles** (Chainlink
  TWAP vs CF Benchmarks BRTI average), so the same question can resolve differently.
- **Binance spot timestamps switched from milliseconds to microseconds on 2025-01-01**; the
  loader detects units by magnitude. `data.binance.vision` is reachable from the US even though
  trading endpoints are geo-blocked.
- Polars infers all-null columns (e.g. Kalshi `token_id`) as the Null type; always cast to the
  declared schema before merging frames.

## Research pitfalls (each of these produced a wrong or misleading number once)
- **Never anchor a horizon to the realized resolution time.** "N days before close" using a
  market's actual close leaks the outcome whenever outcome and close time are linked (events
  that happen close their markets early). Use the scheduled end date, known in advance. This
  manufactured a fake +3-8 point "mispricing" in Polymarket general markets
  (`docs/findings/2026-10-22-first-look.md`).
- **Define the universe before seeing outcomes.** "Largest resolved markets by total volume" is
  chosen with hindsight and over-represents multi-outcome events with dependent legs.
- **Bootstrap intervals collapse when every outcome in a bin is identical** (all wins). The
  evaluation tool uses an exact Clopper-Pearson-style bound there.
- **Mixed price sources have a basis.** Kalshi's BRTI index is ~13 bp below Binance BTCUSDT;
  measure strike and spot on the same source.
- **History that ends before the data you join it to silently serves stale values.** The
  digital dataset now drops rows whose spot candle is more than 150 s old.
- **Many CIs => some false positives.** Pre-declare tests, report every combination, and treat
  post-hoc pooling choices as hypotheses.

- **Thin markets need a long sampling lookback.** Kalshi weather brackets trade a few times a day;
  a 120 s lookback found a trade for only a third of the moments and biased samples toward active
  times. Use hours and record each price's age (`last_trade_ts`).

## Tooling
- `pkill -f <pattern>` run from the agent's own shell matches that shell (its command line
  contains the pattern) and kills it. Use `pgrep -f "bin/drc-record"` to find the PID, then
  `kill -INT <pid>`.
- Chain test, lint, and commit with `&&`, never `;`, so a failing test blocks the commit.
  Never pipe the test run through `tail`/`head`: a pipe returns the last command's exit code, so
  a failing pytest does not block the commit. Redirect full output to a file and read parts of it.
  Run `ruff check` and `ruff format --check` too (CI does).
- `ruff format` rewraps code, so scripted string-replace edits of formatted files can silently
  miss. Check that an edit applied before committing.
