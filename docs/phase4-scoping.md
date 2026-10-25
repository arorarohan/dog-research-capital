# Phase 4 scoping: what "platform and UI" should mean for us

Status: **draft for Rohan's decisions** (2026-10-25). Nothing here is built. Roadmap text for
Phase 4 was written before we had any research results; this doc re-derives it from where we
actually are. Open questions are in section 6, each with an explanation.

## 0. Decisions so far (Rohan, 2026-10-25)
1. Purpose: better decisions first, learning how a shop runs second.
2. Pull a thin forward-test slice ahead of Phase 3: **yes**.
3. UI: a real web app, but as a **later phase**; static reports first. Rohan has a domain for it.
4. **One fund per hypothesis**, plus a firm-level ledger of total invested, per-fund performance
   and overall PnL to date (`docs/design/forward-test-platform.md` section 4).
5. Paper trading is **fully automatic**; no manual approvals.
6. Viewing away from the laptop is wanted, later.
7. Simulation-only by construction, **but built and judged as if it could handle real money**:
   the same strategy code and risk checks a live system would need, pessimistic fills, nothing
   discretionary (design doc section 1).

Q1-Q7 below are answered by this list. New questions are in section 6b.

## 1. Where we actually are

- Phase 0-2 are mostly done: recorder, historical data layer, evaluation toolkit, four written
  findings. Verdict so far: no edge in 15-minute contracts; weather brackets calibrated (no edge
  at a 2c half-spread); **one candidate survives**: the 5-60c scheduled-end gap on Polymarket
  (+1.2c net, holdout, primary test passed 2026-10-25), with spread and volume filter
  unresolved. Its documented next step is a **forward test**.
- Phase 3 (settlement-aware engine, sizing, feature store, live paper sessions) is not started.
- We have ~1 hour/day of live time, no real money, and a two-person team. The "users" of any
  UI are Rohan and Claude.

Implication: the roadmap's Phase 4 bullets (fund NAVs, positions, blotter) describe the view of
a shop that already has running strategies. We have one *candidate*. A dashboard of empty funds
would be decoration; a dashboard that stops us from fooling ourselves would be valuable.

## 2. What real trading shops have (and which parts we need)

Rohan is new to this, so here is the anatomy. A small systematic shop is roughly five systems:

| System | What it does | Real-shop version | Do we need it? |
|---|---|---|---|
| **Research platform** | Data access, backtests, experiment tracking, "which ideas did we try, what happened" | Notebook environment + shared data lake + experiment registry | **Yes, the core.** We have data+backtester; we lack the registry |
| **Strategy runner / signal service** | Runs approved strategies on fresh data, emits desired positions | Scheduled jobs or a live engine | **Yes**, but daily-batch, not streaming (our strategies hold days) |
| **Order/execution management (OMS/EMS)** | Turns desired positions into orders, tracks working orders and fills | FIX connections, smart routers | **Paper version only**: simulated fills from recorded books with the conservative model |
| **Portfolio and risk** | Positions, exposure, drawdown, limits, PnL attribution | Real-time risk dashboards | **Light**: per-fund exposure and loss limits; PnL split into edge vs cost |
| **Monitoring / ops** | Is data flowing, did jobs run, alerts | Dashboards + pagers | **Light**: recorder health, data freshness, "did today's session run" |

What a *good* dashboard shows at a real shop is mostly: (a) PnL and positions now, (b) risk vs
limits, (c) is everything running, (d) what changed. Researchers' views are different: run
comparisons, hypothesis status, data coverage. The PM/portfolio views earn their keep only
when there is capital to allocate. We have none, so (a) and (b) are practice for later, and (c)
and the researcher view are the immediate value.

## 3. Three possible directions

**A. Lab notebook (research ops).** A registry of hypotheses and runs: each hypothesis has a
pre-declared rule, status (proposed / testing / killed / confirmed), the findings doc, the
numbers, and the *count of tests run* so multiple-testing correction is automatic. Views:
findings board, run comparison, data coverage. Goal: make honest go/no-go decisions faster and
stop us from fooling ourselves. Cost: small. Payoff: direct, because our biggest risk is a false
positive, not missing infrastructure.

**B. Paper fund (forward-test + simulated shop).** Made-up funds with capital, mandate and risk
limits. Each day a "session" runs the approved strategy, logs every decision with the book as
seen at that moment, simulates fills conservatively, marks positions, settles when markets
resolve, and reports live-paper vs backtest-predicted results. Views: fund NAV, positions,
blotter, exposure, calibration of predicted vs realized. Goal: turn a backtest candidate into
forward evidence, and learn how a shop's books work. Cost: medium (needs Phase 3's settlement
engine). Payoff: this *is* the forward test the candidate needs, plus the thing that looks
like a trading shop.

**C. Operations console.** Recorder/backfill health, data freshness, one-command
start/stop sessions. Goal: save the ~1 h/day we get. Cost: small. Payoff: modest alone, but it
is the glue for A and B.

### Recommendation

**B as the destination, built in the order C-minimum -> A -> B, in thin vertical slices,
with one design rule: a single append-only event log is the source of truth and every screen is
a projection of it.** Reasons:

1. The forward test is the only thing that can turn the surviving candidate into evidence, and
   its key input cannot be recreated later: the *decision-time snapshot* (prices, spread, size
   we could have taken) at the moment we would have traded. Backfilled history has only
   sampled prices, so every day we delay logging is a day of forward evidence lost. This argues
   for pulling a **thin slice of B forward ahead of the rest of Phase 3**: a daily job that
   logs candidate trades and decision-time books, nothing fancier.
2. An event log (decisions, orders, fills, marks, settlements, with timestamps) makes the
   fund dashboard, the blotter, backtest-vs-live comparison, and audit ("why did we take
   this?") all just queries. Rebuilding views when we change our minds is free; rebuilding
   history is impossible. It mirrors the recorder's "raw first, normalize later" rule.
3. A is cheap and attacks our real failure mode (false positives). It should exist before we
   have ten studies, not after.

What we deliberately **do not** build: real-money order routing, streaming/real-time
dashboards, auth/multi-user, mobile app, anything latency-sensitive.

## 4. Proposed shape (if the recommendation is accepted)

Three slices, each shippable alone and each ending in something Rohan can look at.

**Slice 1: session + decision log (smallest forward-test harness).**
- `drc-session`: refresh data, run the declared strategy over currently open markets, write
  decisions + decision-time book snapshots to an event log, print a summary.
- Event log: append-only Parquet (or SQLite) under `data/ledger/`, gitignored. Event types:
  `decision`, `order`, `fill`, `mark`, `settlement`, `session`. Every row carries fund id,
  strategy id + version (git sha), and timestamps (decision, receive).
- Report: generated static HTML per session (no server).

**Slice 2: funds, blotter, NAV.**
- Fund = id, capital, mandate (which strategies/venues), risk limits (max per event, max
  drawdown). Reuse the existing `Ledger` for accounting; settlement already exists there.
- Views: NAV curve, positions, blotter (trade list), exposure by event/category, cost
  attribution (gross edge vs fees vs spread).
- Live vs backtest: each fund shows realized per-trade edge against the backtest's predicted
  distribution; the gap is the headline number.

**Slice 3: lab notebook.**
- Hypothesis registry (the pre-declared rule, kill criterion, status, test count) generated
  from a small TOML/Markdown file per hypothesis plus links to `docs/findings/`.
- Run registry: every `drc-*` run writes a manifest (git sha, params, data snapshot, key
  metrics); a page lists and compares them.
- Data health page: catalog coverage and freshness (extends `drc-backfill catalog`).

Tech default (open question 3): Python end to end; the event log and views are polars/DuckDB
queries; reports start as generated static HTML (git-diffable, works on a phone, zero
servers). A small FastAPI + frontend app is added only when we want interactivity (filters,
drill-down) that static pages cannot give.

## 5. Risks and how to read results

- **Paper PnL flatters.** The dashboard must show liquidation-marked NAV and cost attribution
  by default, never mid-marked headline numbers.
- **Dashboard-driven overfitting.** Looking at many slices of live results is itself a
  multiple-testing problem. The registry should record which views we consulted before
  declaring a result, and funds should have a pre-declared evaluation horizon.
- **Tiny samples.** With a few trades a day, a forward test takes weeks to detect a 1c edge.
  The UI should show the power/minimum-detectable-edge next to every live result so we do not
  over-read noise.
- **Scope creep.** A pretty UI is the classic way for a research shop to feel productive while
  doing no research. Each slice must name the research decision it unblocks.

## 6. Open questions for Rohan

Each includes what the choice means so you can decide without trading background.

**Q1. What is this platform *for*? (the direction question)**
A fund dashboard and a research registry are different products. Think of it like the
difference between a *production monitoring dashboard* (is the thing running, what is it
earning) and a *CI/experiment tracker* (what did we try, what did it prove). Real shops have
both, but the second is what protects a small team that has no proven strategy yet.
- (a) Decision support first: A, then B. **My recommendation.**
- (b) Look-and-feel of a real shop first: B's dashboard, with synthetic or thin data, to
  learn what the views are. Fun, and a legitimate learning goal, but risks being decoration.
- (c) Ops convenience first: C only. Smallest, but does not advance evidence.
Which of "make better decisions", "learn how a shop runs", or "save time" matters most to you?

**Q2. Pull a thin forward-test slice ahead of Phase 3?**
Explained above: decision-time snapshots can't be recovered later, so delay costs evidence.
The price is that we build the logging before the full settlement-aware engine, and may
rework parts of it. Options: (a) yes, do Slice 1 next. **Recommended.** (b) keep strict order
(finish Phase 3 first), simpler but loses weeks of forward data.

**Q3. UI technology.** You know full-stack, so this is partly "what do you want to maintain".
- Static generated HTML reports: simplest, git-friendly, no server, limited interactivity.
- A notebook/dashboard framework (Streamlit, Marimo, Panel): fast to build, interactive,
  Python-only, less polished and harder to customise.
- A real web app (FastAPI + React/HTMX): fully flexible and the closest to a shop's internal
  tools, but the most maintenance, and time you would not spend researching.
My recommendation: static reports now, web app later if the reports hit a wall. If you want
the web app *as a project in itself* (portfolio value, enjoyment), say so; that is a valid
reason and changes the plan.

**Q4. What is a "fund" for us?**
A fund is a made-up account with its own capital, rules, and track record. Reason to have
several: so each strategy's results are separate and a good one cannot hide a bad one (or vice
versa); reason not to: premature structure while we have one candidate. Options: (a) one fund
per strategy/hypothesis, created when a hypothesis reaches forward testing. **Recommended.**
(b) a few thematic funds now (prediction markets, slow crypto). (c) a single fund until
there are multiple surviving strategies.
Also: starting capital is arbitrary; propose a round number (e.g. $10k per fund) so sizing
and risk-limit math is concrete.

**Q5. How automated should paper trading be?**
- Human-in-the-loop: the session proposes trades, you approve in a short daily review. Fits
  1 h/day, and your judgment can catch silly trades, but it contaminates the forward test
  with discretion unless approvals are logged and the unapproved trades are scored too.
- Fully automatic: the session trades whatever the pre-declared rule says; cleaner evidence.
  **Recommended for the forward test**, with you reviewing, not approving.
Strategies we hold for days settle slowly, so a once-a-day batch is enough; no always-on
process required (and nothing to babysit on WSL2).

**Q6. Where should the data live and run?**
Local files (Parquet/SQLite) on the laptop are the default; they match CLAUDE.md and the
free-tier constraint. Question is whether you want to view dashboards from your phone/away from
the laptop. If yes, generated static reports can be pushed to a private page (Artifact or a
private repo page); if no, local only. Defaults to local.

**Q7. How strictly do we hold the "no real money" line in the design?**
Currently nothing here can trade. A UI with "orders" invites eventually wiring a real
exchange. Options: keep the execution interface simulation-only by construction (no
credentials in the repo, no live order endpoint). **Recommended**, and consistent with
CLAUDE.md; mention only because "platform" often implies the opposite.

## 7. Suggested next step

If Q1/Q2 go as recommended: write `docs/design/event-log.md` (event schema, how it reuses
`Ledger` and the backtest `Fill`), then build Slice 1 against the single surviving candidate
(Polymarket 5-60c scheduled-end gap). Roadmap Phase 4 would be rewritten to the three slices
above with Slice 1 moved ahead of Phase 3.

## 6b. Open questions raised by the design (2026-10-25)

**Q8. Where is the evidence backed up?** The event log of a forward test cannot be
re-downloaded; if the laptop disk dies, weeks of evidence are gone. Options: (a) a private Git
repo holding only the small ledger (our own records, no raw market data, consistent with
CLAUDE.md's "never commit market data"; book snapshots kept out or referenced by hash);
(b) a periodic copy to a cloud bucket or a host on your domain; (c) laptop only for now.
Recommended: (a) or (b) from day one.

**Q9. Starting capital.** Allocation is arbitrary but should be large enough that sizing and
depth limits bite. Proposal: treasury $100k notional, $10k per fund, trades of $50-100 each
(small against the $50k-$1M-volume markets this strategy targets). Bigger trades fill worse,
which is the realism we want to feel. OK, or different numbers?

**Q10. Strategy parameters for the first fund.** Proposal: 3-day horizon, 5-60c ask band, buy
Yes, hold to resolution, ex-ante volume floor (stand-in for the backtest's $50k total volume,
e.g. $10k trailing 24h volume, to be justified from the data before launch). The live version
deliberately uses the ask, not the last trade. Do you want to review the charter before it
freezes? I would: after the first trade it is locked.

**Q11. How long and how big a forward test?** Each trade pays 0 or 1, so per-trade noise has
standard deviation near 40c. Detecting +1.2c at 80% power and 5% significance needs about
((1.96+0.84) x 0.40 / 0.012)^2, roughly 9,000 independent events. A fund taking a few dozen
trades a day needs many months. So the forward test will validate our *cost model* (live
spreads, fill rates, fees vs the backtest's assumptions) within weeks, but the *edge* only
slowly. Options: (a) accept that, judge costs early and call the edge inconclusive until the
sample exists; (b) also widen the universe (more horizons, more markets) to get more events
per day, at the price of testing a different hypothesis than the one we backtested.
Recommended: (a), and (b) only as a separate fund.

**Q12. Where does the daily job run?** "Fully automatic" means it runs whether or not you are
at the laptop. WSL2 laptops sleep, so sessions will be missed (logged as missed, never
backfilled). Options: (a) a scheduled task on the laptop: free, simple, misses days;
(b) GitHub Actions on a schedule: free and reliably timed, but state must live outside the
runner (ties to Q8); (c) a free-tier cloud VM: most robust, can run the recorder too, and
CLAUDE.md already requires the recorder to run unchanged on one. The roadmap parks cloud
"until justified"; unattended forward testing arguably justifies it. Recommended: (a) while we
build, (c) before the forward test's clock officially starts.

## 6c. Answers to Q8-Q12 (Rohan, 2026-10-25)
- Q8 backup: private repo `arorarohan/drc-ledger` (created, empty). Ledger lives at
  `data/ledger/` as a nested git repo; main repo is public, so nothing sensitive goes there.
- Q9 capital: accepted ($100k treasury, $10k per fund, $50-100 per trade).
- Q10 charter: accepted as v0; Rohan reviews the final file before it freezes.
- Q11 test length: Rohan asked what the decision is; restated as "accept slow edge validation,
  judging costs early" (recommended) versus widening the universe; answer pending.
- Q12 hosting: cloud VM as soon as possible. Scripts in `deploy/`, steps in `docs/deploy.md`.
