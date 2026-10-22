# Dog Research Capital

Two-person (user + Claude) quant research shop. Goal: research signals, build our own
trading platform, paper-trade on real exchange data, and track made-up funds. Nothing here
ever trades real money.

## Scope and priorities
- Asset classes: crypto first (infra proving ground), prediction markets (Polymarket, Kalshi)
  as the main alpha hunt. Equities much later.
- Free data only for now. Python first; rebuild hot paths in Rust only when profiling says so.
- Everything runs on a laptop (WSL2). The recorder must also run unchanged on a free cloud VM.

## Working rules
- **Commit and push incrementally, as a developer would.** Small, focused commits with
  conventional-style messages (`feat(recorder): ...`, `fix: ...`, `test: ...`, `docs: ...`,
  `chore: ...`). Push to `origin main` after each logical unit of work, not in one big dump at
  the end. Run tests and ruff before committing.
- Never commit market data, secrets, or `.env` files (`data/` is gitignored).
- Be skeptical of our own results: paper PnL flatters. Fill models must be conservative, and
  backtests must account for multiple testing. Never silently interpolate across recording gaps.
- Raw capture first, normalize later: the recorder stores exchange messages verbatim with a
  local receive timestamp so parsers can be fixed and re-run on old data.

## Teaching Rohan
Rohan knows probability theory and full-stack dev but is new to markets. Explain market jargon
when first encountered. Before explaining a term, check `docs/learning/INDEX.md`; if it is
`taught`, just use it. When you teach something new, add or update its note under
`docs/learning/concepts/` and its row in the index in the same commit. Keep `profile.md`
current. See `docs/learning/README.md`.

## Gotchas (learned the hard way)
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

## Layout
- `src/drc/recorder/` standalone market-data recorder (no deps on the rest of the stack)
- `src/drc/normalize/` raw JSONL -> Parquet (`trades`, `book_events`), per-venue parsers
- `src/drc/market/` order book, replay to top-of-book, 1s bars
- `src/drc/validate/` ground-truth checks on our data and reconstruction
- `docs/learning/` Rohan's learning library (profile, concept index, notes)
- `config/` TOML configs (what to record, retention)
- `tests/` pytest (asyncio_mode=auto)
- `data/` local recordings (gitignored)

## Commands
- `uv sync` install; `uv run pytest` tests; `uv run ruff check . && uv run ruff format .` lint
- `uv run drc-record --config config/recorder.toml` run the recorder
- `uv run drc-normalize` raw -> Parquet; `uv run drc-bars` Parquet -> 1s bars (`data/norm/bars_1s/`)
