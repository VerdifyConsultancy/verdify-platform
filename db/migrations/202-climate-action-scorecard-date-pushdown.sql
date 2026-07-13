-- 202-climate-action-scorecard-date-pushdown.sql
--
-- #498: outcome_kpi()'s action-scorecard read had a ~6-minute-per-call CPU
-- floor. The issue hypothesized the `date = $1` predicate was applied above
-- the aggregation (whole-window evaluation); a prod EXPLAIN ANALYZE
-- (2026-07-13, day 2026-07-12, 1,947 action rows, 348.6 s total) shows the
-- planner ALREADY pushes the date predicate to the climate_action_log scans
-- as a row filter — the real cost is per-row function-call fan-out inside
-- fn_climate_action_effectiveness:
--
--   duty LATERAL (16 samples x 8 fn_equip_at calls per action row) 293.8 s (84%)
--   targetlist fn_setpoint_at (4 calls per action row)              ~51 s (15%)
--   non-sargable date filter over the 14-day window's chunks         ~2 s
--   c0/c1 climate LATERAL lookups                                   ~1.6 s
--
-- fn_equip_at / fn_setpoint_at are non-inlinable scalar SQL functions (ORDER
-- BY + LIMIT bodies); each call re-executes a ChunkAppend over the whole
-- hypertable (~1.2 ms and ~6.6 ms per call respectively). 1,947 rows/day x
-- 132 calls/row is the floor. Chunk pruning alone would save ~2 s of 348 s.
--
-- Fix: fn_climate_action_daily_scorecard(target_date) — a single-day,
-- set-based scorecard whose OUTPUT is byte-identical to
-- `SELECT * FROM v_climate_action_daily_scorecard WHERE date = target_date`
-- while touching only the target day's chunks:
--
--   * sargable bounds: ts >= GREATEST(local-midnight, now() - '14 days')
--     AND ts < next-local-midnight — [local-midnight, next) is exactly the
--     row set of `(ts AT TIME ZONE 'America/Denver')::date = target_date`
--     (DST-safe: America/Denver transitions at 02:00, midnight always
--     exists and is unambiguous), and the GREATEST reproduces the
--     effectiveness function's rolling `ts >= now() - interval '14 days'`
--     window so the truncated oldest day aggregates identically;
--   * c0/c1 climate lookups and the after-setpoint resolution are the
--     EXACT SELECT bodies of migration 142 / fn_setpoint_at, inlined as
--     LATERAL subqueries (same index paths, same values, no per-call
--     SQL-function machinery);
--   * the duty computation is one set-based last-observation-carried-forward
--     pass over equipment_state edges instead of 112 fn_equip_at calls per
--     row. Tie fidelity: fn_equip_at's `ORDER BY ts DESC LIMIT 1` btree scan
--     returns the smallest-ctid row among equal-ts duplicates (btree stores
--     equal keys in TID order), so edges are deduplicated with
--     DISTINCT ON (equipment, ts) ... ORDER BY equipment, ts, ctid — the
--     same row a conflicting same-timestamp pair resolves to today.
--     Per-sample duty averages are avg(int) -> numeric, i.e. exact rational
--     arithmetic — no float-order sensitivity.
--
-- The v_climate_action_effectiveness_5m/15m views, the daily-scorecard view,
-- and fn_climate_action_effectiveness(interval) are UNTOUCHED — the view
-- stays the whole-window ad-hoc surface; this function is the single-day
-- read path (outcome_kpi). Equivalence is pinned by
-- db/migrations/tests/test-202-climate-action-scorecard-date-pushdown.sql
-- (fn == view row-for-row on seeded fixtures, including a conflicting
-- same-timestamp equipment_state pair, a NULL-greenhouse action row, a
-- missing-after-sample row, local-midnight boundary rows, the 14-day cutoff,
-- and an empty day) and by the PR's prod golden capture.
--
-- Non-self-transactional: CREATE OR REPLACE FUNCTION + COMMENT only — no
-- top-level COMMIT, no commit-forcing statements. Safe for an outer
-- BEGIN..ROLLBACK proof. Functional rollback: DROP FUNCTION
-- public.fn_climate_action_daily_scorecard(date) and point consumers back at
-- v_climate_action_daily_scorecard.

CREATE OR REPLACE FUNCTION public.fn_climate_action_daily_scorecard(target_date date)
RETURNS TABLE (
    date date,
    greenhouse_id text,
    climate_action text,
    decisions bigint,
    avg_abs_temp_error_before_f numeric,
    avg_abs_vpd_error_before_kpa numeric,
    avg_temp_abs_error_delta_15m_f numeric,
    avg_vpd_abs_error_delta_15m_kpa numeric,
    avg_wet_relay_duty_pct numeric,
    avg_vent_fan_duty_pct numeric,
    mister_water_delta_gal numeric,
    wet_blocked_decisions bigint,
    fog_blocked_decisions bigint
)
LANGUAGE sql
STABLE
AS $$
WITH bounds AS (
    SELECT
        GREATEST(
            (target_date::timestamp AT TIME ZONE 'America/Denver'),
            now() - interval '14 days'
        ) AS start_ts,
        ((target_date + 1)::timestamp AT TIME ZONE 'America/Denver') AS end_ts
),
-- One row per climate_action_log decision on the target day, with the
-- migration-142 c0 (last climate sample at or before the action) and c1
-- (first climate sample in [ts+15m, ts+18m]) lookups inlined verbatim and
-- the after-setpoints resolved with fn_setpoint_at's exact body.
actions AS (
    SELECT
        row_number() OVER () AS rid,
        l.ts,
        l.greenhouse_id,
        l.climate_action,
        l.temp_band_error_f,
        l.vpd_band_error_kpa,
        l.wet_assist_block_reason,
        l.fog_block_reason,
        c0.mister_water_today AS before_mister_water_gal,
        c1.ts AS after_climate_ts,
        c1.temp_avg AS after_temp_f,
        c1.vpd_avg AS after_vpd_kpa,
        c1.mister_water_today AS after_mister_water_gal,
        sp_tl.value AS after_temp_low,
        sp_th.value AS after_temp_high,
        sp_vl.value AS after_vpd_low,
        sp_vh.value AS after_vpd_high
    FROM public.climate_action_log l
    LEFT JOIN LATERAL (
        SELECT c.*
        FROM public.climate c
        WHERE COALESCE(c.greenhouse_id, 'vallery') = COALESCE(l.greenhouse_id, 'vallery')
          AND c.temp_avg IS NOT NULL
          AND c.vpd_avg IS NOT NULL
          AND c.ts <= l.ts
        ORDER BY c.ts DESC
        LIMIT 1
    ) c0 ON true
    LEFT JOIN LATERAL (
        SELECT c.*
        FROM public.climate c
        WHERE COALESCE(c.greenhouse_id, 'vallery') = COALESCE(l.greenhouse_id, 'vallery')
          AND c.temp_avg IS NOT NULL
          AND c.vpd_avg IS NOT NULL
          AND c.ts >= l.ts + interval '15 minutes'
          AND c.ts <= l.ts + interval '15 minutes' + interval '3 minutes'
        ORDER BY c.ts ASC
        LIMIT 1
    ) c1 ON true
    LEFT JOIN LATERAL (
        SELECT sc.value
        FROM public.setpoint_changes sc
        WHERE sc.greenhouse_id = l.greenhouse_id
          AND sc.parameter = 'temp_low'
          AND sc.ts <= COALESCE(c1.ts, l.ts)
          AND (sc.expired_at IS NULL OR sc.expired_at > COALESCE(c1.ts, l.ts))
        ORDER BY sc.ts DESC
        LIMIT 1
    ) sp_tl ON true
    LEFT JOIN LATERAL (
        SELECT sc.value
        FROM public.setpoint_changes sc
        WHERE sc.greenhouse_id = l.greenhouse_id
          AND sc.parameter = 'temp_high'
          AND sc.ts <= COALESCE(c1.ts, l.ts)
          AND (sc.expired_at IS NULL OR sc.expired_at > COALESCE(c1.ts, l.ts))
        ORDER BY sc.ts DESC
        LIMIT 1
    ) sp_th ON true
    LEFT JOIN LATERAL (
        SELECT sc.value
        FROM public.setpoint_changes sc
        WHERE sc.greenhouse_id = l.greenhouse_id
          AND sc.parameter = 'vpd_low'
          AND sc.ts <= COALESCE(c1.ts, l.ts)
          AND (sc.expired_at IS NULL OR sc.expired_at > COALESCE(c1.ts, l.ts))
        ORDER BY sc.ts DESC
        LIMIT 1
    ) sp_vl ON true
    LEFT JOIN LATERAL (
        SELECT sc.value
        FROM public.setpoint_changes sc
        WHERE sc.greenhouse_id = l.greenhouse_id
          AND sc.parameter = 'vpd_high'
          AND sc.ts <= COALESCE(c1.ts, l.ts)
          AND (sc.expired_at IS NULL OR sc.expired_at > COALESCE(c1.ts, l.ts))
        ORDER BY sc.ts DESC
        LIMIT 1
    ) sp_vh ON true
    -- scalar-subquery bounds (InitPlan params) so TimescaleDB excludes
    -- non-target chunks at executor startup; a CROSS JOIN would make these
    -- join quals and merely index-descend every chunk
    WHERE l.ts >= (SELECT start_ts FROM bounds)
      AND l.ts < (SELECT end_ts FROM bounds)
),
-- The seven relay channels the scorecard's duty percentages read
-- (migration 142's wet OR: fog + three misters; vent OR: vent + two fans).
duty_channels AS (
    SELECT unnest(ARRAY[
        'fog', 'mister_south', 'mister_west', 'mister_center',
        'vent', 'fan1', 'fan2'
    ]) AS equipment
),
-- Last-known state per channel at the window start (fn_equip_at's exact
-- body evaluated once per channel instead of once per sample).
-- MATERIALIZED: referenced once, so without it the planner inlines the
-- scalar subquery into the duty join and re-executes it per grp=0
-- sample-channel row (measured 46k executions / 11.5 s on prod for a day
-- where one fan never transitioned).
carry AS MATERIALIZED (
    SELECT
        dc.equipment,
        (
            SELECT es.state
            FROM public.equipment_state es
            WHERE es.equipment = dc.equipment
              AND es.ts <= b.start_ts
            ORDER BY es.ts DESC
            LIMIT 1
        ) AS state
    FROM duty_channels dc
    CROSS JOIN bounds b
),
-- In-window transitions, one row per (equipment, ts). Among equal-ts
-- duplicates fn_equip_at's index scan returns the smallest-ctid row, so the
-- dedup keeps exactly that row.
edges AS (
    SELECT DISTINCT ON (es.equipment, es.ts)
        es.equipment,
        es.ts,
        es.state
    FROM public.equipment_state es
    WHERE es.equipment IN (
            'fog', 'mister_south', 'mister_west', 'mister_center',
            'vent', 'fan1', 'fan2'
          )
      AND es.ts > (SELECT start_ts FROM bounds)
      AND es.ts <= (SELECT end_ts FROM bounds) + interval '15 minutes'
    ORDER BY es.equipment, es.ts, es.ctid
),
-- The migration-142 sample grid: minute instants anchored at each action's
-- own timestamp, inclusive of both ends (16 instants per row).
samples AS (
    SELECT
        a.rid,
        s.sample_ts
    FROM actions a
    CROSS JOIN LATERAL generate_series(
        a.ts, a.ts + interval '15 minutes', interval '1 minute'
    ) AS s(sample_ts)
),
-- One LOCF pass: interleave edges (kind 0) and sample instants (kind 1) per
-- channel; grp counts edges at or before each row (an edge applies at its
-- own instant, matching fn_equip_at's ts <= sample), and edge_state carries
-- the grp-leader edge's state to every sample in the group.
events AS (
    SELECT e.equipment, e.ts AS ts_point, 0 AS kind,
           NULL::bigint AS rid, NULL::timestamptz AS sample_ts, e.state AS state_val
    FROM edges e
    UNION ALL
    SELECT dc.equipment, s.sample_ts, 1,
           s.rid, s.sample_ts, NULL::boolean
    FROM samples s
    CROSS JOIN duty_channels dc
),
resolved AS (
    SELECT
        ev.*,
        count(CASE WHEN ev.kind = 0 THEN 1 END)
            OVER (PARTITION BY ev.equipment ORDER BY ev.ts_point, ev.kind) AS grp
    FROM events ev
),
states AS (
    SELECT
        r.*,
        bool_or(CASE WHEN r.kind = 0 THEN r.state_val END)
            OVER (PARTITION BY r.equipment, r.grp) AS edge_state
    FROM resolved r
),
-- Per sample instant: the wet-relay OR and vent/fan OR of migration 142,
-- with fn_equip_at's NULL -> false coalescing applied per channel.
sample_flags AS (
    SELECT
        st.rid,
        st.sample_ts,
        bool_or(COALESCE(CASE WHEN st.grp > 0 THEN st.edge_state ELSE ci.state END, false))
            FILTER (WHERE st.equipment IN ('fog', 'mister_south', 'mister_west', 'mister_center'))
            AS wet_on,
        bool_or(COALESCE(CASE WHEN st.grp > 0 THEN st.edge_state ELSE ci.state END, false))
            FILTER (WHERE st.equipment IN ('vent', 'fan1', 'fan2'))
            AS vent_on
    FROM states st
    JOIN carry ci ON ci.equipment = st.equipment
    WHERE st.kind = 1
    GROUP BY st.rid, st.sample_ts
),
duty AS (
    SELECT
        sf.rid,
        round((avg(sf.wet_on::int) * 100.0)::numeric, 2)::double precision AS wet_relay_duty_pct,
        round((avg(sf.vent_on::int) * 100.0)::numeric, 2)::double precision AS vent_fan_duty_pct
    FROM sample_flags sf
    GROUP BY sf.rid
),
-- Migration 142's scored/after expressions, verbatim.
enriched AS (
    SELECT
        a.*,
        CASE
            WHEN a.after_temp_f IS NULL OR a.after_temp_low IS NULL OR a.after_temp_high IS NULL THEN NULL
            WHEN a.after_temp_f < a.after_temp_low THEN a.after_temp_f - a.after_temp_low
            WHEN a.after_temp_f > a.after_temp_high THEN a.after_temp_f - a.after_temp_high
            ELSE 0.0
        END AS after_temp_band_error,
        CASE
            WHEN a.after_vpd_kpa IS NULL OR a.after_vpd_low IS NULL OR a.after_vpd_high IS NULL THEN NULL
            WHEN a.after_vpd_kpa < a.after_vpd_low THEN a.after_vpd_kpa - a.after_vpd_low
            WHEN a.after_vpd_kpa > a.after_vpd_high THEN a.after_vpd_kpa - a.after_vpd_high
            ELSE 0.0
        END AS after_vpd_band_error,
        CASE
            WHEN a.before_mister_water_gal IS NULL OR a.after_mister_water_gal IS NULL THEN NULL
            ELSE greatest(0.0, a.after_mister_water_gal - a.before_mister_water_gal)
        END AS mister_water_delta_gal
    FROM actions a
)
SELECT
    (e.ts AT TIME ZONE 'America/Denver')::date AS date,
    e.greenhouse_id,
    e.climate_action,
    count(*) AS decisions,
    round(avg(abs(e.temp_band_error_f))::numeric, 2) AS avg_abs_temp_error_before_f,
    round(avg(abs(e.vpd_band_error_kpa))::numeric, 3) AS avg_abs_vpd_error_before_kpa,
    round(avg(
        CASE
            WHEN e.after_temp_band_error IS NULL OR e.temp_band_error_f IS NULL THEN NULL
            ELSE abs(e.after_temp_band_error) - abs(e.temp_band_error_f)
        END
    )::numeric, 2) AS avg_temp_abs_error_delta_15m_f,
    round(avg(
        CASE
            WHEN e.after_vpd_band_error IS NULL OR e.vpd_band_error_kpa IS NULL THEN NULL
            ELSE abs(e.after_vpd_band_error) - abs(e.vpd_band_error_kpa)
        END
    )::numeric, 3) AS avg_vpd_abs_error_delta_15m_kpa,
    round(avg(d.wet_relay_duty_pct)::numeric, 2) AS avg_wet_relay_duty_pct,
    round(avg(d.vent_fan_duty_pct)::numeric, 2) AS avg_vent_fan_duty_pct,
    round(sum(coalesce(e.mister_water_delta_gal, 0.0))::numeric, 3) AS mister_water_delta_gal,
    count(*) FILTER (WHERE e.wet_assist_block_reason IS NOT NULL) AS wet_blocked_decisions,
    count(*) FILTER (WHERE e.fog_block_reason IS NOT NULL AND e.fog_block_reason <> 'none') AS fog_blocked_decisions
FROM enriched e
JOIN duty d ON d.rid = e.rid
GROUP BY 1, 2, 3;
$$;

COMMENT ON FUNCTION public.fn_climate_action_daily_scorecard(date) IS
    'Single-day climate-action scorecard, byte-identical to v_climate_action_daily_scorecard filtered to the target Denver-local date, with sargable time bounds (chunk pruning) and set-based duty resolution (#498).';
