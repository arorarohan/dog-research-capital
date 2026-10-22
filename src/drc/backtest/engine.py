"""Bar-driven backtest engine with conservative taker fills.

Timeline per bar t:
  1. Orders decided `latency_bars` ago are filled against the book of bar t.
  2. The portfolio is marked and an equity point is recorded.
  3. The strategy sees bar t (and history) and may submit orders, which fill no earlier
     than bar t + latency_bars.

Fill model (market orders only; passive fills are not modelled yet):
  - buy price  = max(ask at decision, ask at fill), sell price = min(bid at decision, bid at fill)
    i.e. the worse of the book you saw and the book you got, plus `slippage_bps`;
  - quantity is capped at `max_participation` x the smaller of the two top-of-book sizes; the
    unfilled remainder is cancelled (immediate-or-cancel);
  - taker fees per venue (see fees.py), charged on the filled notional.
Equity is marked at liquidation value: longs at the bid, shorts at the ask. Rejections (no
book, crossed book, insufficient cash, short not allowed, leverage) are recorded in `events`.

Reconnect gaps (a new `epoch`) cancel that symbol's pending orders. Positions are carried
through at the last mark and the gap is recorded; nothing is interpolated.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import polars as pl

from drc.backtest.fees import VENUE_FEES, FeeModel
from drc.backtest.ledger import Fill, Ledger
from drc.backtest.strategy import Context, Order, Strategy

BAR_NS = 1_000_000_000

EQUITY_SCHEMA = {"ts": pl.Int64, "equity": pl.Float64, "cash": pl.Float64, "fees": pl.Float64}
FILLS_SCHEMA = {
    "ts": pl.Int64,
    "symbol": pl.Utf8,
    "side": pl.Utf8,
    "qty": pl.Float64,
    "price": pl.Float64,
    "fee": pl.Float64,
    "order_id": pl.Int64,
    "tag": pl.Utf8,
    "mid": pl.Float64,
}


@dataclass
class Config:
    initial_cash: float = 10_000.0
    latency_bars: int = 1
    slippage_bps: float = 1.0
    max_participation: float = 1.0
    allow_short: bool = False
    max_leverage: float = 1.0  # gross exposure / equity cap, enforced when allow_short
    history_len: int = 600
    fee_model: FeeModel | None = None  # default: by venue
    bar_ns: int = BAR_NS


@dataclass
class Pending:
    order: Order
    order_id: int
    ready_ts: int
    decision_bar: dict


@dataclass
class Result:
    ledger: Ledger
    equity: pl.DataFrame
    fills: pl.DataFrame
    events: list[dict]
    config: Config


def _tradeable(bar: dict | None) -> bool:
    return (
        bar is not None
        and bar["bid"] is not None
        and bar["ask"] is not None
        and not bar["crossed"]
        and bar["bid"] <= bar["ask"]
    )


def run_backtest(bars: pl.DataFrame, strategy: Strategy, cfg: Config | None = None) -> Result:
    cfg = cfg or Config()
    ledger = Ledger(cfg.initial_cash)
    events: list[dict] = []
    fee_model = cfg.fee_model or VENUE_FEES[bars["venue"][0]]

    # ts -> {symbol: row}; a later epoch overwrites an earlier one at the same timestamp
    by_ts: dict[int, dict[str, dict]] = {}
    for row in bars.sort("ts", "epoch").iter_rows(named=True):
        by_ts.setdefault(row["ts"], {})[row["symbol"]] = row

    history: dict[str, deque] = {}
    last_epoch: dict[str, int] = {}
    last_quote: dict[str, tuple[float, float]] = {}  # symbol -> (bid, ask) of last good book
    pending: list[Pending] = []
    equity_rows: list[tuple] = []
    next_id = 0

    def reject(ts: int, order: Order, reason: str) -> None:
        events.append({"ts": ts, "event": "reject", "symbol": order.symbol, "reason": reason})

    def liquidation_marks() -> dict[str, float] | None:
        marks = {}
        for s, pos in ledger.positions.items():
            if pos.qty != 0:
                if s not in last_quote:
                    return None
                bid, ask = last_quote[s]
                marks[s] = bid if pos.qty > 0 else ask
        return marks

    for ts in sorted(by_ts):
        now = by_ts[ts]

        for sym, bar in now.items():
            if sym in last_epoch and bar["epoch"] != last_epoch[sym]:
                dropped = sum(p.order.symbol == sym for p in pending)
                pending = [p for p in pending if p.order.symbol != sym]
                events.append(
                    {"ts": ts, "event": "gap", "symbol": sym, "cancelled_orders": dropped}
                )
            last_epoch[sym] = bar["epoch"]
            if _tradeable(bar):
                last_quote[sym] = (bar["bid"], bar["ask"])

        # 1. fill orders that are due
        still: list[Pending] = []
        for p in pending:
            bar = now.get(p.order.symbol)
            if p.ready_ts > ts or bar is None:
                still.append(p)
                continue
            fill = _try_fill(p, bar, ledger, cfg, fee_model, liquidation_marks(), reject)
            if fill is not None:
                ledger.apply_fill(fill)
        pending = still

        # 2. mark and record equity
        marks = liquidation_marks()
        if marks is not None:
            equity_rows.append((ts, ledger.equity(marks), ledger.cash, ledger.fees))

        # 3. strategy decides on information up to and including this bar
        ctx = Context(
            ts=ts,
            bars=now,
            history=history,
            positions={s: p.qty for s, p in ledger.positions.items()},
            cash=ledger.cash,
            equity=equity_rows[-1][1] if equity_rows else ledger.cash,
        )
        for order in strategy.on_bar(ctx):
            bar = now.get(order.symbol)
            if order.qty <= 0 or order.side not in ("buy", "sell"):
                reject(ts, order, "invalid order")
            elif not _tradeable(bar):
                reject(ts, order, "no tradeable book at decision")
            else:
                next_id += 1
                ready = ts + cfg.latency_bars * cfg.bar_ns
                pending.append(Pending(order, next_id, ready, bar))

        for sym, bar in now.items():  # history excludes the current bar until it is over
            history.setdefault(sym, deque(maxlen=cfg.history_len)).append(bar)

    return Result(
        ledger,
        pl.DataFrame(equity_rows, schema=EQUITY_SCHEMA, orient="row"),
        pl.DataFrame([f.__dict__ for f in ledger.fills], schema=FILLS_SCHEMA),
        events,
        cfg,
    )


def _try_fill(
    p: Pending,
    bar: dict,
    ledger: Ledger,
    cfg: Config,
    fee_model: FeeModel,
    marks: dict[str, float] | None,
    reject,
) -> Fill | None:
    o, dec, ts = p.order, p.decision_bar, bar["ts"]
    if not _tradeable(bar):
        reject(ts, o, "no tradeable book at fill")
        return None
    if o.side == "buy":
        px = max(dec["ask"], bar["ask"]) * (1 + cfg.slippage_bps / 1e4)
        qty = min(o.qty, min(dec["ask_sz"], bar["ask_sz"]) * cfg.max_participation)
    else:
        px = min(dec["bid"], bar["bid"]) * (1 - cfg.slippage_bps / 1e4)
        qty = min(o.qty, min(dec["bid_sz"], bar["bid_sz"]) * cfg.max_participation)

    held = ledger.position(o.symbol)
    if o.side == "sell" and not cfg.allow_short:
        if held <= 0:
            reject(ts, o, "short selling not allowed")
            return None
        qty = min(qty, held)
    if o.side == "buy" and not (cfg.allow_short and held < 0):
        # cash account: what can we afford, fee included
        qty = min(qty, max(ledger.cash, 0.0) / (px + fee_model.fee(1.0, px)))
    if cfg.allow_short and marks is not None:
        qty = min(qty, _leverage_room(ledger, cfg, marks, o, px, held))
    if qty <= 1e-12:
        reject(ts, o, "no size available (book depth, cash, or leverage)")
        return None
    return Fill(
        ts, o.symbol, o.side, qty, px, fee_model.fee(qty, px), p.order_id, o.tag, bar["mid"]
    )


def _leverage_room(ledger, cfg, marks, o, px, held) -> float:
    """Largest quantity keeping gross exposure within max_leverage x equity."""
    signed = 1.0 if o.side == "buy" else -1.0
    # exposure-reducing part is always allowed
    reducing = min(abs(held), float("inf")) if held * signed < 0 else 0.0
    room_value = cfg.max_leverage * ledger.equity(marks) - ledger.gross_exposure(marks)
    return reducing + max(room_value, 0.0) / px
