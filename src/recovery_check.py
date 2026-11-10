"""Compare analysis estimates with the simulator's ground truth.

This is a VALIDATION step for the synthetic data, kept out of the readout:
a real team would never have ground truth. It checks whether each true
effect lies inside the estimated 95% CI (and inside a Bonferroni-adjusted CI
across all metrics, which is what the automated test asserts).

Run:  ``python -m src.recovery_check``
"""

from __future__ import annotations

import json

import pandas as pd
from scipy.stats import norm

from src import config

VALIDATION_DIR = config.ROOT / "outputs" / "validation"


def load_truth() -> dict:
    """Read ground_truth.json (validation only)."""
    return json.loads((config.RAW_DIR / "ground_truth.json").read_text())


def load_results() -> dict:
    """Read the analysis results."""
    return json.loads((config.TABLES_DIR / "results.json").read_text())


def metric_recovery(truth: dict, results: dict, family_alpha: float = 0.05) -> pd.DataFrame:
    """True vs estimated absolute effect for every scorecard metric."""
    sc = pd.DataFrame(results["scorecard"])
    z_bonf = norm.ppf(1 - family_alpha / (2 * len(sc)))
    rows = []
    for r in sc.itertuples():
        t = truth["true_effects"][r.metric]["abs_diff"]
        se = (r.abs_ci_high - r.abs_ci_low) / (2 * norm.ppf(1 - config.ALPHA / 2))
        rows.append({
            "metric": r.metric, "role": r.role, "true_abs_diff": t, "estimate": r.abs_diff,
            "ci_low": r.abs_ci_low, "ci_high": r.abs_ci_high,
            "in_95ci": r.abs_ci_low <= t <= r.abs_ci_high,
            "z_error": (r.abs_diff - t) / se,
            "in_bonferroni_ci": abs(r.abs_diff - t) <= z_bonf * se,
        })
    return pd.DataFrame(rows)


def segment_recovery(truth: dict, results: dict, family_alpha: float = 0.05) -> pd.DataFrame:
    """True vs estimated D7 effect per pre-specified segment."""
    seg = pd.DataFrame(results["segments"])
    z_bonf = norm.ppf(1 - family_alpha / (2 * len(seg)))
    rows = []
    for r in seg.itertuples():
        t = truth["segment_true_effects_d7"][r.dimension][r.segment]["abs_diff"]
        rows.append({"dimension": r.dimension, "segment": r.segment, "true_abs_diff": t,
                     "estimate": r.abs_diff, "ci_low": r.ci_low, "ci_high": r.ci_high,
                     "in_95ci": r.ci_low <= t <= r.ci_high,
                     "in_bonferroni_ci": abs(r.abs_diff - t) <= z_bonf * r.se})
    return pd.DataFrame(rows)


def main() -> None:
    """Print and save recovery tables."""
    truth, results = load_truth(), load_results()
    mr, sr = metric_recovery(truth, results), segment_recovery(truth, results)
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    mr.to_csv(VALIDATION_DIR / "recovery_metrics.csv", index=False)
    sr.to_csv(VALIDATION_DIR / "recovery_segments.csv", index=False)
    (VALIDATION_DIR / "recovery_check.md").write_text(
        "# Effect recovery check (validation only - SIMULATED DATA)\n\n"
        "True effects come from `data/raw/ground_truth.json`; estimates from `outputs/tables/results.json`.\n\n"
        "## Metrics\n\n" + mr.to_markdown(index=False, floatfmt=".5f") +
        "\n\n## D7 effect by segment\n\n" + sr.to_markdown(index=False, floatfmt=".5f") + "\n"
    )
    pd.set_option("display.width", 200)
    print(mr.round(5).to_string(index=False))
    print(sr.round(5).to_string(index=False))
    print(f"95% CI coverage: metrics {mr['in_95ci'].sum()}/{len(mr)}, segments {sr['in_95ci'].sum()}/{len(sr)}")


if __name__ == "__main__":
    main()
