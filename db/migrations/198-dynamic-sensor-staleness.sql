-- 198-dynamic-sensor-staleness.sql
--
-- 2026-07-11 audit (alert-hygiene finding): v_sensor_staleness resolved
-- climate-sourced sensors through a HARDCODED source_column CASE map. Sixteen
-- sensor_registry rows added after the map was written (solar_phase,
-- solar_*_min, house_*_target/delta, vpd_target_*, vpd_delta_*) fell through
-- to ELSE NULL — so the view reported them permanently stale (ratio NULL)
-- even though the columns carry live data every minute. The alert monitor
-- therefore kept 16 sensor_offline warnings open since 2026-06-11: the keys
-- were re-asserted as stale every cycle, so the auto-resolver never fired.
--
-- Fix: resolve the climate column DYNAMICALLY via to_jsonb(row) ->> column,
-- eliminating the drift class instead of extending the map. Cost: to_jsonb
-- over the freshness window (minutes of rows) per registered climate sensor,
-- on the 300 s monitor cadence — negligible. A registry row whose column
-- genuinely does not exist still reads stale (ratio NULL), which is the
-- correct drift signal.
--
-- Depends on the migration-101/102 view lineage. Non-self-transactional:
-- CREATE OR REPLACE VIEW only. Safe for an outer rollback proof.
-- Functional rollback: restore the prior hardcoded-CASE body.

CREATE OR REPLACE VIEW public.v_sensor_staleness AS
WITH last_readings AS (
    SELECT
        sr.sensor_id,
        sr.type,
        sr.zone,
        sr.expected_interval_s,
        sr.source_table,
        sr.source_column,
        CASE sr.source_table
            WHEN 'climate' THEN (
                SELECT max(c.ts)
                  FROM public.climate c
                 WHERE c.ts > (now() - GREATEST('02:00:00'::interval,
                                                sr.expected_interval_s::double precision * '00:00:02'::interval))
                   AND (to_jsonb(c) ->> sr.source_column) IS NOT NULL
            )
            WHEN 'equipment_state' THEN (
                SELECT max(es.ts)
                  FROM public.equipment_state es
                 WHERE es.equipment = sr.source_column
            )
            WHEN 'system_state' THEN (
                SELECT max(ss.ts)
                  FROM public.system_state ss
                 WHERE ss.entity = sr.source_column
            )
            WHEN 'diagnostics' THEN (
                SELECT max(d.ts)
                  FROM public.diagnostics d
                 WHERE d.ts > (now() - '02:00:00'::interval)
            )
            ELSE NULL::timestamptz
        END AS last_seen_at
    FROM public.sensor_registry sr
    WHERE sr.active = true
)
SELECT
    sensor_id,
    type,
    zone,
    expected_interval_s,
    last_seen_at,
    EXTRACT(epoch FROM now() - last_seen_at)::integer AS seconds_since,
    CASE
        WHEN last_seen_at IS NULL THEN true
        WHEN EXTRACT(epoch FROM now() - last_seen_at) > (expected_interval_s * 2)::numeric THEN true
        ELSE false
    END AS is_stale,
    CASE
        WHEN last_seen_at IS NULL THEN NULL::numeric
        ELSE round(EXTRACT(epoch FROM now() - last_seen_at) / NULLIF(expected_interval_s, 0)::numeric, 1)
    END AS staleness_ratio
FROM last_readings;

COMMENT ON VIEW public.v_sensor_staleness IS
'Per-registered-sensor freshness (migration 198). climate-sourced sensors '
'resolve their column dynamically via to_jsonb ->> source_column so new '
'registry rows can never fall through a hardcoded map into permanent '
'phantom staleness (the 2026-06-11 sixteen-zombie sensor_offline cluster).';
