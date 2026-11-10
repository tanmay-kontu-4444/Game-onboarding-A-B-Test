-- =============================================================================
-- 01_assignment_qc.sql  --  Assignment quality checks (SIMULATED DATA)
-- -----------------------------------------------------------------------------
-- Run BEFORE any outcome analysis. Produces one row per check with counts split
-- by the player's FIRST logged variant, so asymmetric problems are visible.
--
-- Inputs : players, assignments (raw views)
-- Params : getvariable('enrol_start'), getvariable('enrol_end_excl')
-- Output : table assignment_qc(check_order, check_name, control, treatment, total)
-- =============================================================================

CREATE OR REPLACE TABLE assignment_qc AS
WITH
-- One row per assignment log line, enriched with the install record.
assignment_rows AS (
    SELECT
        a.player_id,
        a.variant,
        a.assigned_ts,
        p.install_ts
    FROM assignments AS a
    LEFT JOIN players AS p USING (player_id)
),

-- Collapse to one row per player and describe what the logs look like.
per_player AS (
    SELECT
        player_id,
        arg_min(variant, assigned_ts)          AS first_variant,
        count(*)                               AS n_rows,
        count(DISTINCT variant)                AS n_variants,
        min(assigned_ts)                       AS first_assigned_ts,
        any_value(install_ts)                  AS install_ts
    FROM assignment_rows
    GROUP BY player_id
),

-- Flag each data-quality condition per player.
flags AS (
    SELECT
        first_variant,
        TRUE                                                        AS is_assigned,
        n_rows > n_variants                                         AS has_duplicate_rows,
        n_variants > 1                                              AS in_both_arms,
        install_ts IS NULL                                          AS missing_install,
        first_assigned_ts < install_ts                              AS assigned_before_install,
        install_ts <  getvariable('enrol_start')
            OR install_ts >= getvariable('enrol_end_excl')          AS outside_enrolment
    FROM per_player
),

-- Long format: one row per (check, variant).
checks AS (
    SELECT 1 AS check_order, 'assignment log rows' AS check_name, variant AS first_variant, count(*) AS n
    FROM assignment_rows GROUP BY variant
    UNION ALL
    SELECT 2, 'distinct assigned players',            first_variant, count(*) FROM flags GROUP BY 1, 2, 3
    UNION ALL
    SELECT 3, 'players with duplicate same-arm rows', first_variant, count_if(has_duplicate_rows) FROM flags GROUP BY 1, 2, 3
    UNION ALL
    SELECT 4, 'players assigned to both arms',        first_variant, count_if(in_both_arms) FROM flags GROUP BY 1, 2, 3
    UNION ALL
    SELECT 5, 'assigned before install',              first_variant, count_if(assigned_before_install) FROM flags GROUP BY 1, 2, 3
    UNION ALL
    SELECT 6, 'install outside enrolment window',     first_variant, count_if(outside_enrolment) FROM flags GROUP BY 1, 2, 3
    UNION ALL
    SELECT 7, 'assigned but no install record',       first_variant, count_if(missing_install) FROM flags GROUP BY 1, 2, 3
)

SELECT
    check_order,
    check_name,
    sum(n) FILTER (WHERE first_variant = 'control')    AS control,
    sum(n) FILTER (WHERE first_variant = 'treatment')  AS treatment,
    sum(n)                                             AS total
FROM checks
GROUP BY check_order, check_name

UNION ALL

-- Installs in the enrolment window that never produced an assignment
-- (app never launched). No variant exists for these players.
SELECT
    8,
    'enrolment-window installs never assigned',
    NULL,
    NULL,
    count(*)
FROM players AS p
WHERE p.install_ts >= getvariable('enrol_start')
  AND p.install_ts <  getvariable('enrol_end_excl')
  AND NOT EXISTS (SELECT 1 FROM assignments AS a WHERE a.player_id = p.player_id)

ORDER BY check_order;
