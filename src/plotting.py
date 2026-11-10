"""Shared matplotlib style for every figure in the project.

Colours follow a validated, colour-blind-safe categorical palette
(control = slot 1 blue, treatment = slot 2 orange; adjacent-pair CVD dE 24.7).
Status colours are reserved for verdicts and always paired with a text label.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"
NEUTRAL = "#8a8984"

CONTROL = "#2a78d6"
TREATMENT = "#eb6834"
ARM_COLORS = {"control": CONTROL, "treatment": TREATMENT}

STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}

SIM_NOTE = "Simulated data - synthetic A/B test for portfolio purposes"


def apply_style() -> None:
    """Set global rcParams: recessive axes/grid, thin marks, readable type."""
    plt.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID,
        "axes.labelcolor": TEXT_SECONDARY,
        "axes.titlecolor": TEXT,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.labelsize": 10,
        "axes.grid": True,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "xtick.color": TEXT_SECONDARY,
        "ytick.color": TEXT_SECONDARY,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "lines.linewidth": 2.0,
        "legend.frameon": False,
        "legend.fontsize": 9,
        "font.size": 10,
        "text.color": TEXT,
    })


def save(fig: plt.Figure, path: Path, note: bool = True) -> Path:
    """Add the 'simulated data' footnote and write a PNG at 2x density."""
    if note:
        fig.text(0.01, -0.01, SIM_NOTE, fontsize=7.5, color=NEUTRAL, ha="left", va="top")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path
