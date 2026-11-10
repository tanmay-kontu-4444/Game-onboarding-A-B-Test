"""Unit tests for src/stats_utils.py against statsmodels / scipy reference implementations."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.proportion import confint_proportions_2indep, proportions_ztest
from statsmodels.stats.weightstats import CompareMeans, DescrStatsW

from src import stats_utils as su

PROPORTION_CASES = [
    (1800, 10_000, 1950, 10_000),
    (7053, 39_604, 7456, 39_297),
    (25, 1_000, 18, 1_100),
    (500, 2_000, 500, 2_000),
]


@pytest.mark.parametrize("x_c,n_c,x_t,n_t", PROPORTION_CASES)
def test_ztest_pvalue_matches_statsmodels(x_c, n_c, x_t, n_t):
    est = su.two_proportion_ztest(x_c, n_c, x_t, n_t)
    _, p_ref = proportions_ztest([x_t, x_c], [n_t, n_c])
    assert est.p_value == pytest.approx(p_ref, rel=1e-9, abs=1e-12)


@pytest.mark.parametrize("x_c,n_c,x_t,n_t", PROPORTION_CASES)
def test_wald_ci_matches_statsmodels(x_c, n_c, x_t, n_t):
    est = su.two_proportion_ztest(x_c, n_c, x_t, n_t)
    lo, hi = confint_proportions_2indep(x_t, n_t, x_c, n_c, method="wald", compare="diff")
    assert est.abs_ci_low == pytest.approx(lo, abs=1e-12)
    assert est.abs_ci_high == pytest.approx(hi, abs=1e-12)
    assert est.abs_diff == pytest.approx(x_t / n_t - x_c / n_c)


def test_relative_lift_delta_method_known_value():
    # p_c = 0.18, p_t = 0.192, n = 40k each: closed-form delta-method SE
    p_c, p_t, n = 0.18, 0.192, 40_000
    var_c, var_t = p_c * (1 - p_c) / n, p_t * (1 - p_t) / n
    rel, lo, hi = su.relative_lift_ci(p_c, var_c, p_t, var_t)
    se = np.sqrt(var_t / p_c**2 + p_t**2 * var_c / p_c**4)
    assert rel == pytest.approx(p_t / p_c - 1)
    assert hi - lo == pytest.approx(2 * 1.959963985 * se, rel=1e-6)


def test_relative_lift_ci_has_nominal_coverage():
    rng = np.random.default_rng(1)
    p_c, p_t, n = 0.18, 0.19, 20_000
    covered = 0
    for _ in range(1_000):
        c, t = rng.binomial(n, p_c) / n, rng.binomial(n, p_t) / n
        _, lo, hi = su.relative_lift_ci(c, c * (1 - c) / n, t, t * (1 - t) / n)
        covered += lo <= p_t / p_c - 1 <= hi
    assert 0.93 <= covered / 1_000 <= 0.97


def test_welch_matches_scipy_and_statsmodels():
    rng = np.random.default_rng(0)
    y_c, y_t = rng.lognormal(0, 1.2, 3_000), rng.lognormal(0.05, 1.3, 2_500)
    est = su.welch_ttest(y_c, y_t)
    ref = stats.ttest_ind(y_t, y_c, equal_var=False)
    assert est.p_value == pytest.approx(ref.pvalue, rel=1e-9)
    lo, hi = CompareMeans(DescrStatsW(y_t), DescrStatsW(y_c)).tconfint_diff(usevar="unequal")
    assert (est.abs_ci_low, est.abs_ci_high) == pytest.approx((lo, hi), rel=1e-9)


def test_bootstrap_ci_close_to_analytic_for_binary():
    rng = np.random.default_rng(3)
    y_c, y_t = rng.binomial(1, 0.18, 40_000), rng.binomial(1, 0.19, 40_000)
    est = su.two_proportion_ztest(y_c.sum(), y_c.size, y_t.sum(), y_t.size)
    boot = su.bootstrap_diff_ci(y_c, y_t, n_boot=4_000, seed=1)
    assert boot["abs_ci_low"] == pytest.approx(est.abs_ci_low, abs=6e-4)
    assert boot["abs_ci_high"] == pytest.approx(est.abs_ci_high, abs=6e-4)


def test_bootstrap_continuous_close_to_welch():
    rng = np.random.default_rng(4)
    y_c, y_t = rng.normal(10, 3, 5_000), rng.normal(10.2, 3, 5_000)
    est = su.welch_ttest(y_c, y_t)
    boot = su.bootstrap_diff_ci(y_c, y_t, n_boot=1_000, seed=2)
    assert boot["abs_ci_low"] == pytest.approx(est.abs_ci_low, abs=0.03)
    assert boot["abs_ci_high"] == pytest.approx(est.abs_ci_high, abs=0.03)


def test_cuped_unbiased_and_narrower():
    rng = np.random.default_rng(5)
    n = 50_000
    x = rng.poisson(2.3, n)
    treat = rng.random(n) < 0.5
    y = (rng.random(n) < 0.08 + 0.04 * x + 0.01 * treat).astype(float)
    raw = su.welch_ttest(y[~treat], y[treat])
    adj, theta = su.cuped(y, x, treat)
    assert theta > 0
    assert adj.ci_width < raw.ci_width
    assert adj.abs_diff == pytest.approx(0.01, abs=3 * adj.se)


def test_ols_adjusted_matches_cuped_with_single_covariate():
    rng = np.random.default_rng(6)
    n = 20_000
    df = pd.DataFrame({"x": rng.normal(size=n), "variant": np.where(rng.random(n) < 0.5, "treatment", "control")})
    df["y"] = 1 + 0.5 * df["x"] + 0.1 * (df["variant"] == "treatment") + rng.normal(size=n)
    ols = su.ols_adjusted(df, "y", ["x"])
    cup, _ = su.cuped(df["y"], df["x"], df["variant"] == "treatment")
    assert ols.abs_diff == pytest.approx(cup.abs_diff, abs=2e-3)
    assert ols.se == pytest.approx(cup.se, rel=0.02)


def test_holm_and_bh_match_statsmodels():
    p = [0.001, 0.02, 0.04, 0.3]
    assert np.allclose(su.adjust_pvalues(p, "holm"), multipletests(p, method="holm")[1])
    assert np.allclose(su.adjust_pvalues(p, "fdr_bh"), multipletests(p, method="fdr_bh")[1])


def test_cochran_q_homogeneous_and_heterogeneous():
    same = su.cochran_q([0.01, 0.01, 0.01], [0.005, 0.005, 0.005])
    assert same["Q"] == pytest.approx(0.0) and same["p_value"] == pytest.approx(1.0)
    diff = su.cochran_q([0.0, 0.03], [0.005, 0.005])
    assert diff["Q"] == pytest.approx(0.03**2 / (2 * 0.005**2))  # = 18
    assert diff["p_value"] < 0.001


@pytest.mark.parametrize(
    "est,lo,hi,thr,direction,expected",
    [
        (0.02, -0.05, 0.09, -0.10, "lower_is_worse", "PASS"),
        (0.02, -0.12, 0.16, -0.10, "lower_is_worse", "INCONCLUSIVE"),
        (-0.11, -0.20, -0.02, -0.10, "lower_is_worse", "FAIL"),
        (0.01, -0.04, 0.05, 0.10, "higher_is_worse", "PASS"),
        (0.05, -0.02, 0.12, 0.10, "higher_is_worse", "INCONCLUSIVE"),
        (0.12, 0.05, 0.19, 0.10, "higher_is_worse", "FAIL"),
    ],
)
def test_guardrail_verdict(est, lo, hi, thr, direction, expected):
    assert su.guardrail_verdict(est, lo, hi, thr, direction) == expected


def test_smd_zero_for_identical_and_scaled_for_shift():
    x = np.arange(100.0)
    assert su.standardized_mean_difference(x, x) == 0
    assert su.standardized_mean_difference(x, x + x.std(ddof=1)) == pytest.approx(1.0)
