# Strategy families

What you can trade *on*, roughly ordered from fastest to slowest. Each has an edge source, a
holding period, and a cost hurdle: the move you must capture per round trip to beat fees and
spread.

| Family | Edge source | Typical hold | Main risk |
|---|---|---|---|
| Latency / HFT | Being faster than everyone | microseconds-seconds | Out of scope for us |
| Market making | Earning the spread by quoting both sides | seconds-minutes | Adverse selection, inventory |
| Statistical arbitrage / relative value | Related prices that drift apart then converge | minutes-days | Relationship breaks (regime change) |
| Carry | Being paid to hold a position (funding, basis, yield, time decay) | days-months | Rare large losses ("picking up pennies in front of a steamroller") |
| Trend / momentum | Prices persist in a direction | days-months | Whipsaw; low hit rate, fat winners |
| Cross-sectional factors | Rank many assets; long the best, short the worst | weeks-months | Crowding; factor decay |
| Event / information (forecasting) | A better probability estimate than the market's | hours-weeks | Model error; resolution surprises |
| Bias exploitation | Systematic mispricing (e.g. favorite-longshot in betting markets) | until resolution | The bias is arbitraged away or was never there net of fees |

Rule of thumb: **holding period must scale with costs.** A 60 bp round-trip fee needs a >0.6%
expected move per trade, which is days of crypto drift but nothing at one-second horizons.
