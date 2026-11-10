"""Build the interactive results website from the analysis outputs.

Like the readout, the site contains no hand-typed numbers: everything comes
from ``outputs/tables/results.json`` and the power module. Statistical
quantities the page lets you explore (sample size per MDE, power curves) are
precomputed here with ``src.power`` and embedded, so the page never
re-implements statistics in JavaScript.

Outputs:
* ``site/index.html`` - standalone page (open locally or host on GitHub Pages)
* ``--fragment PATH`` - the same page without the html/head/body skeleton

Run:  ``python -m src.site``
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src import config, power

SITE_DIR = config.ROOT / "site"
TEMPLATE = SITE_DIR / "template.html"
OUTPUT = SITE_DIR / "index.html"
BODY_MARKER = "<!--BODY-->"
DATA_MARKER = "/*__DATA__*/null"


def _clean(obj: Any) -> Any:
    """Recursively replace NaN/inf with None so the payload is valid JSON."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def power_payload() -> dict[str, Any]:
    """MDE -> sample-size grid and power curves, computed with src.power."""
    s = power.summary()
    grid = []
    for mde_pp in np.round(np.arange(0.4, 2.01, 0.05), 2):
        n = power.n_per_arm_proportions(config.BASELINE_D7, mde_pp / 100)
        grid.append({"mde_pp": float(mde_pp), "n_per_arm": n, "days": power.runtime_days(n)})
    lifts = np.round(np.arange(0.0, 2.001, 0.02), 2)
    curves = {
        key: [power.power_proportions(config.BASELINE_D7, l / 100, n) if l > 0 else config.ALPHA for l in lifts]
        for key, n in [("planned", s["planned_n_per_arm"]), ("design", s["n_per_arm_for_design_mde"])]
    }
    g = pd.read_csv(config.TABLES_DIR / "power_guardrail_mde.csv")
    return {
        "summary": s,
        "grid": grid,
        "curve_lifts_pp": lifts.tolist(),
        "curves": curves,
        "guardrail_mde": g[["metric", "mde_rel_pct", "threshold_rel_pct"]].to_dict(orient="records"),
    }


def build_payload() -> dict[str, Any]:
    """Everything the page renders, in one JSON object."""
    r = json.loads((config.TABLES_DIR / "results.json").read_text())
    curve = pd.DataFrame(r["retention_curve"])
    wide = curve.pivot(index="day", columns="variant", values=["rate", "ci_low", "ci_high", "n"])
    diffs = curve[curve["variant"] == "control"].set_index("day")
    retention = [{
        "day": int(d),
        "control": wide.loc[d, ("rate", "control")], "treatment": wide.loc[d, ("rate", "treatment")],
        "c_lo": wide.loc[d, ("ci_low", "control")], "c_hi": wide.loc[d, ("ci_high", "control")],
        "t_lo": wide.loc[d, ("ci_low", "treatment")], "t_hi": wide.loc[d, ("ci_high", "treatment")],
        "diff": diffs.loc[d, "abs_diff"], "d_lo": diffs.loc[d, "diff_ci_low"], "d_hi": diffs.loc[d, "diff_ci_high"],
        "p": diffs.loc[d, "p_value"],
    } for d in wide.index]
    ps = r["practical_significance"]
    rev = next(x for x in r["scorecard"] if x["metric"] == "revenue_d7")
    srm = next(x for x in r["srm"] if x["population"] == "Final analysis cohort")
    payload = {
        "meta": {
            "experiment_id": "onb_short_tutorial_v1",
            "enrolment": f"{config.ENROLMENT_START:%d %b} – {(pd.Timestamp(config.ENROLMENT_END_EXCL) - pd.Timedelta(days=1)):%d %b %Y}",
            "follow_up_days": config.FOLLOW_UP_DAYS,
            "n_cohort": r["n_cohort"],
            "srm_p": srm["p_value"], "control_share": srm["control_share"],
            "max_smd": r["balance_max_abs_smd"],
            "decision": r["decision"]["decision"], "rule": r["decision"]["rule"],
            "alpha": config.ALPHA,
        },
        "scorecard": r["scorecard"],
        "variance": r["variance_reduction"],
        "cuped": r["cuped_by_metric"],
        "segments": r["segments"],
        "heterogeneity": r["segment_heterogeneity"],
        "novelty": r["novelty"],
        "retention": retention,
        "value": {
            **ps,
            "control_revenue_d7": rev["control"],
            "rev_abs_ci_low": rev["abs_ci_low"], "rev_abs_ci_high": rev["abs_ci_high"],
        },
        "power": power_payload(),
        "guardrails": {k: v for k, v in config.GUARDRAILS.items()},
    }
    return _clean(json.loads(json.dumps(payload, default=float)))


def render_parts() -> tuple[str, str]:
    """(head, body) HTML strings with the data payload injected."""
    text = TEMPLATE.read_text()
    if DATA_MARKER not in text or BODY_MARKER not in text:
        raise RuntimeError("site template is missing its markers")
    data = json.dumps(build_payload(), separators=(",", ":")).replace("</", "<\\/")
    head, body = text.split(BODY_MARKER)
    return head.strip(), body.replace(DATA_MARKER, data).strip()


def render_standalone() -> str:
    """Full HTML document."""
    head, body = render_parts()
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
        f"{head}\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def render_fragment() -> str:
    """Page content without the document skeleton (for hosts that add their own)."""
    head, body = render_parts()
    return f"{head}\n{body}\n"


def main() -> None:
    """Write site/index.html (and optionally a fragment)."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fragment", type=Path, help="also write a skeleton-less fragment here")
    args = ap.parse_args()
    OUTPUT.write_text(render_standalone())
    print(f"[site] wrote {OUTPUT.relative_to(config.ROOT)}")
    if args.fragment:
        args.fragment.write_text(render_fragment())
        print(f"[site] wrote fragment {args.fragment}")


if __name__ == "__main__":
    main()
