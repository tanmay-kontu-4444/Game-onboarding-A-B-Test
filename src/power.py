"""Sample-size, power and minimum-detectable-effect (MDE) calculations.

Proportions use the arcsine (Cohen's h) normal approximation -- the same
method as ``statsmodels.stats.power.NormalIndPower`` -- so results can be
checked against ``solve_power``. Continuous metrics use the two-sample normal
approximation with a planning standard deviation.

All inputs are PLANNING ASSUMPTIONS fixed before the (simulated) test ran.

Run:  ``python -m src.power``            -> tables + power curve
      ``python -m src.power --write-plan`` -> also refresh the power section
                                            of docs/01_experiment_plan.md
"""

from __future__ import annotations

import argparse
import json
import math
import re

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm

from src import config, plotting

# Planning assumptions for guardrails (would come from pre-test dashboards).
GUARDRAIL_PLANNING = {
    "purchased_d7": {"type": "proportion", "baseline": 0.025},
    "revenue_d7": {"type": "mean", "baseline": 0.20, "sd": 2.50},
    "crashed_d7": {"type": "proportion", "baseline": 0.08},
    "avg_session_len": {"type": "mean", "baseline": 330.0, "sd": 330.0},
}
ELIGIBILITY_RATE = 0.97  # expected share of assigned installs left after exclusions


def cohens_h(p1: float, p2: float) -> float:
    """Cohen's h effect size between two proportions: 2*asin(sqrt p1) - 2*asin(sqrt p2)."""
    return 2 * math.asin(math.sqrt(p1)) - 2 * math.asin(math.sqrt(p2))


def power_proportions(p_base: float, mde_abs: float, n_per_arm: float, alpha: float = config.ALPHA) -> float:
    """Power of a two-sided two-sample test of proportions (equal allocation)."""
    h = abs(cohens_h(p_base + mde_abs, p_base))
    crit = norm.ppf(1 - alpha / 2)
    shift = h * math.sqrt(n_per_arm / 2)
    return float(norm.sf(crit - shift) + norm.cdf(-crit - shift))


def n_per_arm_proportions(
    p_base: float, mde_abs: float, alpha: float = config.ALPHA, power: float = config.POWER
) -> int:
    """Required sample size per arm to detect ``mde_abs`` (absolute) from ``p_base``."""
    if not 0 < p_base < 1 or not 0 < p_base + mde_abs < 1 or mde_abs == 0:
        raise ValueError("baseline and baseline + mde must lie strictly in (0, 1), mde != 0")
    n = brentq(lambda x: power_proportions(p_base, mde_abs, x, alpha) - power, 2, 1e9)
    return int(math.ceil(n))


def mde_proportions(
    p_base: float, n_per_arm: float, alpha: float = config.ALPHA, power: float = config.POWER,
    decrease: bool = False,
) -> float:
    """Smallest absolute change (as a positive number) detectable at ``n_per_arm``.

    ``decrease=True`` solves for a drop below ``p_base`` (asymmetric for small p).
    """
    sign = -1.0 if decrease else 1.0
    upper = (p_base if decrease else 1 - p_base) - 1e-9
    return float(brentq(lambda d: power_proportions(p_base, sign * d, n_per_arm, alpha) - power, 1e-9, upper))


def mde_means(sd: float, n_per_arm: float, alpha: float = config.ALPHA, power: float = config.POWER) -> float:
    """Smallest detectable absolute difference in means (two-sample normal approx.)."""
    return float((norm.ppf(1 - alpha / 2) + norm.ppf(power)) * sd * math.sqrt(2 / n_per_arm))


def runtime_days(n_per_arm: int, daily_installs: float = config.PLANNED_DAILY_INSTALLS,
                 eligibility: float = ELIGIBILITY_RATE) -> int:
    """Days of enrolment needed to collect ``2 * n_per_arm`` eligible players."""
    return int(math.ceil(2 * n_per_arm / (daily_installs * eligibility)))


def mde_table(p_base: float = config.BASELINE_D7,
              mdes_pp: tuple[float, ...] = (0.6, 0.8, 1.0, 1.2, 1.5, 2.0)) -> pd.DataFrame:
    """MDE vs required sample size and run time for the primary metric."""
    rows = []
    for mde in mdes_pp:
        n = n_per_arm_proportions(p_base, mde / 100)
        rows.append({
            "mde_abs_pp": mde,
            "mde_rel_pct": round(100 * (mde / 100) / p_base, 1),
            "n_per_arm": n,
            "n_total": 2 * n,
            "days_at_planned_volume": runtime_days(n),
        })
    return pd.DataFrame(rows)


def planned_n_per_arm() -> int:
    """Expected eligible players per arm after the full 28-day enrolment."""
    return int(config.PLANNED_DAILY_INSTALLS * ELIGIBILITY_RATE * config.ENROLMENT_DAYS / 2)


def guardrail_mde_table(n_per_arm: int) -> pd.DataFrame:
    """Detectable degradation for each guardrail at the planned sample size."""
    rows = []
    for metric, a in GUARDRAIL_PLANNING.items():
        if a["type"] == "proportion":
            mde = mde_proportions(a["baseline"], n_per_arm,
                                  decrease=config.GUARDRAILS[metric]["direction"] == "lower_is_worse")
        else:
            mde = mde_means(a["sd"], n_per_arm)
        thr = config.GUARDRAILS[metric]
        rows.append({
            "metric": metric,
            "planning_baseline": a["baseline"],
            "planning_sd": a["sd"] if "sd" in a else math.sqrt(a["baseline"] * (1 - a["baseline"])),
            "mde_abs": mde,
            "mde_rel_pct": 100 * mde / a["baseline"],
            "threshold_rel_pct": 100 * float(thr["threshold"]),
        })
    return pd.DataFrame(rows)


def summary() -> dict[str, float | int]:
    """Headline power numbers used in the plan."""
    n_design = n_per_arm_proportions(config.BASELINE_D7, config.PLANNED_MDE_ABS)
    n_planned = planned_n_per_arm()
    return {
        "alpha": config.ALPHA,
        "power": config.POWER,
        "baseline_d7": config.BASELINE_D7,
        "design_mde_abs": config.PLANNED_MDE_ABS,
        "design_mde_rel": config.PLANNED_MDE_ABS / config.BASELINE_D7,
        "n_per_arm_for_design_mde": n_design,
        "n_total_for_design_mde": 2 * n_design,
        "days_for_design_mde": runtime_days(n_design),
        "planned_daily_installs": config.PLANNED_DAILY_INSTALLS,
        "eligibility_rate": ELIGIBILITY_RATE,
        "planned_enrolment_days": config.ENROLMENT_DAYS,
        "planned_n_per_arm": n_planned,
        "mde_abs_at_planned_n": mde_proportions(config.BASELINE_D7, n_planned),
        "power_at_design_mde_planned_n": power_proportions(config.BASELINE_D7, config.PLANNED_MDE_ABS, n_planned),
    }


def plot_power_curve(path=None):
    """Power vs true absolute D7 lift, for the design n and the planned 28-day n."""
    plotting.apply_style()
    import matplotlib.pyplot as plt

    s = summary()
    lifts = np.linspace(0.0, 0.02, 201)
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    for n, color, label in [
        (s["n_per_arm_for_design_mde"], plotting.NEUTRAL, f"Minimum design: {s['n_per_arm_for_design_mde']:,}/arm"),
        (s["planned_n_per_arm"], plotting.CONTROL, f"Planned 28-day run: {s['planned_n_per_arm']:,}/arm"),
    ]:
        pw = [power_proportions(config.BASELINE_D7, d, n) if d > 0 else config.ALPHA for d in lifts]
        ax.plot(100 * lifts, pw, color=color, label=label)
    ax.axhline(config.POWER, color=plotting.TEXT_SECONDARY, lw=1, ls="--")
    ax.axvline(100 * config.PLANNED_MDE_ABS, color=plotting.TEXT_SECONDARY, lw=1, ls=":")
    ax.text(100 * config.PLANNED_MDE_ABS + 0.03, 0.42, "design MDE\n1.0pp", fontsize=8, color=plotting.TEXT_SECONDARY)
    ax.text(0.03, config.POWER + 0.02, "80% power", fontsize=8, color=plotting.TEXT_SECONDARY)
    ax.set_xlabel("True absolute lift in D7 retention (percentage points)")
    ax.set_ylabel("Power (two-sided, alpha = 0.05)")
    ax.set_ylim(0, 1.02)
    ax.set_xlim(0, 2.0)
    ax.set_title("Power curve - primary metric (D7 retention, baseline 18%)")
    ax.legend(loc="lower right")
    return plotting.save(fig, path or config.FIGURES_DIR / "power_curve.png")


def _fmt_baseline(metric: str, value: float) -> str:
    """Human-readable planning baseline (rate, USD or seconds)."""
    if GUARDRAIL_PLANNING[metric]["type"] == "proportion":
        return f"{value:.1%}"
    if metric == "revenue_d7":
        return f"${value:.2f} (SD ${GUARDRAIL_PLANNING[metric]['sd']:.2f})"
    return f"{value:,.0f}s (SD {GUARDRAIL_PLANNING[metric]['sd']:,.0f}s)"


def render_plan_section() -> str:
    """Markdown block injected into docs/01_experiment_plan.md."""
    s = summary()
    t = mde_table()
    g = guardrail_mde_table(s["planned_n_per_arm"])
    t_md = pd.DataFrame({
        "MDE (abs, pp)": [f"{v:.1f}" for v in t["mde_abs_pp"]],
        "MDE (rel, %)": [f"{v:.1f}" for v in t["mde_rel_pct"]],
        "n per arm": [f"{v:,}" for v in t["n_per_arm"]],
        "n total": [f"{v:,}" for v in t["n_total"]],
        "Days of enrolment": [str(v) for v in t["days_at_planned_volume"]],
    }).to_markdown(index=False, disable_numparse=True)
    g_md = pd.DataFrame({
        "Guardrail": g["metric"].map(config.METRIC_LABELS),
        "Planning baseline": [_fmt_baseline(m, v) for m, v in zip(g["metric"], g["planning_baseline"])],
        "MDE at planned n (rel)": [f"{v:.1f}%" for v in g["mde_rel_pct"]],
        "Unacceptable threshold (rel)": [f"{v:+.0f}%" for v in g["threshold_rel_pct"]],
        "Powered for threshold?": ["yes" if m <= abs(th) else "no" for m, th in zip(g["mde_rel_pct"], g["threshold_rel_pct"])],
    }).to_markdown(index=False, disable_numparse=True)
    return (
        f"| Input | Value |\n|---|---|\n"
        f"| Significance level (two-sided) | alpha = {s['alpha']} |\n"
        f"| Power | {s['power']:.0%} |\n"
        f"| Baseline D7 retention | {s['baseline_d7']:.0%} |\n"
        f"| Design MDE | +{100*s['design_mde_abs']:.1f}pp absolute ({s['design_mde_rel']:.1%} relative) |\n"
        f"| **Required sample size** | **{s['n_per_arm_for_design_mde']:,} per arm ({s['n_total_for_design_mde']:,} total)** |\n"
        f"| Eligible installs per day (planning) | {s['planned_daily_installs']:,} x {s['eligibility_rate']:.0%} eligibility |\n"
        f"| Minimum run time | {s['days_for_design_mde']} days |\n"
        f"| **Planned run time** | **{s['planned_enrolment_days']} days enrolment** (4 full weekly cycles) + {config.FOLLOW_UP_DAYS} days follow-up |\n"
        f"| Expected sample at 28 days | ~{s['planned_n_per_arm']:,} per arm |\n"
        f"| MDE at planned sample | +{100*s['mde_abs_at_planned_n']:.2f}pp |\n"
        f"| Power for a 1.0pp lift at planned sample | {s['power_at_design_mde_planned_n']:.1%} |\n\n"
        f"**MDE vs sample size (primary metric, 80% power, alpha 0.05):**\n\n{t_md}\n\n"
        f"**Guardrail sensitivity at the planned sample (~{s['planned_n_per_arm']:,}/arm):**\n\n{g_md}\n\n"
        f"![Power curve](../outputs/figures/power_curve.png)\n"
    )


def write_plan_section() -> None:
    """Replace the content between the POWER markers in the experiment plan."""
    path = config.DOCS_DIR / "01_experiment_plan.md"
    text = path.read_text()
    block = f"<!-- BEGIN:power (generated by `python -m src.power --write-plan`) -->\n{render_plan_section()}<!-- END:power -->"
    new, k = re.subn(r"<!-- BEGIN:power.*?<!-- END:power -->", lambda _: block, text, flags=re.S)
    if k != 1:
        raise RuntimeError("power markers not found in experiment plan")
    path.write_text(new)


def main() -> None:
    """Write power tables, summary JSON and the power curve figure."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write-plan", action="store_true", help="refresh the plan's power section")
    args = ap.parse_args()

    config.TABLES_DIR.mkdir(parents=True, exist_ok=True)
    t = mde_table()
    t.to_csv(config.TABLES_DIR / "power_mde_table.csv", index=False)
    (config.TABLES_DIR / "power_mde_table.md").write_text(t.to_markdown(index=False) + "\n")
    g = guardrail_mde_table(planned_n_per_arm())
    g.to_csv(config.TABLES_DIR / "power_guardrail_mde.csv", index=False)
    with open(config.TABLES_DIR / "power_summary.json", "w") as fh:
        json.dump(summary(), fh, indent=2)
    plot_power_curve()
    if args.write_plan:
        write_plan_section()
    s = summary()
    print(f"[power] n/arm for {100*config.PLANNED_MDE_ABS:.1f}pp MDE: {s['n_per_arm_for_design_mde']:,} "
          f"({s['days_for_design_mde']} days); planned n/arm {s['planned_n_per_arm']:,} "
          f"-> MDE {100*s['mde_abs_at_planned_n']:.2f}pp")
    print(t.to_string(index=False))
    print(g.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
