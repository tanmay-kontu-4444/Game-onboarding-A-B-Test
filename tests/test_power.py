"""Power / sample-size calculations against statsmodels."""

from __future__ import annotations

import re

import pytest
from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize

from src import config, power


@pytest.mark.parametrize("p_base,mde", [(0.18, 0.010), (0.18, 0.006), (0.38, 0.015), (0.025, -0.003), (0.5, 0.02)])
def test_sample_size_matches_solve_power(p_base, mde):
    h = proportion_effectsize(p_base + mde, p_base)
    ref = NormalIndPower().solve_power(effect_size=abs(h), alpha=0.05, power=0.8, ratio=1.0)
    n = power.n_per_arm_proportions(p_base, mde)
    assert n == pytest.approx(ref, abs=1.0)  # ours is ceil()'d
    assert n >= ref


@pytest.mark.parametrize("n", [5_000, 23_665, 40_000])
def test_power_matches_statsmodels(n):
    h = proportion_effectsize(0.19, 0.18)
    ref = NormalIndPower().power(effect_size=h, nobs1=n, alpha=0.05, ratio=1.0)
    assert power.power_proportions(0.18, 0.01, n) == pytest.approx(ref, rel=1e-9)


def test_mde_inverts_sample_size():
    n = power.n_per_arm_proportions(0.18, 0.01)
    assert power.mde_proportions(0.18, n) == pytest.approx(0.01, abs=2e-5)


def test_mde_means_formula():
    # (1.96 + 0.8416) * sd * sqrt(2/n)
    assert power.mde_means(1.0, 10_000) == pytest.approx(2.8016 * (2 / 10_000) ** 0.5, rel=1e-3)


def test_headline_numbers():
    s = power.summary()
    assert s["n_per_arm_for_design_mde"] == 23_665
    assert s["power_at_design_mde_planned_n"] > 0.9


def test_plan_power_section_is_current():
    """The pre-registration's generated power block must equal what power.py produces now."""
    plan = (config.DOCS_DIR / "01_experiment_plan.md").read_text()
    block = re.search(r"<!-- BEGIN:power.*?-->\n(.*?)<!-- END:power -->", plan, re.S).group(1)
    assert block == power.render_plan_section()


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        power.n_per_arm_proportions(0.18, 0.0)
    with pytest.raises(ValueError):
        power.n_per_arm_proportions(0.99, 0.02)
