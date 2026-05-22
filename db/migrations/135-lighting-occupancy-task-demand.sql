-- Migration 135: lighting occupancy is a lux-gated task-light demand
--
-- Firmware now treats occupancy as a separate task-light demand rather than an
-- unconditional force-on. Keep live status views aligned with that contract so
-- dashboards show whether lights are expected because of plant supplementation
-- or because an occupied greenhouse is dark enough for task lighting.

DROP VIEW IF EXISTS v_lighting_traceability_now;
DROP VIEW IF EXISTS v_lighting_minutes_status_now;

CREATE VIEW v_lighting_minutes_status_now AS
WITH policy AS (
    SELECT * FROM fn_lighting_minutes_policy(now(), 'vallery')
),
today_window AS (
    SELECT
        p.light_key,
        p.equipment,
        p.target_light_minutes,
        p.start_hour,
        p.cutoff_hour,
        p.lux_on_threshold,
        p.lux_hysteresis,
        p.lux_off_threshold,
        ((now() AT TIME ZONE 'America/Denver')::date + make_interval(hours => p.start_hour)) AT TIME ZONE 'America/Denver' AS start_ts,
        LEAST(
            ((now() AT TIME ZONE 'America/Denver')::date + make_interval(hours => p.cutoff_hour)) AT TIME ZONE 'America/Denver',
            now()
        ) AS end_ts
    FROM policy p
),
minute_grid AS (
    SELECT
        w.*,
        gs.minute_ts
    FROM today_window w
    LEFT JOIN LATERAL generate_series(
        w.start_ts,
        w.end_ts - interval '1 minute',
        interval '1 minute'
    ) AS gs(minute_ts) ON w.end_ts > w.start_ts
),
lux_min AS (
    SELECT
        time_bucket('1 minute', c.ts) AS minute_ts,
        avg(COALESCE(c.outdoor_lux, c.lux)) AS natural_lux
    FROM climate c
    WHERE c.greenhouse_id = 'vallery'
      AND c.ts >= (SELECT min(start_ts) FROM today_window)
      AND c.ts <= (SELECT max(end_ts) FROM today_window)
      AND COALESCE(c.outdoor_lux, c.lux) IS NOT NULL
    GROUP BY 1
),
state_seed AS (
    SELECT
        w.light_key,
        w.equipment,
        w.start_ts AS ts,
        COALESCE((
            SELECT e.state
            FROM equipment_state e
            WHERE e.greenhouse_id = 'vallery'
              AND e.equipment = w.equipment
              AND e.ts <= w.start_ts
            ORDER BY e.ts DESC
            LIMIT 1
        ), false) AS state
    FROM today_window w
),
state_events AS (
    SELECT w.light_key, w.equipment, e.ts, e.state
    FROM today_window w
    JOIN equipment_state e
      ON e.greenhouse_id = 'vallery'
     AND e.equipment = w.equipment
     AND e.ts >= w.start_ts
     AND e.ts <= w.end_ts
),
state_timeline AS (
    SELECT
        x.light_key,
        x.equipment,
        x.ts,
        x.state,
        lead(x.ts, 1, w.end_ts) OVER (
            PARTITION BY x.light_key, x.equipment
            ORDER BY x.ts
        ) AS next_ts
    FROM (
        SELECT * FROM state_seed
        UNION ALL
        SELECT * FROM state_events
    ) x
    JOIN today_window w
      ON w.light_key = x.light_key
     AND w.equipment = x.equipment
),
state_segments AS (
    SELECT
        st.light_key,
        st.equipment,
        GREATEST(st.ts, w.start_ts) AS start_ts,
        LEAST(st.next_ts, w.end_ts) AS end_ts
    FROM state_timeline st
    JOIN today_window w
      ON w.light_key = st.light_key
     AND w.equipment = st.equipment
    WHERE st.state IS TRUE
      AND st.ts < w.end_ts
      AND st.next_ts > w.start_ts
),
minute_eval AS (
    SELECT
        mg.light_key,
        mg.equipment,
        mg.minute_ts,
        COALESCE(lm.natural_lux, 0.0) >= mg.lux_on_threshold AS natural_qualified,
        EXISTS (
            SELECT 1
            FROM state_segments s
            WHERE s.light_key = mg.light_key
              AND s.equipment = mg.equipment
              AND s.start_ts < mg.minute_ts + interval '1 minute'
              AND s.end_ts > mg.minute_ts
        ) AS switch_on
    FROM minute_grid mg
    LEFT JOIN lux_min lm
      ON lm.minute_ts = mg.minute_ts
),
today AS (
    SELECT
        w.light_key,
        w.equipment,
        count(me.minute_ts)::integer AS observed_minutes,
        count(me.minute_ts) FILTER (WHERE me.natural_qualified)::integer AS natural_qualified_minutes,
        count(me.minute_ts) FILTER (WHERE me.switch_on)::integer AS switch_on_minutes,
        count(me.minute_ts) FILTER (WHERE me.natural_qualified AND me.switch_on)::integer AS overlap_minutes,
        count(me.minute_ts) FILTER (WHERE me.natural_qualified OR me.switch_on)::integer AS qualified_light_minutes,
        count(me.minute_ts) FILTER (WHERE NOT me.natural_qualified AND NOT me.switch_on)::integer AS below_threshold_off_minutes
    FROM today_window w
    LEFT JOIN minute_eval me
      ON me.light_key = w.light_key
     AND me.equipment = w.equipment
    GROUP BY w.light_key, w.equipment
),
latest_climate AS (
    SELECT ts, dli_today, lux, outdoor_lux
    FROM climate
    WHERE greenhouse_id = 'vallery'
    ORDER BY ts DESC
    LIMIT 1
),
latest_equipment AS (
    SELECT DISTINCT ON (equipment) equipment, state, ts
    FROM equipment_state
    WHERE greenhouse_id = 'vallery'
      AND equipment IN ('grow_light_main', 'grow_light_grow')
    ORDER BY equipment, ts DESC
),
current_firmware_start AS (
    WITH firmware_ordered AS (
        SELECT
            ts,
            firmware_version,
            lag(firmware_version) OVER (ORDER BY ts) AS previous_firmware_version
        FROM diagnostics
        WHERE firmware_version IS NOT NULL
          AND firmware_version <> ''
          AND ts > now() - interval '30 days'
    ),
    current_firmware AS (
        SELECT firmware_version
        FROM diagnostics
        WHERE firmware_version IS NOT NULL
          AND firmware_version <> ''
        ORDER BY ts DESC
        LIMIT 1
    )
    SELECT max(fo.ts) AS ts
    FROM firmware_ordered fo
    CROSS JOIN current_firmware cf
    WHERE fo.firmware_version = cf.firmware_version
      AND fo.previous_firmware_version IS DISTINCT FROM fo.firmware_version
),
latest_reason AS (
    SELECT DISTINCT ON (entity) entity, value, ts
    FROM system_state
    WHERE entity IN ('gl_main_state', 'gl_main_reason', 'gl_grow_state', 'gl_grow_reason')
    ORDER BY entity, ts DESC
),
latest_occupancy AS (
    SELECT DISTINCT ON (entity) entity, value, ts
    FROM system_state
    WHERE entity IN ('occupancy', 'occupancy_until')
    ORDER BY entity, ts DESC
),
occupancy AS (
    SELECT
        COALESCE(state.value = 'occupied', false)
        AND COALESCE(
            CASE
                WHEN until_row.value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' THEN until_row.value::timestamptz > now()
                ELSE false
            END,
            false
        ) AS occupancy_active
    FROM (SELECT 1) seed
    LEFT JOIN latest_occupancy state ON state.entity = 'occupancy'
    LEFT JOIN latest_occupancy until_row ON until_row.entity = 'occupancy_until'
),
outdoor_staleness AS (
    SELECT COALESCE((
        SELECT greatest(120, least(1800, round(value)::integer))
        FROM setpoint_snapshot
        WHERE COALESCE(greenhouse_id, 'vallery') = 'vallery'
          AND parameter = 'outdoor_staleness_max_s'
        ORDER BY ts DESC
        LIMIT 1
    ), 600) AS max_age_s
),
joined AS (
    SELECT
        p.*,
        COALESCE(t.qualified_light_minutes, 0) AS qualified_light_minutes,
        COALESCE(t.natural_qualified_minutes, 0) AS natural_qualified_minutes,
        COALESCE(t.switch_on_minutes, 0) AS switch_on_minutes,
        COALESCE(t.overlap_minutes, 0) AS overlap_minutes,
        greatest(0, p.target_light_minutes - COALESCE(t.qualified_light_minutes, 0)) AS remaining_light_minutes,
        c.ts AS climate_ts,
        c.dli_today,
        c.lux AS indoor_lux,
        c.outdoor_lux,
        c.outdoor_lux AS exterior_lux,
        COALESCE(c.outdoor_lux, c.lux, 0.0) AS natural_lux,
        COALESCE(c.outdoor_lux, c.lux, 0.0) >= p.lux_on_threshold AS natural_qualified_now,
        EXTRACT(hour FROM now() AT TIME ZONE 'America/Denver')::integer AS local_hour,
        CASE
            WHEN p.start_hour <= p.cutoff_hour THEN
                EXTRACT(hour FROM now() AT TIME ZONE 'America/Denver')::integer >= p.start_hour
                AND EXTRACT(hour FROM now() AT TIME ZONE 'America/Denver')::integer < p.cutoff_hour
            ELSE
                EXTRACT(hour FROM now() AT TIME ZONE 'America/Denver')::integer >= p.start_hour
                OR EXTRACT(hour FROM now() AT TIME ZONE 'America/Denver')::integer < p.cutoff_hour
        END AS in_light_window,
        COALESCE(t.qualified_light_minutes, 0) < p.target_light_minutes AS minutes_below_target,
        COALESCE(c.outdoor_lux, c.lux, 0.0) < p.lux_on_threshold AS lux_below_on_threshold,
        COALESCE(c.outdoor_lux, c.lux, 0.0) < p.lux_off_threshold AS lux_below_off_threshold,
        c.outdoor_lux IS NOT NULL
            AND c.ts > now() - make_interval(secs => os.max_age_s) AS exterior_lux_fresh,
        c.outdoor_lux IS NOT NULL
            AND c.ts > now() - make_interval(secs => os.max_age_s)
            AND c.outdoor_lux < p.lux_on_threshold AS exterior_lux_below_on_threshold,
        c.outdoor_lux IS NOT NULL
            AND c.ts > now() - make_interval(secs => os.max_age_s)
            AND c.outdoor_lux < p.lux_off_threshold AS exterior_lux_below_off_threshold,
        o.occupancy_active,
        COALESCE(e.state, false) AS actual_on,
        state_row.value AS firmware_state,
        reason_row.value AS firmware_reason,
        (
            state_row.ts >= COALESCE((SELECT ts FROM current_firmware_start), now() - interval '24 hours')
            AND reason_row.ts >= COALESCE((SELECT ts FROM current_firmware_start), now() - interval '24 hours')
            AND state_row.ts > now() - interval '15 minutes'
            AND reason_row.ts > now() - interval '15 minutes'
        ) AS firmware_telemetry_fresh,
        e.ts AS equipment_ts
    FROM policy p
    LEFT JOIN today t
      ON t.light_key = p.light_key
     AND t.equipment = p.equipment
    LEFT JOIN latest_climate c ON true
    LEFT JOIN latest_equipment e ON e.equipment = p.equipment
    LEFT JOIN latest_reason state_row ON state_row.entity = 'gl_' || p.light_key || '_state'
    LEFT JOIN latest_reason reason_row ON reason_row.entity = 'gl_' || p.light_key || '_reason'
    CROSS JOIN occupancy o
    CROSS JOIN outdoor_staleness os
)
SELECT
    j.*,
    (
        j.auto_enabled
        AND j.in_light_window
        AND j.minutes_below_target
        AND (
            j.lux_below_on_threshold
            OR (
                j.actual_on
                OR (j.firmware_telemetry_fresh AND upper(COALESCE(j.firmware_state, '')) = 'ON')
            ) AND j.lux_below_off_threshold
        )
    ) AS plant_supplement_demand,
    (
        j.auto_enabled
        AND j.occupancy_active
        AND j.exterior_lux_fresh
        AND (
            j.exterior_lux_below_on_threshold
            OR (
                j.actual_on
                OR (j.firmware_telemetry_fresh AND upper(COALESCE(j.firmware_state, '')) = 'ON')
            ) AND j.exterior_lux_below_off_threshold
        )
    ) AS occupancy_lux_demand,
    (
        (
            j.auto_enabled
            AND j.in_light_window
            AND j.minutes_below_target
            AND (
                j.lux_below_on_threshold
                OR (
                    j.actual_on
                    OR (j.firmware_telemetry_fresh AND upper(COALESCE(j.firmware_state, '')) = 'ON')
                ) AND j.lux_below_off_threshold
            )
        )
        OR (
            j.auto_enabled
            AND j.occupancy_active
            AND j.exterior_lux_fresh
            AND (
                j.exterior_lux_below_on_threshold
                OR (
                    j.actual_on
                    OR (j.firmware_telemetry_fresh AND upper(COALESCE(j.firmware_state, '')) = 'ON')
                ) AND j.exterior_lux_below_off_threshold
            )
        )
    ) AS expected_on
FROM joined j;

COMMENT ON VIEW v_lighting_minutes_status_now IS
    'Current per-circuit qualified-light-minutes policy, plant supplement demand, lux-gated occupancy task-light demand, firmware state, and actual Lutron switch state.';

CREATE VIEW v_lighting_traceability_now AS
WITH status AS (
    SELECT * FROM v_lighting_minutes_status_now
),
latest_desired AS (
    SELECT DISTINCT ON (parameter)
        parameter,
        value,
        delivery_status,
        ts
    FROM setpoint_changes
    WHERE COALESCE(greenhouse_id, 'vallery') = 'vallery'
      AND COALESCE(source, '') <> 'esp32'
    ORDER BY parameter, ts DESC
),
latest_cfg AS (
    SELECT DISTINCT ON (parameter)
        parameter,
        value,
        ts
    FROM setpoint_snapshot
    WHERE COALESCE(greenhouse_id, 'vallery') = 'vallery'
    ORDER BY parameter, ts DESC
),
latest_decision AS (
    SELECT DISTINCT ON (entity)
        entity,
        value,
        ts
    FROM system_state
    WHERE entity IN ('gl_main_decision_epoch', 'gl_grow_decision_epoch')
    ORDER BY entity, ts DESC
)
SELECT
    s.*,
    cfg_target.value AS cfg_target_light_minutes,
    cfg_lux.value AS cfg_lux_on_threshold,
    cfg_hyst.value AS cfg_lux_hysteresis,
    cfg_auto.value >= 0.5 AS cfg_auto_enabled,
    cfg_auto.ts AS cfg_auto_ts,
    desired_target.value AS desired_target_light_minutes,
    desired_lux.value AS desired_lux_on_threshold,
    desired_hyst.value AS desired_lux_hysteresis,
    desired_auto.value >= 0.5 AS desired_auto_enabled,
    desired_auto.delivery_status AS desired_auto_delivery_status,
    desired_auto.ts AS desired_auto_ts,
    CASE
        WHEN decision.value ~ '^[0-9]+([.]0+)?$' THEN round(decision.value::numeric)::bigint
        ELSE NULL
    END AS firmware_decision_epoch,
    decision.ts AS firmware_decision_ts,
    decision.ts > now() - interval '15 minutes' AS firmware_decision_fresh,
    (
        s.auto_enabled IS NOT DISTINCT FROM (cfg_auto.value >= 0.5)
        AND s.target_light_minutes IS NOT DISTINCT FROM round(cfg_target.value)::integer
        AND COALESCE(abs(s.lux_on_threshold - cfg_lux.value) < 0.5, false)
        AND COALESCE(abs(s.lux_hysteresis - cfg_hyst.value) < 0.5, false)
    ) AS policy_matches_cfg
FROM status s
LEFT JOIN latest_cfg cfg_target ON cfg_target.parameter = 'gl_' || s.light_key || '_target_light_minutes'
LEFT JOIN latest_cfg cfg_lux ON cfg_lux.parameter = 'gl_' || s.light_key || '_lux_threshold'
LEFT JOIN latest_cfg cfg_hyst ON cfg_hyst.parameter = 'gl_' || s.light_key || '_lux_hysteresis'
LEFT JOIN latest_cfg cfg_auto ON cfg_auto.parameter = 'sw_gl_' || s.light_key || '_auto_mode'
LEFT JOIN latest_desired desired_target ON desired_target.parameter = 'gl_' || s.light_key || '_target_light_minutes'
LEFT JOIN latest_desired desired_lux ON desired_lux.parameter = 'gl_' || s.light_key || '_lux_threshold'
LEFT JOIN latest_desired desired_hyst ON desired_hyst.parameter = 'gl_' || s.light_key || '_lux_hysteresis'
LEFT JOIN latest_desired desired_auto ON desired_auto.parameter = 'sw_gl_' || s.light_key || '_auto_mode'
LEFT JOIN latest_decision decision ON decision.entity = 'gl_' || s.light_key || '_decision_epoch';

COMMENT ON VIEW v_lighting_traceability_now IS
    'Lighting policy traceability split into plant supplement demand, lux-gated occupancy task-light demand, desired setpoint rows, confirmed cfg readbacks, exact firmware decision epoch text, and physical Lutron state.';
