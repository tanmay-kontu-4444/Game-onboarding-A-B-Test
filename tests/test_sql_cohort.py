"""SQL layer: invariants on the real cohort + rule-by-rule checks on a hand-built dataset."""

from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import pandas as pd
import pytest

from src import cohorts, config


# =============================================================================
# Invariants on the real pipeline output
# =============================================================================
def test_no_duplicate_players(cohort, metrics):
    assert cohort["player_id"].is_unique
    assert metrics["player_id"].is_unique
    assert len(metrics) == len(cohort)


def test_no_player_in_both_arms(cohort):
    raw = pd.read_parquet(config.RAW_DIR / "assignments.parquet")
    arms = raw[raw["player_id"].isin(cohort["player_id"])].groupby("player_id")["variant"].nunique()
    assert (arms == 1).all()
    assert set(cohort["variant"]) == {"control", "treatment"}


def test_every_player_has_complete_d7_window(cohort, metrics):
    cutoff = pd.Timestamp(config.DATA_CUTOFF).normalize()
    install = pd.to_datetime(cohort["install_date"])
    assert (install + pd.Timedelta(days=7) < cutoff).all()
    assert metrics["d7_retained"].notna().all()
    assert metrics["d1_retained"].notna().all()


def test_enrolment_window_and_assignment_timing(cohort):
    assert (cohort["install_ts"] >= pd.Timestamp(config.ENROLMENT_START)).all()
    assert (cohort["install_ts"] < pd.Timestamp(config.ENROLMENT_END_EXCL)).all()
    assert (cohort["assigned_ts"] >= cohort["install_ts"]).all()


def test_no_bot_like_players_remain(cohort):
    con = duckdb.connect()
    max_daily = con.execute(
        f"""
        SELECT max(n) FROM (
            SELECT player_id, CAST(session_start_ts AS DATE) d, count(DISTINCT session_id) n
            FROM read_parquet('{(config.PROCESSED_DIR / "player_cohort.parquet").as_posix()}') c
            JOIN read_parquet('{(config.RAW_DIR / "sessions.parquet").as_posix()}') s USING (player_id)
            GROUP BY 1, 2)
        """
    ).fetchall()[0][0]
    assert max_daily <= config.BOT_MAX_SESSIONS_PER_DAY


def test_metric_ranges(metrics):
    for col in ["d1_retained", "d7_retained", "tutorial_complete", "purchased_d7", "crashed_d7"]:
        assert set(metrics[col].unique()) <= {0, 1}
    assert (metrics["revenue_d7"] >= 0).all()
    assert (metrics.loc[metrics["purchased_d7"] == 0, "revenue_d7"] == 0).all()
    assert (metrics["levels_by_d3"] >= 0).all()


def test_segment_metrics_reconcile_with_player_level(metrics):
    seg = pd.read_parquet(config.PROCESSED_DIR / "segment_metrics.parquet")
    overall = seg[seg["segment_dimension"] == "overall"].set_index("variant")
    for arm, g in metrics.groupby("variant"):
        assert overall.loc[arm, "n_players"] == len(g)
        assert overall.loc[arm, "d7_retention"] == pytest.approx(g["d7_retained"].mean())
    for dim in config.SEGMENT_DIMENSIONS:
        assert seg.loc[seg["segment_dimension"] == dim, "n_players"].sum() == len(metrics)


# =============================================================================
# Hand-built dataset: one player per rule
# =============================================================================
T = datetime(2026, 9, 5, 10, 0, 0)


def _ts(day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2026, 9, 5) + timedelta(days=day, hours=hour, minutes=minute)


@pytest.fixture()
def mini_raw(tmp_path):
    """Seven players, each exercising one rule. Written as raw Parquet files."""
    players = pd.DataFrame({
        "player_id": [1, 2, 3, 4, 5, 6, 7],
        "install_ts": [T, T, T, T, datetime(2026, 9, 30, 9), T, datetime(2026, 9, 28, 23)],
        "platform": ["iOS", "Android", "iOS", "Android", "iOS", "Android", "iOS"],
        "country_tier": ["T1"] * 7, "acquisition_channel": ["organic"] * 7, "device_tier": ["mid"] * 7,
    })
    a = T + timedelta(seconds=30)
    assignments = pd.DataFrame({
        "player_id": [1, 2, 2, 3, 4, 5, 6, 6, 7],
        "variant": ["control", "control", "treatment", "treatment", "control", "treatment",
                    "treatment", "treatment", "treatment"],
        "assigned_ts": [a, a, a + timedelta(hours=5), T - timedelta(minutes=3), a,
                        datetime(2026, 9, 30, 9, 1), a, a + timedelta(minutes=1), datetime(2026, 9, 28, 23, 1)],
    })
    sess = [
        # player 1: day 0 (duplicated row), day 7 -> d7 retained, not d1
        (1, 101, a, 300), (1, 101, a, 300), (1, 102, _ts(7), 200),
        (6, 601, a, 100), (7, 701, datetime(2026, 9, 28, 23, 1), 50), (7, 702, datetime(2026, 9, 29, 8), 60),
        (2, 201, a, 100), (3, 301, a, 100), (5, 501, datetime(2026, 9, 30, 9, 1), 100),
    ]
    sess += [(4, 4000 + i, _ts(1, 8, i), 5) for i in range(60)]  # bot: 60 sessions on day 1
    sessions = pd.DataFrame(sess, columns=["player_id", "session_id", "session_start_ts", "session_length_sec"])
    prog = pd.DataFrame({
        "player_id": [1, 1, 1, 1, 1],
        "event_ts": [a + timedelta(minutes=5), a + timedelta(minutes=5), _ts(3, 9), _ts(4, 9), a + timedelta(minutes=2)],
        "event_type": ["level_complete", "level_complete", "level_complete", "level_complete", "tutorial_complete"],
        "level_number": pd.array([1, 1, 2, 3, None], dtype="Int64"),
        "tutorial_step": pd.array([None] * 5, dtype="Int64"),
    })
    purchases = pd.DataFrame({
        "player_id": [1, 1, 1],
        "purchase_ts": [_ts(6, 20), _ts(6, 20), _ts(7, 9)],   # day 6 duplicated; day 7 outside window
        "revenue_usd": [4.99, 4.99, 9.99],
    })
    crashes = pd.DataFrame({"player_id": [1], "crash_ts": [_ts(2)]})
    for name, df in {"players": players, "assignments": assignments, "sessions": sessions,
                     "progression_events": prog, "purchases": purchases, "crashes": crashes}.items():
        df.to_parquet(tmp_path / f"{name}.parquet", index=False)
    return tmp_path


def _run(raw_dir, cutoff: str | None = None) -> duckdb.DuckDBPyConnection:
    con = cohorts.connect(raw_dir)
    if cutoff:
        con.execute(f"SET VARIABLE cutoff_ts = TIMESTAMP '{cutoff}'")
    cohorts.run_sql(con)
    return con


def test_mini_exclusion_reasons(mini_raw):
    con = _run(mini_raw)
    audit = con.execute("SELECT player_id, exclusion_reason FROM cohort_audit").df().set_index("player_id")
    reasons = audit["exclusion_reason"].to_dict()
    assert pd.isna(reasons[1]) and pd.isna(reasons[6]) and pd.isna(reasons[7])
    assert reasons[2] == "assigned_both_arms"
    assert reasons[3] == "assigned_before_install"
    assert reasons[4] == "bot_like_activity"
    assert reasons[5] == "outside_enrolment_window"


def test_mini_duplicate_assignment_collapsed(mini_raw):
    con = _run(mini_raw)
    c = con.execute("SELECT player_id, variant FROM player_cohort").df()
    assert c["player_id"].is_unique
    assert c.set_index("player_id").loc[6, "variant"] == "treatment"


def test_mini_metric_definitions(mini_raw):
    con = _run(mini_raw)
    m = con.execute("SELECT * FROM metrics_per_player").df().set_index("player_id")
    p1 = m.loc[1]
    assert p1["day0_sessions"] == 1              # duplicate session row removed
    assert p1["d1_retained"] == 0 and p1["d7_retained"] == 1
    assert p1["levels_by_d3"] == 2               # dup removed; day-4 level excluded
    assert p1["tutorial_complete"] == 1
    assert p1["purchased_d7"] == 1
    assert p1["revenue_d7"] == pytest.approx(4.99)  # dup removed; day-7 purchase excluded
    assert p1["crashed_d7"] == 1
    assert p1["avg_session_len"] == pytest.approx(300)  # day-7 session outside days 0..6
    assert m.loc[7, "d1_retained"] == 1          # 23:01 install, next calendar day session
    assert m.loc[6, "crashed_d7"] == 0 and m.loc[6, "purchased_d7"] == 0


def test_mini_incomplete_d7_window_excluded(mini_raw):
    # cutoff 2026-09-12 00:00: a 2026-09-05 install's day 7 (09-12) is not complete
    con = _run(mini_raw, cutoff="2026-09-12 00:00:00")
    audit = con.execute("SELECT player_id, exclusion_reason FROM cohort_audit").df().set_index("player_id")
    assert audit.loc[1, "exclusion_reason"] == "incomplete_d7_window"
    assert audit.loc[7, "exclusion_reason"] == "incomplete_d7_window"
