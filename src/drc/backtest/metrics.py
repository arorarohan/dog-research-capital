"""Run summary. Deliberately plain: sample size and costs matter more than any ratio."""

from __future__ import annotations

import math

import polars as pl

from drc.backtest.engine import Result


def summarize(result: Result, bars: pl.DataFrame) -> dict:
    eq = result.equity
    cfg = result.config
    out: dict = {
        "bars": eq.height,
        "span_hours": round(eq.height * cfg.bar_ns / 1e9 / 3600, 3),
        "initial_equity": cfg.initial_cash,
        "final_equity": None,
        "return_pct": None,
        "max_drawdown_pct": None,
        "fills": result.fills.height,
        "fees": round(result.ledger.fees, 4),
        "rejects": sum(e["event"] == "reject" for e in result.events),
        "gaps": sum(e["event"] == "gap" for e in result.events),
    }
    warnings = []
    if eq.height >= 2:
        e = eq["equity"]
        out["final_equity"] = round(e[-1], 4)
        out["return_pct"] = round(100 * (e[-1] / cfg.initial_cash - 1), 4)
        out["max_drawdown_pct"] = round(100 * ((e / e.cum_max()) - 1).min(), 4)
        rets = e.pct_change().drop_nulls()
        sd = rets.std()
        if sd and rets.len() > 1:
            out["bar_return_t_stat"] = round(rets.mean() / (sd / math.sqrt(rets.len())), 3)
        if out["span_hours"] < 24:
            warnings.append("sample under 24h: not statistically meaningful")
        if out["fills"] < 30:
            warnings.append("fewer than 30 fills: results are anecdotal")

    f = result.fills
    if f.height:
        notional = (f["qty"] * f["price"]).sum()
        slip = ((f["price"] - f["mid"]).abs() * f["qty"]).sum()
        out["turnover_x"] = round(notional / cfg.initial_cash, 3)
        out["cost_bps_of_notional"] = {
            "spread_and_slippage": round(1e4 * slip / notional, 2),
            "fees": round(1e4 * f["fee"].sum() / notional, 2),
        }

    mids = bars.drop_nulls("mid").sort("ts")["mid"]
    if mids.len() >= 2:
        out["market_move_pct"] = round(100 * (mids[-1] / mids[0] - 1), 4)  # first symbol-agnostic
    out["warnings"] = warnings
    return out
