-- Migration 185: per-circuit lighting crop policy (#294).
--
-- Before: fn_lighting_circuit_policy CROSS JOINed BOTH circuits onto the single
-- house-wide base (fn_lighting_policy's max-DLI crop), so main+grow were byte-
-- identical unless an operator/planner override row existed. After: each circuit
-- carries its own crop BASE:
--   MAIN = orchid    — 12h photoperiod, 06:00–18:00, DLI ~12 mol. Vanda/Phalaenopsis
--                      are moderate-light and flower on intensity/DLI, not daylength;
--                      a fixed 12h window with a true dark period is the safe choice.
--   GROW = jalapeno  — 16h photoperiod, 06:00–22:00, DLI ~22 mol. Day-neutral pepper;
--                      growth is DLI-driven, so a long hydroponic day.
-- The gl_<key>_* per-circuit overrides and the legacy gl_* overrides are PRESERVED
-- on top of the new base, so operator/planner tuning still wins. fn_lighting_policy
-- is no longer needed by this function (it only supplied the three base values).
--
-- COUPLING NOTE (#294): the dispatcher's biological-activity window (which gates all
-- direct-wet irrigation) was derived from the MAIN circuit. Shortening MAIN to a 12h
-- orchid day would shrink irrigation — so the dispatcher is decoupled in lockstep
-- (ingestor/tasks/dispatcher.py: activity window now follows the GROW/jalapeno day).
--
-- Self-contained CREATE OR REPLACE (no top-level COMMIT; safe to rollback-validate
-- wrapped). Mirror in db/schema.sql (drift guard).

CREATE OR REPLACE FUNCTION public.fn_lighting_circuit_policy(p_ts timestamp with time zone DEFAULT now(), p_greenhouse_id text DEFAULT 'vallery'::text) RETURNS TABLE(greenhouse_id text, ts timestamp with time zone, light_key text, equipment text, dli_target double precision, start_hour integer, cutoff_hour integer, lux_on_threshold double precision, lux_hysteresis double precision, lux_off_threshold double precision, min_on_s integer, min_off_s integer, auto_enabled boolean, source_chain text, controller_contract text)
    LANGUAGE sql STABLE
    AS $$
WITH recommendation AS (
    SELECT * FROM fn_lighting_lux_threshold_recommendation(p_ts, p_greenhouse_id)
),
circuits AS (
    -- light_key, equipment, crop_type, base_dli (mol), base_start_hour, base_cutoff_hour
    SELECT *
    FROM (VALUES
        ('main'::text, 'grow_light_main'::text, 'orchid'::text,   12.0::double precision, 6, 18),
        ('grow'::text, 'grow_light_grow'::text, 'jalapeno'::text, 22.0::double precision, 6, 22)
    ) AS v(light_key, equipment, crop_type, base_dli, base_start, base_cutoff)
),
latest_changes AS (
    SELECT DISTINCT ON (parameter)
        parameter,
        value::double precision AS value
    FROM setpoint_changes
    WHERE COALESCE(greenhouse_id, p_greenhouse_id) = p_greenhouse_id
      AND COALESCE(source, '') <> 'esp32'
    ORDER BY parameter, ts DESC
),
resolved AS (
    SELECT
        c.light_key,
        c.equipment,
        COALESCE(dli.value, legacy_dli.value, c.base_dli)::double precision AS dli_target,
        COALESCE(start_h.value, legacy_start.value, c.base_start)::integer AS start_hour,
        COALESCE(cutoff_h.value, legacy_cutoff.value, c.base_cutoff)::integer AS cutoff_hour,
        COALESCE(
            lux_on.value,
            legacy_lux.value,
            r.current_gl_lux_threshold,
            r.recommended_gl_lux_threshold,
            40000.0
        )::double precision AS lux_on_threshold,
        COALESCE(
            lux_hyst.value,
            legacy_hyst.value,
            r.current_gl_lux_hysteresis,
            r.recommended_gl_lux_hysteresis,
            8000.0
        )::double precision AS lux_hysteresis,
        COALESCE(min_on.value, 120.0)::integer AS min_on_s,
        COALESCE(min_off.value, 60.0)::integer AS min_off_s,
        COALESCE(auto_mode.value, legacy_auto.value, 1.0) >= 0.5 AS auto_enabled
    FROM circuits c
    CROSS JOIN recommendation r
    LEFT JOIN latest_changes legacy_dli ON legacy_dli.parameter = 'gl_dli_target'
    LEFT JOIN latest_changes legacy_start ON legacy_start.parameter = 'gl_sunrise_hour'
    LEFT JOIN latest_changes legacy_cutoff ON legacy_cutoff.parameter = 'gl_sunset_hour'
    LEFT JOIN latest_changes legacy_lux ON legacy_lux.parameter = 'gl_lux_threshold'
    LEFT JOIN latest_changes legacy_hyst ON legacy_hyst.parameter = 'gl_lux_hysteresis'
    LEFT JOIN latest_changes legacy_auto ON legacy_auto.parameter = 'sw_gl_auto_mode'
    LEFT JOIN latest_changes dli ON dli.parameter = 'gl_' || c.light_key || '_dli_target'
    LEFT JOIN latest_changes start_h ON start_h.parameter = 'gl_' || c.light_key || '_sunrise_hour'
    LEFT JOIN latest_changes cutoff_h ON cutoff_h.parameter = 'gl_' || c.light_key || '_sunset_hour'
    LEFT JOIN latest_changes lux_on ON lux_on.parameter = 'gl_' || c.light_key || '_lux_threshold'
    LEFT JOIN latest_changes lux_hyst ON lux_hyst.parameter = 'gl_' || c.light_key || '_lux_hysteresis'
    LEFT JOIN latest_changes min_on ON min_on.parameter = 'gl_' || c.light_key || '_min_on_s'
    LEFT JOIN latest_changes min_off ON min_off.parameter = 'gl_' || c.light_key || '_min_off_s'
    LEFT JOIN latest_changes auto_mode ON auto_mode.parameter = 'sw_gl_' || c.light_key || '_auto_mode'
)
SELECT
    p_greenhouse_id AS greenhouse_id,
    p_ts AS ts,
    r.light_key,
    r.equipment,
    greatest(1.0, least(50.0, r.dli_target)) AS dli_target,
    greatest(0, least(23, r.start_hour)) AS start_hour,
    greatest(0, least(23, r.cutoff_hour)) AS cutoff_hour,
    greatest(100.0, least(100000.0, r.lux_on_threshold)) AS lux_on_threshold,
    greatest(0.0, least(25000.0, r.lux_hysteresis)) AS lux_hysteresis,
    greatest(100.0, least(100000.0, r.lux_on_threshold))
        + greatest(0.0, least(25000.0, r.lux_hysteresis)) AS lux_off_threshold,
    greatest(0, least(3600, r.min_on_s)) AS min_on_s,
    greatest(0, least(3600, r.min_off_s)) AS min_off_s,
    r.auto_enabled,
    'per-circuit crop base (main=orchid 12h, grow=jalapeno 16h) + gl_<key>_* overrides + Tempest lux history -> fn_lighting_circuit_policy() -> planner/default setpoints -> dispatcher/API -> ESP32 per-circuit lighting state machines -> Lutron switches -> equipment_state'::text
        AS source_chain,
    'Each circuit turns on independently inside its own crop photoperiod window when DLI is below its goal and Tempest outdoor lux is below its ON threshold; each circuit holds until lux reaches ON+hysteresis or the window/DLI/auto gate exits.'::text
        AS controller_contract
FROM resolved r;
$$;
