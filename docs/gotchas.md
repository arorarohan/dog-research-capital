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

## Tooling
- `pkill -f <pattern>` run from the agent's own shell matches that shell (its command line
  contains the pattern) and kills it. Use `pgrep -f "bin/drc-record"` to find the PID, then
  `kill -INT <pid>`.
- Chain test, lint, and commit with `&&`, never `;`, so a failing test blocks the commit.
- `ruff format` rewraps code, so scripted string-replace edits of formatted files can silently
  miss. Check that an edit applied before committing.
