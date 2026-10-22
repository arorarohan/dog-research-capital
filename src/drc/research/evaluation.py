"""Forecast-evaluation tools for prediction-market studies.

Everything that reports an uncertainty uses a **cluster bootstrap**: markets that resolve off
the same underlying event (a ladder of strikes, the legs of one election) are not independent,
so we resample whole events, not individual markets. A naive interval over-states precision
whenever clusters are large.
"""

from __future__ import annotations

import numpy as np
import polars as pl

DEFAULT_BINS = (0.0, 0.05, 0.10, 0.20, 0.40, 0.60, 0.80, 0.90, 0.95, 1.0000001)


def brier(p: np.ndarray, y: np.ndarray) -> float:
    """Mean squared error of probabilities; 0 is perfect, 0.25 is a coin-flip forecaster."""
    return float(np.mean((p - y) ** 2))


def log_loss(p: np.ndarray, y: np.ndarray, eps: float = 1e-4) -> float:
    p = np.clip(p, eps, 1 - eps)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _cluster_codes(clusters: np.ndarray) -> tuple[np.ndarray, int]:
    _, codes = np.unique(clusters, return_inverse=True)
    return codes, int(codes.max()) + 1 if len(codes) else 0


def cluster_bootstrap_mean(
    values: np.ndarray, clusters: np.ndarray, n_boot: int = 2000, seed: int = 0, ci: float = 0.95
) -> tuple[float, float, float]:
    """Mean of `values` with a percentile CI from resampling whole clusters."""
    if len(values) == 0:
        return float("nan"), float("nan"), float("nan")
    codes, k = _cluster_codes(clusters)
    sums = np.bincount(codes, weights=values, minlength=k)
    counts = np.bincount(codes, minlength=k).astype(float)
    rng = np.random.default_rng(seed)
    draw = rng.integers(0, k, size=(n_boot, k))
    means = sums[draw].sum(axis=1) / counts[draw].sum(axis=1)
    lo, hi = np.percentile(means, [(1 - ci) / 2 * 100, (1 + ci) / 2 * 100])
    return float(values.mean()), float(lo), float(hi)


def calibration(
    df: pl.DataFrame,
    price: str = "price",
    outcome: str = "result_value",
    cluster: str = "event_id",
    bins: tuple[float, ...] = DEFAULT_BINS,
    n_boot: int = 2000,
    seed: int = 0,
) -> pl.DataFrame:
    """Realized frequency vs quoted price, by price bin, with cluster-bootstrap CI on the gap.

    `gap` = frequency - average price. A positive gap means the contracts won more often than
    their price implied (buying them was profitable before costs).
    """
    d = df.drop_nulls([price, outcome, cluster])
    p, y, c = d[price].to_numpy(), d[outcome].to_numpy(), d[cluster].to_numpy()
    rows = []
    for lo, hi in zip(bins[:-1], bins[1:], strict=True):
        m = (p >= lo) & (p < hi)
        if not m.any():
            continue
        gap, g_lo, g_hi = cluster_bootstrap_mean(y[m] - p[m], c[m], n_boot, seed)
        rows.append(
            {
                "bin": f"{lo:.2f}-{min(hi, 1.0):.2f}",
                "n": int(m.sum()),
                "clusters": int(len(np.unique(c[m]))),
                "avg_price": round(float(p[m].mean()), 4),
                "freq_yes": round(float(y[m].mean()), 4),
                "gap": round(gap, 4),
                "gap_ci_lo": round(g_lo, 4),
                "gap_ci_hi": round(g_hi, 4),
            }
        )
    return pl.DataFrame(rows)


def score_summary(
    df: pl.DataFrame, price: str, outcome: str = "result_value", cluster: str = "event_id"
) -> dict:
    d = df.drop_nulls([price, outcome, cluster])
    p, y = d[price].to_numpy(), d[outcome].to_numpy()
    return {
        "n": len(p),
        "clusters": d[cluster].n_unique(),
        "brier": round(brier(p, y), 5),
        "log_loss": round(log_loss(p, y), 5),
        "base_rate": round(float(y.mean()), 4),
        "brier_of_base_rate": round(brier(np.full_like(p, y.mean()), y), 5),
    }
