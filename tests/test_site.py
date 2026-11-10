"""The interactive website must be generated from current outputs."""

from __future__ import annotations

import json
import re

from src import site


def test_site_is_up_to_date():
    assert site.OUTPUT.read_text() == site.render_standalone(), "run `make site`"


def test_site_payload_matches_results(results):
    html = site.OUTPUT.read_text()
    payload = json.loads(re.search(r"const D = (\{.*?\});\n", html, re.S).group(1))
    prim = next(r for r in payload["scorecard"] if r["metric"] == "d7_retained")
    ref = next(r for r in results["scorecard"] if r["metric"] == "d7_retained")
    assert prim["abs_diff"] == ref["abs_diff"]
    assert payload["meta"]["decision"] == results["decision"]["decision"]


def test_power_grid_matches_power_module():
    from src import config, power
    payload = site.build_payload()
    one = next(g for g in payload["power"]["grid"] if abs(g["mde_pp"] - 1.0) < 1e-9)
    assert one["n_per_arm"] == power.n_per_arm_proportions(config.BASELINE_D7, 0.01)
