# Dog Research Capital

Quant research shop run by Rohan and Chris, each working with their own Claude Code agent.
Goal: research signals, build our own trading platform, paper-trade on real exchange data, and
track made-up funds. Nothing here ever trades real money.

## Scope and priorities
- Asset classes: crypto first (infra proving ground), prediction markets (Polymarket, Kalshi)
  as the main alpha hunt. Equities much later.
- Latency-sensitive / HFT strategies are out of scope (laptop, home internet).
- Free data only for now. Python first; rebuild hot paths in Rust only when profiling says so.
- Everything runs on a laptop (WSL2). The recorder must also run unchanged on a free cloud VM.

## Plan
The living roadmap is `docs/roadmap.md` (big picture); GitHub issues are the units of work.
Check both before choosing what to build next. Update the roadmap (tick items, add dates, record
decisions) in the PR that lands a milestone or changes priorities.

## Team workflow (read `docs/workflow.md` before your first git command)
- **GitHub issues are the project tracker.** Every task is an issue assigned to Rohan
  (`arorarohan`) or Chris (`BigManChris`). Work on issues assigned to your user. Agents may open
  issues without asking but must tell their user, with the link, in the same reply.
- **All work happens on a branch, merged by PR.** Before starting: `git switch main &&
  git pull --ff-only && git switch -c <type>/<issue#>-<slug>`. Never commit on or push to `main`;
  a hook (`.claude/hooks/guard_main.py`) blocks it. PR bodies start with `Closes #N`.
- **Only Rohan merges** (merge commits, no squash, branches kept). Agents never merge unless
  Rohan says so in that session.
- **Keep up with main** by `git merge origin/main` into your branch, not rebase. No force-push
  except `--force-with-lease` on your own branch; no destructive git commands without asking.
- **Research decisions:** approving a result (declaring an edge, promoting to forward test or
  paper fund) needs both Rohan's and Chris's sign-off. Killing your own hypothesis needs none;
  killing the other person's needs their agreement.
- **Personal stuff stays local:** each person's preferences and learning notes go in
  gitignored `CLAUDE.local.md` / `.local/`, never in `CLAUDE.md`.

## Working rules
- **Commit and push incrementally, on your branch.** Small, focused commits with
  conventional-style messages (`feat(recorder): ...`, `fix: ...`, `test: ...`, `docs: ...`,
  `chore: ...`) ending with `Refs #N`. Push the branch after each logical unit of work. Run
  tests and ruff before committing, chained with `&&` so a failure blocks the commit.
- Never commit market data, secrets, or `.env` files (`data/` is gitignored).
- Be skeptical of our own results: paper PnL flatters. Fill models must be conservative, and
  backtests must account for multiple testing. Never silently interpolate across recording gaps.
- Raw capture first, normalize later: the recorder stores exchange messages verbatim with a
  local receive timestamp so parsers can be fixed and re-run on old data.
- Explain market jargon when first using it unless the user's `CLAUDE.local.md` says they know it.

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
- `docs/workflow.md` team git/PR/issue rules; `docs/onboarding.md` new-contributor setup
- `.claude/` shared Claude Code settings and hooks (`settings.json`, `hooks/guard_main.py`)
- `config/` TOML configs (what to record, retention)
- `tests/` pytest (asyncio_mode=auto)
- `data/` local recordings (gitignored)

## Commands
- `uv sync` install; `uv run pytest` tests; `uv run ruff check . && uv run ruff format .` lint
- `uv run drc-record --config config/recorder.toml` run the recorder
- `uv run drc-backtest --venue V --symbol S --strategy {hold,random,imbalance}` run a baseline
- `uv run drc-normalize` raw -> Parquet; `uv run drc-bars` Parquet -> 1s bars (`data/norm/bars_1s/`)
