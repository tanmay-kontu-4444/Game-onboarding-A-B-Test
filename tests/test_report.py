"""The readout must be fully generated from current outputs."""

from __future__ import annotations

import re

from src import config, report


def test_readout_is_up_to_date():
    assert report.OUTPUT.read_text() == report.render(), "run `make report`"


def test_no_unresolved_placeholders():
    assert not re.search(r"\{[a-z_0-9]+\}", report.OUTPUT.read_text())


def test_headline_numbers_match_scorecard_table(results):
    text = report.OUTPUT.read_text()
    prim = next(r for r in results["scorecard"] if r["metric"] == config.PRIMARY_METRIC)
    assert f"D7 retention from {100*prim['control']:.2f}% to {100*prim['treatment']:.2f}%" in text
    assert f"{100*prim['abs_diff']:+.2f}pp, 95% CI {100*prim['abs_ci_low']:+.2f}pp to {100*prim['abs_ci_high']:+.2f}pp" in text


def test_recommendation_in_first_lines(results):
    lines = [l for l in report.OUTPUT.read_text().splitlines() if l.strip()]
    tldr = lines.index("## TL;DR")
    assert results["decision"]["decision"] in lines[tldr + 1]


def test_figures_exist_and_are_embedded():
    text = report.OUTPUT.read_text()
    for fig in ["retention_curves", "primary_effect", "metric_scorecard", "segment_forest"]:
        assert (config.FIGURES_DIR / f"{fig}.png").exists()
        assert f"../outputs/figures/{fig}.png" in text
    assert (config.FIGURES_DIR / "power_curve.png").exists()
