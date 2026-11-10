-- =============================================================================
-- 03_metrics_per_player.sql  --  One row per cohort player (SIMULATED DATA)
-- -----------------------------------------------------------------------------
-- Every event is mapped to day_n = calendar days since install (UTC, day 0 =
-- install day). Metric definitions follow docs/01_experiment_plan.md 3:
--   dN_retained        >=1 session on day N (NULL if day N not complete at cutoff)
--   tutorial_complete  any tutorial_complete event
--   levels_by_d3       level_complete events on days 0..levels_last_day
--   purchased_d7 / revenue_d7 / crashed_d7 / avg_session_len  on days 0..early_window-1
--   day0_sessions      sessions on install day (CUPED covariate)
--
-- Outputs: player_active_days (player_id, day_n) for retention curves,
--          metrics_per_player
-- =============================================================================

CREATE OR REPLACE TABLE player_active_days AS
SELECT DISTINCT
    s.player_id,
    date_diff('day', c.install_date, CAST(s.session_start_ts AS DATE)) AS day_n
FROM sessions_dedup AS s
JOIN player_cohort AS c USING (player_id)
WHERE date_diff('day', c.install_date, CAST(s.session_start_ts AS DATE))
      BETWEEN 0 AND getvariable('curve_max_day');

CREATE OR REPLACE TABLE metrics_per_player AS
WITH
cohort AS (
    SELECT
        *,
        -- last calendar day index fully observed before the cutoff
        date_diff('day', install_date, CAST(getvariable('cutoff_ts') AS DATE)) - 1 AS last_complete_day
    FROM player_cohort
),

session_days AS (
    SELECT
        s.player_id,
        date_diff('day', c.install_date, CAST(s.session_start_ts AS DATE)) AS day_n,
        s.session_length_sec
    FROM sessions_dedup AS s
    JOIN cohort AS c USING (player_id)
),

session_metrics AS (
    SELECT
        player_id,
        count(*)            FILTER (WHERE day_n = 0)                                   AS day0_sessions,
        bool_or(day_n = 1)                                                             AS active_d1,
        bool_or(day_n = 7)                                                             AS active_d7,
        count(*)            FILTER (WHERE day_n < getvariable('early_window_days'))    AS sessions_d7,
        avg(session_length_sec) FILTER (WHERE day_n < getvariable('early_window_days')) AS avg_session_len
    FROM session_days
    GROUP BY player_id
),

progression_metrics AS (
    SELECT
        e.player_id,
        bool_or(e.event_type = 'tutorial_complete')                                     AS tutorial_complete,
        count(*) FILTER (
            WHERE e.event_type = 'level_complete'
              AND date_diff('day', c.install_date, CAST(e.event_ts AS DATE))
                  <= getvariable('levels_last_day')
        )                                                                               AS levels_by_d3
    FROM progression_dedup AS e
    JOIN cohort AS c USING (player_id)
    GROUP BY e.player_id
),

purchase_metrics AS (
    SELECT
        p.player_id,
        count(*)          AS purchases_d7,
        sum(revenue_usd)  AS revenue_d7
    FROM purchases_dedup AS p
    JOIN cohort AS c USING (player_id)
    WHERE date_diff('day', c.install_date, CAST(p.purchase_ts AS DATE)) < getvariable('early_window_days')
    GROUP BY p.player_id
),

crash_metrics AS (
    SELECT DISTINCT x.player_id
    FROM crashes_dedup AS x
    JOIN cohort AS c USING (player_id)
    WHERE date_diff('day', c.install_date, CAST(x.crash_ts AS DATE)) < getvariable('early_window_days')
)

SELECT
    c.player_id,
    c.variant,
    c.install_date,
    c.enrolment_week,
    c.platform,
    c.device_tier,
    c.country_tier,
    c.acquisition_channel,
    -- retention: only defined once the day-N window has fully elapsed
    CASE WHEN c.last_complete_day >= 1 THEN coalesce(s.active_d1, FALSE)::INT END AS d1_retained,
    CASE WHEN c.last_complete_day >= 7 THEN coalesce(s.active_d7, FALSE)::INT END AS d7_retained,
    coalesce(pr.tutorial_complete, FALSE)::INT                                     AS tutorial_complete,
    coalesce(pr.levels_by_d3, 0)                                                   AS levels_by_d3,
    (pu.player_id IS NOT NULL)::INT                                                AS purchased_d7,
    coalesce(pu.revenue_d7, 0.0)                                                   AS revenue_d7,
    (cr.player_id IS NOT NULL)::INT                                                AS crashed_d7,
    s.avg_session_len,                                  -- NULL if no sessions in days 0..6
    coalesce(s.sessions_d7, 0)                                                     AS sessions_d7,
    coalesce(s.day0_sessions, 0)                                                   AS day0_sessions
FROM cohort AS c
LEFT JOIN session_metrics     AS s  USING (player_id)
LEFT JOIN progression_metrics AS pr USING (player_id)
LEFT JOIN purchase_metrics    AS pu USING (player_id)
LEFT JOIN crash_metrics       AS cr USING (player_id);
