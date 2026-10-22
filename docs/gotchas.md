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

## Tooling
- `pkill -f <pattern>` run from the agent's own shell matches that shell (its command line
  contains the pattern) and kills it. Use `pgrep -f "bin/drc-record"` to find the PID, then
  `kill -INT <pid>`.
- Chain test, lint, and commit with `&&`, never `;`, so a failing test blocks the commit.
- `ruff format` rewraps code, so scripted string-replace edits of formatted files can silently
  miss. Check that an edit applied before committing.
