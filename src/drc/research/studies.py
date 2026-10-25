"""Studies on backfilled data. Run: `uv run python -m drc.research.studies [name ...]`.

Each study prints plain tables with sample sizes and cluster-bootstrap intervals. They are
exploratory: every number here is a hypothesis to test out of sample, not a result to trade.
"""

from __future__ import annotations

import datetime as dt
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


MAX_STALE_S = 86400  # a sample whose last trade is older than this is not "the price then"


DISCOVERY_END = "2026-02-09"  # markets scheduled to end before this were looked at on 2026-10-23


def scheduled_calibration_study(
    series: str = "general-sched", end_before: str | None = None, end_after: str | None = None
) -> None:
    """Polymarket general markets with horizons anchored to the SCHEDULED end date.

    Replaces the first-look study, whose horizons used the realized close and so leaked the
    outcome (docs/findings/2026-10-22-first-look.md section 5). The universe is every resolved
    Yes/No market whose scheduled end fell in a date window fixed in advance; a market enters a
    horizon only if it was open then. Pre-declared: horizons 60/30/14/7/3/1 days, default bins,
    primary result = pooled over price bins at each horizon, plus volume tiers as robustness.
    """
    m = load_markets("polymarket", series).select(
        "market_id", "event_id", "result_value", "close_ts", "end_ts", "volume"
    )
    s = load_samples("polymarket", series).drop_nulls("price").filter(pl.col("secs_to_close") > 0)
    df = (
        s.join(m, on="market_id")
        .drop_nulls("result_value")
        .with_columns(((pl.col("target_ts") - pl.col("last_trade_ts")) / 1e9).alias("stale_s"))
    )
    n_all = df.height
    df = df.filter(pl.col("stale_s") <= MAX_STALE_S)
    n_stale = n_all - df.height
    if end_before:
        df = df.filter(pl.col("end_ts") < _to_ns(end_before))
    if end_after:
        df = df.filter(pl.col("end_ts") >= _to_ns(end_after))
    print(
        f"\n=== Calibration, scheduled-end anchor: polymarket/{series} | "
        f"{df['market_id'].n_unique():,} markets, scheduled ends "
        f"{_d(df['end_ts'].min())}..{_d(df['end_ts'].max())}; dropped "
        f"{n_stale} stale samples (> {MAX_STALE_S // 3600}h since last trade)"
    )
    pooled = []
    for h in sorted(df["secs_to_close"].unique().to_list(), reverse=True):
        d = df.filter(pl.col("secs_to_close") == h)
        sc = score_summary(d, "price")
        mid = d.filter((pl.col("price") >= 0.05) & (pl.col("price") < 0.60))
        gap, lo, hi = cluster_bootstrap_mean(
            (mid["result_value"] - mid["price"]).to_numpy(), mid["event_id"].to_numpy(), 2000
        )
        pooled.append(
            {
                "horizon": horizon_label(h),
                "n": sc["n"],
                "events": sc["clusters"],
                "brier": sc["brier"],
                "brier_base_rate": sc["brier_of_base_rate"],
                "n_5_60c": mid.height,
                "gap_5_60c": round(gap, 4),
                "ci_lo": round(lo, 4),
                "ci_hi": round(hi, 4),
            }
        )
    print("Pooled gap (freq_yes - price) for contracts priced 5-60c, the band that looked")
    print("mispriced in the flawed study; one row per pre-declared horizon:")
    print(pl.DataFrame(pooled))
    h1 = df.filter(
        (pl.col("secs_to_close") == 3 * 86400)
        & (pl.col("price") >= 0.80)
        & (pl.col("price") < 0.95)
    )
    if h1.height:
        gap, lo, hi = cluster_bootstrap_mean(
            (h1["result_value"] - h1["price"]).to_numpy(), h1["event_id"].to_numpy(), 5000
        )
        print(
            f"\nH1 check (3d before scheduled end, priced 80-95c): n={h1.height}, "
            f"events={h1['event_id'].n_unique()}, avg price={h1['price'].mean():.4f}, "
            f"freq yes={h1['result_value'].mean():.4f}, gap={gap:+.4f}, "
            f"95% CI [{lo:+.4f}, {hi:+.4f}]"
        )
    for h in (7, 3):
        d = df.filter(pl.col("secs_to_close") == h * 86400)
        print(f"\n-- {h}d before scheduled end: calibration by price bin")
        print(calibration(d, n_boot=1000))
        tiers = [("50k-250k", 5e4, 2.5e5), ("250k-1M", 2.5e5, 1e6), (">=1M", 1e6, 1e12)]
        rows = []
        for name, vlo, vhi in tiers:
            t = d.filter((pl.col("volume") >= vlo) & (pl.col("volume") < vhi))
            t = t.filter((pl.col("price") >= 0.05) & (pl.col("price") < 0.60))
            if t.height < 20:
                continue
            gap, lo, hi = cluster_bootstrap_mean(
                (t["result_value"] - t["price"]).to_numpy(), t["event_id"].to_numpy(), 2000
            )
            rows.append(
                {
                    "volume_tier": name,
                    "n": t.height,
                    "events": t["event_id"].n_unique(),
                    "gap_5_60c": round(gap, 4),
                    "ci_lo": round(lo, 4),
                    "ci_hi": round(hi, 4),
                }
            )
        print(f"{h}d, 5-60c band by total-volume tier (robustness):")
        print(pl.DataFrame(rows))


def _to_ns(day: str) -> int:
    return int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.UTC).timestamp()) * 1_000_000_000


WEATHER_SERIES = tuple(f"KXHIGH{c}" for c in ("NY", "CHI", "MIA", "AUS", "LAX", "DEN", "PHIL"))
WEATHER_PRIMARY = (86400, 43200)  # later horizons: the day's high has mostly happened already
WEATHER_GROUPS = (("under 15c", 0.0, 0.15), ("15-85c", 0.15, 0.85), ("over 85c", 0.85, 1.01))


def weather_study(half_spread: float = 0.02) -> None:
    """Are Kalshi daily-high-temperature bracket prices calibrated, and could the gap be traded?

    No forecast data is used: this asks whether the crowd's prices alone are exploitable.
    Pre-declared before looking: horizons 24h/12h before close are primary (by 6h and 2h before
    close the day's high is usually known, so prices near 0/1 reflect observed weather, not
    forecasts; reported as a check only); price groups under 15c / 15-85c / over 85c; for each
    group both sides are priced net of Kalshi's 7% x p(1-p) fee and an assumed 2c half-spread
    (thin markets, unmeasured). All 24 combinations are printed. Kill: no primary combination
    has a lower confidence bound above 0 net of costs.
    """
    fee = KalshiFee().rate
    frames = []
    for series in WEATHER_SERIES:
        m = load_markets("kalshi", series).select(
            "market_id", "event_id", "series", "result_value", "volume", "strike_type"
        )
        smp = load_samples("kalshi", series).drop_nulls("price").filter(pl.col("secs_to_close") > 0)
        frames.append(smp.join(m, on="market_id").drop_nulls("result_value"))
    df = pl.concat(frames)
    days = df["event_id"].n_unique()
    print(
        f"\n=== Kalshi daily-high weather brackets | {df['market_id'].n_unique():,} markets, "
        f"{days:,} city-days, {len(WEATHER_SERIES)} cities, {df.height:,} price samples"
    )
    rows, bracket_rows = [], []
    for h in sorted(df["secs_to_close"].unique().to_list(), reverse=True):
        d = df.filter(pl.col("secs_to_close") == h)
        stale_min = ((d["target_ts"] - d["last_trade_ts"]) / 6e10).to_numpy()
        sc = score_summary(d, "price")
        print(
            f"\n-- {horizon_label(h) if h < 86400 else '24h'}"
            f"{' (primary)' if h in WEATHER_PRIMARY else ' (check only)'} before close: "
            f"n={sc['n']}, city-days={sc['clusters']}, brier={sc['brier']} "
            f"(constant base rate: {sc['brier_of_base_rate']}), log_loss={sc['log_loss']}; "
            f"price age at that moment: median {np.median(stale_min):.0f} min, "
            f"90th pct {np.percentile(stale_min, 90):.0f} min"
        )
        print(calibration(d, bins=(0.0, 0.05, 0.15, 0.35, 0.65, 0.85, 0.95, 1.0), n_boot=1000))
        for name, lo, hi in WEATHER_GROUPS:
            g = d.filter((pl.col("price") >= lo) & (pl.col("price") < hi))
            if g.height < 20:
                continue
            p, y, c = g["price"].to_numpy(), g["result_value"].to_numpy(), g["event_id"].to_numpy()
            for hs in (half_spread, 0.0):  # 0.0 = best case: the verdict cannot hinge on spread
                cost = fee * p * (1 - p) + hs
                sides = (("buy Yes", y - p - cost), ("buy No", p - y - cost))
                for side, profit in sides:
                    mean, lo_, hi_ = cluster_bootstrap_mean(profit, c, 2000)
                    rows.append(
                        {
                            "secs_to_close": h,
                            "price_group": name,
                            "side": side,
                            "half_spread": hs,
                            "n": g.height,
                            "city_days": g["event_id"].n_unique(),
                            "gross_gap": round(
                                float((y - p).mean() * (1 if side == "buy Yes" else -1)), 4
                            ),
                            "net_per_contract": round(mean, 4),
                            "ci_lo": round(lo_, 4),
                            "ci_hi": round(hi_, 4),
                        }
                    )
        # complement check: the brackets of one city-day are exhaustive, so prices sum to ~1
        full = d.group_by("event_id").agg(
            pl.col("price").sum().alias("total"), pl.len().alias("seen")
        )
        n_brackets = (
            pl.concat([load_markets("kalshi", s) for s in WEATHER_SERIES])
            .group_by("event_id")
            .agg(pl.len().alias("n_total"))
        )
        full = full.join(n_brackets, on="event_id").filter(pl.col("seen") == pl.col("n_total"))
        if full.height:
            bracket_rows.append(
                {
                    "secs_to_close": h,
                    "complete_city_days": full.height,
                    "median_sum": round(float(full["total"].median()), 3),
                    "share_sum_gt_1.05": round(float((full["total"] > 1.05).mean()), 3),
                    "share_sum_lt_0.95": round(float((full["total"] < 0.95).mean()), 3),
                }
            )
    print("\nNet of costs ('net_per_contract' = profit per contract after fee and half-spread):")
    print(pl.DataFrame(rows))
    print("\nBracket-sum check (city-days where every bracket has a price; ideal sum = 1):")
    print(pl.DataFrame(bracket_rows) if bracket_rows else "no complete city-days")


def scheduled_net_study() -> None:
    """Does the replicated 5-60c gap survive Polymarket's fees and a spread? (buy Yes, hold)

    Pre-declared before running (2026-10-25): trade = buy Yes at the horizon price on every
    contract priced 5-60c, hold to resolution. Cost per contract = the market's own stated fee,
    rate x (p(1-p))^exponent from the markets table (0 where fees are not enabled), plus an
    ASSUMED half-spread (unmeasured; shown at 0, 0.5c, 1c, 2c). Primary test: holdout set
    (scheduled end >= DISCOVERY_END), 3-day horizon, 1c half-spread. Survives only if its
    cluster-bootstrap 95% CI lower bound is above 0; otherwise the strategy is dead. The other
    horizons, spreads, fee-regime and price-band splits are reported, not selected from.
    Caveat: Gamma's fee schedule is the market's CURRENT one, so markets whose fees were
    switched on mid-life are charged for their whole life (conservative).
    """
    m = load_markets("polymarket", "general-sched").select(
        "market_id",
        "event_id",
        "result_value",
        "end_ts",
        "fees_enabled",
        "fee_rate",
        "fee_exponent",
    )
    smp = load_samples("polymarket", "general-sched").drop_nulls("price")
    df = (
        smp.filter(pl.col("secs_to_close") > 0)
        .join(m, on="market_id")
        .drop_nulls("result_value")
        .with_columns(((pl.col("target_ts") - pl.col("last_trade_ts")) / 1e9).alias("stale_s"))
        .filter((pl.col("stale_s") <= MAX_STALE_S) & (pl.col("price") >= 0.05))
        .filter(pl.col("price") < 0.60)
        .with_columns(
            (
                pl.col("fee_rate").fill_null(0.0)
                * (pl.col("price") * (1 - pl.col("price"))) ** pl.col("fee_exponent").fill_null(1.0)
            ).alias("fee")
        )
    )
    cut = _to_ns(DISCOVERY_END)
    spreads = (0.0, 0.005, 0.01, 0.02)
    rows = []
    for period, part in (
        ("discovery", df.filter(pl.col("end_ts") < cut)),
        ("holdout", df.filter(pl.col("end_ts") >= cut)),
    ):
        for h in (7, 3, 1):
            d = part.filter(pl.col("secs_to_close") == h * 86400)
            y, p = d["result_value"].to_numpy(), d["price"].to_numpy()
            fee, c = d["fee"].to_numpy(), d["event_id"].to_numpy()
            for hs in spreads:
                net = y - p - fee - hs
                mean, lo, hi = cluster_bootstrap_mean(net, c, 2000)
                rows.append(
                    {
                        "period": period,
                        "horizon_d": h,
                        "half_spread": hs,
                        "n": d.height,
                        "events": d["event_id"].n_unique(),
                        "gross_gap": round(float((y - p).mean()), 4),
                        "mean_fee": round(float(fee.mean()), 4),
                        "net": round(mean, 4),
                        "ci_lo": round(lo, 4),
                        "ci_hi": round(hi, 4),
                    }
                )
    print("\n=== Buy Yes on 5-60c contracts, hold to resolution, net of per-market fee and spread")
    print("(PRIMARY: holdout, 3d, half_spread 0.01)")
    print(pl.DataFrame(rows))

    hold = df.filter(
        (pl.col("end_ts") >= cut) & (pl.col("secs_to_close").is_in([3 * 86400, 86400]))
    )
    splits = []
    for label, expr in (
        ("fees enabled", pl.col("fees_enabled")),
        ("fee-free", ~pl.col("fees_enabled")),
    ):
        splits.append((label, expr))
    for lo_p, hi_p in ((0.05, 0.10), (0.10, 0.20), (0.20, 0.40), (0.40, 0.60)):
        splits.append(
            (f"price {lo_p:.2f}-{hi_p:.2f}", (pl.col("price") >= lo_p) & (pl.col("price") < hi_p))
        )
    out = []
    for h in (3, 1):
        for label, expr in splits:
            d = hold.filter((pl.col("secs_to_close") == h * 86400) & expr)
            if d.height < 30:
                continue
            y, p = d["result_value"].to_numpy(), d["price"].to_numpy()
            fee, c = d["fee"].to_numpy(), d["event_id"].to_numpy()
            mean, lo, hi = cluster_bootstrap_mean(y - p - fee - 0.01, c, 2000)
            out.append(
                {
                    "horizon_d": h,
                    "subset": label,
                    "n": d.height,
                    "events": d["event_id"].n_unique(),
                    "gross_gap": round(float((y - p).mean()), 4),
                    "mean_fee": round(float(fee.mean()), 4),
                    "net_at_1c": round(mean, 4),
                    "ci_lo": round(lo, 4),
                    "ci_hi": round(hi, 4),
                    "breakeven_half_spread": round(float((y - p - fee).mean()), 4),
                }
            )
    print("\nHoldout splits (half_spread 0.01; break-even = gross gap minus fee):")
    print(pl.DataFrame(out))


def digital_dataset() -> tuple[pl.DataFrame, dict]:
    m = load_markets("kalshi", "KXBTC15M")
    kl = pl.read_parquet(list((ROOT / "klines_1m" / "symbol=BTCUSDT").glob("*.parquet")))
    d = build_dataset(m, load_samples("kalshi", "KXBTC15M"), kl).drop_nulls("result_value")
    d = d.with_columns((1e4 * (pl.col("strike") / pl.col("strike_binance") - 1)).alias("basis_bps"))
    clean = d.filter(pl.col("basis_bps").abs() <= MAX_STRIKE_BASIS_BPS)
    info = {
        "markets_total": m.height,
        "no_fresh_spot": m.height - d["market_id"].n_unique(),
        "basis_outliers": d["market_id"].n_unique() - clean["market_id"].n_unique(),
        "markets_used": clean["market_id"].n_unique(),
        "median_basis_bps": round(float(d["basis_bps"].median()), 1),
        "spot_history_ends": _d(int(kl["ts"].max())),
    }
    return clean, info


def digital_study(half_spread: float = 0.005) -> None:
    d, info = digital_dataset()
    print(
        f"\n=== Digital-option model vs Kalshi price (KXBTC15M) | {info['markets_used']} of "
        f"{info['markets_total']} markets used"
    )
    print(
        f"    dropped: {info['no_fresh_spot']} with no fresh Binance spot (history ends "
        f"{info['spot_history_ends']}), {info['basis_outliers']} with index strike "
        f">{MAX_STRIKE_BASIS_BPS:.0f}bp from Binance; median index-vs-Binance basis "
        f"{info['median_basis_bps']} bp"
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
    # FLAWED; kept to reproduce docs/findings/2026-10-23-early-close-lookahead.md
    "calibration_poly_general_flawed": lambda: calibration_study("polymarket", "general"),
    "calibration_poly_sched": lambda: scheduled_calibration_study(end_before=DISCOVERY_END),
    "calibration_poly_sched_holdout": lambda: scheduled_calibration_study(end_after=DISCOVERY_END),
    "poly_net": scheduled_net_study,
    "weather": weather_study,
    "digital": digital_study,
    "cross_venue": cross_venue_study,
}

if __name__ == "__main__":
    for name in sys.argv[1:] or STUDIES:
        STUDIES[name]()
