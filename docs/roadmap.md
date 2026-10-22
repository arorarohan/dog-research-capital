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
- [ ] Polymarket loader for general closed markets (keyset pagination) for broad calibration studies
- [ ] Kalshi: other series beyond BTC/ETH 15m (hourly/daily crypto ladders, econ, weather)
- [x] Data catalog (`drc-backfill catalog`): ranges, row counts, resolved and sampled markets
Exit: one command refreshes everything; coverage report shows date ranges and gaps.

## Phase 2: First research results (next)
Goal: find out whether any edge exists, honestly.
- [ ] Calibration study: realized frequency vs price by time-to-resolution, net of fees,
      clustered by event (Kalshi + Polymarket)
- [ ] Digital-option fair-value study: Kalshi/Polymarket crypto contracts vs spot-implied
      probability (needs Binance klines + volatility model)
- [ ] Consistency scans: ladder monotonicity, complement sums (on live snapshots first)
- [ ] Evaluation toolkit: Brier/log score, reliability curves, event clustering, multiple-
      testing correction (deflated Sharpe style), bootstrap confidence intervals
Exit: a written verdict per hypothesis (edge / no edge / inconclusive) with numbers.

## Phase 3: Prediction-market strategy engine (later)
- Settlement-aware backtester on trade-price bars with a calibrated friction model
  (spread and depth by market type, measured in live sessions)
- Kelly-style sizing; per-event exposure limits
- Point-in-time feature store for external signals (weather, econ nowcasts, polls)
- Live sessions: forward paper-trading of surviving strategies with full trade logging;
  compare live paper results to backtest predictions (the gap is itself a result)

## Phase 4: Platform and UI (later)
- Dashboard: fund NAVs, positions, blotter, signal monitors, research runs
- Multiple made-up funds with separate mandates and capital; run registry
- One-command "session" workflow: start recorder + paper trader, summarize, shut down

## Phase 5: Slow crypto book (later)
- Funding-rate carry and trend on hourly/daily bars; multi-asset portfolio backtests
- Portfolio construction and risk (vol targeting, correlation, drawdown limits)

## Parked until justified
- Cloud recorder (revisit if we want unattended capture of specific events)
- Depth-ladder fills / passive (maker) order simulation / queue models
- Rust rewrite of hot paths
- Equities; options

## Open questions
- Verify fee schedules (Kalshi, Polymarket, Coinbase, Kraken); results near break-even depend on them.
- How far back do Kalshi's `/historical` and Polymarket's data-api trades actually go? (Kalshi KXBTC15M historical tier confirmed to at least 2026-08-12.)
- Do we need an API key for Kalshi's websocket (live sessions at higher fidelity)?
