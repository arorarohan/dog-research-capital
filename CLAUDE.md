# Dog Research Capital

Two-person (user + Claude) quant research shop. Goal: research signals, build our own
trading platform, paper-trade on real exchange data, and track made-up funds. Nothing here
ever trades real money.

## Scope and priorities
- Asset classes: crypto first (infra proving ground), prediction markets (Polymarket, Kalshi)
  as the main alpha hunt. Equities much later.
- Latency-sensitive / HFT strategies are out of scope (laptop, home internet).
- Free data only for now. Python first; rebuild hot paths in Rust only when profiling says so.
- Everything runs on a laptop (WSL2). The recorder must also run unchanged on a free cloud VM.

## Plan
The living roadmap is `docs/roadmap.md`. Check it before choosing what to build next, and
update it (tick items, add dates, record decisions) when a milestone lands or priorities change.

## Working rules
- **Commit and push incrementally, as a developer would.** Small, focused commits with
  conventional-style messages (`feat(recorder): ...`, `fix: ...`, `test: ...`, `docs: ...`,
  `chore: ...`). Push to `origin main` after each logical unit of work, not in one big dump at
  the end. Run tests and ruff before committing, chained with `&&` so a failure blocks the commit.
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

## Gotchas
Non-obvious traps (clocks, trade-side semantics, depth-limited books) are in
`docs/gotchas.md`. Read it before touching the recorder, parsers, replay, or timestamps, and
add to it whenever we learn something the hard way.

## Layout
- `src/drc/recorder/` standalone market-data recorder (no deps on the rest of the stack)
- `src/drc/normalize/` raw JSONL -> Parquet (`trades`, `book_events`), per-venue parsers
- `src/drc/market/` order book, replay to top-of-book, 1s bars
- `src/drc/backtest/` ledger, engine, fee models, baseline strategies (see `docs/backtester.md`)
- `src/drc/validate/` ground-truth checks on our data and reconstruction
- `docs/learning/` Rohan's learning library (profile, concept index, notes)
- `config/` TOML configs (what to record, retention)
- `tests/` pytest (asyncio_mode=auto)
- `data/` local recordings (gitignored)

## Commands
- `uv sync` install; `uv run pytest` tests; `uv run ruff check . && uv run ruff format .` lint
- `uv run drc-record --config config/recorder.toml` run the recorder
- `uv run drc-backtest --venue V --symbol S --strategy {hold,random,imbalance}` run a baseline
- `uv run drc-normalize` raw -> Parquet; `uv run drc-bars` Parquet -> 1s bars (`data/norm/bars_1s/`)
