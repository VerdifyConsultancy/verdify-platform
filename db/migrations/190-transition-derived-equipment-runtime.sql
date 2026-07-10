-- 190-transition-derived-equipment-runtime.sql
--
-- Issues #389/#410: replace per-day partitioned LAG counting with a raw-state
-- transition contract that carries state across local midnight, collapses
-- repeated/duplicate observations, surfaces conflicting timestamps and partial
-- days, and preserves each grow-light circuit independently.  No history is
-- rewritten; this view derives truth from equipment_state at query time.
--
-- The first four columns retain the legacy v_equipment_runtime_daily contract
-- (day, equipment, on_minutes, cycles).  New completeness/quality, short-cycle,
-- and transition-rate columns are additive.  Only rows with
-- is_deploy_gate_eligible=true are suitable for release comparisons.
--
-- Non-self-transactional: CREATE OR REPLACE VIEW only.  Safe for an outer
-- rollback proof.  Functional rollback: restore the migration-126 view body.

CREATE OR REPLACE VIEW public.v_equipment_runtime_daily AS
WITH relevant AS (
    SELECT
        COALESCE(greenhouse_id, 'vallery') AS greenhouse_id,
        equipment,
        ts,
        bool_or(state) AS state,
        count(*)::bigint AS raw_rows,
        count(DISTINCT state) > 1 AS conflicting_state
    FROM public.equipment_state
    WHERE equipment IN (
        'fan1', 'fan2', 'heat1', 'heat2', 'fog', 'vent',
        'mister_south', 'mister_west', 'mister_center',
        'grow_light_main', 'grow_light_grow',
        'drip_wall', 'drip_center',
        'drip_wall_fert', 'drip_center_fert',
        'mister_south_fert', 'mister_west_fert',
        'fert_master_valve'
    )
    GROUP BY COALESCE(greenhouse_id, 'vallery'), equipment, ts
),
ordered AS (
    SELECT
        r.*,
        lag(state) OVER (
            PARTITION BY greenhouse_id, equipment
            ORDER BY ts
        ) AS previous_state
    FROM relevant r
),
transitions AS (
    SELECT *
    FROM ordered
    WHERE previous_state IS DISTINCT FROM state
       OR previous_state IS NULL
),
equipment_bounds AS (
    SELECT
        greenhouse_id,
        equipment,
        min((ts AT TIME ZONE 'America/Denver')::date) AS first_day,
        (now() AT TIME ZONE 'America/Denver')::date AS last_day
    FROM relevant
    GROUP BY greenhouse_id, equipment
),
days AS (
    SELECT
        b.greenhouse_id,
        b.equipment,
        gs::date AS day
    FROM equipment_bounds b
    CROSS JOIN LATERAL generate_series(
        b.first_day::timestamp,
        b.last_day::timestamp,
        interval '1 day'
    ) gs
    WHERE b.first_day <= b.last_day
),
day_bounds AS (
    SELECT
        d.*,
        d.day::timestamp AT TIME ZONE 'America/Denver' AS start_ts,
        (d.day + 1)::timestamp AT TIME ZONE 'America/Denver' AS end_ts,
        LEAST(
            (d.day + 1)::timestamp AT TIME ZONE 'America/Denver',
            now()
        ) AS effective_end_ts,
        d.day < (now() AT TIME ZONE 'America/Denver')::date AS is_complete_day
    FROM days d
),
day_context AS (
    SELECT
        b.*,
        start_event.state AS start_state,
        start_event.ts AS start_state_ts
    FROM day_bounds b
    LEFT JOIN LATERAL (
        SELECT t.state, t.ts
        FROM transitions t
        WHERE t.greenhouse_id = b.greenhouse_id
          AND t.equipment = b.equipment
          AND t.ts < b.start_ts
        ORDER BY t.ts DESC
        LIMIT 1
    ) start_event ON true
),
points AS (
    SELECT
        d.greenhouse_id,
        d.equipment,
        d.day,
        d.start_ts,
        d.end_ts,
        d.effective_end_ts,
        d.is_complete_day,
        d.start_state,
        d.start_state_ts,
        d.start_ts AS ts,
        d.start_state AS state,
        false AS is_event
    FROM day_context d
    WHERE d.start_state IS NOT NULL

    UNION ALL

    SELECT
        d.greenhouse_id,
        d.equipment,
        d.day,
        d.start_ts,
        d.end_ts,
        d.effective_end_ts,
        d.is_complete_day,
        d.start_state,
        d.start_state_ts,
        t.ts,
        t.state,
        true AS is_event
    FROM day_context d
    JOIN transitions t
      ON t.greenhouse_id = d.greenhouse_id
     AND t.equipment = d.equipment
     AND t.ts >= d.start_ts
     AND t.ts < d.effective_end_ts
),
sequenced_points AS (
    SELECT
        p.*,
        lag(state) OVER (
            PARTITION BY greenhouse_id, equipment, day
            ORDER BY ts, is_event
        ) AS prior_state,
        lead(ts, 1, effective_end_ts) OVER (
            PARTITION BY greenhouse_id, equipment, day
            ORDER BY ts, is_event
        ) AS next_ts
    FROM points p
),
segment_metrics AS (
    SELECT
        greenhouse_id,
        equipment,
        day,
        round((
            sum(extract(epoch FROM (next_ts - ts)) / 60.0)
                FILTER (WHERE state IS TRUE AND next_ts > ts)
        )::numeric, 1) AS on_minutes,
        count(*) FILTER (
            WHERE is_event AND state IS TRUE AND prior_state IS FALSE
        )::bigint AS starts,
        count(*) FILTER (
            WHERE is_event AND state IS TRUE AND prior_state IS NULL
        )::bigint AS starts_with_unknown_prior_state,
        count(*) FILTER (
            WHERE is_event AND state IS TRUE AND prior_state IS FALSE
              AND extract(epoch FROM (next_ts - ts)) < 60
        )::bigint AS cycles_under_1m,
        count(*) FILTER (
            WHERE is_event AND state IS TRUE AND prior_state IS FALSE
              AND extract(epoch FROM (next_ts - ts)) >= 60
              AND extract(epoch FROM (next_ts - ts)) < 300
        )::bigint AS cycles_1m_to_5m,
        count(*) FILTER (
            WHERE is_event AND state IS TRUE AND prior_state IS FALSE
              AND extract(epoch FROM (next_ts - ts)) >= 300
              AND extract(epoch FROM (next_ts - ts)) < 900
        )::bigint AS cycles_5m_to_15m,
        count(*) FILTER (
            WHERE is_event AND state IS TRUE AND prior_state IS FALSE
              AND extract(epoch FROM (next_ts - ts)) >= 900
        )::bigint AS cycles_15m_plus,
        count(*) FILTER (
            WHERE is_event AND state IS TRUE AND next_ts = effective_end_ts
        )::bigint AS open_pulses_at_cutoff,
        (array_agg(state ORDER BY ts DESC, is_event DESC))[1] AS end_state
    FROM sequenced_points
    GROUP BY greenhouse_id, equipment, day
),
day_event_quality AS (
    SELECT
        d.greenhouse_id,
        d.equipment,
        d.day,
        COALESCE(sum(o.raw_rows), 0)::bigint AS raw_event_rows,
        COALESCE(sum(o.raw_rows - 1), 0)::bigint AS same_timestamp_duplicate_rows,
        count(*) FILTER (WHERE o.previous_state IS NOT DISTINCT FROM o.state)::bigint
            AS redundant_state_rows,
        count(*) FILTER (WHERE o.conflicting_state)::bigint
            AS conflicting_timestamp_count,
        count(*) FILTER (
            WHERE o.previous_state IS NOT NULL
              AND o.previous_state IS DISTINCT FROM o.state
        )::bigint AS normalized_transition_count
    FROM day_context d
    LEFT JOIN ordered o
      ON o.greenhouse_id = d.greenhouse_id
     AND o.equipment = d.equipment
     AND o.ts >= d.start_ts
     AND o.ts < d.effective_end_ts
    GROUP BY d.greenhouse_id, d.equipment, d.day
),
hourly_transitions AS (
    SELECT
        d.greenhouse_id,
        d.equipment,
        d.day,
        date_trunc('hour', o.ts) AS hour_bucket,
        count(*)::int AS transitions_in_hour
    FROM day_context d
    JOIN ordered o
      ON o.greenhouse_id = d.greenhouse_id
     AND o.equipment = d.equipment
     AND o.ts >= d.start_ts
     AND o.ts < d.effective_end_ts
     AND o.previous_state IS NOT NULL
     AND o.previous_state IS DISTINCT FROM o.state
    GROUP BY d.greenhouse_id, d.equipment, d.day, date_trunc('hour', o.ts)
),
peak_transitions AS (
    SELECT greenhouse_id, equipment, day,
           max(transitions_in_hour)::int AS peak_transitions_per_hour
    FROM hourly_transitions
    GROUP BY greenhouse_id, equipment, day
)
SELECT
    d.day,
    d.equipment,
    COALESCE(s.on_minutes, 0.0::numeric) AS on_minutes,
    COALESCE(s.starts, 0)::bigint AS cycles,
    d.greenhouse_id,
    d.start_ts AS day_started_at,
    d.effective_end_ts AS observed_through,
    d.is_complete_day,
    d.start_state IS NOT NULL AS start_state_known,
    d.start_state,
    s.end_state,
    COALESCE(s.end_state, d.start_state, false) AS open_at_end,
    d.is_complete_day
        AND d.start_state IS NOT NULL
        AND COALESCE(q.conflicting_timestamp_count, 0) = 0
        AS is_deploy_gate_eligible,
    CASE
        WHEN NOT d.is_complete_day THEN 'partial_day'
        WHEN d.start_state IS NULL THEN 'unknown_start_state'
        WHEN COALESCE(q.conflicting_timestamp_count, 0) > 0 THEN 'conflicting_events'
        ELSE 'complete'
    END AS quality,
    array_remove(ARRAY[
        CASE WHEN NOT d.is_complete_day THEN 'partial_day' END,
        CASE WHEN d.start_state IS NULL THEN 'unknown_start_state' END,
        CASE WHEN COALESCE(q.conflicting_timestamp_count, 0) > 0
            THEN 'conflicting_same_timestamp' END,
        CASE WHEN COALESCE(s.open_pulses_at_cutoff, 0) > 0
            THEN 'open_pulse_at_cutoff' END,
        CASE WHEN COALESCE(q.same_timestamp_duplicate_rows, 0) > 0
                  OR COALESCE(q.redundant_state_rows, 0) > 0
            THEN 'duplicates_collapsed' END
    ]::text[], NULL) AS quality_flags,
    COALESCE(s.starts, 0)::bigint AS starts,
    COALESCE(s.cycles_under_1m, 0)::bigint AS cycles_under_1m,
    COALESCE(s.cycles_1m_to_5m, 0)::bigint AS cycles_1m_to_5m,
    COALESCE(s.cycles_under_1m, 0)::bigint
        + COALESCE(s.cycles_1m_to_5m, 0)::bigint AS short_cycles_under_5m,
    COALESCE(s.cycles_5m_to_15m, 0)::bigint AS cycles_5m_to_15m,
    COALESCE(s.cycles_15m_plus, 0)::bigint AS cycles_15m_plus,
    COALESCE(s.open_pulses_at_cutoff, 0)::bigint AS open_pulses_at_cutoff,
    COALESCE(p.peak_transitions_per_hour, 0)::int AS peak_transitions_per_hour,
    COALESCE(q.raw_event_rows, 0)::bigint AS raw_event_rows,
    COALESCE(q.normalized_transition_count, 0)::bigint AS normalized_transition_count,
    COALESCE(q.same_timestamp_duplicate_rows, 0)::bigint
        AS same_timestamp_duplicate_rows,
    COALESCE(q.redundant_state_rows, 0)::bigint AS redundant_state_rows,
    COALESCE(q.conflicting_timestamp_count, 0)::bigint
        AS conflicting_timestamp_count,
    COALESCE(s.starts_with_unknown_prior_state, 0)::bigint
        AS starts_with_unknown_prior_state
FROM day_context d
LEFT JOIN segment_metrics s USING (greenhouse_id, equipment, day)
LEFT JOIN day_event_quality q USING (greenhouse_id, equipment, day)
LEFT JOIN peak_transitions p USING (greenhouse_id, equipment, day);

COMMENT ON VIEW public.v_equipment_runtime_daily IS
'Transition-derived per-equipment local-day truth from raw equipment_state. '
'Carries state across midnight; collapses repeated and same-timestamp duplicate '
'observations; preserves per-light circuits; exposes runtime, starts, short-cycle '
'buckets, open pulses, peak transitions/hour, completeness, and quality. Use only '
'is_deploy_gate_eligible rows for release comparisons; current/future days are '
'never eligible. Firmware daily counters remain diagnostics.';
