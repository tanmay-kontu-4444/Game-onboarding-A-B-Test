"""Reusable statistical functions for A/B test analysis.

Conventions
-----------
* "diff" is always treatment - control; "rel" is treatment / control - 1.
* Confidence intervals are two-sided at level ``1 - alpha``.
* Relative-lift CIs use the delta method on the ratio of two independent means.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, Sequence

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy import stats
from statsmodels.stats.multitest import multipletests

Verdict = Literal["PASS", "INCONCLUSIVE", "FAIL"]


# =============================================================================
# Result container
# =============================================================================
@dataclass
class EffectEstimate:
    """Treatment-vs-control comparison of one metric."""

    metric: str
    method: str
    n_control: int
    n_treatment: int
    control_mean: float
    treatment_mean: float
    abs_diff: float
    abs_ci_low: float
    abs_ci_high: float
    rel_diff: float
    rel_ci_low: float
    rel_ci_high: float
    p_value: float
    se: float

    @property
    def ci_width(self) -> float:
        """Width of the absolute-difference CI."""
        return self.abs_ci_high - self.abs_ci_low

    def to_dict(self) -> dict[str, float | int | str]:
        """Plain-dict view (for DataFrames / JSON)."""
        return asdict(self)


def _z(alpha: float) -> float:
    return float(stats.norm.ppf(1 - alpha / 2))


# =============================================================================
# Relative lift (delta method)
# =============================================================================
def relative_lift_ci(
    mean_c: float, var_mean_c: float, mean_t: float, var_mean_t: float, alpha: float = 0.05
) -> tuple[float, float, float]:
    """Delta-method CI for ``mean_t / mean_c - 1`` with independent arms.

    ``var_mean_*`` are the sampling variances of the arm means (e.g. p(1-p)/n).
    Var(T/C) ~= var_T / C^2 + T^2 var_C / C^4.
    """
    rel = mean_t / mean_c - 1.0
    se = np.sqrt(var_mean_t / mean_c**2 + mean_t**2 * var_mean_c / mean_c**4)
    z = _z(alpha)
    return float(rel), float(rel - z * se), float(rel + z * se)


# =============================================================================
# Two-sample tests
# =============================================================================
def two_proportion_ztest(
    x_c: int, n_c: int, x_t: int, n_t: int, alpha: float = 0.05, metric: str = ""
) -> EffectEstimate:
    """Two-proportion z-test (pooled SE for the p-value) with a Wald CI (unpooled SE).

    Matches ``statsmodels`` ``proportions_ztest`` (p-value) and
    ``confint_proportions_2indep(method='wald')`` (CI).
    """
    p_c, p_t = x_c / n_c, x_t / n_t
    diff = p_t - p_c
    p_pool = (x_c + x_t) / (n_c + n_t)
    se_pool = np.sqrt(p_pool * (1 - p_pool) * (1 / n_c + 1 / n_t))
    z_stat = diff / se_pool if se_pool > 0 else 0.0
    p_value = 2 * stats.norm.sf(abs(z_stat))
    var_c, var_t = p_c * (1 - p_c) / n_c, p_t * (1 - p_t) / n_t
    se = np.sqrt(var_c + var_t)
    z = _z(alpha)
    rel, rlo, rhi = relative_lift_ci(p_c, var_c, p_t, var_t, alpha)
    return EffectEstimate(metric, "two-proportion z-test", n_c, n_t, p_c, p_t, diff,
                          diff - z * se, diff + z * se, rel, rlo, rhi, float(p_value), float(se))


def welch_ttest(y_c: np.ndarray, y_t: np.ndarray, alpha: float = 0.05, metric: str = "") -> EffectEstimate:
    """Welch's unequal-variance t-test on means, with a Welch-df CI."""
    y_c, y_t = np.asarray(y_c, float), np.asarray(y_t, float)
    n_c, n_t = y_c.size, y_t.size
    m_c, m_t = y_c.mean(), y_t.mean()
    var_c, var_t = y_c.var(ddof=1) / n_c, y_t.var(ddof=1) / n_t
    se = np.sqrt(var_c + var_t)
    df = (var_c + var_t) ** 2 / (var_c**2 / (n_c - 1) + var_t**2 / (n_t - 1))
    t_crit = stats.t.ppf(1 - alpha / 2, df)
    diff = m_t - m_c
    p_value = 2 * stats.t.sf(abs(diff / se), df)
    rel, rlo, rhi = relative_lift_ci(m_c, var_c, m_t, var_t, alpha)
    return EffectEstimate(metric, "Welch t-test", n_c, n_t, float(m_c), float(m_t), float(diff),
                          float(diff - t_crit * se), float(diff + t_crit * se), rel, rlo, rhi,
                          float(p_value), float(se))


def mann_whitney_p(y_c: np.ndarray, y_t: np.ndarray) -> float:
    """Two-sided Mann-Whitney U p-value (distributional sensitivity check)."""
    return float(stats.mannwhitneyu(y_t, y_c, alternative="two-sided").pvalue)


# =============================================================================
# Bootstrap
# =============================================================================
def bootstrap_diff_ci(
    y_c: np.ndarray, y_t: np.ndarray, n_boot: int = 2000, alpha: float = 0.05,
    seed: int = 0, chunk: int = 100,
) -> dict[str, float]:
    """Percentile bootstrap CIs for the difference and relative lift in means.

    Resamples players within each arm (stratified bootstrap). Binary outcomes
    are resampled exactly via the binomial distribution, which is equivalent
    and much faster.
    """
    rng = np.random.default_rng(seed)
    y_c, y_t = np.asarray(y_c, float), np.asarray(y_t, float)
    is_binary = np.isin(y_c, (0.0, 1.0)).all() and np.isin(y_t, (0.0, 1.0)).all()
    if is_binary:
        m_c = rng.binomial(y_c.size, y_c.mean(), n_boot) / y_c.size
        m_t = rng.binomial(y_t.size, y_t.mean(), n_boot) / y_t.size
    else:
        m_c, m_t = np.empty(n_boot), np.empty(n_boot)
        for start in range(0, n_boot, chunk):
            k = min(chunk, n_boot - start)
            m_c[start:start + k] = y_c[rng.integers(0, y_c.size, (k, y_c.size))].mean(axis=1)
            m_t[start:start + k] = y_t[rng.integers(0, y_t.size, (k, y_t.size))].mean(axis=1)
    diff, rel = m_t - m_c, m_t / m_c - 1
    q = [100 * alpha / 2, 100 * (1 - alpha / 2)]
    d_lo, d_hi = np.percentile(diff, q)
    r_lo, r_hi = np.percentile(rel, q)
    return {"abs_ci_low": float(d_lo), "abs_ci_high": float(d_hi),
            "rel_ci_low": float(r_lo), "rel_ci_high": float(r_hi), "n_boot": n_boot}


# =============================================================================
# Variance reduction
# =============================================================================
def cuped(
    y: np.ndarray, x: np.ndarray, treat: np.ndarray, alpha: float = 0.05, metric: str = ""
) -> tuple[EffectEstimate, float]:
    """CUPED: y_adj = y - theta (x - mean(x)), theta = cov(y, x) / var(x) (pooled).

    Returns the Welch estimate on the adjusted outcome and theta. Relative
    lift is computed against the *unadjusted* control mean (the adjustment
    has mean zero, so the control mean is unchanged in expectation).
    """
    y, x, treat = np.asarray(y, float), np.asarray(x, float), np.asarray(treat, bool)
    theta = np.cov(y, x, ddof=1)[0, 1] / np.var(x, ddof=1)
    y_adj = y - theta * (x - x.mean())
    est = welch_ttest(y_adj[~treat], y_adj[treat], alpha, metric)
    est.method = "CUPED (day-0 sessions)"
    m_c = y[~treat].mean()
    var_c = y_adj[~treat].var(ddof=1) / (~treat).sum()
    var_t = y_adj[treat].var(ddof=1) / treat.sum()
    est.control_mean, est.treatment_mean = float(m_c), float(m_c + est.abs_diff)
    est.rel_diff, est.rel_ci_low, est.rel_ci_high = relative_lift_ci(m_c, var_c, m_c + est.abs_diff, var_t, alpha)
    return est, float(theta)


def ols_adjusted(
    df: pd.DataFrame, outcome: str, covariates: Sequence[str], alpha: float = 0.05
) -> EffectEstimate:
    """OLS regression adjustment: outcome ~ treat + covariates, HC2 robust SEs.

    Numeric covariates are mean-centred so the treatment coefficient is the
    average treatment effect; string covariates enter as categorical dummies.
    """
    d = df[[outcome, "variant", *covariates]].copy()
    d["treat"] = (d["variant"] == "treatment").astype(float)
    terms = []
    for c in covariates:
        if pd.api.types.is_numeric_dtype(d[c]):
            d[c] = d[c] - d[c].mean()
            terms.append(c)
        else:
            terms.append(f"C({c})")
    fit = smf.ols(f"{outcome} ~ treat + {' + '.join(terms)}", data=d).fit(cov_type="HC2")
    coef, se = float(fit.params["treat"]), float(fit.bse["treat"])
    lo, hi = fit.conf_int(alpha).loc["treat"]
    n_c, n_t = int((d["treat"] == 0).sum()), int((d["treat"] == 1).sum())
    m_c = float(d.loc[d["treat"] == 0, outcome].mean())
    rel_se = se / m_c
    z = _z(alpha)
    return EffectEstimate(outcome, "OLS adjusted (HC2)", n_c, n_t, m_c, m_c + coef, coef, float(lo),
                          float(hi), coef / m_c, coef / m_c - z * rel_se, coef / m_c + z * rel_se,
                          float(fit.pvalues["treat"]), se)


# =============================================================================
# Validity checks
# =============================================================================
def srm_check(observed: Sequence[int], expected_ratio: Sequence[float] = (0.5, 0.5),
              alpha: float = 0.001) -> dict[str, float | bool]:
    """Sample-ratio-mismatch chi-square goodness-of-fit test."""
    obs = np.asarray(observed, float)
    ratio = np.asarray(expected_ratio, float) / np.sum(expected_ratio)
    exp = ratio * obs.sum()
    chi2, p = stats.chisquare(obs, exp)
    return {"chi2": float(chi2), "p_value": float(p), "srm_detected": bool(p < alpha),
            "observed_share_first": float(obs[0] / obs.sum())}


def standardized_mean_difference(x_c: np.ndarray, x_t: np.ndarray) -> float:
    """SMD = (mean_t - mean_c) / sqrt((var_t + var_c) / 2)."""
    x_c, x_t = np.asarray(x_c, float), np.asarray(x_t, float)
    pooled = np.sqrt((x_c.var(ddof=1) + x_t.var(ddof=1)) / 2)
    return float((x_t.mean() - x_c.mean()) / pooled) if pooled > 0 else 0.0


# =============================================================================
# Multiplicity and heterogeneity
# =============================================================================
def adjust_pvalues(p_values: Sequence[float], method: Literal["holm", "fdr_bh"]) -> np.ndarray:
    """Holm (FWER) or Benjamini-Hochberg (FDR) adjusted p-values."""
    return multipletests(np.asarray(p_values, float), method=method)[1]


def cochran_q(effects: Sequence[float], ses: Sequence[float]) -> dict[str, float]:
    """Cochran's Q test that a set of independent effects share one true value."""
    e, s = np.asarray(effects, float), np.asarray(ses, float)
    w = 1 / s**2
    pooled = np.sum(w * e) / np.sum(w)
    q = float(np.sum(w * (e - pooled) ** 2))
    df = e.size - 1
    return {"Q": q, "df": df, "p_value": float(stats.chi2.sf(q, df)), "pooled_effect": float(pooled)}


# =============================================================================
# Guardrails
# =============================================================================
def guardrail_verdict(
    estimate: float, ci_low: float, ci_high: float, threshold: float,
    direction: Literal["lower_is_worse", "higher_is_worse"],
) -> Verdict:
    """Classify a guardrail against its unacceptable-degradation threshold.

    PASS          the CI's worse bound is on the acceptable side of the threshold
    FAIL          the point estimate itself is beyond the threshold
    INCONCLUSIVE  point estimate acceptable but the CI's worse bound crosses it
    """
    if direction == "lower_is_worse":
        if estimate < threshold:
            return "FAIL"
        return "PASS" if ci_low > threshold else "INCONCLUSIVE"
    if estimate > threshold:
        return "FAIL"
    return "PASS" if ci_high < threshold else "INCONCLUSIVE"
