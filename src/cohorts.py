"""Run the SQL layer (sql/01..04) in DuckDB and write processed Parquet files.

Raw Parquet files are exposed as views; pre-registered parameters are passed
in as DuckDB variables so the SQL contains no magic numbers.

Run:  ``python -m src.cohorts``
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from src import config

RAW_TABLES = ["players", "assignments", "sessions", "progression_events", "purchases", "crashes"]
SQL_FILES = [
    "01_assignment_qc.sql",
    "02_player_cohort.sql",
    "03_metrics_per_player.sql",
    "04_segment_metrics.sql",
]
EXPORT_TABLES = [
    "assignment_qc",
    "cohort_audit",
    "player_cohort",
    "player_active_days",
    "metrics_per_player",
    "segment_metrics",
]


def connect(raw_dir: Path = config.RAW_DIR) -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB connection with raw views and parameter variables set."""
    con = duckdb.connect()
    for t in RAW_TABLES:
        path = raw_dir / f"{t}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing - run `make data` first")
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{path.as_posix()}')")
    params = {
        "enrol_start": f"TIMESTAMP '{config.ENROLMENT_START} 00:00:00'",
        "enrol_end_excl": f"TIMESTAMP '{config.ENROLMENT_END_EXCL} 00:00:00'",
        "cutoff_ts": f"TIMESTAMP '{config.DATA_CUTOFF:%Y-%m-%d %H:%M:%S}'",
        "bot_max_sessions": str(config.BOT_MAX_SESSIONS_PER_DAY),
        "early_window_days": str(config.EARLY_WINDOW_DAYS),
        "levels_last_day": str(config.LEVELS_WINDOW_LAST_DAY),
        "curve_max_day": str(config.RETENTION_CURVE_MAX_DAY),
    }
    for name, value in params.items():
        con.execute(f"SET VARIABLE {name} = {value}")
    return con


def run_sql(con: duckdb.DuckDBPyConnection, sql_dir: Path = config.SQL_DIR) -> None:
    """Execute every SQL file in order."""
    for f in SQL_FILES:
        con.execute((sql_dir / f).read_text())


def exclusions_by_arm(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Players removed at each eligibility step, by assigned arm."""
    return con.execute(
        """
        SELECT coalesce(exclusion_reason, 'eligible (analysis cohort)') AS step,
               count(*) FILTER (WHERE variant = 'control')   AS control,
               count(*) FILTER (WHERE variant = 'treatment') AS treatment,
               count(*)                                       AS total
        FROM cohort_audit
        GROUP BY exclusion_reason
        ORDER BY exclusion_reason NULLS LAST
        """
    ).df()


def build(raw_dir: Path = config.RAW_DIR, out_dir: Path = config.PROCESSED_DIR,
          tables_dir: Path = config.TABLES_DIR) -> dict[str, int]:
    """Run the full SQL pipeline and export tables. Returns row counts."""
    con = connect(raw_dir)
    run_sql(con)
    out_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for t in EXPORT_TABLES:
        con.execute(f"COPY {t} TO '{(out_dir / f'{t}.parquet').as_posix()}' (FORMAT parquet)")
        counts[t] = con.execute(f"SELECT count(*) FROM {t}").fetchall()[0][0]

    qc = con.execute("SELECT * FROM assignment_qc ORDER BY check_order").df().drop(columns="check_order")
    qc[["control", "treatment", "total"]] = qc[["control", "treatment", "total"]].astype("Int64")
    qc.to_csv(tables_dir / "assignment_qc.csv", index=False)
    (tables_dir / "assignment_qc.md").write_text(qc.to_markdown(index=False) + "\n")
    ex = exclusions_by_arm(con)
    ex.to_csv(tables_dir / "cohort_exclusions.csv", index=False)
    (tables_dir / "cohort_exclusions.md").write_text(ex.to_markdown(index=False) + "\n")

    dedup = con.execute(
        """
        SELECT 'sessions' AS table_name, (SELECT count(*) FROM sessions) AS raw_rows,
               (SELECT count(*) FROM sessions_dedup) AS dedup_rows
        UNION ALL SELECT 'progression_events', (SELECT count(*) FROM progression_events),
               (SELECT count(*) FROM progression_dedup)
        UNION ALL SELECT 'purchases', (SELECT count(*) FROM purchases), (SELECT count(*) FROM purchases_dedup)
        UNION ALL SELECT 'crashes', (SELECT count(*) FROM crashes), (SELECT count(*) FROM crashes_dedup)
        """
    ).df()
    dedup["duplicates_removed"] = dedup["raw_rows"] - dedup["dedup_rows"]
    dedup.to_csv(tables_dir / "dedup_summary.csv", index=False)
    (tables_dir / "dedup_summary.md").write_text(dedup.to_markdown(index=False) + "\n")

    print("[cohorts] assignment QC\n" + qc.to_string(index=False))
    print("[cohorts] exclusions\n" + ex.to_string(index=False))
    print("[cohorts] dedup\n" + dedup.to_string(index=False))
    return counts


def main() -> None:
    """CLI entry point."""
    counts = build()
    for t, n in counts.items():
        print(f"  {t:<22} {n:>10,} rows -> data/processed/{t}.parquet")


if __name__ == "__main__":
    main()
