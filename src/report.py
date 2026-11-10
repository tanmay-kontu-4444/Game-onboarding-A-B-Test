"""Render docs/02_experiment_readout.md from the analysis outputs.

Every number in the readout comes from ``outputs/tables/results.json`` (or
the power summary). The narrative lives in
``docs/templates/02_experiment_readout.template.md`` with ``{placeholders}``.
Because the narrative also makes qualitative claims (e.g. "revenue is the only
inconclusive guardrail"), :func:`check_narrative_claims` asserts each one
against the results and refuses to render if any is no longer true.

Run:  ``python -m src.report``
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from src import config

TEMPLATE = config.DOCS_DIR / "templates" / "02_experiment_readout.template.md"
OUTPUT = config.DOCS_DIR / "02_experiment_readout.md"
README = config.ROOT / "README.md"

HEADLINE_TEMPLATE = (
    "**{decision}.** The short onboarding lifted **D7 retention from {d7_retained_c} to {d7_retained_t} "
    "({d7_retained_diff}, 95% CI {d7_retained_lo} to {d7_retained_hi}; p {d7_retained_p})**, "
    "about **{extra_ret} extra retained players per 100k installs**. Tutorial completion rose {tutorial_complete_diff}.\n"
    "Every guardrail passed except first-week revenue, which is **inconclusive**: its CI ({revenue_d7_rel_lo} to {revenue_d7_rel_hi}) "
    "cannot rule out the pre-set −10% limit. So the pre-registered rule calls for a staged rollout with a holdback, "
    "plus a follow-up test that brings back an optional shop prompt.\n"
    "The analysis recovers the simulator's true D7 effect inside its 95% CI (see `make recovery`).\n"
)


# =============================================================================
# Formatting helpers
# =============================================================================
def fmt_p(p: float) -> str:
    """p-value: '<0.0001' or 4 decimals."""
    if p != p:  # NaN
        return "-"
    return "< 0.0001" if p < 1e-4 else f"{p:.4f}"


def fmt_pct(x: float, d: int = 2) -> str:
    return f"{100 * x:.{d}f}%"


def fmt_pp(x: float, d: int = 2) -> str:
    return f"{100 * x:+.{d}f}pp"


def fmt_rel(x: float, d: int = 1) -> str:
    return f"{100 * x:+.{d}f}%"


def fmt_usd(x: float, d: int = 0) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.{d}f}"


def fmt_value(metric: str, x: float) -> str:
    """Arm-level value in the metric's natural unit."""
    if metric in {"d7_retained", "d1_retained", "tutorial_complete", "purchased_d7", "crashed_d7"}:
        return fmt_pct(x)
    if metric == "revenue_d7":
        return f"${x:.3f}"
    if metric == "avg_session_len":
        return f"{x:.1f}s"
    return f"{x:.2f}"


def fmt_diff(metric: str, x: float) -> str:
    """Absolute difference in the metric's natural unit."""
    if metric in {"d7_retained", "d1_retained", "tutorial_complete", "purchased_d7", "crashed_d7"}:
        return fmt_pp(x)
    if metric == "revenue_d7":
        return f"{x:+.3f}"
    if metric == "avg_session_len":
        return f"{x:+.1f}s"
    return f"{x:+.2f}"


# =============================================================================
# Generated blocks
# =============================================================================
def scorecard_block(sc: pd.DataFrame) -> str:
    """Results scorecard table (metric, arms, diffs, CI, p, adj p, verdict)."""
    rows = []
    for r in sc.itertuples():
        rows.append({
            "Metric": r.label, "Role": r.role,
            "Control": fmt_value(r.metric, r.control), "Treatment": fmt_value(r.metric, r.treatment),
            "Abs diff": fmt_diff(r.metric, r.abs_diff), "Rel diff": fmt_rel(r.rel_diff),
            "95% CI (abs)": f"[{fmt_diff(r.metric, r.abs_ci_low)}, {fmt_diff(r.metric, r.abs_ci_high)}]",
            "95% CI (rel)": f"[{fmt_rel(r.rel_ci_low)}, {fmt_rel(r.rel_ci_high)}]",
            "p-value": fmt_p(r.p_value),
            "Adj. p": fmt_p(r.p_adj) if r.role == "Secondary" else ("n/a" if r.role == "Guardrail" else fmt_p(r.p_adj)),
            "Verdict": f"**{r.verdict}**",
        })
    return pd.DataFrame(rows).to_markdown(index=False, disable_numparse=True)


def guardrail_block(sc: pd.DataFrame) -> str:
    """Guardrails vs their pre-registered thresholds."""
    rows = []
    for r in sc[sc["role"] == "Guardrail"].itertuples():
        g = config.GUARDRAILS[r.metric]
        worse = r.rel_ci_low if g["direction"] == "lower_is_worse" else r.rel_ci_high
        rows.append({
            "Guardrail": r.label, "Rel diff": fmt_rel(r.rel_diff),
            "95% CI (rel)": f"[{fmt_rel(r.rel_ci_low)}, {fmt_rel(r.rel_ci_high)}]",
            "Bootstrap CI (rel)": f"[{fmt_rel(r.boot_rel_ci_low)}, {fmt_rel(r.boot_rel_ci_high)}]",
            "Unacceptable if worse than": fmt_rel(float(g["threshold"]), 0),
            "Worse CI bound": fmt_rel(worse),
            "Mann-Whitney p": fmt_p(r.mann_whitney_p) if r.mann_whitney_p == r.mann_whitney_p and r.mann_whitney_p is not None else "-",
            "Verdict": f"**{r.verdict}**",
        })
    return pd.DataFrame(rows).to_markdown(index=False, disable_numparse=True)


def variance_block(vr: pd.DataFrame) -> str:
    """Primary-metric estimates under each pre-specified method."""
    rows = [{"Method": r.method, "Lift": fmt_pp(r.abs_diff),
             "95% CI": f"[{fmt_pp(r.ci_low)}, {fmt_pp(r.ci_high)}]",
             "CI width": f"{100 * r.ci_width:.2f}pp",
             "Width vs unadjusted": f"{r.ci_width_change_pct:+.1f}%", "p-value": fmt_p(r.p_value)}
            for r in vr.itertuples()]
    return pd.DataFrame(rows).to_markdown(index=False, disable_numparse=True)


def segment_block(seg: pd.DataFrame, het: pd.DataFrame) -> str:
    """Exploratory segment table plus heterogeneity tests."""
    names = {"platform": "Platform", "device_tier": "Device tier", "country_tier": "Country tier"}
    q = het.set_index("dimension")["p_value"]
    rows = [{"Segment": f"{names[r.dimension]}: {r.segment}", "n (C / T)": f"{r.n_control:,} / {r.n_treatment:,}",
             "Control D7": fmt_pct(r.control), "Treatment D7": fmt_pct(r.treatment), "Lift": fmt_pp(r.abs_diff),
             "95% CI": f"[{fmt_pp(r.ci_low)}, {fmt_pp(r.ci_high)}]", "p": fmt_p(r.p_value),
             "BH-adj. p": fmt_p(r.p_adj_bh), "Heterogeneity p (Cochran's Q)": fmt_p(q[r.dimension])}
            for r in seg.itertuples()]
    return pd.DataFrame(rows).to_markdown(index=False, disable_numparse=True)


def novelty_block(wk: pd.DataFrame) -> str:
    """D7 lift by enrolment week."""
    rows = [{"Enrolment week": int(r.enrolment_week), "n (C / T)": f"{r.n_control:,} / {r.n_treatment:,}",
             "Lift": fmt_pp(r.abs_diff), "95% CI": f"[{fmt_pp(r.ci_low)}, {fmt_pp(r.ci_high)}]",
             "p": fmt_p(r.p_value)} for r in wk.itertuples()]
    return pd.DataFrame(rows).to_markdown(index=False, disable_numparse=True)


def srm_block(srm: pd.DataFrame) -> str:
    """SRM and exclusion-balance checks."""
    rows = [{"Population": r.population, "Control": f"{r.control:,}", "Treatment": f"{r.treatment:,}",
             "Control share": fmt_pct(r.control_share), "p-value": fmt_p(r.p_value),
             "Alarm (p < 0.001)": "yes" if r.srm_detected else "no"} for r in srm.itertuples()]
    return pd.DataFrame(rows).to_markdown(index=False, disable_numparse=True)


def practical_block(ps: dict[str, float]) -> str:
    """Value-vs-cost table per 100k installs."""
    rows = [
        {"Item (per 100k installs)": "Extra D7-retained players",
         "Central": f"{ps['extra_d7_retained']:,.0f}",
         "Range (95% CI)": f"{ps['extra_d7_retained_ci_low']:,.0f} to {ps['extra_d7_retained_ci_high']:,.0f}"},
        {"Item (per 100k installs)": "Value of extra retention (A1, A2)",
         "Central": fmt_usd(ps["gain_central"]),
         "Range (95% CI)": f"{fmt_usd(ps['gain_low'])} to {fmt_usd(ps['gain_high'])}"},
        {"Item (per 100k installs)": "Observed change in first-week revenue",
         "Central": fmt_usd(ps["early_rev_change_per_100k"]),
         "Range (95% CI)": f"{fmt_usd(ps['early_rev_change_ci_low'])} to {fmt_usd(ps['early_rev_change_ci_high'])}"},
        {"Item (per 100k installs)": "180-day revenue at risk from early-revenue change (A3)",
         "Central": fmt_usd(ps["cost_central_banked"]) + (" (upside not banked)" if ps["cost_central"] < 0 else ""),
         "Range (95% CI)": f"worst case {fmt_usd(ps['cost_worst'])}"},
        {"Item (per 100k installs)": "**Net (value - revenue at risk)**",
         "Central": f"**{fmt_usd(ps['net_central'])}**",
         "Range (95% CI)": f"pessimistic (low gain, worst cost): {fmt_usd(ps['net_pessimistic'])}"},
    ]
    return pd.DataFrame(rows).to_markdown(index=False, disable_numparse=True)


# =============================================================================
# Placeholder values
# =============================================================================
def build_values(r: dict[str, Any], power: dict[str, Any]) -> dict[str, str]:
    """Flat dict of every formatted value referenced in the template."""
    sc = pd.DataFrame(r["scorecard"])
    s = sc.set_index("metric")
    vr = pd.DataFrame(r["variance_reduction"])
    cbm = pd.DataFrame(r["cuped_by_metric"]).set_index("metric")
    seg = pd.DataFrame(r["segments"])
    het = pd.DataFrame(r["segment_heterogeneity"]).set_index("dimension")
    wk = pd.DataFrame(r["novelty"]["by_week"])
    ps = r["practical_significance"]
    srm = pd.DataFrame(r["srm"]).set_index("population")
    curve = pd.DataFrame(r["retention_curve"])
    seg_i = seg.set_index(["dimension", "segment"])
    v: dict[str, str] = {}

    for m in s.index:
        e = s.loc[m]
        k = m
        v[f"{k}_c"], v[f"{k}_t"] = fmt_value(m, e["control"]), fmt_value(m, e["treatment"])
        v[f"{k}_diff"] = fmt_diff(m, e["abs_diff"])
        v[f"{k}_lo"], v[f"{k}_hi"] = fmt_diff(m, e["abs_ci_low"]), fmt_diff(m, e["abs_ci_high"])
        v[f"{k}_rel"] = fmt_rel(e["rel_diff"])
        v[f"{k}_rel_lo"], v[f"{k}_rel_hi"] = fmt_rel(e["rel_ci_low"]), fmt_rel(e["rel_ci_high"])
        v[f"{k}_p"], v[f"{k}_padj"] = fmt_p(e["p_value"]), fmt_p(e["p_adj"])
        v[f"{k}_verdict"] = e["verdict"]
        v[f"{k}_boot_lo"], v[f"{k}_boot_hi"] = fmt_diff(m, e["boot_abs_ci_low"]), fmt_diff(m, e["boot_abs_ci_high"])
        v[f"{k}_boot_rel_lo"], v[f"{k}_boot_rel_hi"] = fmt_rel(e["boot_rel_ci_low"]), fmt_rel(e["boot_rel_ci_high"])
        v[f"{k}_mw_p"] = fmt_p(e["mann_whitney_p"]) if e["mann_whitney_p"] == e["mann_whitney_p"] and e["mann_whitney_p"] is not None else "-"

    v["n_cohort"] = f"{r['n_cohort']:,}"
    v["n_control"], v["n_treatment"] = f"{int(s.loc['d7_retained', 'n_control']):,}", f"{int(s.loc['d7_retained', 'n_treatment']):,}"
    v["srm_p"] = fmt_p(srm.loc["Final analysis cohort", "p_value"])
    v["srm_p_assigned"] = fmt_p(srm.loc["All players assigned in enrolment window", "p_value"])
    v["srm_share"] = fmt_pct(srm.loc["Final analysis cohort", "control_share"])
    v["max_smd"] = f"{r['balance_max_abs_smd']:.3f}"
    v["day0_c"], v["day0_t"] = f"{r['balance_day0']['control']:.3f}", f"{r['balance_day0']['treatment']:.3f}"
    v["day0_p"] = fmt_p(r["balance_day0"]["p_value"])

    cup = vr[vr["method"].str.startswith("CUPED")].iloc[0]
    ols2 = vr[vr["method"].str.contains("pre-assignment")].iloc[0]
    v["cuped_diff"], v["cuped_lo"], v["cuped_hi"] = fmt_pp(cup["abs_diff"]), fmt_pp(cup["ci_low"]), fmt_pp(cup["ci_high"])
    v["cuped_narrow"] = f"{-cup['ci_width_change_pct']:.1f}%"
    v["ols2_narrow"] = f"{-ols2['ci_width_change_pct']:.1f}%"
    v["cuped_corr_d7"] = f"{cbm.loc['d7_retained', 'corr_with_day0_sessions']:.2f}"
    v["cuped_vr_d7"] = f"{cbm.loc['d7_retained', 'variance_reduction_pct']:.1f}%"
    v["cuped_corr_levels"] = f"{cbm.loc['levels_by_d3', 'corr_with_day0_sessions']:.2f}"
    v["cuped_narrow_levels"] = f"{-cbm.loc['levels_by_d3', 'ci_width_change_pct']:.1f}%"

    for (dim, segm), row in seg_i.iterrows():
        key = f"seg_{segm.lower()}"
        v[key] = fmt_pp(row["abs_diff"])
        v[f"{key}_lo"], v[f"{key}_hi"] = fmt_pp(row["ci_low"]), fmt_pp(row["ci_high"])
        v[f"{key}_padj"] = fmt_p(row["p_adj_bh"])
    for dim in het.index:
        v[f"het_{dim}_p"] = fmt_p(het.loc[dim, "p_value"])
    v["n_sig_segments"] = str(int(seg["significant_bh"].sum()))
    v["n_segments"] = str(len(seg))

    v["novelty_q_p"] = fmt_p(r["novelty"]["p_value"])
    v["novelty_trend"] = fmt_pp(r["novelty"]["trend_slope_per_week"])
    v["novelty_trend_p"] = fmt_p(r["novelty"]["trend_p_value"])
    w4 = wk[wk["enrolment_week"] == 4].iloc[0]
    v["week4_diff"], v["week4_lo"], v["week4_hi"] = fmt_pp(w4["abs_diff"]), fmt_pp(w4["ci_low"]), fmt_pp(w4["ci_high"])

    cc = curve[curve["variant"] == "control"].set_index("day")
    v["d14_diff"], v["d14_lo"], v["d14_hi"] = fmt_pp(cc.loc[14, "abs_diff"]), fmt_pp(cc.loc[14, "diff_ci_low"]), fmt_pp(cc.loc[14, "diff_ci_high"])
    v["n_days_sig"] = str(int((cc.loc[1:, "diff_ci_low"] > 0).sum()))

    v["extra_ret"] = f"{ps['extra_d7_retained']:,.0f}"
    v["extra_ret_lo"], v["extra_ret_hi"] = f"{ps['extra_d7_retained_ci_low']:,.0f}", f"{ps['extra_d7_retained_ci_high']:,.0f}"
    v["v_ret"], v["v_not"] = f"${ps['rev_d7_d34_retained']:.2f}", f"${ps['rev_d7_d34_not_retained']:.2f}"
    v["n_value_window"] = f"{ps['n_players_value_window']:,}"
    v["ltv_mult_ret"], v["ltv_mult_early"] = f"{ps['ltv_mult_retained']:.1f}", f"{ps['ltv_mult_early_revenue']:.1f}"
    v["value_per_ret"] = f"${ps['value_per_extra_retained']:.2f}"
    v["gain_central"], v["gain_low"] = fmt_usd(ps["gain_central"]), fmt_usd(ps["gain_low"])
    v["cost_worst"] = fmt_usd(ps["cost_worst"])
    v["net_central"], v["net_pess"] = fmt_usd(ps["net_central"]), fmt_usd(ps["net_pessimistic"])
    v["breakeven_drop"] = f"{abs(100 * ps['breakeven_rev_drop_rel']):.1f}%"

    v["decision"] = r["decision"]["decision"]
    v["decision_rule"] = str(r["decision"]["rule"])
    v["mde_planned"] = fmt_pp(power["mde_abs_at_planned_n"])
    v["n_per_arm_design"] = f"{power['n_per_arm_for_design_mde']:,}"
    gp = pd.read_csv(config.TABLES_DIR / "power_guardrail_mde.csv").set_index("metric")
    v["purchase_mde_rel"] = f"{gp.loc['purchased_d7', 'mde_rel_pct']:.1f}%"
    v["revenue_mde_rel"] = f"{gp.loc['revenue_d7', 'mde_rel_pct']:.1f}%"

    ex = pd.read_csv(config.TABLES_DIR / "cohort_exclusions.csv").set_index("step")
    for step in ex.index:
        v[f"excl_{step.split(' ')[0]}"] = f"{int(ex.loc[step, 'total']):,}"
    dd = pd.read_csv(config.TABLES_DIR / "dedup_summary.csv").set_index("table_name")
    v["dup_sessions"] = f"{int(dd.loc['sessions', 'duplicates_removed']):,}"
    v["dup_progression"] = f"{int(dd.loc['progression_events', 'duplicates_removed']):,}"

    v["block_scorecard"] = scorecard_block(sc)
    v["block_guardrails"] = guardrail_block(sc)
    v["block_variance"] = variance_block(vr)
    v["block_segments"] = segment_block(seg, het.reset_index())
    v["block_novelty"] = novelty_block(wk)
    v["block_srm"] = srm_block(pd.DataFrame(r["srm"]))
    v["block_practical"] = practical_block(ps)
    return v


# =============================================================================
# Narrative guard
# =============================================================================
def check_narrative_claims(r: dict[str, Any]) -> None:
    """Assert every qualitative claim the template's prose makes."""
    s = pd.DataFrame(r["scorecard"]).set_index("metric")
    het = pd.DataFrame(r["segment_heterogeneity"]).set_index("dimension")["p_value"]
    seg = pd.DataFrame(r["segments"]).set_index(["dimension", "segment"])
    srm = pd.DataFrame(r["srm"])
    claims = {
        "no SRM / no unbalanced exclusion": not srm["srm_detected"].any(),
        "covariates balanced (|SMD| < 0.1)": r["balance_max_abs_smd"] < 0.1,
        "primary significantly positive": s.loc["d7_retained", "abs_ci_low"] > 0,
        "primary point estimate above design MDE": s.loc["d7_retained", "abs_diff"] > config.PLANNED_MDE_ABS,
        "all secondaries Holm-significant and positive": bool(((s.loc[config.SECONDARY_METRICS, "p_adj"] < config.ALPHA)
                                                              & (s.loc[config.SECONDARY_METRICS, "abs_diff"] > 0)).all()),
        "decision is rule 2": r["decision"]["rule"] == 2,
        "revenue is the only INCONCLUSIVE guardrail": r["decision"]["inconclusive_guardrails"] == ["revenue_d7"],
        "no guardrail FAIL": r["decision"]["failed_guardrails"] == [],
        "purchase conversion point estimate not negative": s.loc["purchased_d7", "abs_diff"] >= 0,
        "purchase conversion not significant": s.loc["purchased_d7", "p_value"] > config.ALPHA,
        "session length significantly down but within tolerance": s.loc["avg_session_len", "abs_ci_high"] < 0
                                                                   and s.loc["avg_session_len", "verdict"] == "PASS",
        "platform heterogeneity significant": het["platform"] < config.ALPHA,
        "device and country heterogeneity not significant": het["device_tier"] > config.ALPHA and het["country_tier"] > config.ALPHA,
        "iOS lift not significant": seg.loc[("platform", "iOS"), "p_adj_bh"] > config.ALPHA,
        "Android lift significant": seg.loc[("platform", "Android"), "p_adj_bh"] < config.ALPHA,
        "T1 lift not significant": seg.loc[("country_tier", "T1"), "p_adj_bh"] > config.ALPHA,
        "high-tier device lift not significant": seg.loc[("device_tier", "high"), "p_adj_bh"] > config.ALPHA,
        "low and mid device lift above high": seg.loc[("device_tier", "low"), "abs_diff"] > seg.loc[("device_tier", "high"), "abs_diff"]
                                              and seg.loc[("device_tier", "mid"), "abs_diff"] > seg.loc[("device_tier", "high"), "abs_diff"],
        "T2 and T3 lifts significant": seg.loc[("country_tier", "T2"), "p_adj_bh"] < config.ALPHA
                                       and seg.loc[("country_tier", "T3"), "p_adj_bh"] < config.ALPHA,
        "week 4 has the smallest weekly lift": pd.DataFrame(r["novelty"]["by_week"]).set_index("enrolment_week")["abs_diff"].idxmin() == 4,
        "no novelty heterogeneity or trend": r["novelty"]["p_value"] > config.ALPHA and r["novelty"]["trend_p_value"] > config.ALPHA,
        "central net value positive, pessimistic negative": r["practical_significance"]["net_central"] > 0
                                                            > r["practical_significance"]["net_pessimistic"],
    }
    failed = [k for k, ok in claims.items() if not ok]
    if failed:
        raise AssertionError("Readout narrative is out of date with the results: " + "; ".join(failed))


def render() -> str:
    """Render the readout markdown string."""
    r = json.loads((config.TABLES_DIR / "results.json").read_text())
    power = json.loads((config.TABLES_DIR / "power_summary.json").read_text())
    check_narrative_claims(r)
    values = build_values(r, power)
    return TEMPLATE.read_text().format_map(values)


def render_readme() -> str:
    """README with the generated headline block refreshed."""
    r = json.loads((config.TABLES_DIR / "results.json").read_text())
    power = json.loads((config.TABLES_DIR / "power_summary.json").read_text())
    block = HEADLINE_TEMPLATE.format_map(build_values(r, power))
    marker = "<!-- BEGIN:headline (generated by `python -m src.report`) -->"
    text = README.read_text()
    start, end = text.index(marker) + len(marker), text.index("<!-- END:headline -->")
    return text[:start] + "\n" + block + text[end:]


def main() -> None:
    """Write docs/02_experiment_readout.md and refresh the README headline."""
    OUTPUT.write_text(render())
    README.write_text(render_readme())
    print(f"[report] wrote {OUTPUT.relative_to(config.ROOT)} and README headline")


if __name__ == "__main__":
    main()
