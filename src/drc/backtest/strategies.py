"""Baseline strategies. These exist to sanity-check the machinery, not to make money."""

from __future__ import annotations

import random

from drc.backtest.strategy import Context, Order, Strategy


class BuyAndHold(Strategy):
    """Buy once with `fraction` of equity and hold. The benchmark any signal must beat."""

    def __init__(self, symbol: str, fraction: float = 0.95) -> None:
        self.symbol, self.fraction, self.done = symbol, fraction, False

    def on_bar(self, ctx: Context) -> list[Order]:
        bar = ctx.bars.get(self.symbol)
        if self.done or bar is None or bar["ask"] is None:
            return []
        self.done = True
        return [Order(self.symbol, "buy", self.fraction * ctx.equity / bar["ask"], "hold")]


class RandomTrader(Strategy):
    """Flip between flat and long at random. Must lose roughly its trading costs.

    If this strategy ever makes money on a long sample, the fill model is too generous.
    """

    def __init__(self, symbol: str, seed: int = 0, prob: float = 0.01, notional: float = 1000.0):
        self.symbol, self.rng, self.prob, self.notional = (
            symbol,
            random.Random(seed),
            prob,
            notional,
        )

    def on_bar(self, ctx: Context) -> list[Order]:
        bar = ctx.bars.get(self.symbol)
        if bar is None or bar["ask"] is None or self.rng.random() >= self.prob:
            return []
        held = ctx.positions.get(self.symbol, 0.0)
        if held > 0:
            return [Order(self.symbol, "sell", held, "random")]
        return [Order(self.symbol, "buy", self.notional / bar["ask"], "random")]


class ImbalanceFollower(Strategy):
    """Go long when top-of-book depth is bid-heavy, exit when it flips ask-heavy.

    `imbalance` = (bid_depth - ask_depth) / (bid_depth + ask_depth) over the best 5 levels.
    A classic microstructure hypothesis; at 1-second horizons costs usually swamp it.
    """

    def __init__(self, symbol: str, enter: float = 0.5, exit_: float = -0.1, notional=1000.0):
        self.symbol, self.enter, self.exit, self.notional = symbol, enter, exit_, notional

    def on_bar(self, ctx: Context) -> list[Order]:
        bar = ctx.bars.get(self.symbol)
        if bar is None or bar.get("imbalance") is None or bar["ask"] is None:
            return []
        held = ctx.positions.get(self.symbol, 0.0)
        if held == 0 and bar["imbalance"] > self.enter:
            return [Order(self.symbol, "buy", self.notional / bar["ask"], "imb")]
        if held > 0 and bar["imbalance"] < self.exit:
            return [Order(self.symbol, "sell", held, "imb")]
        return []


STRATEGIES = {"hold": BuyAndHold, "random": RandomTrader, "imbalance": ImbalanceFollower}
