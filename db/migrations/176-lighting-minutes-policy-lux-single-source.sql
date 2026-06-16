-- 176 — Unify the grow-light lux threshold on ONE AI-tunable source of truth.
--
-- BUG (2026-06-16): the grow lights turned off at dawn (06:33) because the
-- device ran lux_on=3000/hyst=1500 (off 4500) instead of the intended
-- 40000/8000 (off 48000). Root cause was a FEEDBACK LOOP in
-- fn_lighting_minutes_policy — the function the dispatcher reads to decide what
-- to push. Its lux thresholds resolve from `latest_values`, which ranks the
-- `latest_snapshot` source (the DEVICE's own cfg readback, source_rank 1) ABOVE
-- the confirmed planner/operator change (source_rank 2). After a reboot reverted
-- the restore_value:no firmware globals to their cold defaults (3000/1500), the
-- device snapshot became 3000, fn_lighting_minutes_policy returned 3000, and the
-- dispatcher dutifully re-pushed 3000 every cycle — locking the device on the
-- wrong threshold. Meanwhile fn_lighting_circuit_policy (which EXCLUDES the
-- device readback and resolves planner-setpoint -> AI recommendation
-- (fn_lighting_lux_threshold_recommendation) -> default) correctly returned
-- 40000/8000 the whole time, but nothing pushed it.
--
-- FIX: make fn_lighting_minutes_policy source its lux_on_threshold /
-- lux_hysteresis (and the derived lux_off_threshold) from
-- fn_lighting_circuit_policy — the single AI-tunable source of truth — via a
-- lateral join on light_key. This:
--   * removes the device-snapshot feedback loop for the lux thresholds, so a
--     post-reboot revert can no longer poison the dispatcher's push;
--   * makes the dispatcher (which reads this function) push the AI-resolved
--     value, restoring 40000/8000 on the next dispatch cycle and re-asserting it
--     after every future reboot/OTA;
--   * guarantees the dispatcher push, the Grafana lighting panel, and any
--     drift alert all read the SAME resolved threshold (no two policy functions
--     that can disagree).
-- fn_lighting_circuit_policy does NOT call fn_lighting_minutes_policy, so there
-- is no circular dependency. All OTHER columns (target_light_minutes, windows,
-- dwell, dli, auto) are unchanged — only the lux thresholds move to the single
-- source. AI-tunability is preserved/strengthened: the planner or the
-- recommendation function changes one value (in fn_lighting_circuit_policy) and
-- it now flows to the firmware via the dispatcher automatically.
--
-- Non-self-transactional (plain CREATE OR REPLACE, no commit-forcing stmts):
-- safe to rollback-validate under an outer BEGIN; ... ROLLBACK;.

CREATE OR REPLACE FUNCTION public.fn_lighting_minutes_policy(p_ts timestamp with time zone DEFAULT now(), p_greenhouse_id text DEFAULT 'vallery'::text)
 RETURNS TABLE(greenhouse_id text, ts timestamp with time zone, light_key text, equipment text, target_light_minutes integer, start_hour integer, cutoff_hour integer, lux_on_threshold double precision, lux_hysteresis double precision, lux_off_threshold double precision, min_on_s integer, min_off_s integer, auto_enabled boolean, legacy_dli_target double precision, source_chain text, controller_contract text)
 LANGUAGE sql
 STABLE ROWS 2
AS $function$
WITH base AS (
    SELECT * FROM fn_lighting_policy(p_ts, p_greenhouse_id)
),
recommendation AS (
    SELECT * FROM fn_lighting_lux_threshold_recommendation(p_ts, p_greenhouse_id)
),
circuits AS (
    SELECT *
    FROM (VALUES
        ('main'::text, 'grow_light_main'::text),
        ('grow'::text, 'grow_light_grow'::text)
    ) AS v(light_key, equipment)
),
tracked_params AS (
    SELECT unnest(ARRAY[
        'gl_dli_target',
        'gl_sunrise_hour',
        'gl_sunset_hour',
        'gl_lux_threshold',
        'gl_lux_hysteresis',
        'sw_gl_auto_mode',
        'gl_main_target_light_minutes',
        'gl_main_dli_target',
        'gl_main_sunrise_hour',
        'gl_main_sunset_hour',
        'gl_main_lux_threshold',
        'gl_main_lux_hysteresis',
        'gl_main_min_on_s',
        'gl_main_min_off_s',
        'sw_gl_main_auto_mode',
        'gl_grow_target_light_minutes',
        'gl_grow_dli_target',
        'gl_grow_sunrise_hour',
        'gl_grow_sunset_hour',
        'gl_grow_lux_threshold',
        'gl_grow_lux_hysteresis',
        'gl_grow_min_on_s',
        'gl_grow_min_off_s',
        'sw_gl_grow_auto_mode'
    ]::text[]) AS parameter
),
latest_snapshot AS (
    SELECT DISTINCT ON (ss.parameter)
        ss.parameter,
        ss.value::double precision AS value,
        ss.ts,
        1 AS source_rank
    FROM setpoint_snapshot ss
    JOIN tracked_params tp ON tp.parameter = ss.parameter
    WHERE COALESCE(ss.greenhouse_id, p_greenhouse_id) = p_greenhouse_id
    ORDER BY ss.parameter, ss.ts DESC
),
latest_confirmed_changes AS (
    SELECT DISTINCT ON (sc.parameter)
        sc.parameter,
        sc.value::double precision AS value,
        COALESCE(sc.confirmed_at, sc.ts) AS ts,
        2 AS source_rank
    FROM setpoint_changes sc
    JOIN tracked_params tp ON tp.parameter = sc.parameter
    WHERE COALESCE(sc.greenhouse_id, p_greenhouse_id) = p_greenhouse_id
      AND COALESCE(sc.source, '') <> 'esp32'
      AND (
          sc.confirmed_at IS NOT NULL
          OR COALESCE(sc.delivery_status, '') = 'confirmed'
      )
      AND COALESCE(sc.delivery_status, '') NOT IN ('pending', 'superseded', 'expired', 'deferred_heap_pressure')
    ORDER BY sc.parameter, COALESCE(sc.confirmed_at, sc.ts) DESC
),
latest_switch_actual AS (
    SELECT
        'sw_gl_auto_mode'::text AS parameter,
        CASE WHEN e.state THEN 1.0 ELSE 0.0 END AS value,
        e.ts,
        0 AS source_rank
    FROM (
        SELECT DISTINCT ON (equipment) equipment, state, ts
        FROM equipment_state
        WHERE COALESCE(greenhouse_id, p_greenhouse_id) = p_greenhouse_id
          AND equipment = 'gl_auto_mode'
        ORDER BY equipment, ts DESC
    ) e
),
latest_values AS (
    SELECT DISTINCT ON (parameter)
        parameter,
        value
    FROM (
        SELECT * FROM latest_switch_actual
        UNION ALL
        SELECT * FROM latest_snapshot
        UNION ALL
        SELECT * FROM latest_confirmed_changes
    ) values_union
    ORDER BY parameter, source_rank, ts DESC
),
resolved AS (
    SELECT
        c.light_key,
        c.equipment,
        COALESCE(target_min.value, b.target_light_hours * 60.0)::double precision AS target_light_minutes,
        COALESCE(start_h.value, legacy_start.value, b.sunrise_hour)::integer AS start_hour,
        COALESCE(cutoff_h.value, legacy_cutoff.value, b.cutoff_hour)::integer AS cutoff_hour,
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
        COALESCE(auto_mode.value, legacy_auto.value, 1.0) >= 0.5 AS auto_enabled,
        COALESCE(dli.value, legacy_dli.value, b.target_dli)::double precision AS legacy_dli_target
    FROM circuits c
    CROSS JOIN base b
    CROSS JOIN recommendation r
    LEFT JOIN latest_values legacy_dli ON legacy_dli.parameter = 'gl_dli_target'
    LEFT JOIN latest_values legacy_start ON legacy_start.parameter = 'gl_sunrise_hour'
    LEFT JOIN latest_values legacy_cutoff ON legacy_cutoff.parameter = 'gl_sunset_hour'
    LEFT JOIN latest_values legacy_lux ON legacy_lux.parameter = 'gl_lux_threshold'
    LEFT JOIN latest_values legacy_hyst ON legacy_hyst.parameter = 'gl_lux_hysteresis'
    LEFT JOIN latest_values legacy_auto ON legacy_auto.parameter = 'sw_gl_auto_mode'
    LEFT JOIN latest_values target_min ON target_min.parameter = 'gl_' || c.light_key || '_target_light_minutes'
    LEFT JOIN latest_values dli ON dli.parameter = 'gl_' || c.light_key || '_dli_target'
    LEFT JOIN latest_values start_h ON start_h.parameter = 'gl_' || c.light_key || '_sunrise_hour'
    LEFT JOIN latest_values cutoff_h ON cutoff_h.parameter = 'gl_' || c.light_key || '_sunset_hour'
    LEFT JOIN latest_values lux_on ON lux_on.parameter = 'gl_' || c.light_key || '_lux_threshold'
    LEFT JOIN latest_values lux_hyst ON lux_hyst.parameter = 'gl_' || c.light_key || '_lux_hysteresis'
    LEFT JOIN latest_values min_on ON min_on.parameter = 'gl_' || c.light_key || '_min_on_s'
    LEFT JOIN latest_values min_off ON min_off.parameter = 'gl_' || c.light_key || '_min_off_s'
    LEFT JOIN latest_values auto_mode ON auto_mode.parameter = 'sw_gl_' || c.light_key || '_auto_mode'
)
SELECT
    p_greenhouse_id AS greenhouse_id,
    p_ts AS ts,
    r.light_key,
    r.equipment,
    greatest(0, least(1080, round(r.target_light_minutes)::integer)) AS target_light_minutes,
    greatest(0, least(23, r.start_hour)) AS start_hour,
    greatest(0, least(23, r.cutoff_hour)) AS cutoff_hour,
    -- SINGLE SOURCE OF TRUTH (176): lux thresholds come from
    -- fn_lighting_circuit_policy (planner-setpoint -> AI recommendation ->
    -- default; device cfg readback EXCLUDED), so a post-reboot firmware revert
    -- cannot feedback-poison the dispatcher push. Fallback to the local resolve
    -- only if the circuit policy somehow returns NULL (it never does).
    greatest(100.0, least(100000.0, COALESCE(cp.lux_on_threshold, r.lux_on_threshold))) AS lux_on_threshold,
    greatest(0.0, least(25000.0, COALESCE(cp.lux_hysteresis, r.lux_hysteresis))) AS lux_hysteresis,
    greatest(100.0, least(100000.0, COALESCE(cp.lux_on_threshold, r.lux_on_threshold)))
        + greatest(0.0, least(25000.0, COALESCE(cp.lux_hysteresis, r.lux_hysteresis))) AS lux_off_threshold,
    greatest(0, least(3600, r.min_on_s)) AS min_on_s,
    greatest(0, least(3600, r.min_off_s)) AS min_off_s,
    r.auto_enabled,
    greatest(1.0, least(50.0, r.legacy_dli_target)) AS legacy_dli_target,
    'AI-tunable lux source (fn_lighting_circuit_policy: planner/recommendation/default, device-readback excluded) -> dispatcher/API -> ESP32 per-circuit qualified-minutes state machines -> Lutron switches -> equipment_state'::text
        AS source_chain,
    'Each circuit starts counting at sunrise. A minute qualifies once when natural lux is at or above the ON threshold OR the actual switch is ON. The circuit turns ON below threshold until target_light_minutes is met, with ON+hysteresis as the OFF threshold. Lux thresholds are the single AI-tunable source (fn_lighting_circuit_policy), not the device snapshot.'::text
        AS controller_contract
FROM resolved r
LEFT JOIN LATERAL (
    SELECT cpp.lux_on_threshold, cpp.lux_hysteresis
    FROM fn_lighting_circuit_policy(p_ts, p_greenhouse_id) cpp
    WHERE cpp.light_key = r.light_key
) cp ON true;
$function$;
