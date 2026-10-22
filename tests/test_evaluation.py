import numpy as np
import polars as pl

from drc.research.evaluation import (
    brier,
    calibration,
    cluster_bootstrap_mean,
    log_loss,
    score_summary,
)


def test_scores_on_known_values():
    p, y = np.array([0.9, 0.1]), np.array([1.0, 0.0])
    assert abs(brier(p, y) - 0.01) < 1e-12
    assert abs(log_loss(p, y) - (-np.log(0.9))) < 1e-9
    assert abs(brier(np.array([0.5, 0.5]), y) - 0.25) < 1e-12


def synthetic(n, bias=0.0, cluster_size=1, seed=0):
    rng = np.random.default_rng(seed)
    n_clusters = n // cluster_size
    base = rng.uniform(0.05, 0.95, n_clusters)
    price = np.repeat(base, cluster_size)
    # outcomes within a cluster share a latent shock, creating dependence
    shock = np.repeat(rng.uniform(size=n_clusters), cluster_size)
    y = (shock < np.clip(price + bias, 0, 1)).astype(float)
    return pl.DataFrame(
        {
            "price": price,
            "result_value": y,
            "event_id": np.repeat(np.arange(n_clusters), cluster_size).astype(str),
        }
    )


def test_calibrated_data_has_gap_ci_covering_zero_and_biased_data_does_not():
    ok = calibration(synthetic(6000), n_boot=500)
    covered = (ok["gap_ci_lo"] <= 0.0) & (ok["gap_ci_hi"] >= 0.0)
    # 95% intervals: by chance ~1 bin in 20 misses zero even for perfectly calibrated data
    assert covered.mean() >= 0.8
    biased = (
        calibration(synthetic(6000, bias=0.15), n_boot=500)
        .filter(pl.col("avg_price") > 0.2)
        .filter(pl.col("avg_price") < 0.8)
    )
    assert (biased["gap_ci_lo"] > 0.0).all()


def test_cluster_bootstrap_is_wider_than_independent_when_clusters_are_large():
    rng = np.random.default_rng(1)
    k, size = 40, 50
    cluster_effect = rng.normal(0, 1, k)
    clusters = np.repeat(np.arange(k), size)
    values = cluster_effect[clusters] + rng.normal(0, 0.1, k * size)
    _, lo_c, hi_c = cluster_bootstrap_mean(values, clusters, n_boot=1000)
    _, lo_i, hi_i = cluster_bootstrap_mean(values, np.arange(len(values)), n_boot=1000)
    assert (hi_c - lo_c) > 3 * (hi_i - lo_i)  # ignoring clustering badly overstates precision


def test_score_summary_reports_base_rate_benchmark():
    df = pl.DataFrame(
        {
            "price": [0.9, 0.2, 0.7, 0.4],
            "result_value": [1.0, 0.0, 1.0, 1.0],
            "event_id": list("abcd"),
        }
    )
    s = score_summary(df, "price")
    assert s["n"] == 4 and s["clusters"] == 4 and s["base_rate"] == 0.75
    assert s["brier"] < s["brier_of_base_rate"]  # informative prices beat the constant forecast


def test_all_wins_bin_gets_an_exact_interval_not_a_collapsed_bootstrap():
    # 94 of 94 contracts priced ~97.5% won: the data cannot rule out a true rate near 97%.
    rng = np.random.default_rng(0)
    df = pl.DataFrame(
        {
            "price": 0.95 + rng.uniform(0, 0.05, 94),
            "result_value": np.ones(94),
            "event_id": np.arange(94).astype(str),
        }
    )
    row = calibration(df, n_boot=300).to_dicts()[0]
    assert row["boundary"] is True
    assert row["gap_ci_lo"] < 0.0 < row["gap_ci_hi"]  # not (falsely) significant
