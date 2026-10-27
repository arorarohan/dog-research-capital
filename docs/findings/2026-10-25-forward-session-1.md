# Forward session 1: real spreads on an ex-ante universe (2026-10-25)

Status: first session of the forward-test recorder. Measures spreads and depth; no outcomes yet,
so nothing here says whether the strategies make money.

Setup: `uv run drc-record --config config/recorder-forward.toml`, 10:05-11:05 UTC on a **Sunday**,
one hour, data in `data/raw_forward` (1.3 GB raw, 261 MB compressed), normalized to
`data/norm_forward`. Reproduce: `uv run python -m drc.research.spreads --norm data/norm_forward
--raw data/raw_forward`. Six Kalshi polls hit HTTP 429 (rate limit) and were skipped; no
disconnects. The Kalshi books are polled once a minute and forward-filled into 1-second bars, so
Kalshi states can be up to 60 s stale.

## Polymarket: the ex-ante deadline universe
714 tokens (357 markets: scheduled end within 7 days, at least $50k volume to date when selected),
5.8M book events, 2.2M one-second bars (99.4% inside the session; the rest carry older snapshot
timestamps). Seconds with a one-sided book (215k) or a locked book (15k) are excluded.

**Strategy universe: 91 tokens priced 5-60c, 1-7 days before the scheduled end.**
- Spread per token (cents): 10th percentile 0.4, 25th 1.0, **median 1.0**, 75th 2.0, 90th 4.0.
  Mean half-spread **1.13c**. 5.5% of tokens have a half-spread above the 2.2c break-even of the
  3-day trade and 16.5% above 1.5c.
- Share of tokens quoting at most 1c: 54% (5-10c), 53% (10-20c), 54% (20-40c), 70% (40-60c).
- **Depth at the best ask is small**: median $45, quartiles $12 and $248; 42% of tokens show under
  $25. By price band the median is $22 (5-10c), $18 (10-20c), $46 (20-40c), $254 (40-60c). The
  10-40c bands carried the backtest edge.
- 10.7% of bars in the 1-7 day window were one-sided (no bid or no ask): no taker fill then.

Other cuts (all in-band tokens, 222): zero-fee markets are the widest (median spread 2c, only 35%
at or under 1c), fee-bearing ones mostly 1c. Tokens on the 0.001 tick quote 1.2c medians but only
$12 of depth.

**Reading against the backtest.** Holdout 3-day net was +1.2c at a 1c half-spread (CI
[+0.5, +1.9]); gross minus fee is 2.2c, so at the measured mean half-spread of 1.13c the net
would be about +1.1c, *before* any depth or impact effect. Depth, not the quoted spread, is now
the binding limit: fills beyond the displayed ask walk to worse prices, so size must stay small.

## Kalshi weather brackets (hypothesis H2)
Only **one event day** (Oct 25 brackets, 42 across 7 cities), 17.9 to 21.9 hours before close, so
this is a spread measurement, not a test of H2.
- Two-sided bars: median spread 1c in every price band. Share at or under 1c: 90% (mid under 5c),
  63% (5-15c), 71% (15-85c).
- **Longshots often cannot be sold short.** In bar-time where the Yes ask was at most 15c, 39.7%
  had no Yes bid, so a taker cannot buy No; 10 of 26 such brackets lacked a Yes bid at some point.
  H2's trade (buy No under 15c) is executable only part of the time, mostly absent at the
  extreme tails where the overpricing was largest.

## Caveats
One hour, one Sunday morning; spreads vary with the time of day and week. Thin overnight or
Sunday books may be wider than a weekday session. Selected markets are the ones that already had
$50k of volume, as the backtest did, but now chosen from volume to date.

## What this changes
- Spread alone no longer threatens the Polymarket result; depth does. Expect real forward fills to
  be small, and judge them with depth-aware, pessimistic fills (see the forward-test platform
  design on branch `worktree-phase4-scoping`, `docs/design/forward-test-platform.md`).
- H2 has an execution problem on top of the statistical one.
- More sessions at other times of day and week are needed before trusting the spread numbers.
