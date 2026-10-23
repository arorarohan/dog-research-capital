# Lookahead bias

Letting a backtest or study use information that was not available at decision time. It turns
noise (or the outcome itself) into apparent profit.

**Engine version.** Deciding on a bar's close and filling at that same close. Our guards: the
strategy only sees bars up to now; orders fill at least one bar later; fills use the worse of
the book you saw and the book at fill time. The engine tests assert all three.

**Study version (we got burned).** Anything that defines *when* a row is sampled or *which* rows
are included must be knowable then. We measured "7 days before close" from each market's
*actual* close time. But "Will X happen by June?" closes in March when X happens, so for those
markets the sampling moment depends on the outcome. Result: a fake +3 to +8 point mispricing
with intervals excluding zero at five horizons. Markets that ran to their scheduled end showed
+1.5 points (CI [-2.1, +5.0]); those that closed early showed +26. Full post-mortem:
`docs/findings/2026-10-23-early-close-lookahead.md`.

**Toy version.** Ten "by date" markets, scheduled to end on day 100. Suppose the market is
perfectly calibrated: 3 of the 10 resolve Yes, and a Yes market closes on day 90 when the event
happens, while the 7 No markets run to day 100. "Seven days before close" is then day 83 for the
Yes markets but day 93 for the No markets. The two groups are sampled at different stages of
their lives, and the choice of stage was made by the outcome. Prices drift as an event nears
(more so for Yes markets as the event approaches), so the sampled prices no longer mean "the
price a trader saw N days out", and a calibrated market can look mispriced. Which way the error
points depends on the price paths, but it is not zero. Anchoring both groups to day 100 gives
every market the same sampling point, chosen before anyone knows the outcome.

**Rule.** For every variable defining when/which, ask "could a trader have known this at
the time?" Scheduled dates, rules text, listing time: yes. Close time, final volume,
resolution date: no.
