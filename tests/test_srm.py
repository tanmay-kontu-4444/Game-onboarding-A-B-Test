"""Sample-ratio-mismatch check."""

from __future__ import annotations

import pytest
from scipy import stats

from src import stats_utils as su


def test_balanced_split_no_srm():
    r = su.srm_check([40_000, 40_100])
    assert not r["srm_detected"]
    assert r["p_value"] > 0.5


def test_imbalance_at_scale_is_detected():
    r = su.srm_check([40_600, 39_400])  # 50.75% / 49.25%: chi2 = 2 * 600^2 / 40,000 = 18
    assert r["chi2"] == pytest.approx(18.0)
    assert r["srm_detected"]
    assert r["p_value"] < 0.001


def test_threshold_is_strict():
    r = su.srm_check([40_400, 39_600])  # chi2 = 8, p ~ 0.005: suspicious but below the 0.001 alarm
    assert 0.001 < r["p_value"] < 0.01
    assert not r["srm_detected"]


def test_matches_scipy_chisquare():
    obs = [39_604, 39_297]
    r = su.srm_check(obs)
    ref = stats.chisquare(obs, [sum(obs) / 2] * 2)
    assert r["chi2"] == pytest.approx(ref.statistic)
    assert r["p_value"] == pytest.approx(ref.pvalue)


def test_unequal_expected_ratio():
    r = su.srm_check([9_000, 1_000], expected_ratio=(0.9, 0.1))
    assert not r["srm_detected"] and r["chi2"] == pytest.approx(0.0)


def test_pipeline_has_no_srm(results):
    main = [s for s in results["srm"] if not s["population"].startswith("Excluded")]
    assert len(main) == 2
    assert not any(s["srm_detected"] for s in results["srm"])
