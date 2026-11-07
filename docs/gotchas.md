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
- **A recorder poll that fails must not advance its time window** (Kalshi trades, #20). The
  window used to move forward before the request, so any 429 or timeout that outlasted the
  retries lost that interval's trades for good (2.8-6.7% of trades on 429-heavy days). Anything
  that polls "since the last poll" advances its cursor only after success and dedups by id.
  Compare against the venue's own history (`drc.validate.kalshi`) after changing it.
- **Stamp every channel with `writer.now_ns()`, never `time.time_ns()`** (Kalshi order books did,
  #20): the raw wall clock steps backwards on WSL2 and puts that channel out of receive order,
  and the normalizer then has to sort it in memory.
- **A message with nothing to parse must be counted, not skipped.** Normalization tallies every
  raw line per hour (`<norm>/_accounting`) and `drc-normalize` exits 1 on unparseable lines,
  invalid JSON, or a raw channel nobody declared. Parsers raise on unknown sides/types instead of
  defaulting (before: any non-`BUY` Polymarket change became an ask). A new exchange field or
  channel fails `python -m drc.validate.fields` until it is added to `normalize/fieldmap.py`.
- **Coinbase `sequence_num` is shared by all channels of a connection**: check it on the merged
  stream (`drc.validate.continuity`); one channel alone shows thousands of false gaps.
- **A gap that starts at the same instant on several venues is a local stall** (the process or
  machine paused; 3.5 s on 2026-10-22), not a venue outage. Messages after it are stamped late,
  not lost. Same-venue gaps next to a `disconnect` event are real blind spots.
- **Polymarket replay differs from the exchange's best bid/ask by a tick about 1 time in 500**
  for a few seconds (a `book` snapshot and its `price_change` arrive slightly out of order). Not
  corruption: the next update to the level fixes it. Do not treat a one-tick top-of-book change
  that reverts within seconds as signal.
- **Hour files start mid-stream.** A Parquet hour holds updates for books that were snapshotted in
  an earlier hour; load contiguous hours before replaying (replay drops, and counts, updates for
  symbols with no snapshot yet in the epoch).

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

## Recorder volume
- **Polymarket deadline mode records ~600 `price_change` messages per second** for ~700 tokens
  (about 22 MB per minute, ~1.3 GB per hour uncompressed while the hour is open; hourly zstd
  compression cuts it roughly tenfold). Fine for a one-hour session; do not leave it running for
  days without the `max_total_gb` valve. Stop with SIGINT so the open hour flushes.
- **Size normalization memory by the biggest hour, not the average.** Polymarket price-change hours
  on the deadline universe reach 500-600 MB compressed (about 5 GB of JSON, ~18M rows); the old
  normalizer held every parsed message of the hour in a list and read each file whole, peaking at
  11.7 GB and getting OOM-killed on the 12 GB VM, while earlier laptop hours were 3x smaller and
  fine. It now streams (k-way merge of the time-ordered channel files, lazy zst reads, chunked
  tables): the same hour peaks at 3.5 GB. Two traps remain: Kalshi orderbook files are NOT in
  receive-time order (explicit timestamps), so those hours take the full-sort fallback; and
  `ulimit -v` breaks Go programs (rclone), so cap only the Python step.
- **Polymarket closes a websocket it cannot send to fast enough** (close code 1013 "slow consumer:
  send buffer full"). With ~800 tokens on one connection the VM got ~18 a day (166 between
  2026-10-28 and 11-06), bunched at 00-03 UTC (busiest trading) and at :00-:10 and :30-:40 past
  the hour (twice the other windows): the hourly compression of a ~5 GB price_change hour on the
  VM's slow disk, and the hourly market refresh. Our CPU was ~5% and the socket receive queue
  empty when quiet, so it is bursts, not average load. Fix (#23): raw writes run on a writer
  thread so a disk stall cannot stop the socket reader, and tokens are spread over connections
  of at most `shard_size` (200). Watch `max writer backlog` in the maintenance log line.
- **Epochs are per symbol, not per venue.** Each Polymarket shard's `connect` event lists its
  `assets`, and tokens added to a live shard get a `subscribe` event; a symbol's epoch is the
  latest such event covering it (a `connect` without `assets` still covers the whole venue). So
  replay must reset books per (venue, symbol) on an epoch change, never the whole venue, or one
  shard's reconnect would wipe books on shards that never dropped and that will not resend a
  snapshot. Data recorded before #23 normalizes and replays identically under either rule.

## Books on near-resolved markets
- **Locked books are not replay bugs.** Polymarket tokens of near-resolved markets sit at 99.8c /
  0.2c with bid == ask exactly (seen 2026-10-22: 4 tokens, 1,786 of 558,598 bars; none strictly
  crossed). Spread studies must require ask > bid. `drc-bars` counts these as crossed states.
- **`data/norm` is stale until re-run.** The normalized bars had been built mid-recording and
  covered 20 of the 100 recorded minutes; re-run `drc-normalize` and `drc-bars` before analysis.

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
  post-hoc pooling choices as hypotheses. Count every look in `hypotheses/` (`drc-registry`).
  Correction is not enough: H1's discovery cell survived Bonferroni over all 574 looks and still
  failed its holdout (`docs/findings/2026-11-06-multiple-testing.md`).

- **Thin markets need a long sampling lookback.** Kalshi weather brackets trade a few times a day;
  a 120 s lookback found a trade for only a third of the moments and biased samples toward active
  times. Use hours and record each price's age (`last_trade_ts`).

## Tooling
- `pkill -f <pattern>` run from the agent's own shell matches that shell (its command line
  contains the pattern) and kills it. Use `pgrep -f "bin/drc-record"` to find the PID, then
  `kill -INT <pid>`. The same happens over SSH (`ssh host 'pkill -f drc-normalize'` kills its own
  remote shell: exit 255 or 143, and the job it was meant to stop may or may not die): put the
  commands in a script file, or write the pattern as `[d]rc-normalize`.
- Chain test, lint, and commit with `&&`, never `;`, so a failing test blocks the commit.
  Never pipe the test run through `tail`/`head`: a pipe returns the last command's exit code, so
  a failing pytest does not block the commit. Redirect full output to a file and read parts of it.
  Run `ruff check` and `ruff format --check` too (CI does).
- `ruff format` rewraps code, so scripted string-replace edits of formatted files can silently
  miss. Check that an edit applied before committing.
- **Kalshi's public API 429s on bursts, from any IP.** Firing one request per market at once
  (84 weather brackets plus trades) failed 16% of requests (41 of 255, measured 2026-11-04 from a
  home connection and from the Oracle VM alike), and each failure silently dropped that market's
  book for the cycle. The poller now paces all Kalshi requests to `max_rps` (default 5) and
  retries 429s with `Retry-After`; the same 255 requests then all succeeded. A throttled-and-
  recovered request is logged as a `throttled` meta event, so look for those.
- **Recorder disk caps can delete data nobody has backed up.** `max_total_gb` prunes the oldest
  days of channels that have a retention rule, with no idea whether they were uploaded. Polymarket
  `price_change` is ~4.5 GB/day compressed on the deadline universe, so the old 30 GB cap silently
  deleted days within a week. Size the cap above (days kept locally x daily volume) and sync before
  relying on it.
- **Old `_meta` is needed to normalize new data.** A row's `epoch` comes from the last `connect`
  in the venue's `_meta`, which can be days old (Kalshi logs a connect only at start-up). Delete
  old `_meta` and a re-normalize silently gives epoch 0 to every row and overwrites good archived
  Parquet. `_meta` and `_catalog` are never pruned locally or expired remotely (#41).
