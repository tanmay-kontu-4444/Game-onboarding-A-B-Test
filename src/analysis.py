"""Full experiment analysis, following docs/01_experiment_plan.md.

Reads only ``data/processed/*.parquet`` (built by the SQL layer). Never reads
the simulator's ground truth. Writes:

* ``outputs/tables/*.csv|md``  - every table quoted in the readout
* ``outputs/tables/results.json`` - machine-readable results for src/report.py
* ``outputs/figures/*.png``    - labelled figures

Run:  ``python -m src.analysis``
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from src import config, plotting
from src import stats_utils as su

ROLE = {config.PRIMARY_METRIC: "Primary",
        **{m: "Secondary" for m in config.SECONDARY_METRICS},
        **{m: "Guardrail" for m in config.GUARDRAIL_METRICS}}
BINARY = {"d7_retained", "d1_retained", "tutorial_complete", "purchased_d7", "crashed_d7"}

# Practical-significance assumptions (stated explicitly in the readout)
INSTALL_BASIS = 100_000
LTV_MULT_RETAINED = 2.5      # 180-day post-D7 value ~= 2.5x observed day 7-34 value
LTV_MULT_EARLY_REVENUE = 2.0  # $1 lost first-week revenue ~= $2 lost 180-day revenue


# =============================================================================
# Loading
# =============================================================================
def load() -> dict[str, pd.DataFrame]:
    """Load processed tables produced by ``src.cohorts``."""
    names = ["metrics_per_player", "player_active_days", "cohort_audit", "segment_metrics"]
    out = {}
    for n in names:
        path = config.PROCESSED_DIR / f"{n}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing - run `make cohorts` first")
        out[n] = pd.read_parquet(path)
    out["metrics_per_player"]["treat"] = out["metrics_per_player"]["variant"].eq("treatment")
    return out


def write_table(df: pd.DataFrame, name: str, floatfmt: str = ".4f") -> None:
    """Write a table as CSV and Markdown."""
    config.TABLES_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(config.TABLES_DIR / f"{name}.csv", index=False)
    (config.TABLES_DIR / f"{name}.md").write_text(df.to_markdown(index=False, floatfmt=floatfmt) + "\n")


# =============================================================================
# 1. Validity: SRM, exclusion balance, covariate balance
# =============================================================================
def srm_checks(audit: pd.DataFrame, m: pd.DataFrame) -> pd.DataFrame:
    """SRM on all in-window assignments and on the final cohort, plus exclusion balance."""
    in_window = audit[~audit["exclusion_reason"].isin(["outside_enrolment_window", "missing_install_record"])]
    rows = []
    for label, df in [("All players assigned in enrolment window", in_window),
                      ("Final analysis cohort", m)]:
        n_c, n_t = int((df["variant"] == "control").sum()), int((df["variant"] == "treatment").sum())
        r = su.srm_check([n_c, n_t], alpha=config.SRM_ALPHA)
        rows.append({"population": label, "control": n_c, "treatment": n_t,
                     "control_share": r["observed_share_first"], "chi2": r["chi2"],
                     "p_value": r["p_value"], "srm_detected": r["srm_detected"]})
    # Exclusion steps: is any step removing players unevenly by arm?
    for reason, g in in_window.groupby(in_window["exclusion_reason"].fillna("eligible")):
        if reason == "eligible":
            continue
        tab = pd.crosstab(in_window["variant"], in_window["exclusion_reason"].eq(reason))
        p = stats.fisher_exact(tab.to_numpy())[1] if tab.shape == (2, 2) else np.nan
        rows.append({"population": f"Excluded: {reason}",
                     "control": int((g["variant"] == "control").sum()),
                     "treatment": int((g["variant"] == "treatment").sum()),
                     "control_share": float((g["variant"] == "control").mean()),
                     "chi2": np.nan, "p_value": float(p), "srm_detected": bool(p < config.SRM_ALPHA)})
    return pd.DataFrame(rows)


def covariate_balance(m: pd.DataFrame) -> pd.DataFrame:
    """Shares / means by arm with SMDs and tests for pre-assignment covariates and day-0 sessions."""
    rows = []
    c, t = m[~m["treat"]], m[m["treat"]]
    for var in ["platform", "device_tier", "country_tier", "acquisition_channel"]:
        p_chi = stats.chi2_contingency(pd.crosstab(m["variant"], m[var]).to_numpy())[1]
        for level in sorted(m[var].unique()):
            rows.append({"covariate": var, "level": level,
                         "control": float((c[var] == level).mean()),
                         "treatment": float((t[var] == level).mean()),
                         "smd": su.standardized_mean_difference(c[var] == level, t[var] == level),
                         "p_value": float(p_chi)})
    est = su.welch_ttest(c["day0_sessions"], t["day0_sessions"])
    rows.append({"covariate": "day0_sessions", "level": "mean", "control": est.control_mean,
                 "treatment": est.treatment_mean,
                 "smd": su.standardized_mean_difference(c["day0_sessions"], t["day0_sessions"]),
                 "p_value": est.p_value})
    out = pd.DataFrame(rows)
    out["flag_smd_gt_0.1"] = out["smd"].abs() > 0.1
    return out


# =============================================================================
# 2. Metric estimates
# =============================================================================
def estimate_metric(m: pd.DataFrame, metric: str, seed: int) -> dict[str, Any]:
    """Main estimate + bootstrap (+ Mann-Whitney for non-binary) for one metric."""
    d = m[m[metric].notna()]
    y_c = d.loc[~d["treat"], metric].to_numpy(float)
    y_t = d.loc[d["treat"], metric].to_numpy(float)
    if metric in BINARY:
        est = su.two_proportion_ztest(int(y_c.sum()), y_c.size, int(y_t.sum()), y_t.size, config.ALPHA, metric)
        mw = None
    else:
        est = su.welch_ttest(y_c, y_t, config.ALPHA, metric)
        mw = su.mann_whitney_p(y_c, y_t)
    boot = su.bootstrap_diff_ci(y_c, y_t, n_boot=config.N_BOOTSTRAP, alpha=config.ALPHA, seed=seed)
    return {**est.to_dict(), "role": ROLE[metric],
            "boot_abs_ci_low": boot["abs_ci_low"], "boot_abs_ci_high": boot["abs_ci_high"],
            "boot_rel_ci_low": boot["rel_ci_low"], "boot_rel_ci_high": boot["rel_ci_high"],
            "mann_whitney_p": mw}


def primary_variance_reduction(m: pd.DataFrame, unadjusted: dict[str, Any]) -> pd.DataFrame:
    """Unadjusted vs bootstrap vs CUPED vs OLS for the primary metric."""
    d = m[m[config.PRIMARY_METRIC].notna()]
    cup, theta = su.cuped(d[config.PRIMARY_METRIC], d["day0_sessions"], d["treat"], config.ALPHA,
                          config.PRIMARY_METRIC)
    ols1 = su.ols_adjusted(d, config.PRIMARY_METRIC, ["day0_sessions"])
    ols2 = su.ols_adjusted(d, config.PRIMARY_METRIC,
                           ["day0_sessions", "platform", "device_tier", "country_tier", "acquisition_channel"])
    base_w = unadjusted["abs_ci_high"] - unadjusted["abs_ci_low"]
    rows = [
        {"method": "Unadjusted (z-test, Wald CI)", "abs_diff": unadjusted["abs_diff"],
         "ci_low": unadjusted["abs_ci_low"], "ci_high": unadjusted["abs_ci_high"],
         "p_value": unadjusted["p_value"]},
        {"method": f"Bootstrap percentile ({config.N_BOOTSTRAP:,} resamples)", "abs_diff": unadjusted["abs_diff"],
         "ci_low": unadjusted["boot_abs_ci_low"], "ci_high": unadjusted["boot_abs_ci_high"], "p_value": np.nan},
        {"method": "CUPED (day-0 sessions)", "abs_diff": cup.abs_diff, "ci_low": cup.abs_ci_low,
         "ci_high": cup.abs_ci_high, "p_value": cup.p_value},
        {"method": "OLS: day-0 sessions (HC2)", "abs_diff": ols1.abs_diff, "ci_low": ols1.abs_ci_low,
         "ci_high": ols1.abs_ci_high, "p_value": ols1.p_value},
        {"method": "OLS: day-0 sessions + pre-assignment covariates (HC2)", "abs_diff": ols2.abs_diff,
         "ci_low": ols2.abs_ci_low, "ci_high": ols2.abs_ci_high, "p_value": ols2.p_value},
    ]
    out = pd.DataFrame(rows)
    out["ci_width"] = out["ci_high"] - out["ci_low"]
    out["ci_width_change_pct"] = 100 * (out["ci_width"] / base_w - 1)
    out["cuped_theta"] = [np.nan, np.nan, theta, np.nan, np.nan]
    return out


def cuped_by_metric(m: pd.DataFrame) -> pd.DataFrame:
    """How much CUPED (day-0 sessions) narrows the CI for each non-guardrail metric."""
    rows = []
    for metric in [config.PRIMARY_METRIC, "d1_retained", "levels_by_d3"]:
        d = m[m[metric].notna()]
        y, x, t = d[metric].to_numpy(float), d["day0_sessions"].to_numpy(float), d["treat"].to_numpy()
        raw = su.welch_ttest(y[~t], y[t])
        adj, theta = su.cuped(y, x, t)
        rows.append({"metric": metric, "corr_with_day0_sessions": float(np.corrcoef(y, x)[0, 1]),
                     "theta": theta, "unadjusted_diff": raw.abs_diff, "unadjusted_ci_width": raw.ci_width,
                     "cuped_diff": adj.abs_diff, "cuped_ci_width": adj.ci_width,
                     "ci_width_change_pct": 100 * (adj.ci_width / raw.ci_width - 1),
                     "variance_reduction_pct": 100 * (1 - (adj.se / raw.se) ** 2)})
    return pd.DataFrame(rows)


def scorecard(estimates: dict[str, dict[str, Any]]) -> pd.DataFrame:
    """One row per metric with corrected p-values and verdicts."""
    sec = config.SECONDARY_METRICS
    holm = su.adjust_pvalues([estimates[k]["p_value"] for k in sec], "holm")
    adj = dict(zip(sec, holm))
    rows = []
    for metric in [config.PRIMARY_METRIC, *sec, *config.GUARDRAIL_METRICS]:
        e = estimates[metric]
        role = ROLE[metric]
        if role == "Guardrail":
            g = config.GUARDRAILS[metric]
            if g["kind"] == "relative":
                est, lo, hi = e["rel_diff"], e["rel_ci_low"], e["rel_ci_high"]
            else:
                est, lo, hi = e["abs_diff"], e["abs_ci_low"], e["abs_ci_high"]
            verdict = su.guardrail_verdict(est, lo, hi, float(g["threshold"]), g["direction"])
            p_adj, thr = np.nan, float(g["threshold"])
        else:
            p_adj = e["p_value"] if role == "Primary" else adj[metric]
            sig = p_adj < config.ALPHA
            verdict = ("Significant increase" if e["abs_diff"] > 0 else "Significant decrease") if sig \
                else "Not significant"
            thr = np.nan
        rows.append({"role": role, "metric": metric, "label": config.METRIC_LABELS[metric],
                     "control": e["control_mean"], "treatment": e["treatment_mean"],
                     "abs_diff": e["abs_diff"], "abs_ci_low": e["abs_ci_low"], "abs_ci_high": e["abs_ci_high"],
                     "rel_diff": e["rel_diff"], "rel_ci_low": e["rel_ci_low"], "rel_ci_high": e["rel_ci_high"],
                     "p_value": e["p_value"], "p_adj": p_adj,
                     "p_adj_method": {"Primary": "none (single primary)", "Secondary": "Holm",
                                      "Guardrail": "none (CI vs threshold)"}[role],
                     "threshold_rel": thr, "verdict": verdict,
                     "boot_abs_ci_low": e["boot_abs_ci_low"], "boot_abs_ci_high": e["boot_abs_ci_high"],
                     "boot_rel_ci_low": e["boot_rel_ci_low"], "boot_rel_ci_high": e["boot_rel_ci_high"],
                     "mann_whitney_p": e["mann_whitney_p"], "method": e["method"],
                     "n_control": e["n_control"], "n_treatment": e["n_treatment"]})
    return pd.DataFrame(rows)


# =============================================================================
# 3. Segments, retention curves, novelty
# =============================================================================
def d7_effect_by(m: pd.DataFrame, col: str) -> pd.DataFrame:
    """D7 effect (z-test) for every level of ``col``."""
    rows = []
    d = m[m[config.PRIMARY_METRIC].notna()]
    for level, g in d.groupby(col):
        c, t = g.loc[~g["treat"], config.PRIMARY_METRIC], g.loc[g["treat"], config.PRIMARY_METRIC]
        e = su.two_proportion_ztest(int(c.sum()), c.size, int(t.sum()), t.size, config.ALPHA)
        rows.append({"dimension": col, "segment": str(level), "n_control": c.size, "n_treatment": t.size,
                     "control": e.control_mean, "treatment": e.treatment_mean, "abs_diff": e.abs_diff,
                     "ci_low": e.abs_ci_low, "ci_high": e.abs_ci_high, "rel_diff": e.rel_diff,
                     "se": e.se, "p_value": e.p_value})
    return pd.DataFrame(rows)


def segment_analysis(m: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-segment D7 effects with BH correction, and Cochran's Q per dimension."""
    seg = pd.concat([d7_effect_by(m, dim) for dim in config.SEGMENT_DIMENSIONS], ignore_index=True)
    seg["p_adj_bh"] = su.adjust_pvalues(seg["p_value"], "fdr_bh")
    seg["significant_bh"] = seg["p_adj_bh"] < config.ALPHA
    het = []
    for dim, g in seg.groupby("dimension", sort=False):
        q = su.cochran_q(g["abs_diff"], g["se"])
        het.append({"dimension": dim, **q})
    return seg, pd.DataFrame(het)


def retention_curves(m: pd.DataFrame, active: pd.DataFrame) -> pd.DataFrame:
    """Dn retention by arm for n = 0..14, counting only players with a complete Dn window."""
    cutoff_date = pd.Timestamp(config.DATA_CUTOFF).normalize()
    base = m[["player_id", "variant", "install_date"]].copy()
    base["install_date"] = pd.to_datetime(base["install_date"])
    act = set(zip(active["player_id"], active["day_n"]))
    rows = []
    for n in range(config.RETENTION_CURVE_MAX_DAY + 1):
        elig = base[base["install_date"] + timedelta(days=n) < cutoff_date]
        flag = np.fromiter(((pid, n) in act for pid in elig["player_id"]), bool, len(elig))
        for arm in ["control", "treatment"]:
            sel = (elig["variant"] == arm).to_numpy()
            k, nn = int(flag[sel].sum()), int(sel.sum())
            p = k / nn
            se = np.sqrt(p * (1 - p) / nn)
            rows.append({"day": n, "variant": arm, "n": nn, "retained": k, "rate": p,
                         "ci_low": p - 1.96 * se, "ci_high": p + 1.96 * se})
    curves = pd.DataFrame(rows)
    wide = curves.pivot(index="day", columns="variant", values=["retained", "n"])
    diffs = []
    for n, r in wide.iterrows():
        e = su.two_proportion_ztest(int(r[("retained", "control")]), int(r[("n", "control")]),
                                    int(r[("retained", "treatment")]), int(r[("n", "treatment")]))
        diffs.append({"day": n, "abs_diff": e.abs_diff, "diff_ci_low": e.abs_ci_low,
                      "diff_ci_high": e.abs_ci_high, "p_value": e.p_value})
    return curves.merge(pd.DataFrame(diffs), on="day")


def novelty_check(m: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    """D7 effect by enrolment week, plus heterogeneity and linear-trend tests."""
    wk = d7_effect_by(m, "enrolment_week").rename(columns={"segment": "enrolment_week"})
    wk["enrolment_week"] = wk["enrolment_week"].astype(int)
    q = su.cochran_q(wk["abs_diff"], wk["se"])
    w = 1 / wk["se"] ** 2
    x = wk["enrolment_week"].astype(float)
    xbar = np.sum(w * x) / np.sum(w)
    slope = np.sum(w * (x - xbar) * wk["abs_diff"]) / np.sum(w * (x - xbar) ** 2)
    slope_se = np.sqrt(1 / np.sum(w * (x - xbar) ** 2))
    trend_p = 2 * stats.norm.sf(abs(slope / slope_se))
    return wk, {**q, "trend_slope_per_week": float(slope), "trend_slope_se": float(slope_se),
                "trend_p_value": float(trend_p)}


# =============================================================================
# 4. Practical significance and decision
# =============================================================================
def practical_significance(m: pd.DataFrame, sc: pd.DataFrame) -> dict[str, float]:
    """Translate the D7 lift and early-revenue change into value per 100k installs."""
    p = sc.set_index("metric")
    d7, rev = p.loc[config.PRIMARY_METRIC], p.loc["revenue_d7"]
    ctrl = m[(~m["treat"]) & m["revenue_d7_d34"].notna()]
    v_ret = float(ctrl.loc[ctrl["d7_retained"] == 1, "revenue_d7_d34"].mean())
    v_not = float(ctrl.loc[ctrl["d7_retained"] == 0, "revenue_d7_d34"].mean())
    value_per_retained = (v_ret - v_not) * LTV_MULT_RETAINED

    def gain(lift: float) -> float:
        return lift * INSTALL_BASIS * value_per_retained

    def cost(rev_diff: float) -> float:
        return -rev_diff * INSTALL_BASIS * LTV_MULT_EARLY_REVENUE

    out = {
        "install_basis": INSTALL_BASIS,
        "extra_d7_retained": d7["abs_diff"] * INSTALL_BASIS,
        "extra_d7_retained_ci_low": d7["abs_ci_low"] * INSTALL_BASIS,
        "extra_d7_retained_ci_high": d7["abs_ci_high"] * INSTALL_BASIS,
        "n_players_value_window": int(len(ctrl)),
        "rev_d7_d34_retained": v_ret,
        "rev_d7_d34_not_retained": v_not,
        "ltv_mult_retained": LTV_MULT_RETAINED,
        "ltv_mult_early_revenue": LTV_MULT_EARLY_REVENUE,
        "value_per_extra_retained": value_per_retained,
        "gain_central": gain(d7["abs_diff"]),
        "gain_low": gain(d7["abs_ci_low"]),
        "gain_high": gain(d7["abs_ci_high"]),
        "early_rev_diff_per_player": rev["abs_diff"],
        "early_rev_change_per_100k": rev["abs_diff"] * INSTALL_BASIS,
        "early_rev_change_ci_low": rev["abs_ci_low"] * INSTALL_BASIS,
        "early_rev_change_ci_high": rev["abs_ci_high"] * INSTALL_BASIS,
        "cost_central": cost(rev["abs_diff"]),
        "cost_worst": cost(rev["abs_ci_low"]),
        "cost_best": cost(rev["abs_ci_high"]),
    }
    # Conservative: a non-significant revenue *gain* is not banked; only losses count.
    out["cost_central_banked"] = max(out["cost_central"], 0.0)
    out["net_central"] = out["gain_central"] - out["cost_central_banked"]
    out["net_pessimistic"] = out["gain_low"] - out["cost_worst"]
    out["breakeven_rev_drop_rel"] = -out["gain_central"] / (LTV_MULT_EARLY_REVENUE * INSTALL_BASIS * rev["control"])
    return out


def decide(srm: pd.DataFrame, sc: pd.DataFrame) -> dict[str, Any]:
    """Apply the pre-registered decision rules (plan section 8)."""
    if srm.loc[srm["population"].isin(["All players assigned in enrolment window", "Final analysis cohort"]),
               "srm_detected"].any():
        return {"rule": 0, "decision": "NO DECISION - SRM detected; investigate"}
    s = sc.set_index("metric")
    prim = s.loc[config.PRIMARY_METRIC]
    g = s[s["role"] == "Guardrail"]["verdict"]
    sec = s[s["role"] == "Secondary"]
    prim_pos = prim["abs_ci_low"] > 0
    prim_neg = prim["abs_ci_high"] < 0
    if prim_pos and (g == "PASS").all():
        rule, decision = 1, "SHIP"
    elif prim_pos and not (g == "FAIL").any():
        rule, decision = 2, "SHIP WITH MITIGATION (staged rollout + follow-up fix experiment)"
    elif prim_pos:
        rule, decision = 3, "ITERATE (guardrail failure)"
    elif not prim_neg and ((sec["p_adj"] < config.ALPHA) & (sec["abs_diff"] > 0)).any() and not (g == "FAIL").any():
        rule, decision = 4, "ITERATE (secondary signal only)"
    else:
        rule, decision = 5, "DON'T SHIP"
    practical_flag = bool(prim_pos and prim["abs_ci_high"] < 0.005)
    return {"rule": rule, "decision": decision,
            "inconclusive_guardrails": g[g == "INCONCLUSIVE"].index.tolist(),
            "failed_guardrails": g[g == "FAIL"].index.tolist(),
            "lift_ci_entirely_below_0.5pp": practical_flag}


# =============================================================================
# 5. Figures
# =============================================================================
def fig_retention_curves(curves: pd.DataFrame) -> None:
    """D1..D14 retention by arm (top) and the treatment-control difference (bottom)."""
    import matplotlib.pyplot as plt
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(8, 6.4), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    c = curves[curves["day"] >= 1]
    for arm, label in [("control", "Control (7-step tutorial)"), ("treatment", "Treatment (3-step onboarding)")]:
        g = c[c["variant"] == arm]
        ax.plot(g["day"], 100 * g["rate"], color=plotting.ARM_COLORS[arm], marker="o", ms=4, label=label)
        ax.fill_between(g["day"], 100 * g["ci_low"], 100 * g["ci_high"], color=plotting.ARM_COLORS[arm], alpha=0.15, lw=0)
    d0 = curves[curves["day"] == 0].set_index("variant")["rate"]
    ax.text(0.99, 0.95, f"D0 (install day): {100*d0['control']:.1f}% vs {100*d0['treatment']:.1f}%",
            transform=ax.transAxes, ha="right", va="top", fontsize=8, color=plotting.TEXT_SECONDARY)
    ax.set_ylabel("Players retained (%)")
    ax.set_title("Retention curves D1-D14 by variant (95% CI)")
    ax.legend(loc="center right")
    dd = c[c["variant"] == "control"]
    ax2.axhline(0, color=plotting.TEXT_SECONDARY, lw=1)
    ax2.errorbar(dd["day"], 100 * dd["abs_diff"],
                 yerr=[100 * (dd["abs_diff"] - dd["diff_ci_low"]), 100 * (dd["diff_ci_high"] - dd["abs_diff"])],
                 fmt="o", color=plotting.TREATMENT, ms=5, capsize=0, lw=1.5)
    ax2.set_ylabel("Treatment - control (pp)")
    ax2.set_xlabel("Days since install")
    ax2.set_xticks(range(1, 15))
    plotting.save(fig, config.FIGURES_DIR / "retention_curves.png")


def fig_primary(sc: pd.DataFrame, vr: pd.DataFrame) -> None:
    """Left: D7 retention per arm with CI. Right: lift estimate under each method."""
    import matplotlib.pyplot as plt
    p = sc.set_index("metric").loc[config.PRIMARY_METRIC]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.8), gridspec_kw={"width_ratios": [1, 1.6]})
    for i, (arm, rate, n) in enumerate([("control", p["control"], p["n_control"]),
                                         ("treatment", p["treatment"], p["n_treatment"])]):
        se = np.sqrt(rate * (1 - rate) / n)
        a1.errorbar([i], [100 * rate], yerr=[[196 * se], [196 * se]], fmt="o", ms=9,
                    color=plotting.ARM_COLORS[arm], lw=2, capsize=0)
        a1.text(i + 0.12, 100 * rate, f"{100*rate:.2f}%", va="center", fontsize=9)
    a1.set_xticks([0, 1], ["Control", "Treatment"])
    a1.set_xlim(-0.5, 1.7)
    a1.set_ylabel("D7 retention (%)")
    a1.set_title("D7 retention by arm (95% CI)")
    v = vr.iloc[::-1].reset_index(drop=True)
    y = np.arange(len(v))
    a2.errorbar(100 * v["abs_diff"], y, xerr=[100 * (v["abs_diff"] - v["ci_low"]), 100 * (v["ci_high"] - v["abs_diff"])],
                fmt="o", color=plotting.TREATMENT, ms=7, lw=2, capsize=0)
    a2.axvline(0, color=plotting.TEXT_SECONDARY, lw=1)
    a2.axvline(100 * config.PLANNED_MDE_ABS, color=plotting.NEUTRAL, lw=1, ls=":")
    a2.set_ylim(-0.9, len(v) - 0.5)
    a2.text(100 * config.PLANNED_MDE_ABS, -0.7, " design MDE (1.0pp)", fontsize=8, color=plotting.TEXT_SECONDARY)
    a2.set_yticks(y, [s.replace(" (", "\n(") for s in v["method"]], fontsize=8)
    a2.set_xlabel("Absolute lift in D7 retention (pp)")
    a2.set_title("Lift estimate by method (95% CI)")
    a2.grid(axis="y", visible=False)
    fig.tight_layout()
    plotting.save(fig, config.FIGURES_DIR / "primary_effect.png")


def fig_scorecard(sc: pd.DataFrame) -> None:
    """Relative effect + 95% CI for every metric, with guardrail thresholds."""
    import matplotlib.pyplot as plt
    lim = 0.15
    d = sc.iloc[::-1].reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(9.5, 5))
    for i, r in d.iterrows():
        lo, hi, est = r["rel_ci_low"], r["rel_ci_high"], r["rel_diff"]
        color = plotting.TREATMENT if r["role"] != "Guardrail" else plotting.CONTROL
        if est > lim:  # off-scale (e.g. tutorial completion)
            ax.annotate("", xy=(lim, i), xytext=(lim - 0.03, i),
                        arrowprops={"arrowstyle": "->", "color": color, "lw": 2})
            ax.text(lim - 0.035, i + 0.22, f"{100*est:+.1f}% [{100*lo:+.1f}, {100*hi:+.1f}] (off scale)",
                    ha="right", fontsize=8, color=plotting.TEXT_SECONDARY)
        else:
            ax.plot([max(lo, -lim), min(hi, lim)], [i, i], color=color, lw=2.5, solid_capstyle="round")
            ax.plot([est], [i], "o", color=color, ms=7)
            for bound, edge, marker in [(hi, lim, ">"), (lo, -lim, "<")]:
                if abs(bound) > lim:  # CI runs off the axis: arrowhead + true bound
                    ax.plot([edge], [i], marker=marker, color=color, ms=7)
                    ax.text(edge, i - 0.32, f"CI to {100*bound:+.1f}%", ha="right" if edge > 0 else "left",
                            fontsize=7.5, color=plotting.TEXT_SECONDARY)
        if r["role"] == "Guardrail":
            thr = r["threshold_rel"]
            ax.plot([thr], [i], marker="|", ms=16, mew=2.5, color=plotting.STATUS["critical"])
        ax.text(lim + 0.005, i, r["verdict"], va="center", fontsize=8.5, color=plotting.TEXT)
    ax.axvline(0, color=plotting.TEXT_SECONDARY, lw=1)
    ax.set_yticks(range(len(d)), [f"{r.role}: {r.label}" for r in d.itertuples()], fontsize=8.5)
    ax.set_xlim(-lim, lim)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{100*x:+.0f}%"))
    ax.set_xlabel("Relative difference, treatment vs control (95% CI)")
    ax.set_title("Metric scorecard: relative effects (red tick = guardrail threshold)")
    ax.grid(axis="y", visible=False)
    plotting.save(fig, config.FIGURES_DIR / "metric_scorecard.png")


def fig_forest(seg: pd.DataFrame, overall: pd.Series) -> None:
    """Forest plot of D7 effects by pre-specified segment (exploratory)."""
    import matplotlib.pyplot as plt
    names = {"platform": "Platform", "device_tier": "Device tier", "country_tier": "Country tier"}
    rows = [("Overall", overall["abs_diff"], overall["abs_ci_low"], overall["abs_ci_high"], np.nan, True)]
    for dim in config.SEGMENT_DIMENSIONS:
        for r in seg[seg["dimension"] == dim].itertuples():
            rows.append((f"{names[dim]}: {r.segment}", r.abs_diff, r.ci_low, r.ci_high, r.p_adj_bh, False))
    rows = rows[::-1]
    fig, ax = plt.subplots(figsize=(8.5, 5))
    for i, (lab, e, lo, hi, padj, is_overall) in enumerate(rows):
        color = plotting.TEXT if is_overall else plotting.TREATMENT
        ax.plot([100 * lo, 100 * hi], [i, i], color=color, lw=2.5, solid_capstyle="round")
        ax.plot([100 * e], [i], "D" if is_overall else "o", color=color, ms=8 if is_overall else 6)
        txt = f"{100*e:+.2f}pp" + ("" if np.isnan(padj) else f"  (BH p={padj:.3f})")
        ax.text(4.1, i, txt, va="center", fontsize=8, color=plotting.TEXT_SECONDARY)
    ax.axvline(0, color=plotting.TEXT_SECONDARY, lw=1)
    ax.axvline(100 * overall["abs_diff"], color=plotting.NEUTRAL, lw=1, ls=":")
    ax.set_yticks(range(len(rows)), [r[0] for r in rows], fontsize=9)
    ax.set_xlim(-2, 4)
    ax.set_xlabel("Absolute lift in D7 retention (pp, 95% CI)")
    ax.set_title("D7 lift by segment - EXPLORATORY (BH-adjusted p)")
    ax.grid(axis="y", visible=False)
    plotting.save(fig, config.FIGURES_DIR / "segment_forest.png")


def fig_novelty(wk: pd.DataFrame, overall: pd.Series) -> None:
    """D7 lift by enrolment week."""
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 3.6))
    x = wk["enrolment_week"].astype(int)
    ax.errorbar(x, 100 * wk["abs_diff"], yerr=[100 * (wk["abs_diff"] - wk["ci_low"]), 100 * (wk["ci_high"] - wk["abs_diff"])],
                fmt="o", color=plotting.TREATMENT, ms=7, lw=2, capsize=0)
    ax.axhline(0, color=plotting.TEXT_SECONDARY, lw=1)
    ax.axhline(100 * overall["abs_diff"], color=plotting.NEUTRAL, lw=1, ls=":")
    ax.text(4.68, 100 * overall["abs_diff"] + 0.08, f"overall {100*overall['abs_diff']:+.2f}pp", fontsize=8,
            ha="right", va="bottom", color=plotting.TEXT_SECONDARY)
    ax.set_xticks(x, [f"Week {i}" for i in x])
    ax.set_xlim(0.6, 4.7)
    ax.set_ylabel("D7 lift (pp, 95% CI)")
    ax.set_title("Novelty check: D7 lift by enrolment week")
    plotting.save(fig, config.FIGURES_DIR / "novelty_by_week.png")


# =============================================================================
# Orchestration
# =============================================================================
def run() -> dict[str, Any]:
    """Run every pre-registered analysis and write all outputs."""
    plotting.apply_style()
    data = load()
    m = data["metrics_per_player"]

    srm = srm_checks(data["cohort_audit"], m)
    bal = covariate_balance(m)
    write_table(srm, "srm_check")
    write_table(bal, "covariate_balance")

    metrics = [config.PRIMARY_METRIC, *config.SECONDARY_METRICS, *config.GUARDRAIL_METRICS]
    estimates = {k: estimate_metric(m, k, seed=config.SEED + i) for i, k in enumerate(metrics)}
    sc = scorecard(estimates)
    write_table(sc, "scorecard", floatfmt=".5f")
    vr = primary_variance_reduction(m, estimates[config.PRIMARY_METRIC])
    write_table(vr, "primary_variance_reduction", floatfmt=".5f")
    cbm = cuped_by_metric(m)
    write_table(cbm, "cuped_by_metric")

    seg, het = segment_analysis(m)
    write_table(seg, "segment_effects_d7", floatfmt=".5f")
    write_table(het, "segment_heterogeneity")
    curves = retention_curves(m, data["player_active_days"])
    write_table(curves, "retention_curves", floatfmt=".5f")
    wk, nov = novelty_check(m)
    write_table(wk, "novelty_by_week", floatfmt=".5f")

    ps = practical_significance(m, sc)
    decision = decide(srm, sc)

    prim = sc.set_index("metric").loc[config.PRIMARY_METRIC]
    fig_retention_curves(curves)
    fig_primary(sc, vr)
    fig_scorecard(sc)
    fig_forest(seg, prim)
    fig_novelty(wk, prim)

    results = {
        "disclaimer": "SIMULATED DATA - synthetic A/B test",
        "n_cohort": int(len(m)),
        "srm": srm.to_dict(orient="records"),
        "balance_max_abs_smd": float(bal["smd"].abs().max()),
        "balance_day0": bal[bal["covariate"] == "day0_sessions"].iloc[0].to_dict(),
        "scorecard": sc.to_dict(orient="records"),
        "variance_reduction": vr.to_dict(orient="records"),
        "cuped_by_metric": cbm.to_dict(orient="records"),
        "segments": seg.to_dict(orient="records"),
        "segment_heterogeneity": het.to_dict(orient="records"),
        "novelty": {"by_week": wk.to_dict(orient="records"), **nov},
        "retention_curve": curves.to_dict(orient="records"),
        "practical_significance": ps,
        "decision": decision,
    }
    with open(config.TABLES_DIR / "results.json", "w") as fh:
        json.dump(results, fh, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    return results


def main() -> None:
    """CLI entry point: run analysis and print the headline."""
    r = run()
    sc = pd.DataFrame(r["scorecard"])
    pd.set_option("display.width", 200)
    print(sc[["role", "metric", "control", "treatment", "abs_diff", "abs_ci_low", "abs_ci_high",
              "rel_diff", "p_value", "p_adj", "verdict"]].to_string(index=False))
    print("decision:", r["decision"])


if __name__ == "__main__":
    main()
