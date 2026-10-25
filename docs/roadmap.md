# Roadmap

Living plan. Update it when priorities change or a phase finishes (move items, add dates).
Status key: done / now / next / later / parked. Dates are absolute.

## Guiding decisions (2026-10-22)
- No HFT or latency-sensitive strategies. Slow, information-based strategies only.
- Prediction markets (Polymarket, Kalshi) are the main alpha hunt; slow crypto (carry, trend)
  is the second book. Equities much later.
- Live collection is limited to ~1 hour/day, so research is **backfill-first**: free historical
  data finds and sizes edges; short live sessions calibrate execution friction and forward-test.
- Free data only. Python first; Rust only when profiling demands it.
- Every result carries its sample size and cost assumptions. Paper PnL flatters; be skeptical.

## Phase 0: Foundation (done)
- Recorder (Coinbase, Kraken, Polymarket, Kalshi) with gap-aware raw capture
- Raw -> Parquet normalization, book replay, 1s bars
- Validation against ground truth (Kraken checksums, crossed-book rate, trade-side check)
- Backtester + ledger with conservative taker fills; baseline strategies prove costs bite
- Learning library, gotchas, CLAUDE.md conventions

## Phase 1: Historical data layer (now)
Goal: years of research data without running anything live.
- [x] Historical schemas: `markets` (with outcomes), `price_samples`, `klines_1m`, `funding`
- [x] Binance loader: 1m klines + funding (BTC/ETH from 2024-12; 993,600 candles each, no gaps)
- [x] Kalshi loader: settled markets (both tiers) + resumable sampled prices, series-scoped
- [x] Polymarket loader for crypto up/down (5m/15m) markets via slug enumeration + windowed price history
- [x] Polymarket loader for general resolved Yes/No markets (keyset paging by volume, 12h+ price paths, day-scale sampling)
- [x] Kalshi: other series beyond BTC/ETH 15m (2026-10-23): BTC/ETH hourly and daily ladders
      (14 days, strikes with >= 1,000 contracts), daily-high weather brackets for 7 cities (60 days),
      CPI, weekly gas. Still open: more econ series, other weather metrics
- [x] Data catalog (`drc-backfill catalog`): ranges, row counts, resolved and sampled markets
Exit: one command refreshes everything; coverage report shows date ranges and gaps.

## Phase 2: First research results (now)
Goal: find out whether any edge exists, honestly. First look written up in
`docs/findings/2026-10-22-first-look.md`: no tradeable edge found; the 15-minute contracts look
efficient; one apparent anomaly turned out to be our own lookahead.
- [x] Evaluation toolkit: Brier/log score, calibration with cluster-bootstrap CIs, exact
      bounds for all-win/all-loss bins
- [x] Calibration study v1 (15-minute BTC on Kalshi and Polymarket): well calibrated
- [x] Digital-option fair-value study v1: crude model does not beat the market net of costs
- [x] Cross-venue comparison v1 (Kalshi vs Polymarket 15-minute)
- [x] Redo Polymarket general calibration anchored to *scheduled* end dates (2026-10-23, see
      `docs/findings/2026-10-23-scheduled-calibration.md`): old effect mostly an artifact, small
      positive gap at 1-7d replicated on the holdout (+2 to +4 pts, gross); favorites hypothesis H1
      killed on the holdout. Net of per-market fees and a 1c half-spread the 5-60c gap still nets
      +1.2c (3d, holdout, pre-declared primary passed 2026-10-25); spread and hindsight volume
      filter unresolved: forward test next. Recorded liquid Polymarket books show a 1c median spread
      (0.5c half-spread) in the 5-60c band; the quiet universe it trades is unmeasured.
- [ ] Digital model v2: second-level spot (Binance 1s), refresh klines past 2026-10-21, better
      volatility; more 15-minute days to narrow intervals
- [x] Weather price-only check (2026-10-23, `docs/findings/2026-10-23-weather-calibration.md`): prices
      calibrated; no edge at a 2c half-spread; longshot-selling positive only if half-spread < ~1c
      (H2: forward test on fresh days + live spread measurement); forecast pipeline on hold
- [ ] Weather spread session (2026-10-25): recorder series mode + `config/recorder-weather.toml`
      built and smoke-tested; run live sessions to measure real bid/ask spreads on the brackets
- [ ] Consistency scans: ladder monotonicity, complement sums (hourly/daily Kalshi crypto series)
- [ ] Multiple-testing correction across the study set (deflated Sharpe style)
Candidate studies are ranked in `docs/research-plan.md` (selection principles, cost hurdles,
power, kill criteria). Tier 1: deadline-market time decay, weather brackets, wallet flow.
Exit: a written verdict per hypothesis (edge / no edge / inconclusive) with numbers.

## Phase 3: Prediction-market strategy engine (later)
- Settlement-aware backtester on trade-price bars with a calibrated friction model
  (spread and depth by market type, measured in live sessions)
- Kelly-style sizing; per-event exposure limits
- Point-in-time feature store for external signals (weather, econ nowcasts, polls)
- Live sessions: forward paper-trading of surviving strategies with full trade logging;
  compare live paper results to backtest predictions (the gap is itself a result)

## Phase 4: Forward-test platform and UI (scoped 2026-10-25)
Direction decided with Rohan 2026-10-25 (`docs/phase4-scoping.md` section 0): decisions first,
one automatic paper fund per hypothesis, judged as if it handled real money, simulation-only by
construction, web app later. Design: `docs/design/forward-test-platform.md`. A thin slice runs
**ahead of Phase 3** because decision-time snapshots cannot be recreated.
- [ ] Slice 0: append-only hash-chained event log (SQLite), depth-aware `SimBroker`, risk checks,
      reconciliation, treasury/firm-total projections
- [ ] Slice 1: `drc-session` for fund `pm-sched-gap-v1` (charter frozen before first trade),
      scheduling mechanism chosen, static HTML report
- [ ] Slice 2: firm page: allocated, NAV, PnL per fund, firm PnL incl. closed funds, predicted
      vs realized with the power note
- [ ] Later: hypothesis/run registry; real web app on Rohan's domain with remote viewing
Decided 2026-10-25: ledger backed up to private repo `arorarohan/drc-ledger` (created); $100k
treasury, $10k/fund, $50-100 trades; charter v0 parameters accepted for review before freeze;
cloud VM wanted ASAP (`docs/deploy.md`, `deploy/`; waiting on Rohan to create the VM).
- [ ] Cloud VM: create free-tier VM, run `deploy/setup-vm.sh`, confirm all venues reachable
- [ ] Test length: decide how to treat the slow-to-validate edge (scoping doc Q11)

## Phase 5: Slow crypto book (later)
- Funding-rate carry and trend on hourly/daily bars; multi-asset portfolio backtests
- Portfolio construction and risk (vol targeting, correlation, drawdown limits)

## Parked until justified
- ~~Cloud recorder~~ un-parked 2026-10-25: needed for unattended forward testing (Phase 4)
- Depth-ladder fills / passive (maker) order simulation / queue models
- Rust rewrite of hot paths
- Equities; options

## Open questions
- Fee schedules verified 2026-10-23 (Polymarket per market from Gamma; Kraken primary source; Coinbase from secondary copies, recheck signed in). Still open: Kalshi's fee can also vary by series (`fee_type` / `fee_multiplier` on its API), and the engine does not roll 30-day volume or model maker rebates.
- How far back do Kalshi's `/historical` and Polymarket's data-api trades actually go? (Kalshi KXBTC15M historical tier confirmed to at least 2026-08-12.)
- Do we need an API key for Kalshi's websocket (live sessions at higher fidelity)?
