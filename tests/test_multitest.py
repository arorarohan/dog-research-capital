import math

import numpy as np
import pytest

from drc.research.multitest import (
    benjamini_hochberg,
    bonferroni,
    deflated_sharpe_ratio,
    deflated_z,
    expected_max_sharpe,
    holm,
    p_from_ci,
    p_from_z,
    probabilistic_sharpe_ratio,
    reject,
    z_from_ci,
)

# Benjamini & Hochberg (1995), section 4: 15 p-values from a clinical trial. At level 0.05 BH
# rejects the four smallest; Bonferroni (0.05 / 15 = 0.0033) and Holm reject three.
BH_1995 = [
    0.0001,
    0.0004,
    0.0019,
    0.0095,
    0.0201,
    0.0278,
    0.0298,
    0.0344,
    0.0459,
    0.3240,
    0.4262,
    0.5719,
    0.6528,
    0.7590,
    1.0000,
]


def test_bh_1995_example_rejection_counts():
    assert reject(benjamini_hochberg(BH_1995)).sum() == 4
    assert reject(holm(BH_1995)).sum() == 3
    assert reject(bonferroni(BH_1995)).sum() == 3


def test_adjusted_values_match_r_p_adjust():
    # R: p.adjust(c(0.01, 0.04, 0.03, 0.005), "bonferroni" | "holm" | "BH")
    p = [0.01, 0.04, 0.03, 0.005]
    np.testing.assert_allclose(bonferroni(p), [0.04, 0.16, 0.12, 0.02])
    np.testing.assert_allclose(holm(p), [0.03, 0.06, 0.06, 0.02])
    np.testing.assert_allclose(benjamini_hochberg(p), [0.02, 0.04, 0.04, 0.02])


def test_ordering_between_methods_and_caps():
    rng = np.random.default_rng(0)
    p = rng.uniform(size=50) ** 3
    b, h, q = bonferroni(p), holm(p), benjamini_hochberg(p)
    assert np.all(h <= b + 1e-15)  # Holm is never stricter than Bonferroni
    assert np.all(q <= h + 1e-15)  # BH is never stricter than Holm
    assert np.all(q >= p) and np.all(b <= 1.0)


def test_extra_unrecorded_tests_make_corrections_stricter():
    p = [0.001, 0.02]
    np.testing.assert_allclose(bonferroni(p, m=10), [0.01, 0.2])
    np.testing.assert_allclose(holm(p, m=10), [0.01, 0.18])  # 0.02 * 9
    np.testing.assert_allclose(benjamini_hochberg(p, m=10), [0.01, 0.1])  # 0.02 * 10 / 2
    with pytest.raises(ValueError):
        holm(p, m=1)


def test_invalid_pvalues_rejected():
    with pytest.raises(ValueError):
        holm([0.1, 1.2])
    with pytest.raises(ValueError):
        benjamini_hochberg([float("nan")])
    assert len(holm([])) == 0


def test_p_from_ci_is_consistent_with_the_interval():
    # A symmetric 95% CI that just touches zero gives p = 0.05.
    assert math.isclose(p_from_ci(1.96, 0.0, 3.92), 0.05, rel_tol=1e-3)
    # Uses the side facing zero: an interval excluding 0 always yields p < 0.05.
    assert p_from_ci(1.2, 0.5, 1.9) < 0.05
    assert p_from_ci(-0.8, -3.4, 1.7) > 0.05
    assert math.isclose(z_from_ci(-2.0, -3.0, -1.0), -2.0 * 1.959964, rel_tol=1e-5)
    with pytest.raises(ValueError):
        z_from_ci(5.0, 0.0, 1.0)


def test_p_from_z_known_values():
    assert math.isclose(p_from_z(1.959964), 0.05, rel_tol=1e-5)
    assert math.isclose(p_from_z(1.644854, two_sided=False), 0.05, rel_tol=1e-5)


def test_expected_max_of_standard_normals():
    assert expected_max_sharpe(1) == 0.0
    # The approximation is close to the exact E[max] of N standard normals (1.539 for 10,
    # 2.508 for 100) and grows slowly with N.
    assert abs(expected_max_sharpe(10) - 1.539) < 0.05
    assert abs(expected_max_sharpe(100) - 2.508) < 0.05
    assert expected_max_sharpe(1000) > expected_max_sharpe(100)


def test_deflated_sharpe_matches_bailey_lopez_de_prado_example():
    # Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio", numerical example:
    # 100 trials, variance of annualized Sharpe across trials 0.5, best annualized SR 2.5,
    # 1250 daily observations (250 per year), skew -3, kurtosis 10 -> SR_0 ~ 0.1132 per day
    # and DSR ~ 0.9004.
    per_year = 250
    sr0 = expected_max_sharpe(100, 0.5 / per_year)
    assert abs(sr0 - 0.1132) < 5e-4
    dsr = deflated_sharpe_ratio(
        2.5 / math.sqrt(per_year), 1250, 100, var_trials=0.5 / per_year, skew=-3, kurtosis=10
    )
    assert abs(dsr - 0.9004) < 1e-3


def test_psr_and_deflated_z_agree_under_normal_null():
    # With normal returns and the null trial variance, DSR ~ Phi(z - E[max_N]).
    n, sr = 10_000, 0.03
    z = sr * math.sqrt(n - 1)
    assert math.isclose(probabilistic_sharpe_ratio(sr, n), 0.9986, abs_tol=1e-3)
    for trials in (1, 10, 200):
        assert abs(deflated_sharpe_ratio(sr, n, trials) - deflated_z(z, trials)) < 2e-3
    assert deflated_z(z, 1) > deflated_z(z, 200)  # more trials, bigger haircut
