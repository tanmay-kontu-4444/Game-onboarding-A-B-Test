"""Shared fixtures. Integration tests need `make data cohorts analysis` to have run."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from src import config


def _require(path):
    if not path.exists():
        pytest.fail(f"{path} missing - run `make data cohorts analysis` before `make test`")
    return path


@pytest.fixture(scope="session")
def metrics() -> pd.DataFrame:
    return pd.read_parquet(_require(config.PROCESSED_DIR / "metrics_per_player.parquet"))


@pytest.fixture(scope="session")
def cohort() -> pd.DataFrame:
    return pd.read_parquet(_require(config.PROCESSED_DIR / "player_cohort.parquet"))


@pytest.fixture(scope="session")
def results() -> dict:
    return json.loads(_require(config.TABLES_DIR / "results.json").read_text())


@pytest.fixture(scope="session")
def ground_truth() -> dict:
    return json.loads(_require(config.RAW_DIR / "ground_truth.json").read_text())
