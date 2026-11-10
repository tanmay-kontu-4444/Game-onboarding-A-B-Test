"""Shared constants for the (simulated) onboarding A/B test.

Everything that more than one module needs -- paths, experiment calendar,
pre-registered thresholds -- lives here so there is a single source of truth.
All data in this project is SYNTHETIC.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
ROOT: Path = Path(__file__).resolve().parents[1]
RAW_DIR: Path = ROOT / "data" / "raw"
PROCESSED_DIR: Path = ROOT / "data" / "processed"
SQL_DIR: Path = ROOT / "sql"
TABLES_DIR: Path = ROOT / "outputs" / "tables"
FIGURES_DIR: Path = ROOT / "outputs" / "figures"
DOCS_DIR: Path = ROOT / "docs"

# --------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------
SEED: int = 20260901

# --------------------------------------------------------------------------
# Experiment calendar (all timestamps UTC)
# --------------------------------------------------------------------------
ENROLMENT_START: date = date(2026, 9, 1)          # first enrolment day (inclusive)
ENROLMENT_END_EXCL: date = date(2026, 9, 29)      # 28 days of enrolment
ENROLMENT_DAYS: int = 28
FOLLOW_UP_DAYS: int = 14
# Data cutoff: the extract contains events strictly before this instant.
DATA_CUTOFF: datetime = datetime(2026, 10, 13, 0, 0, 0)

# --------------------------------------------------------------------------
# Pre-registered analysis rules (see docs/01_experiment_plan.md)
# --------------------------------------------------------------------------
ALPHA: float = 0.05
POWER: float = 0.80
BASELINE_D7: float = 0.18
PLANNED_MDE_ABS: float = 0.010          # 1.0 percentage point
PLANNED_DAILY_INSTALLS: int = 2_850     # planning assumption (pre-test dashboards)
BOT_MAX_SESSIONS_PER_DAY: int = 50      # > this on any calendar day => bot-like
SRM_ALPHA: float = 0.001                # SRM alarm threshold
RETENTION_CURVE_MAX_DAY: int = 14
EARLY_WINDOW_DAYS: int = 7              # "first 7 days" = calendar days 0..6
LEVELS_WINDOW_LAST_DAY: int = 3         # "levels by D3" = calendar days 0..3
N_BOOTSTRAP: int = 2_000

# Guardrail "unacceptable degradation" thresholds, expressed as the worst
# acceptable value of the treatment-vs-control difference.
#   kind="relative": threshold on (treatment / control - 1)
#   kind="absolute": threshold on (treatment - control)
#   direction="lower_is_worse": a drop is bad; "higher_is_worse": a rise is bad.
GUARDRAILS: dict[str, dict[str, object]] = {
    "purchased_d7": {"kind": "relative", "threshold": -0.10, "direction": "lower_is_worse"},
    "revenue_d7": {"kind": "relative", "threshold": -0.10, "direction": "lower_is_worse"},
    "crashed_d7": {"kind": "relative", "threshold": 0.10, "direction": "higher_is_worse"},
    "avg_session_len": {"kind": "relative", "threshold": -0.05, "direction": "lower_is_worse"},
}

PRIMARY_METRIC: str = "d7_retained"
SECONDARY_METRICS: list[str] = ["tutorial_complete", "levels_by_d3", "d1_retained"]
GUARDRAIL_METRICS: list[str] = list(GUARDRAILS)
SEGMENT_DIMENSIONS: list[str] = ["platform", "device_tier", "country_tier"]

METRIC_LABELS: dict[str, str] = {
    "d7_retained": "D7 retention",
    "d1_retained": "D1 retention",
    "tutorial_complete": "Tutorial completion",
    "levels_by_d3": "Levels completed by D3",
    "purchased_d7": "Purchase conversion (first 7 days)",
    "revenue_d7": "Revenue per player (first 7 days, USD)",
    "crashed_d7": "Crash rate (first 7 days)",
    "avg_session_len": "Avg session length (sec, first 7 days)",
}
