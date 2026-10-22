# Lookahead bias

Letting a backtest use information that would not have been available at decision time. The
classic version: deciding on a bar's close and filling at that same close. It silently turns
noise into profit.

Our guards: the strategy only sees bars up to now; orders fill at least one bar later; and
fills use the *worse* of the book you saw and the book at fill time. The engine tests assert
all three.
