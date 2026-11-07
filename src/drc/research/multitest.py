"""Multiple-testing corrections: how much to believe a result after looking at many.

If you run 20 independent tests of true-null hypotheses at the 5% level, you expect one to
"pass" by luck. Every cell, horizon, price band and spread we look at is such a test, so a raw
p-value (or a 95% interval that excludes zero) overstates the evidence. This module turns raw
p-values into corrected ones:

- **Bonferroni** and **Holm** control the family-wise error rate (FWER): the chance that *any*
  rejected hypothesis is a false positive. Holm is uniformly more powerful than Bonferroni and
  needs no independence assumption.
- **Benjamini-Hochberg** (BH) controls the false discovery rate (FDR): the expected *share* of
  rejected hypotheses that are false. Less strict; valid for independent or positively
  dependent tests (our overlapping horizons are positively dependent).
- **Deflated Sharpe ratio** (Bailey and Lopez de Prado, 2014): the probability that a strategy's
  true Sharpe is above zero after accounting for having picked it as the best of `n_trials`
  tries (and for non-normal returns). It is the "haircut" for strategy search.

Plain numpy and the standard library (no scipy dependency).
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

_N = NormalDist()
EULER_GAMMA = 0.5772156649015329


def _as_pvalues(p) -> np.ndarray:
    a = np.asarray(p, dtype=float)
    if a.ndim != 1:
        raise ValueError("p-values must be a 1-D sequence")
    if np.any(np.isnan(a)) or np.any((a < 0) | (a > 1)):
        raise ValueError("p-values must be in [0, 1]")
    return a


def bonferroni(p, m: int | None = None) -> np.ndarray:
    """Bonferroni-adjusted p-values: min(1, p * m).

    `m` defaults to len(p); pass a larger `m` when more tests were run than you have p-values
    for (cells looked at but not recorded). Reject where adjusted p <= alpha.
    """
    a = _as_pvalues(p)
    m = len(a) if m is None else m
    if m < len(a):
        raise ValueError("m cannot be smaller than the number of p-values")
    return np.minimum(1.0, a * m)


def holm(p, m: int | None = None) -> np.ndarray:
    """Holm step-down adjusted p-values (FWER control, same guarantee as Bonferroni).

    Sort ascending; the i-th smallest (1-based) is multiplied by (m - i + 1), then a running
    maximum keeps the adjusted values monotone. Matches R's p.adjust(method="holm") when
    m == len(p). With m > len(p) the unrecorded tests are treated as having p = 1, which is
    valid only if every unrecorded p was larger than every recorded one; when that is not
    known, use `bonferroni(p, m)`, which is always valid.
    """
    a = _as_pvalues(p)
    n = len(a)
    m = n if m is None else m
    if m < n:
        raise ValueError("m cannot be smaller than the number of p-values")
    if n == 0:
        return a
    order = np.argsort(a, kind="stable")
    mult = m - np.arange(n)
    adj = np.minimum(1.0, np.maximum.accumulate(a[order] * mult))
    out = np.empty(n)
    out[order] = adj
    return out


def benjamini_hochberg(p, m: int | None = None) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values ("q-values"; FDR control).

    The i-th smallest p (1-based) becomes p * m / i, then a running minimum from the largest
    down keeps them monotone. Matches R's p.adjust(method="BH") when m == len(p). Reject where
    q <= alpha: the expected share of false discoveries among the rejections is then <= alpha.
    """
    a = _as_pvalues(p)
    n = len(a)
    m = n if m is None else m
    if m < n:
        raise ValueError("m cannot be smaller than the number of p-values")
    if n == 0:
        return a
    order = np.argsort(a, kind="stable")
    ranks = np.arange(1, n + 1)
    scaled = a[order] * m / ranks
    adj = np.minimum(1.0, np.minimum.accumulate(scaled[::-1])[::-1])
    out = np.empty(n)
    out[order] = adj
    return out


def reject(adjusted, alpha: float = 0.05) -> np.ndarray:
    """Boolean mask of rejections given adjusted p-values."""
    return np.asarray(adjusted, dtype=float) <= alpha


# --- p-values from what our findings actually report (estimate + confidence interval) ---------


def z_from_ci(estimate: float, lo: float, hi: float, level: float = 0.95) -> float:
    """Approximate z-statistic from a point estimate and its confidence interval.

    Our studies report cluster-bootstrap percentile intervals, not p-values. Assuming the
    sampling distribution is roughly normal, the standard error is the distance from the
    estimate to the interval bound **on the side facing zero**, divided by the normal quantile.
    Using that side (not the average half-width) means the derived two-sided p is below
    1 - level exactly when the interval excludes zero, so the conversion never upgrades a
    result. Inputs are usually rounded to 0.1, so treat the result as approximate.
    """
    if not lo <= estimate <= hi:
        raise ValueError(f"estimate {estimate} is not inside [{lo}, {hi}]")
    zq = _N.inv_cdf(0.5 + level / 2)
    half = (estimate - lo) if estimate >= 0 else (hi - estimate)
    if half <= 0:
        raise ValueError("interval has zero width on the side facing zero")
    return estimate / (half / zq)


def p_from_z(z: float, two_sided: bool = True) -> float:
    """p-value of a z-statistic (two-sided by default; one-sided tests the sign of z)."""
    if two_sided:
        return 2.0 * (1.0 - _N.cdf(abs(z)))
    return 1.0 - _N.cdf(z)


def p_from_ci(estimate: float, lo: float, hi: float, level: float = 0.95) -> float:
    """Two-sided p-value derived from an estimate and its CI (see `z_from_ci`)."""
    return p_from_z(z_from_ci(estimate, lo, hi, level))


# --- Deflated Sharpe ratio --------------------------------------------------------------------


def expected_max_sharpe(n_trials: int, var_trials: float = 1.0) -> float:
    """Expected maximum of `n_trials` Sharpe estimates when every true Sharpe is zero.

    Bailey and Lopez de Prado (2014), eq. for SR_0: sqrt(V) * ((1 - g) * Z^-1(1 - 1/N)
    + g * Z^-1(1 - 1/(N e))), g = Euler-Mascheroni constant. With var_trials = 1 it is the
    expected maximum of N standard normals (approximately): the bar a z-score must clear just
    to match what luck alone produces among N tries.
    """
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if n_trials == 1:
        return 0.0
    a = _N.inv_cdf(1.0 - 1.0 / n_trials)
    b = _N.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(var_trials) * ((1.0 - EULER_GAMMA) * a + EULER_GAMMA * b)


def probabilistic_sharpe_ratio(
    sr: float, n_obs: int, sr_benchmark: float = 0.0, skew: float = 0.0, kurtosis: float = 3.0
) -> float:
    """P(true Sharpe > sr_benchmark) given an estimated per-period Sharpe `sr` over `n_obs`.

    Bailey and Lopez de Prado (2012). `kurtosis` is the raw (not excess) kurtosis; normal = 3.
    Negative skew and fat tails widen the uncertainty of the Sharpe estimate.
    """
    if n_obs < 2:
        raise ValueError("n_obs must be >= 2")
    denom = 1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr**2
    if denom <= 0:
        raise ValueError("non-positive variance term; check skew/kurtosis")
    return _N.cdf((sr - sr_benchmark) * math.sqrt(n_obs - 1) / math.sqrt(denom))


def deflated_sharpe_ratio(
    sr: float,
    n_obs: int,
    n_trials: int,
    var_trials: float | None = None,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Deflated Sharpe ratio: PSR against the Sharpe that the best of `n_trials` would show by luck.

    `sr` is per period (not annualized) and `var_trials` is the variance of the per-period Sharpe
    estimates across the trials. If the trials' Sharpes were not recorded (our case), the default
    is the variance of a Sharpe estimate under the null, 1 / (n_obs - 1): every trial is assumed
    to be noise around zero. A DSR of 0.95 is the usual bar (like p = 0.05, after the haircut).
    """
    if var_trials is None:
        var_trials = 1.0 / (n_obs - 1)
    sr0 = expected_max_sharpe(n_trials, var_trials)
    return probabilistic_sharpe_ratio(sr, n_obs, sr0, skew, kurtosis)


def deflated_z(z: float, n_trials: int) -> float:
    """Deflated-Sharpe haircut for a test reported as a z-statistic.

    With normal returns and the null trial variance, the DSR of a result with z-statistic `z`
    reduces (for large n) to Phi(z - E[max of n_trials standard normals]). Returns that
    probability: >= 0.95 means the result clears the luck bar of `n_trials` tries.
    The sign matters: a negative z is evidence against a positive effect.
    """
    return _N.cdf(z - expected_max_sharpe(n_trials, 1.0))
