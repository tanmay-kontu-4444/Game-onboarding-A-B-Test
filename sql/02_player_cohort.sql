-- =============================================================================
-- 02_player_cohort.sql  --  Clean analysis cohort (SIMULATED DATA)
-- -----------------------------------------------------------------------------
-- Implements the pre-registered eligibility rules (docs/01_experiment_plan.md 4.1):
--   1. install in the enrolment window
--   2. assigned to exactly one variant (duplicates of the same arm collapsed)
--   3. assignment not before install
--   4. complete D7 window before the data cutoff
--   5. not bot-like: <= bot_max_sessions sessions on every calendar day
-- plus exact-duplicate removal for every event table.
--
-- All rules are applied blind to variant. Exclusions are kept in
-- cohort_audit (with a reason) so they can be reported by arm.
--
-- Outputs: sessions_dedup, progression_dedup, purchases_dedup, crashes_dedup,
--          cohort_audit, player_cohort
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1) De-duplicated event tables.
--    Sessions have an id: keep one row per session_id.
--    Other events have no id: an exact repeat of every column is a duplicate.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE sessions_dedup AS
SELECT player_id, session_id, session_start_ts, session_length_sec
FROM sessions
QUALIFY row_number() OVER (PARTITION BY session_id ORDER BY session_start_ts) = 1;

CREATE OR REPLACE TABLE progression_dedup AS
SELECT player_id, event_ts, event_type, level_number, tutorial_step
FROM progression_events
QUALIFY row_number() OVER (
    PARTITION BY player_id, event_ts, event_type, level_number, tutorial_step
    ORDER BY event_ts
) = 1;

CREATE OR REPLACE TABLE purchases_dedup AS
SELECT player_id, purchase_ts, revenue_usd
FROM purchases
QUALIFY row_number() OVER (PARTITION BY player_id, purchase_ts, revenue_usd ORDER BY purchase_ts) = 1;

CREATE OR REPLACE TABLE crashes_dedup AS
SELECT player_id, crash_ts
FROM crashes
QUALIFY row_number() OVER (PARTITION BY player_id, crash_ts ORDER BY crash_ts) = 1;

-- -----------------------------------------------------------------------------
-- 2) Cohort audit: every assigned player with an exclusion reason (NULL = keep).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE cohort_audit AS
WITH
-- Rank each player's assignment rows chronologically; keep the earliest.
ranked_assignments AS (
    SELECT
        player_id,
        variant,
        assigned_ts,
        row_number() OVER (PARTITION BY player_id ORDER BY assigned_ts) AS rn,
        count(DISTINCT variant) OVER (PARTITION BY player_id)           AS n_variants
    FROM assignments
),

resolved_assignment AS (
    SELECT player_id, variant, assigned_ts, n_variants
    FROM ranked_assignments
    WHERE rn = 1
),

-- Busiest calendar day per player, for the bot rule.
sessions_per_day AS (
    SELECT player_id, CAST(session_start_ts AS DATE) AS activity_date, count(*) AS n_sessions
    FROM sessions_dedup
    GROUP BY ALL
),

max_daily_sessions AS (
    SELECT player_id, max(n_sessions) AS max_sessions_in_a_day
    FROM sessions_per_day
    GROUP BY player_id
),

joined AS (
    SELECT
        r.player_id,
        r.variant,
        r.assigned_ts,
        r.n_variants,
        p.install_ts,
        CAST(p.install_ts AS DATE)                    AS install_date,
        p.platform,
        p.device_tier,
        p.country_tier,
        p.acquisition_channel,
        coalesce(m.max_sessions_in_a_day, 0)          AS max_sessions_in_a_day
    FROM resolved_assignment AS r
    LEFT JOIN players AS p            USING (player_id)
    LEFT JOIN max_daily_sessions AS m USING (player_id)
)

SELECT
    *,
    -- Enrolment week 1..4 (by install date)
    1 + date_diff('day', getvariable('enrol_start'), install_date) // 7 AS enrolment_week,
    -- First failing rule, in the pre-registered order
    CASE
        WHEN install_ts IS NULL                                         THEN 'missing_install_record'
        WHEN install_ts <  getvariable('enrol_start')
          OR install_ts >= getvariable('enrol_end_excl')                THEN 'outside_enrolment_window'
        WHEN n_variants > 1                                             THEN 'assigned_both_arms'
        WHEN assigned_ts < install_ts                                   THEN 'assigned_before_install'
        WHEN install_date + 7 >= CAST(getvariable('cutoff_ts') AS DATE) THEN 'incomplete_d7_window'
        WHEN max_sessions_in_a_day > getvariable('bot_max_sessions')    THEN 'bot_like_activity'
    END AS exclusion_reason
FROM joined;

-- -----------------------------------------------------------------------------
-- 3) The analysis cohort: eligible players only.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE player_cohort AS
SELECT
    player_id,
    variant,
    install_ts,
    install_date,
    assigned_ts,
    enrolment_week,
    platform,
    device_tier,
    country_tier,
    acquisition_channel
FROM cohort_audit
WHERE exclusion_reason IS NULL
ORDER BY player_id;   -- stable row order => byte-identical reruns
