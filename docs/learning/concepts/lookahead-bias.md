# Lookahead bias

Letting a backtest use information that would not have been available at decision time. The
classic version: deciding on a bar's close and filling at that same close. It silently turns
noise into profit.

Our guards: the strategy only sees bars up to now; orders fill at least one bar later; and
fills use the *worse* of the book you saw and the book at fill time. The engine tests assert
all three.

A subtler form we hit for real: **defining the sample or the horizon with information from
the future.** Measuring "7 days before a market closed" used each market's actual close time,
but markets that resolve Yes often close early. That quietly selected on the outcome and
produced a fake 3-8 point "mispricing". Rule: anything that defines *when* or *which* must be
knowable at that time (scheduled end dates, ex-ante universes).
