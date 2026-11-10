"""Effect recovery: estimates vs the simulator's ground truth (validation only)."""

from __future__ import annotations

from pathlib import Path

from src import config, recovery_check


def test_primary_true_effect_inside_ci(results, ground_truth):
    prim = next(r for r in results["scorecard"] if r["metric"] == config.PRIMARY_METRIC)
    truth = ground_truth["true_effects"][config.PRIMARY_METRIC]["abs_diff"]
    assert prim["abs_ci_low"] <= truth <= prim["abs_ci_high"]
    # and the confirmatory test detects it
    assert prim["abs_ci_low"] > 0


def test_primary_relative_lift_inside_ci(results, ground_truth):
    prim = next(r for r in results["scorecard"] if r["metric"] == config.PRIMARY_METRIC)
    truth = ground_truth["true_effects"][config.PRIMARY_METRIC]["rel_diff"]
    assert prim["rel_ci_low"] <= truth <= prim["rel_ci_high"]


def test_all_metrics_inside_bonferroni_ci(results, ground_truth):
    """With 8 metrics, ~1 miss of a 95% CI is expected; Bonferroni CIs should all cover."""
    mr = recovery_check.metric_recovery(ground_truth, results)
    assert mr["in_bonferroni_ci"].all(), mr[~mr["in_bonferroni_ci"]]
    assert mr["in_95ci"].sum() >= len(mr) - 2


def test_segment_effects_inside_bonferroni_ci(results, ground_truth):
    sr = recovery_check.segment_recovery(ground_truth, results)
    assert sr["in_bonferroni_ci"].all(), sr[~sr["in_bonferroni_ci"]]


def test_simulated_baseline_is_as_designed(ground_truth):
    te = ground_truth["true_effects"]
    assert abs(te["d7_retained"]["control"] - 0.18) < 0.005
    assert abs(te["d7_retained"]["abs_diff"] - 0.012) < 1e-4


def test_analysis_code_never_reads_ground_truth():
    src = Path(config.ROOT) / "src"
    for name in ["analysis.py", "stats_utils.py", "cohorts.py", "report.py", "power.py"]:
        assert "ground_truth" not in (src / name).read_text(), name
    for sql in (Path(config.ROOT) / "sql").glob("*.sql"):
        assert "ground_truth" not in sql.read_text()
