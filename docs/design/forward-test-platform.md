# Design: the forward-test platform (Phase 4, Slices 0-2)

Status: **draft, 2026-10-25**, follows Rohan's decisions in `docs/phase4-scoping.md` section 0.
Goal in one line: run each surviving hypothesis as an automatic paper fund under rules written
down in advance, treating it as if it handled real money, and keep a tamper-evident record
that answers "how much did we put in, how is each fund doing, what is our total PnL".

## 1. Design stance: "could be used with real money"

Rohan's requirement: the test must be real, not theatre. We cannot spend real money, so the
bar is: **if every piece of this were swapped for a live broker connection, the strategy code
and its evidence would not change.** Concretely:

1. **Broker interface boundary.** Strategies emit *target positions or orders* to a `Broker`
   interface. The only implementation is `SimBroker`. The strategy never sees whether it is
   simulated and has no access to future data, recorded outcomes or the sim's internals. No
   credentials, no network order endpoint exists in the repo (decision Q7).
2. **Pessimistic, depth-aware fills.** Buys fill against the *displayed ask ladder* at decision
   time plus a latency delay (the book N seconds later, not the instant of the decision),
   walking levels, partial fills allowed, never more than a fixed share of displayed depth,
   rounded to tick and minimum size, using the market's own fee schedule. No fill against
   one-sided, locked or stale books. A missed fill is a logged outcome, not retried silently.
   (This is stricter than the backtester's top-of-book fill, a known limit in `backtester.md`.)
3. **Pre-trade risk checks** that would also protect real capital: per-event and per-fund
   exposure caps, max order size vs depth, drawdown halt. A halt stops new entries and logs
   why; it never silently flattens. A strategy cannot bypass the checks.
4. **Everything logged, including non-trades.** Every candidate market seen, the rule's
   verdict, and the reason it was skipped (outside band, no depth, risk cap, no book). Without
   this, a forward test silently drops its bad candidates.
5. **No discretion.** Fully automatic (decision Q5). Any manual intervention (pausing a fund,
   killing a strategy) is itself a logged event with a reason, and the fund's report flags it.
6. **Missed sessions are data.** If the job did not run, that day is recorded as missed; we
   never backfill a forward test after the fact.
7. **Capital is locked until resolution.** Return is reported per contract, per dollar
   allocated, *and* per dollar-day deployed, because a 3-day-to-resolution trade ties up cash
   and a "+1.2c" is worth different amounts at different lockup lengths.
8. **Reconciliation.** Each session re-derives positions and cash from the event log and
   asserts they equal the ledger. A mismatch halts the fund; this is the paper analogue of
   reconciling with a broker.
9. **Settlement from the venue's resolved outcome**, recorded verbatim with a timestamp.
   Voids and disputes are event types; a voided market returns cost, a late or reversed
   resolution is logged, never edited.

What this still cannot test (state it on every report): market impact beyond displayed depth,
adverse selection against our visible orders, API failures and rate limits, and our own
operational mistakes. The remedy is a standing haircut and a fill-share cap, plus honesty in
the write-up. A forward test with simulated fills is a stronger test than the backtest, not
proof of a live edge.

## 2. Fund charter: pre-registration

One file per fund, `funds/<fund_id>.toml`, committed to git *before* the first session. Its
hash is stored in the event log at fund creation, so the rules a fund ran under are provable.

Fields:
- `hypothesis`: id, link to the finding, one-paragraph claim.
- `universe`: ex-ante rule (what is enumerated at decision time, with only information known then).
- `entry` / `exit`: the rule; exit is hold to resolution unless stated.
- `sizing`: fixed dollars per trade to start (Kelly-style sizing is a Phase 3 topic).
- `risk`: per-event cap, per-fund exposure cap, drawdown halt.
- `capital`: initial allocation, from the firm treasury (section 4).
- `prediction`: the backtest's predicted per-trade net edge with CI, **written before the
  first trade**. The headline forward-test number is realized minus predicted.
- `evaluation`: minimum trades/events and a date; the kill criterion; the pre-declared test
  and alpha. Before launch, compute the minimum detectable edge for that sample size and write
  it in the charter, so we know when "inconclusive" is the correct verdict.
- `version`: changing a rule creates a *new fund* (or a new version with its own track
  record), never an edited one. This is how a forward test avoids being tuned live.

## 3. Event log (source of truth)

**Append-only, hash-chained.** Every event: `event_id` (sortable), `ts_event` (when it
happened in the world, e.g. fill time), `ts_recorded` (local clock), `fund_id`, `session_id`,
`type`, `payload` (JSON), `prev_hash`, `hash` (over the row plus `prev_hash`). Tampering with
or deleting a row breaks the chain; a verify command checks it. This mirrors the recorder's
raw-first rule: store what happened verbatim, derive views later.

Event types (initial):
`fund_created` (charter hash, git sha), `capital_allocated` / `capital_withdrawn`,
`session_started` / `session_finished` / `session_missed`, `market_observed` (candidate +
verdict + reason), `book_snapshot` (raw payload verbatim, with receive time and venue
timestamps; see `docs/gotchas.md` on clocks), `risk_check`, `order_submitted`, `fill` (price,
qty, fee, levels walked), `order_rejected` / `order_unfilled`, `mark` (liquidation value),
`settlement`, `fund_paused` / `fund_halted` / `fund_closed` (with reason), `reconciliation`.

**Storage choice: SQLite**, single file `data/ledger/events.sqlite`, with triggers that forbid
UPDATE/DELETE. Reasons: tens to thousands of events a day, so no scale problem; transactions
make a session atomic (all of its events or none); a future web app can query it directly;
tests are trivial. Raw book payloads go in a sibling zstd JSONL directory referenced by hash,
to keep the DB small. Derived tables (positions, NAV by day, blotter) are views or rebuilt by
a `rebuild` command; they are never the source of truth.

**This log is irreplaceable evidence** (unlike market data it cannot be re-downloaded), so it
needs a backup plan: see open question Q8 in the scoping doc.

## 4. Firm-level totals ("how much invested, how is each fund doing, total PnL")

A notional **treasury** funds the funds, so the totals are explicit rather than inferred.
Everything is derived from the event log:

| Number | Definition |
|---|---|
| Allocated to fund | sum of `capital_allocated` minus `capital_withdrawn` |
| Fund NAV | cash + positions at liquidation value (bid), net of fees |
| Fund PnL | NAV minus net allocated; split into realized, unrealized, fees, spread cost |
| Deployed | cost basis of open positions; cash drag = allocated minus deployed |
| Fund return | PnL / allocated, and PnL / dollar-days deployed |
| Firm allocated | sum across all funds ever created, **including closed ones** |
| Firm PnL to date | sum of fund PnL across all funds, **including killed funds** |
| Hypotheses tested | count of funds and of research tests, for the multiple-testing deflator |

Two rules: (1) closed/killed funds stay in firm totals forever (no survivorship; a dead fund's
loss is part of what finding the live one cost). (2) Firm PnL is reported alongside the
number of hypotheses behind it, so a winner among many tries is read for what it is.

A daily `firm_snapshot` is a *derived* artifact (stored for convenience, rebuildable from the log).

## 5. First fund: the Polymarket scheduled-end gap

Fund `pm-sched-gap-v1`, from `docs/findings/2026-10-23-scheduled-calibration.md`.
Backtest claim to be written into the charter: buy Yes at 5-60c, 3 days (primary) before the
scheduled end, hold to resolution; holdout net +1.2c per contract at a 1c half-spread, CI
[+0.5, +1.9]; break-even half-spread 2.2c; measured live half-spread in the band ~0.5c on
liquid markets (quiet markets unmeasured).

Charter v0 *proposals* (Rohan to confirm, see Q9-Q11):
- Universe: open binary Yes/No Polymarket markets whose scheduled end is 2.5-3.5 days away
  at session time and which are not ended or in a dispute state. The volume floor must be
  *known at decision time* (trailing volume), fixing the backtest's hindsight filter; we also
  log total volume later to measure how much it mattered.
- Entry: best-ask based. Ask in the 5-60c band (the backtest used last-trade price; live fills
  use the ask, a deliberately stricter test). Buy Yes only.
- Size: fixed dollars per trade, capped at a small share of displayed depth.
- Also log, but do not trade, contracts outside the band, so we can check the gap's shape
  live (including the 40-60c band where the backtest says there is no edge).
- A daily session at a fixed time catches every market that crosses into the window.

Where the live version differs from the backtest on purpose: ask not last trade; ex-ante
volume; depth-limited fills; latency delay. The gap between predicted and realized is the
experiment.

## 6. Build plan

- **Slice 0 (core, no network).** Event log + verify; `SimBroker` with depth-aware fills;
  risk checks; fund/treasury projections; reconciliation. Heavy tests: hash-chain tamper,
  atomic sessions, ledger identity, fill never exceeds depth, no same-instant fills, settle
  and void cases, restart safety (re-running a session is idempotent).
- **Slice 1 (session).** `drc-session --fund pm-sched-gap-v1`: enumerate markets, snapshot
  books (one-shot fetch of the order book; confirm endpoint and rate limits before building),
  run the rule, trade through `SimBroker`, settle resolved positions, write a static HTML
  report. Scheduling mechanism per Q12.
- **Slice 2 (treasury and reports).** Firm page: allocated, NAV, PnL per fund, firm total,
  predicted vs realized with the power note.
- **Later.** Hypothesis/run registry; a real web app on Rohan's domain reading the same
  SQLite log; remote viewing and auth.

## 7. Open questions

See `docs/phase4-scoping.md` section 6b (Q8-Q12) for explanations and options.
