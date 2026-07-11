-- 199-bounded-equipment-runtime-view.sql
--
-- 2026-07-11 audit follow-through: daily_summary_live timed out on 100% of
-- runs even at a 600 s budget. Live pg_stat_activity sampling caught the
-- eater: v_equipment_runtime_daily at 200+ s for a single day. The view
-- cross-joins EVERY tracked equipment with EVERY day since first data
-- (2024-11-17), then runs a LATERAL transition lookup per day-row —
-- O(days x transitions), slower every day forever. Bound the generated day
-- series to a rolling 45-day window (seeding lookups still reach further
-- back, so day-start states stay correct for dormant relays). The
-- firmware-deploy preflight and daily refresh only read recent days;
-- long-range history lives in daily_summary / daily_zone_compliance.
--
-- Depends on the migration-15x runtime-view lineage. Non-self-transactional:
-- CREATE OR REPLACE VIEW only (same column list). Safe for an outer rollback
-- proof. Functional rollback: restore the prior unbounded equipment_bounds.

CREATE OR REPLACE VIEW public.v_equipment_runtime_daily AS
WITH relevant AS (
         SELECT COALESCE(equipment_state.greenhouse_id, 'vallery'::text) AS greenhouse_id,
            equipment_state.equipment,
            equipment_state.ts,
            bool_or(equipment_state.state) AS state,
            count(*) AS raw_rows,
            count(DISTINCT equipment_state.state) > 1 AS conflicting_state
           FROM equipment_state
          WHERE equipment_state.equipment = ANY (ARRAY['fan1'::text, 'fan2'::text, 'heat1'::text, 'heat2'::text, 'fog'::text, 'vent'::text, 'mister_south'::text, 'mister_west'::text, 'mister_center'::text, 'grow_light_main'::text, 'grow_light_grow'::text, 'drip_wall'::text, 'drip_center'::text, 'drip_wall_fert'::text, 'drip_center_fert'::text, 'mister_south_fert'::text, 'mister_west_fert'::text, 'fert_master_valve'::text])
          GROUP BY (COALESCE(equipment_state.greenhouse_id, 'vallery'::text)), equipment_state.equipment, equipment_state.ts
        ), ordered AS (
         SELECT r.greenhouse_id,
            r.equipment,
            r.ts,
            r.state,
            r.raw_rows,
            r.conflicting_state,
            lag(r.state) OVER (PARTITION BY r.greenhouse_id, r.equipment ORDER BY r.ts) AS previous_state,
            lag(r.conflicting_state) OVER (PARTITION BY r.greenhouse_id, r.equipment ORDER BY r.ts) AS previous_conflicting_state
           FROM relevant r
        ), transition_events AS (
         SELECT ordered.greenhouse_id,
            ordered.equipment,
            ordered.ts,
            ordered.state,
            ordered.raw_rows,
            ordered.conflicting_state,
            ordered.previous_state,
            ordered.previous_conflicting_state
           FROM ordered
          WHERE ordered.previous_state IS DISTINCT FROM ordered.state OR ordered.previous_state IS NULL OR ordered.conflicting_state OR COALESCE(ordered.previous_conflicting_state, false)
        ), transitions AS (
         SELECT e.greenhouse_id,
            e.equipment,
            e.ts,
            e.state,
            e.raw_rows,
            e.conflicting_state,
            e.previous_state,
            e.previous_conflicting_state,
            lead(e.ts) OVER (PARTITION BY e.greenhouse_id, e.equipment ORDER BY e.ts) AS next_transition_ts
           FROM transition_events e
        ), equipment_bounds AS (
         SELECT relevant.greenhouse_id,
            relevant.equipment,
            -- Migration 199: bound the materialized day series to a rolling
            -- 45-day operational window. The old unbounded series (every
            -- equipment x every day since first data, ~600+ days) drove an
            -- O(days x transitions) LATERAL blowup: 200+ s per query by
            -- 2026-07-11, eating the daily_summary_live budget and growing
            -- forever. Transition seeding still looks arbitrarily far back,
            -- so day-start states for rarely-switched relays stay correct.
            -- History older than 45 days lives in daily_summary /
            -- daily_zone_compliance, not this operational view.
            GREATEST(min((relevant.ts AT TIME ZONE 'America/Denver'::text)::date),
                     ((now() AT TIME ZONE 'America/Denver'::text)::date - 45)) AS first_day,
            (now() AT TIME ZONE 'America/Denver'::text)::date AS last_day
           FROM relevant
          GROUP BY relevant.greenhouse_id, relevant.equipment
        ), days AS (
         SELECT b.greenhouse_id,
            b.equipment,
            gs.gs::date AS day
           FROM equipment_bounds b
             CROSS JOIN LATERAL generate_series(b.first_day::timestamp without time zone, b.last_day::timestamp without time zone, '1 day'::interval) gs(gs)
          WHERE b.first_day <= b.last_day
        ), day_bounds AS (
         SELECT d_1.greenhouse_id,
            d_1.equipment,
            d_1.day,
            (d_1.day::timestamp without time zone AT TIME ZONE 'America/Denver'::text) AS start_ts,
            ((d_1.day + 1)::timestamp without time zone AT TIME ZONE 'America/Denver'::text) AS end_ts,
            LEAST(((d_1.day + 1)::timestamp without time zone AT TIME ZONE 'America/Denver'::text), now()) AS effective_end_ts,
            d_1.day < (now() AT TIME ZONE 'America/Denver'::text)::date AS is_complete_day
           FROM days d_1
        ), day_context AS (
         SELECT b.greenhouse_id,
            b.equipment,
            b.day,
            b.start_ts,
            b.end_ts,
            b.effective_end_ts,
            b.is_complete_day,
            start_event.state AS start_state,
            start_event.ts AS start_state_ts,
            start_event.conflicting_state AS start_state_conflicting
           FROM day_bounds b
             LEFT JOIN LATERAL ( SELECT t.state,
                    t.ts,
                    t.conflicting_state
                   FROM transitions t
                  WHERE t.greenhouse_id = b.greenhouse_id AND t.equipment = b.equipment AND t.ts < b.start_ts
                  ORDER BY t.ts DESC
                 LIMIT 1) start_event ON true
        ), points AS (
         SELECT d_1.greenhouse_id,
            d_1.equipment,
            d_1.day,
            d_1.start_ts,
            d_1.end_ts,
            d_1.effective_end_ts,
            d_1.is_complete_day,
            d_1.start_state,
            d_1.start_state_ts,
            d_1.start_state_conflicting,
            NULL::timestamp with time zone AS next_transition_ts,
            d_1.start_ts AS ts,
            d_1.start_state AS state,
            false AS is_event
           FROM day_context d_1
          WHERE d_1.start_state IS NOT NULL
        UNION ALL
         SELECT d_1.greenhouse_id,
            d_1.equipment,
            d_1.day,
            d_1.start_ts,
            d_1.end_ts,
            d_1.effective_end_ts,
            d_1.is_complete_day,
            d_1.start_state,
            d_1.start_state_ts,
            d_1.start_state_conflicting,
            t.next_transition_ts,
            t.ts,
            t.state,
            true AS is_event
           FROM day_context d_1
             JOIN transitions t ON t.greenhouse_id = d_1.greenhouse_id AND t.equipment = d_1.equipment AND t.ts >= d_1.start_ts AND t.ts < d_1.effective_end_ts
        ), sequenced_points AS (
         SELECT p_1.greenhouse_id,
            p_1.equipment,
            p_1.day,
            p_1.start_ts,
            p_1.end_ts,
            p_1.effective_end_ts,
            p_1.is_complete_day,
            p_1.start_state,
            p_1.start_state_ts,
            p_1.start_state_conflicting,
            p_1.next_transition_ts,
            p_1.ts,
            p_1.state,
            p_1.is_event,
            lag(p_1.state) OVER (PARTITION BY p_1.greenhouse_id, p_1.equipment, p_1.day ORDER BY p_1.ts, p_1.is_event) AS prior_state,
            lead(p_1.ts, 1, p_1.effective_end_ts) OVER (PARTITION BY p_1.greenhouse_id, p_1.equipment, p_1.day ORDER BY p_1.ts, p_1.is_event) AS next_ts
           FROM points p_1
        ), segment_metrics AS (
         SELECT sequenced_points.greenhouse_id,
            sequenced_points.equipment,
            sequenced_points.day,
            round(sum(EXTRACT(epoch FROM sequenced_points.next_ts - sequenced_points.ts) / 60.0) FILTER (WHERE sequenced_points.state IS TRUE AND sequenced_points.next_ts > sequenced_points.ts), 1) AS on_minutes,
            count(*) FILTER (WHERE sequenced_points.is_event AND sequenced_points.state IS TRUE AND sequenced_points.prior_state IS FALSE) AS starts,
            count(*) FILTER (WHERE sequenced_points.is_event AND sequenced_points.state IS TRUE AND sequenced_points.prior_state IS NULL) AS starts_with_unknown_prior_state,
            count(*) FILTER (WHERE sequenced_points.is_event AND sequenced_points.state IS TRUE AND sequenced_points.prior_state IS FALSE AND EXTRACT(epoch FROM LEAST(COALESCE(sequenced_points.next_transition_ts, now()), now()) - sequenced_points.ts) < 60::numeric) AS cycles_under_1m,
            count(*) FILTER (WHERE sequenced_points.is_event AND sequenced_points.state IS TRUE AND sequenced_points.prior_state IS FALSE AND EXTRACT(epoch FROM LEAST(COALESCE(sequenced_points.next_transition_ts, now()), now()) - sequenced_points.ts) >= 60::numeric AND EXTRACT(epoch FROM LEAST(COALESCE(sequenced_points.next_transition_ts, now()), now()) - sequenced_points.ts) < 300::numeric) AS cycles_1m_to_5m,
            count(*) FILTER (WHERE sequenced_points.is_event AND sequenced_points.state IS TRUE AND sequenced_points.prior_state IS FALSE AND EXTRACT(epoch FROM LEAST(COALESCE(sequenced_points.next_transition_ts, now()), now()) - sequenced_points.ts) >= 300::numeric AND EXTRACT(epoch FROM LEAST(COALESCE(sequenced_points.next_transition_ts, now()), now()) - sequenced_points.ts) < 900::numeric) AS cycles_5m_to_15m,
            count(*) FILTER (WHERE sequenced_points.is_event AND sequenced_points.state IS TRUE AND sequenced_points.prior_state IS FALSE AND EXTRACT(epoch FROM LEAST(COALESCE(sequenced_points.next_transition_ts, now()), now()) - sequenced_points.ts) >= 900::numeric) AS cycles_15m_plus,
            count(*) FILTER (WHERE sequenced_points.is_event AND sequenced_points.state IS TRUE AND (sequenced_points.next_transition_ts IS NULL OR sequenced_points.next_transition_ts > sequenced_points.effective_end_ts)) AS open_pulses_at_cutoff,
            (array_agg(sequenced_points.state ORDER BY sequenced_points.ts DESC, sequenced_points.is_event DESC))[1] AS end_state
           FROM sequenced_points
          GROUP BY sequenced_points.greenhouse_id, sequenced_points.equipment, sequenced_points.day
        ), day_event_quality AS (
         SELECT d_1.greenhouse_id,
            d_1.equipment,
            d_1.day,
            COALESCE(sum(o.raw_rows), 0::numeric)::bigint AS raw_event_rows,
            COALESCE(sum(o.raw_rows - 1), 0::numeric)::bigint AS same_timestamp_duplicate_rows,
            count(*) FILTER (WHERE NOT o.previous_state IS DISTINCT FROM o.state) AS redundant_state_rows,
            count(*) FILTER (WHERE o.conflicting_state) AS conflicting_timestamp_count,
            count(*) FILTER (WHERE o.previous_state IS NOT NULL AND o.previous_state IS DISTINCT FROM o.state) AS normalized_transition_count
           FROM day_context d_1
             LEFT JOIN ordered o ON o.greenhouse_id = d_1.greenhouse_id AND o.equipment = d_1.equipment AND o.ts >= d_1.start_ts AND o.ts < d_1.effective_end_ts
          GROUP BY d_1.greenhouse_id, d_1.equipment, d_1.day
        ), hourly_transitions AS (
         SELECT d_1.greenhouse_id,
            d_1.equipment,
            d_1.day,
            date_trunc('hour'::text, o.ts) AS hour_bucket,
            count(*)::integer AS transitions_in_hour
           FROM day_context d_1
             JOIN ordered o ON o.greenhouse_id = d_1.greenhouse_id AND o.equipment = d_1.equipment AND o.ts >= d_1.start_ts AND o.ts < d_1.effective_end_ts AND o.previous_state IS NOT NULL AND o.previous_state IS DISTINCT FROM o.state
          GROUP BY d_1.greenhouse_id, d_1.equipment, d_1.day, (date_trunc('hour'::text, o.ts))
        ), peak_transitions AS (
         SELECT hourly_transitions.greenhouse_id,
            hourly_transitions.equipment,
            hourly_transitions.day,
            max(hourly_transitions.transitions_in_hour) AS peak_transitions_per_hour
           FROM hourly_transitions
          GROUP BY hourly_transitions.greenhouse_id, hourly_transitions.equipment, hourly_transitions.day
        )
 SELECT d.day,
    d.equipment,
    COALESCE(s.on_minutes, 0.0) AS on_minutes,
    COALESCE(s.starts, 0::bigint) AS cycles,
    d.greenhouse_id,
    d.start_ts AS day_started_at,
    d.effective_end_ts AS observed_through,
    d.is_complete_day,
    d.start_state IS NOT NULL AND NOT COALESCE(d.start_state_conflicting, false) AS start_state_known,
    d.start_state,
    s.end_state,
    COALESCE(s.end_state, d.start_state, false) AS open_at_end,
    d.is_complete_day AND d.start_state IS NOT NULL AND NOT COALESCE(d.start_state_conflicting, false) AND COALESCE(q.conflicting_timestamp_count, 0::bigint) = 0 AS is_deploy_gate_eligible,
        CASE
            WHEN NOT d.is_complete_day THEN 'partial_day'::text
            WHEN d.start_state IS NULL THEN 'unknown_start_state'::text
            WHEN COALESCE(d.start_state_conflicting, false) THEN 'conflicting_carry_state'::text
            WHEN COALESCE(q.conflicting_timestamp_count, 0::bigint) > 0 THEN 'conflicting_events'::text
            ELSE 'complete'::text
        END AS quality,
    array_remove(ARRAY[
        CASE
            WHEN NOT d.is_complete_day THEN 'partial_day'::text
            ELSE NULL::text
        END,
        CASE
            WHEN d.start_state IS NULL THEN 'unknown_start_state'::text
            ELSE NULL::text
        END,
        CASE
            WHEN COALESCE(d.start_state_conflicting, false) THEN 'conflicting_carry_state'::text
            ELSE NULL::text
        END,
        CASE
            WHEN COALESCE(q.conflicting_timestamp_count, 0::bigint) > 0 THEN 'conflicting_same_timestamp'::text
            ELSE NULL::text
        END,
        CASE
            WHEN COALESCE(s.open_pulses_at_cutoff, 0::bigint) > 0 THEN 'open_pulse_at_cutoff'::text
            ELSE NULL::text
        END,
        CASE
            WHEN COALESCE(q.same_timestamp_duplicate_rows, 0::bigint) > 0 OR COALESCE(q.redundant_state_rows, 0::bigint) > 0 THEN 'duplicates_collapsed'::text
            ELSE NULL::text
        END], NULL::text) AS quality_flags,
    COALESCE(s.starts, 0::bigint) AS starts,
    COALESCE(s.cycles_under_1m, 0::bigint) AS cycles_under_1m,
    COALESCE(s.cycles_1m_to_5m, 0::bigint) AS cycles_1m_to_5m,
    COALESCE(s.cycles_under_1m, 0::bigint) + COALESCE(s.cycles_1m_to_5m, 0::bigint) AS short_cycles_under_5m,
    COALESCE(s.cycles_5m_to_15m, 0::bigint) AS cycles_5m_to_15m,
    COALESCE(s.cycles_15m_plus, 0::bigint) AS cycles_15m_plus,
    COALESCE(s.open_pulses_at_cutoff, 0::bigint) AS open_pulses_at_cutoff,
    COALESCE(p.peak_transitions_per_hour, 0) AS peak_transitions_per_hour,
    COALESCE(q.raw_event_rows, 0::bigint) AS raw_event_rows,
    COALESCE(q.normalized_transition_count, 0::bigint) AS normalized_transition_count,
    COALESCE(q.same_timestamp_duplicate_rows, 0::bigint) AS same_timestamp_duplicate_rows,
    COALESCE(q.redundant_state_rows, 0::bigint) AS redundant_state_rows,
    COALESCE(q.conflicting_timestamp_count, 0::bigint) AS conflicting_timestamp_count,
    COALESCE(s.starts_with_unknown_prior_state, 0::bigint) AS starts_with_unknown_prior_state
   FROM day_context d
     LEFT JOIN segment_metrics s USING (greenhouse_id, equipment, day)
     LEFT JOIN day_event_quality q USING (greenhouse_id, equipment, day)
     LEFT JOIN peak_transitions p USING (greenhouse_id, equipment, day);
