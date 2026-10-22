"""Studies on backfilled data. Run: `uv run python -m drc.research.studies [name ...]`.

Each study prints plain tables with sample sizes and cluster-bootstrap intervals. They are
exploratory: every number here is a hypothesis to test out of sample, not a result to trade.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import polars as pl

from drc.backfill.catalog import _d
from drc.backtest.fees import KalshiFee
from drc.research.digital import build_dataset
from drc.research.evaluation import calibration, cluster_bootstrap_mean, score_summary

ROOT = Path("data/hist")
MAX_STRIKE_BASIS_BPS = 50.0  # drop markets whose index strike is wildly off Binance
pl.Config.set_tbl_rows(40)
pl.Config.set_tbl_cols(14)
pl.Config.set_fmt_str_lengths(40)


def load_markets(venue: str, series: str) -> pl.DataFrame:
    return pl.read_parquet(ROOT / "markets" / f"venue={venue}" / f"series={series}.parquet")


def load_samples(venue: str, series: str) -> pl.DataFrame:
    files = list((ROOT / "price_samples" / f"venue={venue}" / f"series={series}").glob("*.parquet"))
    return pl.read_parquet(files)


def joined(venue: str, series: str) -> pl.DataFrame:
    m = load_markets(venue, series).select("market_id", "event_id", "result_value", "close_ts")
    s = load_samples(venue, series).drop_nulls("price").filter(pl.col("secs_to_close") > 0)
    return s.join(m, on="market_id").drop_nulls("result_value")


def horizon_label(h: int) -> str:
    return f"{h / 86400:g}d" if h >= 86400 else f"{h}s"


def calibration_study(venue: str, series: str) -> None:
    df = joined(venue, series)
    span = f"{_d(df['close_ts'].min())}..{_d(df['close_ts'].max())}"
    print(f"\n=== Calibration: {venue}/{series} | {df['market_id'].n_unique():,} markets, {span}")
    for h in sorted(df["secs_to_close"].unique().to_list(), reverse=True):
        d = df.filter(pl.col("secs_to_close") == h)
        s = score_summary(d, "price")
        print(
            f"\n-- {horizon_label(h)} before close: n={s['n']}, events={s['clusters']}, "
            f"brier={s['brier']} (constant base rate: {s['brier_of_base_rate']}), "
            f"log_loss={s['log_loss']}"
        )
        print(calibration(d, n_boot=1000))


def digital_dataset() -> tuple[pl.DataFrame, dict]:
    m = load_markets("kalshi", "KXBTC15M")
    kl = pl.read_parquet(list((ROOT / "klines_1m" / "symbol=BTCUSDT").glob("*.parquet")))
    d = build_dataset(m, load_samples("kalshi", "KXBTC15M"), kl).drop_nulls("result_value")
    basis_bps = 1e4 * (pl.col("strike") / pl.col("strike_binance") - 1)
    d = d.with_columns(basis_bps.alias("basis_bps"))
    clean = d.filter(pl.col("basis_bps").abs() <= MAX_STRIKE_BASIS_BPS)
    info = {
        "markets": d["market_id"].n_unique(),
        "excluded_outliers": d["market_id"].n_unique() - clean["market_id"].n_unique(),
        "median_basis_bps": round(float(d["basis_bps"].median()), 1),
    }
    return clean, info


def digital_study(half_spread: float = 0.005) -> None:
    d, info = digital_dataset()
    print(
        f"\n=== Digital-option model vs Kalshi price (KXBTC15M) | {info['markets']} markets, "
        f"{info['excluded_outliers']} excluded (index strike >{MAX_STRIKE_BASIS_BPS:.0f}bp from "
        f"Binance); median index-vs-Binance basis {info['median_basis_bps']} bp"
    )
    forecasters = (
        ("kalshi price", "price"),
        ("model, index strike", "model_p"),
        ("model, binance strike", "model_p_binance_strike"),
    )
    rows = []
    for off in sorted(d["secs_to_close"].unique().to_list(), reverse=True):
        x = d.filter(pl.col("secs_to_close") == off).drop_nulls(
            ["model_p", "model_p_binance_strike"]
        )
        y = x["result_value"].to_numpy()
        for name, col in forecasters:
            rows.append(
                {
                    "secs_to_close": off,
                    "n": len(y),
                    "forecaster": name,
                    "brier": round(float(np.mean((x[col].to_numpy() - y) ** 2)), 5),
                }
            )
    print("Brier score (lower is better; 0.25 = always saying 50%):")
    print(pl.DataFrame(rows).pivot(on="forecaster", index=["secs_to_close", "n"], values="brier"))

    fee = KalshiFee().rate
    print(
        "\nTrade rule on the basis-neutral model: buy Yes if model-price > theta, buy No if "
        f"< -theta. Net of an assumed {half_spread * 100:.1f}c half-spread and Kalshi's "
        f"{fee:.0%} x p(1-p) fee (unrounded).\n6 combinations (theta x offset), all reported, "
        "none selected."
    )
    out = []
    for theta in (0.05, 0.10):
        for off in (420, 300, 180):
            x = d.filter(pl.col("secs_to_close") == off).drop_nulls("model_p_binance_strike")
            edge = (x["model_p_binance_strike"] - x["price"]).to_numpy()
            price, y = x["price"].to_numpy(), x["result_value"].to_numpy()
            buy_yes, take = edge > theta, np.abs(edge) > theta
            pay = np.where(buy_yes, price + half_spread, 1 - price + half_spread)
            profit = np.where(buy_yes, y, 1 - y) - pay - fee * pay * (1 - pay)
            row = {"theta": theta, "offset": off, "trades": int(take.sum())}
            if take.sum() >= 5:
                clusters = x["event_id"].to_numpy()[take]
                mean, lo, hi = cluster_bootstrap_mean(profit[take], clusters, 2000)
                row |= {
                    "share_of_samples": round(float(take.mean()), 3),
                    "net_per_contract": round(mean, 4),
                    "ci_lo": round(lo, 4),
                    "ci_hi": round(hi, 4),
                }
            out.append(row)
    print(pl.DataFrame(out))


def cross_venue_study() -> None:
    def with_open(venue: str, series: str) -> pl.DataFrame:
        opens = load_markets(venue, series).select("market_id", "open_ts")
        return joined(venue, series).join(opens, on="market_id")

    j = with_open("kalshi", "KXBTC15M").join(
        with_open("polymarket", "btc-updown-15m"), on=["open_ts", "secs_to_close"], suffix="_pm"
    )
    print(
        f"\n=== Kalshi vs Polymarket, same 15-minute interval | "
        f"{j['open_ts'].n_unique()} intervals, {j.height} price pairs"
    )
    rows = []
    for off in sorted(j["secs_to_close"].unique().to_list(), reverse=True):
        x = j.filter(pl.col("secs_to_close") == off)
        diff = (x["price_pm"] - x["price"]).to_numpy()
        ky, py = x["result_value"].to_numpy(), x["result_value_pm"].to_numpy()
        rows.append(
            {
                "secs_to_close": off,
                "pairs": len(diff),
                "mean_diff_pm_minus_k": round(float(diff.mean()), 4),
                "mean_abs_diff": round(float(np.abs(diff).mean()), 4),
                "share_abs_gt_5c": round(float((np.abs(diff) > 0.05).mean()), 3),
                "brier_kalshi": round(float(np.mean((x["price"].to_numpy() - ky) ** 2)), 4),
                "brier_poly": round(float(np.mean((x["price_pm"].to_numpy() - py) ** 2)), 4),
                "poly_pts_in_window": float(x["n_window_pm"].median()),
            }
        )
    print(pl.DataFrame(rows))


STUDIES = {
    "calibration_kalshi15": lambda: calibration_study("kalshi", "KXBTC15M"),
    "calibration_poly15": lambda: calibration_study("polymarket", "btc-updown-15m"),
    "calibration_poly_general": lambda: calibration_study("polymarket", "general"),
    "digital": digital_study,
    "cross_venue": cross_venue_study,
}

if __name__ == "__main__":
    for name in sys.argv[1:] or STUDIES:
        STUDIES[name]()
