"""Synthetic data generator for the onboarding A/B test.

*** ALL DATA PRODUCED HERE IS SIMULATED. It does not describe any real game. ***

Design
------
Each player gets covariates (platform, country tier, acquisition channel,
device tier), a latent engagement score ``z`` and a day-0 session count that
is a noisy read-out of ``z``. Every outcome is generated from a per-player
*probability model* (:func:`outcome_params`):

* daily activity  ``P(active on day d) = sigmoid(a_d + base_i + 1[treat] * tau_d * m_i)``
* sessions per active day, session lengths (heavy tailed), levels, purchases
  (discrete price points, heavy tailed) and crashes, all conditional on activity.

Because outcomes are probabilistic functions of player attributes, the TRUE
treatment effects can be computed analytically as the average difference in
expected potential outcomes over a large superpopulation
(:func:`compute_ground_truth`). The same :func:`outcome_params` drives both the
event generator and the ground truth, so they cannot drift apart.

The ground truth is written to ``data/raw/ground_truth.json``; analysis code
must never read it (only the recovery test does).

Run:  ``python -m src.simulate``
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.special import expit

from src import config

N_SIM_DAYS = 42  # day 0 .. day 41 (first enrolment day + 41 = last day before cutoff)
TRUTH_DAYS = config.RETENTION_CURVE_MAX_DAY + 1  # days 0..14 needed for ground truth

PRICE_POINTS = np.array([0.99, 2.99, 4.99, 9.99, 19.99, 49.99, 99.99])
PRICE_PROBS = np.array([0.30, 0.25, 0.20, 0.13, 0.07, 0.04, 0.01])
EXPECTED_PRICE = float(PRICE_POINTS @ PRICE_PROBS)


# =============================================================================
# Parameters
# =============================================================================
@dataclass(frozen=True)
class SimParams:
    """All knobs of the data-generating process (DGP)."""

    daily_installs: float = 2_850.0
    weekend_factor: float = 1.15
    weekday_factor: float = 0.94
    leaked_days: int = 2                 # flag left on 2 days after enrolment closed
    never_launched_rate: float = 0.015   # installs that never open the app (no assignment)

    # Retention targets (control arm, ITT incl. "ghost" players)
    d1_target: float = 0.38
    retention_decay: float = 0.385       # r(d) = d1_target * d ** -decay  -> D7 ~ 18%
    d7_true_lift_abs: float = 0.012      # +1.2pp absolute in D7 retention
    tau_profile_intercept: float = 0.4   # tau_d = tau7 * min(1, 0.4 + 0.1 d): D1 lift smaller
    tau_profile_slope: float = 0.1

    # Heterogeneity multipliers on the retention lift (logit scale)
    het_low: float = 1.8
    het_mid: float = 1.0
    het_high: float = 0.6
    het_t1_ios: float = 0.05             # near-zero lift for T1 iOS

    # Tutorial completion (among players who start a session)
    tutorial_rate_control: float = 0.62
    tutorial_rate_treatment: float = 0.80
    tutorial_steps_control: int = 7
    tutorial_steps_treatment: int = 3

    # Levels per session and treatment multipliers (learn-by-playing => earlier levels)
    level_rate: float = 1.4
    level_mult_treat_d0: float = 1.06
    level_mult_treat_d1_3: float = 1.03

    # Purchases: control conversion within days 0..6 and the "skipped shop step" effect
    purchase_conv_target: float = 0.025
    purchase_day0_factor: float = 1.5
    shop_mult_treat_first7: float = 0.935

    # Sessions & lengths
    session_len_median_sec: float = 200.0
    session_len_sigma: float = 0.9
    idle_tail_prob: float = 0.02         # app left open => x6 length
    idle_tail_mult: float = 6.0
    session_len_mult_treat_first7: float = 0.97

    # Crashes per session by device tier; Android multiplier; ghost first-launch crash
    crash_low: float = 0.020
    crash_mid: float = 0.008
    crash_high: float = 0.004
    crash_android_mult: float = 1.4
    ghost_crash_prob: float = 0.5

    # Messiness
    ghost_rate: float = 0.025            # assigned but no sessions ever logged
    ghost_rate_low_extra: float = 0.025
    n_bots: int = 40
    dup_assignment_rate: float = 0.004
    n_both_arms: int = 25
    n_assigned_before_install: int = 30
    dup_rate_sessions: float = 0.010
    dup_rate_progression: float = 0.015
    dup_rate_purchases: float = 0.005
    dup_rate_crashes: float = 0.005

    n_calibration: int = 300_000
    n_truth: int = 600_000


@dataclass
class Calibration:
    """Intercepts solved numerically so the DGP hits its marginal targets."""

    a_d: np.ndarray = field(repr=False)  # activity intercept per day (index 0 unused)
    tau7: float = 0.0
    purchase_intercept: float = 0.0
    tutorial_intercept_control: float = 0.0
    tutorial_intercept_treatment: float = 0.0

    def to_dict(self) -> dict[str, object]:
        """JSON-serialisable view."""
        return {
            "a_d": [round(float(x), 5) for x in self.a_d],
            "tau7": round(self.tau7, 5),
            "purchase_intercept": round(self.purchase_intercept, 5),
            "tutorial_intercept_control": round(self.tutorial_intercept_control, 5),
            "tutorial_intercept_treatment": round(self.tutorial_intercept_treatment, 5),
        }


# =============================================================================
# Population
# =============================================================================
def draw_population(n: int, rng: np.random.Generator, p: SimParams) -> pd.DataFrame:
    """Draw player covariates, latent engagement, ghost flag and day-0 sessions.

    Nothing here depends on treatment, so day-0 sessions are a valid
    (pre-outcome, treatment-independent) covariate *by construction*.
    """
    ios = rng.random(n) < 0.45
    p_t1 = np.where(ios, 0.45, 0.18)
    u = rng.random(n)
    country = np.where(u < p_t1, "T1", np.where(u < p_t1 + (1 - p_t1) / 2, "T2", "T3"))
    channel = rng.choice(
        ["organic", "paid_social", "paid_search", "cross_promo"], size=n, p=[0.40, 0.30, 0.15, 0.15]
    )
    u = rng.random(n)
    low_cut = np.where(ios, 0.10, 0.40)
    mid_cut = np.where(ios, 0.50, 0.80)
    device = np.where(u < low_cut, "low", np.where(u < mid_cut, "mid", "high"))

    channel_shift = pd.Series(channel).map(
        {"organic": 0.2, "paid_social": -0.2, "paid_search": 0.0, "cross_promo": 0.3}
    ).to_numpy()
    z = rng.normal(size=n) + channel_shift

    ghost_p = p.ghost_rate + p.ghost_rate_low_extra * (device == "low")
    ghost = rng.random(n) < ghost_p
    day0 = np.where(ghost, 0, 1 + rng.poisson(np.exp(0.2 + 0.45 * z)))

    return pd.DataFrame(
        {
            "platform": np.where(ios, "iOS", "Android"),
            "country_tier": country,
            "acquisition_channel": channel,
            "device_tier": device,
            "z": z,
            "is_ghost": ghost,
            "day0_sessions": day0.astype(np.int64),
        }
    )


def _flags(pop: pd.DataFrame) -> dict[str, np.ndarray]:
    """Boolean indicator arrays used by the outcome model."""
    return {
        "ios": (pop["platform"] == "iOS").to_numpy(),
        "t1": (pop["country_tier"] == "T1").to_numpy(),
        "t3": (pop["country_tier"] == "T3").to_numpy(),
        "low": (pop["device_tier"] == "low").to_numpy(),
        "mid": (pop["device_tier"] == "mid").to_numpy(),
        "high": (pop["device_tier"] == "high").to_numpy(),
        "paid_social": (pop["acquisition_channel"] == "paid_social").to_numpy(),
        "organic": (pop["acquisition_channel"] == "organic").to_numpy(),
        "ghost": pop["is_ghost"].to_numpy(),
    }


def retention_base_logit(pop: pd.DataFrame) -> np.ndarray:
    """Player-level part of the daily-activity logit (no day or treatment term)."""
    f = _flags(pop)
    z = pop["z"].to_numpy()
    d0 = pop["day0_sessions"].to_numpy()
    return (
        1.0 * z
        + 0.15 * (d0 - 2.3)
        + 0.15 * f["ios"]
        + 0.10 * f["t1"]
        - 0.10 * f["t3"]
        - 0.20 * f["low"]
        + 0.10 * f["high"]
        - 0.15 * f["paid_social"]
        + 0.10 * f["organic"]
    )


def heterogeneity_multiplier(pop: pd.DataFrame, p: SimParams) -> np.ndarray:
    """Per-player multiplier on the retention lift: bigger on low-end devices, ~0 for T1 iOS."""
    f = _flags(pop)
    m = np.where(f["low"], p.het_low, np.where(f["mid"], p.het_mid, p.het_high))
    return np.where(f["t1"] & f["ios"], p.het_t1_ios, m)


def tau_profile(n_days: int, p: SimParams) -> np.ndarray:
    """Relative size of the retention lift by day (day 0 has none)."""
    d = np.arange(n_days, dtype=float)
    k = np.minimum(1.0, p.tau_profile_intercept + p.tau_profile_slope * d)
    k[0] = 0.0
    return k


# =============================================================================
# Outcome model (shared by event generation and ground truth)
# =============================================================================
def outcome_params(
    pop: pd.DataFrame, treat: np.ndarray, cal: Calibration, p: SimParams, n_days: int
) -> dict[str, np.ndarray]:
    """Per-player (and per-day) outcome probabilities/rates for the given arm.

    Returns arrays of shape (n,) or (n, n_days).
    """
    f = _flags(pop)
    z = pop["z"].to_numpy()
    treat = np.asarray(treat, dtype=bool)
    alive = (~f["ghost"]).astype(float)

    # --- daily activity ---------------------------------------------------
    base = retention_base_logit(pop)
    m = heterogeneity_multiplier(pop, p)
    k = tau_profile(n_days, p)
    logits = cal.a_d[None, :n_days] + base[:, None] + (treat * cal.tau7 * m)[:, None] * k[None, :]
    p_active = expit(logits) * alive[:, None]
    p_active[:, 0] = alive  # every non-ghost plays on install day

    # --- sessions per active day (d >= 1: 1 + Poisson(lambda)) -------------
    sess_lambda = np.exp(-0.1 + 0.35 * z)

    # --- levels per session -----------------------------------------------
    level_mult = np.ones(n_days)
    level_mult_t = np.ones(n_days)
    level_mult_t[0] = p.level_mult_treat_d0
    level_mult_t[1 : min(4, n_days)] = p.level_mult_treat_d1_3
    rho = p.level_rate * np.exp(0.15 * z)
    level_rate = rho[:, None] * np.where(treat[:, None], level_mult_t[None, :], level_mult[None, :])

    # --- purchases per active day -----------------------------------------
    r = np.exp(
        cal.purchase_intercept + 0.7 * z + 0.6 * f["t1"] - 0.6 * f["t3"] + 0.35 * f["ios"]
    )
    day_fac = np.ones(n_days)
    day_fac[0] = p.purchase_day0_factor
    shop = np.ones(n_days)
    shop[: config.EARLY_WINDOW_DAYS] = p.shop_mult_treat_first7
    purchase_rate = r[:, None] * day_fac[None, :] * np.where(treat[:, None], shop[None, :], 1.0)

    # --- crashes ----------------------------------------------------------
    crash_p = np.where(f["low"], p.crash_low, np.where(f["mid"], p.crash_mid, p.crash_high))
    crash_p = crash_p * np.where(f["ios"], 1.0, p.crash_android_mult)

    # --- session length (log-normal + idle tail) ---------------------------
    dev_shift = np.where(f["low"], -0.10, np.where(f["high"], 0.05, 0.0))
    len_mu = np.log(p.session_len_median_sec) + 0.15 * z + dev_shift
    len_mult = np.ones(n_days)
    len_mult[: config.EARLY_WINDOW_DAYS] = p.session_len_mult_treat_first7
    session_len_mult = np.where(treat[:, None], len_mult[None, :], 1.0)

    # --- tutorial ---------------------------------------------------------
    tut_int = np.where(treat, cal.tutorial_intercept_treatment, cal.tutorial_intercept_control)
    tutorial_p = expit(tut_int + 0.6 * z - 0.4 * f["low"] + 0.2 * f["high"]) * alive

    return {
        "p_active": p_active,
        "sess_lambda": sess_lambda,
        "level_rate": level_rate,
        "purchase_rate": purchase_rate,
        "crash_p": crash_p,
        "len_mu": len_mu,
        "session_len_mult": session_len_mult,
        "tutorial_p": tutorial_p,
    }


def expected_metrics(pop: pd.DataFrame, op: dict[str, np.ndarray], p: SimParams) -> dict[str, np.ndarray]:
    """Analytic per-player expectations of every analysis metric under one arm.

    Metric definitions mirror ``sql/03_metrics_per_player.sql``.
    """
    w = config.EARLY_WINDOW_DAYS
    pa = op["p_active"]
    ghost = pop["is_ghost"].to_numpy()
    d0 = pop["day0_sessions"].to_numpy().astype(float)
    lam = op["sess_lambda"]

    exp_sessions = pa * (1.0 + lam)[:, None]
    exp_sessions[:, 0] = d0
    levels_by_d3 = (exp_sessions[:, : config.LEVELS_WINDOW_LAST_DAY + 1]
                    * op["level_rate"][:, : config.LEVELS_WINDOW_LAST_DAY + 1]).sum(axis=1)

    pr = op["purchase_rate"][:, :w]
    no_purchase = np.prod(1.0 - pa[:, :w] * (1.0 - np.exp(-pr)), axis=1)
    revenue = EXPECTED_PRICE * (pa[:, :w] * pr).sum(axis=1)

    c = op["crash_p"]
    day0_nocrash = (1.0 - c) ** d0
    later_nocrash = (1.0 - pa[:, 1:w]) + pa[:, 1:w] * ((1.0 - c) * np.exp(-lam * c))[:, None]
    crashed = 1.0 - day0_nocrash * np.prod(later_nocrash, axis=1)
    crashed = np.where(ghost, p.ghost_crash_prob, crashed)

    tail = (1 - p.idle_tail_prob) + p.idle_tail_prob * p.idle_tail_mult
    exp_len = np.exp(op["len_mu"] + p.session_len_sigma**2 / 2) * tail * op["session_len_mult"][:, 0]

    out = {f"d{d}_retained": pa[:, d] for d in range(pa.shape[1])}
    out.update(
        {
            "tutorial_complete": op["tutorial_p"],
            "levels_by_d3": levels_by_d3,
            "purchased_d7": 1.0 - no_purchase,
            "revenue_d7": revenue,
            "crashed_d7": crashed,
            "avg_session_len": exp_len,          # defined only for players with >=1 session
            "has_session_weight": (~ghost).astype(float),
        }
    )
    return out


# =============================================================================
# Calibration
# =============================================================================
def calibrate(p: SimParams, rng: np.random.Generator) -> Calibration:
    """Solve intercepts so control hits the retention/purchase/tutorial targets."""
    pop = draw_population(p.n_calibration, rng, p)
    base = retention_base_logit(pop)
    alive = ~pop["is_ghost"].to_numpy()
    days = np.arange(1, N_SIM_DAYS)
    targets = p.d1_target * days ** (-p.retention_decay)

    # Vectorised bisection for a_d, d = 1..41 (ITT mean incl. ghosts == target)
    lo, hi = np.full(days.size, -10.0), np.full(days.size, 5.0)
    for _ in range(60):
        mid = (lo + hi) / 2
        rate = (expit(mid[None, :] + base[:, None]) * alive[:, None]).mean(axis=0)
        too_high = rate > targets
        hi = np.where(too_high, mid, hi)
        lo = np.where(too_high, lo, mid)
    a_d = np.concatenate([[0.0], (lo + hi) / 2])
    cal = Calibration(a_d=a_d)

    # tau7: D7 ATE == target lift
    m = heterogeneity_multiplier(pop, p)
    a7 = a_d[7]

    def d7_gap(tau: float) -> float:
        diff = expit(a7 + base + tau * m) - expit(a7 + base)
        return float((diff * alive).mean()) - p.d7_true_lift_abs

    cal.tau7 = brentq(d7_gap, 0.0, 2.0)

    # Purchase intercept: control conversion in days 0..6 == target
    def conv_gap(intercept: float) -> float:
        cal.purchase_intercept = intercept
        op = outcome_params(pop, np.zeros(len(pop), bool), cal, p, config.EARLY_WINDOW_DAYS + 1)
        return float(expected_metrics(pop, op, p)["purchased_d7"].mean()) - p.purchase_conv_target

    cal.purchase_intercept = brentq(conv_gap, -12.0, 0.0)

    # Tutorial intercepts (rates among players who actually start a session)
    f = _flags(pop)
    xb = 0.6 * pop["z"].to_numpy() - 0.4 * f["low"] + 0.2 * f["high"]
    cal.tutorial_intercept_control = brentq(
        lambda c: float(expit(c + xb[alive]).mean()) - p.tutorial_rate_control, -5, 5
    )
    cal.tutorial_intercept_treatment = brentq(
        lambda c: float(expit(c + xb[alive]).mean()) - p.tutorial_rate_treatment, -5, 5
    )
    return cal


# =============================================================================
# Ground truth
# =============================================================================
def _arm_summary(e0: np.ndarray, e1: np.ndarray, w: np.ndarray | None = None) -> dict[str, float]:
    """Control mean, treatment mean, absolute and relative difference."""
    w = np.ones_like(e0) if w is None else w
    c = float(np.sum(e0 * w) / np.sum(w))
    t = float(np.sum(e1 * w) / np.sum(w))
    return {"control": c, "treatment": t, "abs_diff": t - c, "rel_diff": t / c - 1.0}


def compute_ground_truth(p: SimParams, cal: Calibration, rng: np.random.Generator) -> dict[str, object]:
    """True (population-average) treatment effects from analytic potential outcomes."""
    pop = draw_population(p.n_truth, rng, p)
    n = len(pop)
    e0 = expected_metrics(pop, outcome_params(pop, np.zeros(n, bool), cal, p, TRUTH_DAYS), p)
    e1 = expected_metrics(pop, outcome_params(pop, np.ones(n, bool), cal, p, TRUTH_DAYS), p)

    metrics = [
        "d7_retained", "d1_retained", "tutorial_complete", "levels_by_d3",
        "purchased_d7", "revenue_d7", "crashed_d7",
    ]
    effects = {mt: _arm_summary(e0[mt], e1[mt]) for mt in metrics}
    effects["avg_session_len"] = _arm_summary(
        e0["avg_session_len"], e1["avg_session_len"], e0["has_session_weight"]
    )
    effects["day0_sessions"] = _arm_summary(
        pop["day0_sessions"].to_numpy(float), pop["day0_sessions"].to_numpy(float)
    )

    segments: dict[str, dict[str, dict[str, float]]] = {}
    for dim in config.SEGMENT_DIMENSIONS:
        segments[dim] = {}
        for level, idx in pop.groupby(dim).indices.items():
            segments[dim][str(level)] = _arm_summary(e0["d7_retained"][idx], e1["d7_retained"][idx])
    t1_ios = ((pop["country_tier"] == "T1") & (pop["platform"] == "iOS")).to_numpy()
    segments["t1_ios"] = {"T1 iOS": _arm_summary(e0["d7_retained"][t1_ios], e1["d7_retained"][t1_ios])}

    curve = {
        f"d{d}": _arm_summary(e0[f"d{d}_retained"], e1[f"d{d}_retained"]) for d in range(TRUTH_DAYS)
    }
    return {"true_effects": effects, "segment_true_effects_d7": segments, "retention_curve": curve}


# =============================================================================
# Event generation
# =============================================================================
def _install_calendar(p: SimParams, rng: np.random.Generator) -> pd.DataFrame:
    """Install timestamps over the enrolment window plus the leaked days."""
    rows = []
    hour_w = np.array([2, 1.5, 1, 1, 1, 1.5, 2, 3, 4, 4.5, 5, 5, 5, 5, 5, 5.5, 6, 6.5, 7, 7.5, 7, 6, 4.5, 3])
    hour_w = hour_w / hour_w.sum()
    n_days = config.ENROLMENT_DAYS + p.leaked_days
    for k in range(n_days):
        day = pd.Timestamp(config.ENROLMENT_START) + pd.Timedelta(days=k)
        fac = p.weekend_factor if day.dayofweek >= 5 else p.weekday_factor
        cnt = rng.poisson(p.daily_installs * fac * rng.lognormal(0, 0.05))
        hours = rng.choice(24, size=cnt, p=hour_w)
        secs = hours * 3600 + rng.integers(0, 3600, size=cnt)
        rows.append(day + pd.to_timedelta(secs, unit="s"))
    ts = pd.DatetimeIndex(np.concatenate([r.values for r in rows])).as_unit("us")
    return pd.DataFrame({"install_ts": ts})


def _explode(counts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For an (n, D) count matrix return (row index, day index) repeated by count."""
    r, d = np.nonzero(counts)
    c = counts[r, d]
    return np.repeat(r, c), np.repeat(d, c)


def _random_time_in_sessions(
    pd_index: np.ndarray, sess_offset: np.ndarray, sess_count: np.ndarray,
    sess_start: np.ndarray, sess_len: np.ndarray, rng: np.random.Generator,
) -> np.ndarray:
    """Pick a random session of each event's player-day and a random time inside it."""
    pick = sess_offset[pd_index] + (rng.random(pd_index.size) * sess_count[pd_index]).astype(np.int64)
    offs = (rng.random(pd_index.size) * sess_len[pick]).astype(np.int64)
    return sess_start[pick] + offs * 1_000_000  # microseconds


def generate_events(
    p: SimParams, cal: Calibration, rng: np.random.Generator
) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    """Generate all raw tables plus a log of injected data-quality issues."""
    inst = _install_calendar(p, rng)
    n_inst = len(inst)
    pop = draw_population(n_inst, rng, p)
    players = pd.concat([inst, pop], axis=1)

    never = rng.random(n_inst) < p.never_launched_rate
    players["launched"] = ~never
    players["variant"] = np.where(rng.random(n_inst) < 0.5, "treatment", "control")

    install_us = players["install_ts"].values.astype("datetime64[us]").astype(np.int64)
    day_us = 86_400 * 1_000_000
    install_day0_us = install_us - install_us % day_us
    delay = np.minimum(rng.lognormal(np.log(45), 1.0, n_inst), 3600) * 1_000_000
    assigned_us = np.minimum(install_us + delay.astype(np.int64), install_day0_us + day_us - 1_000_000)

    cutoff_us = np.datetime64(config.DATA_CUTOFF, "us").astype(np.int64)
    max_day = (cutoff_us - install_day0_us) // day_us - 1          # last complete day index

    # ----- outcomes for launched (non-bot) players --------------------------
    L = np.flatnonzero(players["launched"].to_numpy())
    lp = pop.iloc[L].reset_index(drop=True)
    treat = (players["variant"].to_numpy()[L] == "treatment")
    op = outcome_params(lp, treat, cal, p, N_SIM_DAYS)
    n = len(L)
    day_idx = np.arange(N_SIM_DAYS)
    observable = day_idx[None, :] <= max_day[L][:, None]

    active = (rng.random((n, N_SIM_DAYS)) < op["p_active"]) & observable
    sessions = np.where(active, 1 + rng.poisson(op["sess_lambda"][:, None], (n, N_SIM_DAYS)), 0)
    sessions[:, 0] = np.where(active[:, 0], lp["day0_sessions"].to_numpy(), 0)

    # Player-day blocks, then sessions within each block
    pd_r, pd_d = np.nonzero(sessions)
    pd_cnt = sessions[pd_r, pd_d]
    pd_off = np.concatenate([[0], np.cumsum(pd_cnt)[:-1]])
    s_pd = np.repeat(np.arange(pd_r.size), pd_cnt)
    s_r, s_d = pd_r[s_pd], pd_d[s_pd]
    s_rank = np.arange(s_pd.size) - pd_off[s_pd]

    day_start = install_day0_us[L][s_r] + s_d * day_us
    a_us = assigned_us[L][s_r]
    win_start = np.where(s_d == 0, a_us, day_start)
    win_len = day_start + day_us - win_start
    s_start = win_start + (rng.random(s_pd.size) * win_len).astype(np.int64)
    s_start = np.where((s_d == 0) & (s_rank == 0), a_us, s_start)
    # sort sessions chronologically inside each player-day block
    order = np.lexsort((s_start, s_pd))
    s_start = s_start[order]

    tail = np.where(rng.random(s_pd.size) < p.idle_tail_prob, p.idle_tail_mult, 1.0)
    s_len = np.exp(op["len_mu"][s_r] + p.session_len_sigma * rng.normal(size=s_pd.size)) * tail
    s_len = np.maximum(3, np.round(s_len * op["session_len_mult"][s_r, s_d])).astype(np.int64)

    sess_df = pd.DataFrame({
        "player_id": players.index.to_numpy()[L][s_r],
        "session_start_ts": s_start,
        "session_length_sec": s_len,
    })

    # ----- levels ---------------------------------------------------------
    lev_counts = np.zeros_like(sessions)
    lev_counts[pd_r, pd_d] = rng.poisson(pd_cnt * op["level_rate"][pd_r, pd_d])
    pd_lookup = np.full((n, N_SIM_DAYS), -1)
    pd_lookup[pd_r, pd_d] = np.arange(pd_r.size)
    lr, ld = _explode(lev_counts)
    lev_ts = _random_time_in_sessions(pd_lookup[lr, ld], pd_off, pd_cnt, s_start, s_len, rng)
    levels = pd.DataFrame({"player_id": players.index.to_numpy()[L][lr], "event_ts": lev_ts,
                           "event_type": "level_complete"})

    # ----- tutorial -------------------------------------------------------
    alive = ~lp["is_ghost"].to_numpy()
    k_steps = np.where(treat, p.tutorial_steps_treatment, p.tutorial_steps_control)
    completed = (rng.random(n) < op["tutorial_p"]) & alive
    steps_done = np.where(completed, k_steps, (rng.random(n) * k_steps).astype(int))
    steps_done = np.where(alive, steps_done, 0)
    t_r = np.repeat(np.arange(n), steps_done)
    t_step = np.arange(t_r.size) - np.repeat(np.cumsum(steps_done) - steps_done, steps_done) + 1
    gap = np.where(treat[t_r], 60, 35) * 1_000_000
    t_ts = assigned_us[L][t_r] + t_step * gap + rng.integers(0, 10_000_000, t_r.size)
    tut_steps = pd.DataFrame({"player_id": players.index.to_numpy()[L][t_r], "event_ts": t_ts,
                              "event_type": "tutorial_step_complete", "tutorial_step": t_step})
    c_r = np.flatnonzero(completed)
    tut_done = pd.DataFrame({
        "player_id": players.index.to_numpy()[L][c_r],
        "event_ts": assigned_us[L][c_r] + (k_steps[c_r] + 1) * np.where(treat[c_r], 60, 35) * 1_000_000,
        "event_type": "tutorial_complete",
    })

    # ----- purchases ------------------------------------------------------
    pur_counts = np.zeros_like(sessions)
    pur_counts[pd_r, pd_d] = rng.poisson(op["purchase_rate"][pd_r, pd_d])
    pr_r, pr_d = _explode(pur_counts)
    pur_ts = _random_time_in_sessions(pd_lookup[pr_r, pr_d], pd_off, pd_cnt, s_start, s_len, rng)
    purchases = pd.DataFrame({
        "player_id": players.index.to_numpy()[L][pr_r],
        "purchase_ts": pur_ts,
        "revenue_usd": rng.choice(PRICE_POINTS, size=pr_r.size, p=PRICE_PROBS),
    })

    # ----- crashes --------------------------------------------------------
    crash_mask = rng.random(s_pd.size) < op["crash_p"][s_r]
    cr_ts = s_start[crash_mask] + (rng.random(crash_mask.sum()) * s_len[crash_mask]).astype(np.int64) * 1_000_000
    crashes = [pd.DataFrame({"player_id": sess_df["player_id"].to_numpy()[crash_mask], "crash_ts": cr_ts})]
    g = np.flatnonzero(~alive & (rng.random(n) < p.ghost_crash_prob))
    crashes.append(pd.DataFrame({"player_id": players.index.to_numpy()[L][g],
                                 "crash_ts": assigned_us[L][g] + 2_000_000}))

    # ----- bots -----------------------------------------------------------
    bot_ids = rng.choice(L[(~lp["is_ghost"].to_numpy()) & (max_day[L] >= 10)], size=p.n_bots, replace=False)
    bot_sessions, bot_levels = [], []
    for pid in bot_ids:
        for d in range(11):
            cnt = 60 + rng.poisson(150)
            st = install_day0_us[pid] + d * day_us + np.sort(rng.integers(0, day_us, cnt))
            if d == 0:
                st = np.maximum(st, assigned_us[pid])
            bot_sessions.append(pd.DataFrame({"player_id": pid, "session_start_ts": st,
                                              "session_length_sec": rng.integers(2, 15, cnt)}))
            bot_levels.append(pd.DataFrame({"player_id": pid, "event_ts": st + 1_000_000,
                                            "event_type": "level_complete"}))
    # bots replace whatever "normal" behaviour was simulated for those ids
    sess_df = pd.concat([sess_df[~sess_df["player_id"].isin(bot_ids)], *bot_sessions], ignore_index=True)
    levels = pd.concat([levels[~levels["player_id"].isin(bot_ids)], *bot_levels], ignore_index=True)

    # ----- assemble progression, number levels sequentially ---------------
    prog = pd.concat([tut_steps, tut_done, levels], ignore_index=True)
    prog = prog.sort_values(["player_id", "event_ts"], kind="stable").reset_index(drop=True)
    is_lvl = prog["event_type"] == "level_complete"
    prog["level_number"] = pd.array([pd.NA] * len(prog), dtype="Int64")
    prog.loc[is_lvl, "level_number"] = prog[is_lvl].groupby("player_id").cumcount().to_numpy() + 1
    prog["tutorial_step"] = prog["tutorial_step"].astype("Int64")
    prog = prog[["player_id", "event_ts", "event_type", "level_number", "tutorial_step"]]
    crashes_df = pd.concat(crashes, ignore_index=True)

    # ----- assignments with messiness ------------------------------------
    assign = pd.DataFrame({"player_id": players.index.to_numpy()[L],
                           "variant": players["variant"].to_numpy()[L],
                           "assigned_ts": assigned_us[L]})
    normal_ids = np.setdiff1d(L, bot_ids)
    dup_ids = rng.choice(normal_ids, size=int(p.dup_assignment_rate * n), replace=False)
    dups = assign.set_index("player_id").loc[dup_ids].reset_index()
    dups["assigned_ts"] += rng.integers(1, 300, len(dups)) * 1_000_000
    both_ids = rng.choice(np.setdiff1d(normal_ids, dup_ids), size=p.n_both_arms, replace=False)
    both = assign.set_index("player_id").loc[both_ids].reset_index()
    both["variant"] = np.where(both["variant"] == "control", "treatment", "control")
    both["assigned_ts"] += rng.integers(3600, 5 * 86_400, len(both)) * 1_000_000
    early_ids = rng.choice(np.setdiff1d(normal_ids, np.concatenate([dup_ids, both_ids])),
                           size=p.n_assigned_before_install, replace=False)
    early_mask = assign["player_id"].isin(early_ids)
    assign.loc[early_mask, "assigned_ts"] = (
        install_us[assign.loc[early_mask, "player_id"].to_numpy()]
        - rng.integers(1, 600, early_mask.sum()) * 1_000_000
    )
    assign = pd.concat([assign, dups, both], ignore_index=True)

    # ----- duplicate events ----------------------------------------------
    def add_dups(df: pd.DataFrame, rate: float) -> tuple[pd.DataFrame, int]:
        k = int(round(rate * len(df)))
        return pd.concat([df, df.sample(n=k, random_state=rng)], ignore_index=True), k

    sess_df["session_id"] = rng.permutation(len(sess_df)).astype(np.int64) + 10_000_000
    sess_df, n_dup_s = add_dups(sess_df, p.dup_rate_sessions)
    prog, n_dup_p = add_dups(prog, p.dup_rate_progression)
    purchases, n_dup_pu = add_dups(purchases, p.dup_rate_purchases)
    crashes_df, n_dup_c = add_dups(crashes_df, p.dup_rate_crashes)

    # ----- final formatting ----------------------------------------------
    def to_ts(x: pd.Series) -> pd.Series:
        return pd.to_datetime(np.asarray(x, dtype=np.int64), unit="us")

    tables = {
        "players": pd.DataFrame({
            "player_id": players.index.to_numpy(),
            "install_ts": players["install_ts"].values,
            "platform": players["platform"], "country_tier": players["country_tier"],
            "acquisition_channel": players["acquisition_channel"], "device_tier": players["device_tier"],
        }),
        "assignments": assign.assign(assigned_ts=lambda d: to_ts(d["assigned_ts"]))
                             .sort_values("assigned_ts").reset_index(drop=True),
        "sessions": sess_df.assign(session_start_ts=lambda d: to_ts(d["session_start_ts"]))
                           [["player_id", "session_id", "session_start_ts", "session_length_sec"]]
                           .sort_values("session_start_ts").reset_index(drop=True),
        "progression_events": prog.assign(event_ts=lambda d: to_ts(d["event_ts"]))
                                  .sort_values("event_ts").reset_index(drop=True),
        "purchases": purchases.assign(purchase_ts=lambda d: to_ts(d["purchase_ts"]))
                              .sort_values("purchase_ts").reset_index(drop=True),
        "crashes": crashes_df.assign(crash_ts=lambda d: to_ts(d["crash_ts"]))
                             .sort_values("crash_ts").reset_index(drop=True),
    }
    for t in tables.values():
        t["player_id"] = t["player_id"].astype(np.int64) + 1_000_000  # realistic-looking ids

    issues = {
        "n_installs": int(n_inst),
        "n_never_launched": int(never.sum()),
        "n_assigned_players": int(n),
        "n_ghost_players": int((~alive).sum()),
        "n_bots": int(p.n_bots),
        "bot_player_ids": [int(b) + 1_000_000 for b in bot_ids],
        "n_duplicate_assignment_rows": int(len(dup_ids)),
        "n_players_both_arms": int(p.n_both_arms),
        "n_assigned_before_install": int(p.n_assigned_before_install),
        "n_leaked_post_enrolment_assigned": int(
            (players["install_ts"].iloc[L] >= pd.Timestamp(config.ENROLMENT_END_EXCL)).sum()
        ),
        "n_duplicate_session_rows": n_dup_s,
        "n_duplicate_progression_rows": n_dup_p,
        "n_duplicate_purchase_rows": n_dup_pu,
        "n_duplicate_crash_rows": n_dup_c,
    }
    return tables, issues


# =============================================================================
# Entry point
# =============================================================================
def main() -> None:
    """Calibrate, compute ground truth, generate and write all raw tables."""
    p = SimParams()
    ss = np.random.SeedSequence(config.SEED)
    rng_cal, rng_truth, rng_events = (np.random.default_rng(s) for s in ss.spawn(3))

    t0 = datetime.now()
    cal = calibrate(p, rng_cal)
    truth = compute_ground_truth(p, cal, rng_truth)
    tables, issues = generate_events(p, cal, rng_events)

    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(config.RAW_DIR / f"{name}.parquet", index=False)

    ground_truth = {
        "WARNING": "SIMULATED DATA. Ground truth for validation only - analysis code must not read this file.",
        "seed": config.SEED,
        "population": "clean eligible population (bots excluded; ghosts included, ITT)",
        "novelty_effect_built_in": False,
        **truth,
        "injected_data_issues": issues,
        "design_parameters": asdict(p),
        "calibration": cal.to_dict(),
    }
    with open(config.RAW_DIR / "ground_truth.json", "w") as fh:
        json.dump(ground_truth, fh, indent=2, default=float)

    print(f"[simulate] done in {(datetime.now() - t0).total_seconds():.1f}s  (SIMULATED DATA)")
    for name, df in tables.items():
        print(f"  {name:<20} {len(df):>10,} rows")
    te = truth["true_effects"]
    print(f"  true D7: control={te['d7_retained']['control']:.4f} "
          f"lift={te['d7_retained']['abs_diff']:+.4f}")


if __name__ == "__main__":
    main()
