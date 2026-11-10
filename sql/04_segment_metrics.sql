-- =============================================================================
-- 04_segment_metrics.sql  --  Metrics by variant x segment (SIMULATED DATA)
-- -----------------------------------------------------------------------------
-- One pass with GROUPING SETS produces the overall row and every
-- pre-registered segment dimension (platform, device tier, country tier).
-- Sums and sums of squares are included so standard errors can be rebuilt
-- downstream without touching player-level data.
--
-- Output: segment_metrics
-- =============================================================================

CREATE OR REPLACE TABLE segment_metrics AS
SELECT
    CASE
        WHEN grouping(platform)     = 0 THEN 'platform'
        WHEN grouping(device_tier)  = 0 THEN 'device_tier'
        WHEN grouping(country_tier) = 0 THEN 'country_tier'
        ELSE 'overall'
    END                                                 AS segment_dimension,
    coalesce(platform, device_tier, country_tier, 'all') AS segment_value,
    variant,
    count(*)                                            AS n_players,
    sum(d7_retained)                                    AS d7_retained_n,
    avg(d7_retained)                                    AS d7_retention,
    avg(d1_retained)                                    AS d1_retention,
    avg(tutorial_complete)                              AS tutorial_completion,
    avg(levels_by_d3)                                   AS mean_levels_by_d3,
    avg(purchased_d7)                                   AS purchase_conversion_d7,
    avg(revenue_d7)                                     AS revenue_per_player_d7,
    sum(revenue_d7 * revenue_d7)                        AS revenue_d7_sumsq,
    avg(crashed_d7)                                     AS crash_rate_d7,
    avg(avg_session_len)                                AS mean_avg_session_len,
    avg(day0_sessions)                                  AS mean_day0_sessions
FROM metrics_per_player
GROUP BY GROUPING SETS (
    (variant),
    (variant, platform),
    (variant, device_tier),
    (variant, country_tier)
)
ORDER BY segment_dimension, segment_value, variant;
