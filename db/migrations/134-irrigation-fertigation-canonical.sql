-- Migration 134: Canonical irrigation/fertigation observability
--
-- Retires the stale irrigation_schedule / irrigation_log tables as
-- compatibility surfaces and replaces their operational use with:
--   * v_irrigation_schedule_current: active-plan/firmware observed schedule
--   * v_irrigation_fertigation_runs: equipment-derived fert job + flush runs
--   * expanded daily_summary irrigation/fertigation accounting columns

ALTER TABLE daily_summary
    ADD COLUMN IF NOT EXISTS runtime_drip_wall_fert_h DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS runtime_drip_center_fert_h DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS runtime_mister_south_fert_h DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS runtime_mister_west_fert_h DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS runtime_fert_master_h DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS runtime_irrigation_clean_h DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS runtime_irrigation_fert_h DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS runtime_irrigation_total_h DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS cycles_drip_wall_fert INTEGER,
    ADD COLUMN IF NOT EXISTS cycles_drip_center_fert INTEGER,
    ADD COLUMN IF NOT EXISTS cycles_mister_south_fert INTEGER,
    ADD COLUMN IF NOT EXISTS cycles_mister_west_fert INTEGER,
    ADD COLUMN IF NOT EXISTS cycles_fert_master INTEGER,
    ADD COLUMN IF NOT EXISTS irrigation_water_gal DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS fertigation_water_gal DOUBLE PRECISION;

COMMENT ON COLUMN daily_summary.runtime_drip_wall_fert_h IS
'Runtime hours for the wall fertigation drip relay, derived from compressed equipment_state intervals.';
COMMENT ON COLUMN daily_summary.runtime_drip_center_fert_h IS
'Runtime hours for the center fertigation drip relay, derived from compressed equipment_state intervals.';
COMMENT ON COLUMN daily_summary.runtime_mister_south_fert_h IS
'Runtime hours for the south fertigation mister relay, derived from compressed equipment_state intervals.';
COMMENT ON COLUMN daily_summary.runtime_mister_west_fert_h IS
'Runtime hours for the west fertigation mister relay, derived from compressed equipment_state intervals.';
COMMENT ON COLUMN daily_summary.runtime_fert_master_h IS
'Runtime hours for the fertilizer master valve, derived from compressed equipment_state intervals.';
COMMENT ON COLUMN daily_summary.runtime_irrigation_clean_h IS
'Aggregate clean-water irrigation runtime hours across drip and mister relays.';
COMMENT ON COLUMN daily_summary.runtime_irrigation_fert_h IS
'Aggregate fertigation relay runtime hours, excluding the fertilizer master valve.';
COMMENT ON COLUMN daily_summary.runtime_irrigation_total_h IS
'Aggregate clean-water plus fertigation relay runtime hours, excluding the fertilizer master valve.';
COMMENT ON COLUMN daily_summary.irrigation_water_gal IS
'Meter-derived irrigation/fertigation gallons from v_irrigation_fertigation_runs when available.';
COMMENT ON COLUMN daily_summary.fertigation_water_gal IS
'Meter-derived fertigation gallons from v_irrigation_fertigation_runs.';

COMMENT ON TABLE irrigation_schedule IS
'Retired compatibility table. Canonical current schedule is v_irrigation_schedule_current, sourced from v_active_plan plus ESP32 observed/readback values.';
COMMENT ON TABLE irrigation_log IS
'Retired compatibility table. Canonical irrigation/fertigation events are reconstructed from equipment_state in v_irrigation_fertigation_runs.';
COMMENT ON VIEW v_irrigation_log IS
'Retired compatibility view reconstructed from v_irrigation_fertigation_runs. Do not read irrigation_log for canonical irrigation/fertigation events.';

SET verdify.allow_retired_irrigation_compat_write = 'on';

UPDATE irrigation_schedule
   SET enabled = false,
       notes = concat_ws(
           ' ',
           NULLIF(notes, ''),
           '[retired 2026-05-21: canonical schedule is v_irrigation_schedule_current; wall drip is one shared south/west path]'
       ),
       updated_at = now()
 WHERE zone IN ('south_wall', 'west_wall')
   AND enabled IS TRUE;

RESET verdify.allow_retired_irrigation_compat_write;

CREATE OR REPLACE FUNCTION prevent_retired_irrigation_compat_write()
RETURNS trigger AS $$
BEGIN
    IF lower(COALESCE(current_setting('verdify.allow_retired_irrigation_compat_write', true), '')) IN ('on', 'true', '1') THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;

    RAISE EXCEPTION
        'retired irrigation compatibility table % is read-only; use v_irrigation_schedule_current and v_irrigation_fertigation_runs',
        TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS block_retired_irrigation_schedule_write ON irrigation_schedule;
CREATE TRIGGER block_retired_irrigation_schedule_write
    BEFORE INSERT OR UPDATE OR DELETE ON irrigation_schedule
    FOR EACH ROW EXECUTE FUNCTION prevent_retired_irrigation_compat_write();

DROP TRIGGER IF EXISTS block_retired_irrigation_log_write ON irrigation_log;
CREATE TRIGGER block_retired_irrigation_log_write
    BEFORE INSERT OR UPDATE OR DELETE ON irrigation_log
    FOR EACH ROW EXECUTE FUNCTION prevent_retired_irrigation_compat_write();

UPDATE water_systems
   SET effectiveness_note = concat_ws(
           ' ',
           NULLIF(effectiveness_note, ''),
           'Canonical topology: the wall drip path is shared and serves both south and west; use zone_path=wall_shared in current irrigation views.'
       )
 WHERE slug IN ('wall_drip_clean', 'wall_drip_fert')
   AND COALESCE(effectiveness_note, '') NOT LIKE '%Canonical topology:%';

INSERT INTO instrumentation_requirements (
    requirement_id,
    category,
    metric,
    target_table,
    target_column,
    current_status,
    blocks_story,
    recommended_source,
    priority
) VALUES
    (
        'south_soil_probe_1_repair',
        'root_zone_feedback',
        'South soil probe 1 moisture/temp/EC',
        'climate',
        'soil_moisture_south_1',
        'needed',
        'South probe 1 is stuck at zero, so south-wall irrigation response should not be trusted from that sensor.',
        'Repair or replace the south SEN0601 probe/address-7 wiring and verify moisture response after an irrigation run.',
        1
    ),
    (
        'center_root_zone_runoff_feedback',
        'root_zone_feedback',
        'Center root-zone moisture plus runoff pH/EC',
        'climate',
        'moisture_center',
        'needed',
        'Center fertigation lacks direct root-zone and runoff validation; current validation is relay state plus water-meter movement.',
        'Add center root-zone moisture and center runoff pH/EC instrumentation, then map the entities into climate ingestion.',
        1
    )
ON CONFLICT (requirement_id) DO UPDATE
   SET current_status = EXCLUDED.current_status,
       blocks_story = EXCLUDED.blocks_story,
       recommended_source = EXCLUDED.recommended_source,
       priority = EXCLUDED.priority,
       updated_at = now();

INSERT INTO equipment_assets (equipment, description, model, notes)
VALUES
    (
        'south_soil_probe_1',
        'South root-zone soil probe 1',
        'DFRobot SEN0601 RS485 soil sensor',
        'Stuck at zero as of 2026-05-21; repair or replace and verify nonzero moisture/EC response.'
    ),
    (
        'center_root_zone_runoff_feedback',
        'Center root-zone moisture and runoff pH/EC instrumentation',
        NULL,
        'Placeholder asset for required center moisture, runoff pH, and runoff EC feedback hardware/entities.'
    )
ON CONFLICT (equipment) DO UPDATE
   SET description = EXCLUDED.description,
       model = EXCLUDED.model,
       notes = EXCLUDED.notes;

CREATE OR REPLACE VIEW v_irrigation_schedule_current AS
WITH param_defaults(parameter, default_value) AS (
    VALUES
        ('irrig_wall_start_hour', 10.0),
        ('irrig_wall_start_min', 30.0),
        ('irrig_wall_duration_min', 10.0),
        ('irrig_wall_fert_duration_min', 6.0),
        ('irrig_wall_fert_every_n', 0.0),
        ('irrig_wall_days_mask', 127.0),
        ('irrig_wall_fert_days_mask', 127.0),
        ('irrig_wall_flush_min', 2.0),
        ('irrig_wall_interval_days', 1.0),
        ('irrig_center_start_hour', 10.0),
        ('irrig_center_start_min', 30.0),
        ('irrig_center_duration_min', 10.0),
        ('irrig_center_fert_duration_min', 6.0),
        ('irrig_center_fert_every_n', 0.0),
        ('irrig_center_days_mask', 127.0),
        ('irrig_center_fert_days_mask', 127.0),
        ('irrig_center_flush_min', 2.0),
        ('irrig_center_interval_days', 1.0)
),
latest_snapshot AS (
    SELECT DISTINCT ON (parameter)
           parameter, value, ts
      FROM setpoint_snapshot
     WHERE parameter IN (SELECT parameter FROM param_defaults)
     ORDER BY parameter, ts DESC
),
latest_esp32 AS (
    SELECT DISTINCT ON (parameter)
           parameter, value, ts
      FROM setpoint_changes
     WHERE source = 'esp32'
       AND parameter IN (SELECT parameter FROM param_defaults)
     ORDER BY parameter, ts DESC
),
values_by_param AS (
    SELECT d.parameter,
           COALESCE(ap.value, e.value, s.value, d.default_value) AS value,
           CASE
             WHEN ap.value IS NOT NULL THEN 'active_plan'
             WHEN e.value IS NOT NULL THEN 'esp32_observed'
             WHEN s.value IS NOT NULL THEN 'cfg_readback'
             ELSE 'default'
           END AS source,
           ap.plan_id,
           ap.ts AS plan_ts,
           e.ts AS esp32_ts,
           s.ts AS readback_ts,
           s.value AS readback_value,
           d.default_value
      FROM param_defaults d
      LEFT JOIN v_active_plan ap ON ap.parameter = d.parameter
      LEFT JOIN latest_esp32 e ON e.parameter = d.parameter
      LEFT JOIN latest_snapshot s ON s.parameter = d.parameter
),
pivoted AS (
    SELECT
        max(value) FILTER (WHERE parameter = 'irrig_wall_start_hour') AS wall_start_hour,
        max(value) FILTER (WHERE parameter = 'irrig_wall_start_min') AS wall_start_min,
        max(value) FILTER (WHERE parameter = 'irrig_wall_duration_min') AS wall_duration_min,
        max(value) FILTER (WHERE parameter = 'irrig_wall_fert_duration_min') AS wall_fert_duration_min,
        max(value) FILTER (WHERE parameter = 'irrig_wall_fert_every_n') AS wall_fert_every_n,
        max(value) FILTER (WHERE parameter = 'irrig_wall_days_mask') AS wall_days_mask,
        max(value) FILTER (WHERE parameter = 'irrig_wall_fert_days_mask') AS wall_fert_days_mask,
        max(value) FILTER (WHERE parameter = 'irrig_wall_flush_min') AS wall_flush_min,
        max(value) FILTER (WHERE parameter = 'irrig_wall_interval_days') AS wall_interval_days,
        max(value) FILTER (WHERE parameter = 'irrig_center_start_hour') AS center_start_hour,
        max(value) FILTER (WHERE parameter = 'irrig_center_start_min') AS center_start_min,
        max(value) FILTER (WHERE parameter = 'irrig_center_duration_min') AS center_duration_min,
        max(value) FILTER (WHERE parameter = 'irrig_center_fert_duration_min') AS center_fert_duration_min,
        max(value) FILTER (WHERE parameter = 'irrig_center_fert_every_n') AS center_fert_every_n,
        max(value) FILTER (WHERE parameter = 'irrig_center_days_mask') AS center_days_mask,
        max(value) FILTER (WHERE parameter = 'irrig_center_fert_days_mask') AS center_fert_days_mask,
        max(value) FILTER (WHERE parameter = 'irrig_center_flush_min') AS center_flush_min,
        max(value) FILTER (WHERE parameter = 'irrig_center_interval_days') AS center_interval_days
      FROM values_by_param
),
source_rollup AS (
    SELECT CASE
             WHEN count(*) FILTER (WHERE source = 'active_plan') > 0 THEN 'active_plan'
             WHEN count(*) FILTER (WHERE source = 'esp32_observed') > 0 THEN 'esp32_observed'
             WHEN count(*) FILTER (WHERE source = 'cfg_readback') > 0 THEN 'cfg_readback'
             ELSE 'default'
           END AS schedule_source,
           max(plan_id) FILTER (WHERE plan_id IS NOT NULL) AS plan_id,
           max(plan_ts) AS plan_ts,
           count(*) FILTER (WHERE readback_ts IS NULL OR readback_ts < now() - interval '15 minutes') AS stale_readback_count,
           count(*) FILTER (
               WHERE readback_value IS NOT NULL
                 AND abs(readback_value - value) / greatest(abs(value), 1e-3) >= 0.01
           ) AS readback_drift_count
      FROM values_by_param
),
latest_equipment AS (
    SELECT DISTINCT ON (equipment)
           equipment, state, ts
      FROM equipment_state
     WHERE equipment IN ('irrigation_enabled', 'irrigation_wall_enabled', 'irrigation_center_enabled')
     ORDER BY equipment, ts DESC
)
SELECT
    'wall_shared'::text AS schedule_id,
    'wall_shared'::text AS zone_path,
    'Wall shared drip + south/west misters'::text AS display_name,
    ARRAY['south', 'west']::text[] AS serves_zones,
    COALESCE((SELECT state FROM latest_equipment WHERE equipment = 'irrigation_enabled'), true)
      AND COALESCE((SELECT state FROM latest_equipment WHERE equipment = 'irrigation_wall_enabled'), true) AS enabled,
    make_time(
        greatest(0, least(23, round(p.wall_start_hour)::int)),
        greatest(0, least(59, round(p.wall_start_min)::int)),
        0
    ) AS start_time,
    round(p.wall_duration_min)::int AS clean_duration_min,
    round(p.wall_fert_duration_min)::int AS fert_duration_min,
    round(p.wall_flush_min)::int AS flush_min,
    round(p.wall_interval_days)::int AS interval_days,
    round(p.wall_days_mask)::int AS days_mask,
    round(p.wall_fert_days_mask)::int AS fert_days_mask,
    round(p.wall_fert_every_n)::int AS fert_every_n,
    (round(p.wall_fert_duration_min)::int > 0)
      AND (round(p.wall_fert_days_mask)::int <> 0 OR round(p.wall_fert_every_n)::int > 0) AS fertigation_enabled,
    ARRAY['drip_wall_fert', 'mister_south_fert', 'mister_west_fert']::text[] AS fert_relays,
    ARRAY['drip_wall', 'mister_south', 'mister_west']::text[] AS flush_relays,
    s.schedule_source,
    s.plan_id,
    s.plan_ts,
    s.stale_readback_count,
    s.readback_drift_count,
    'One wall drip path serves south and west; firmware queues wall drip, south mister, and west mister fert jobs from this schedule.'::text AS notes
  FROM pivoted p CROSS JOIN source_rollup s
UNION ALL
SELECT
    'center'::text AS schedule_id,
    'center'::text AS zone_path,
    'Center drip'::text AS display_name,
    ARRAY['center']::text[] AS serves_zones,
    COALESCE((SELECT state FROM latest_equipment WHERE equipment = 'irrigation_enabled'), true)
      AND COALESCE((SELECT state FROM latest_equipment WHERE equipment = 'irrigation_center_enabled'), true) AS enabled,
    make_time(
        greatest(0, least(23, round(p.center_start_hour)::int)),
        greatest(0, least(59, round(p.center_start_min)::int)),
        0
    ) AS start_time,
    round(p.center_duration_min)::int AS clean_duration_min,
    round(p.center_fert_duration_min)::int AS fert_duration_min,
    round(p.center_flush_min)::int AS flush_min,
    round(p.center_interval_days)::int AS interval_days,
    round(p.center_days_mask)::int AS days_mask,
    round(p.center_fert_days_mask)::int AS fert_days_mask,
    round(p.center_fert_every_n)::int AS fert_every_n,
    (round(p.center_fert_duration_min)::int > 0)
      AND (round(p.center_fert_days_mask)::int <> 0 OR round(p.center_fert_every_n)::int > 0) AS fertigation_enabled,
    ARRAY['drip_center_fert']::text[] AS fert_relays,
    ARRAY['drip_center']::text[] AS flush_relays,
    s.schedule_source,
    s.plan_id,
    s.plan_ts,
    s.stale_readback_count,
    s.readback_drift_count,
    'Center drip/fert path; center root-zone and runoff feedback remain pending physical instrumentation.'::text AS notes
  FROM pivoted p CROSS JOIN source_rollup s;

COMMENT ON VIEW v_irrigation_schedule_current IS
'Canonical current irrigation schedule. Values prefer v_active_plan, then ESP32 observed number states, then cfg_* readbacks, then firmware defaults.';

CREATE OR REPLACE VIEW v_irrigation_fertigation_runs AS
WITH raw AS (
    SELECT e.equipment,
           e.ts,
           e.state,
           lag(e.state) OVER (PARTITION BY e.equipment ORDER BY e.ts) AS prev_state
      FROM equipment_state e
     WHERE e.equipment IN (
           'drip_wall_fert',
           'drip_wall',
           'mister_south_fert',
           'mister_south',
           'mister_west_fert',
           'mister_west',
           'drip_center_fert',
           'drip_center',
           'fert_master_valve',
           'water_flowing'
     )
),
changes AS (
    SELECT equipment, ts, state
      FROM raw
     WHERE prev_state IS NULL OR prev_state IS DISTINCT FROM state
),
intervals AS (
    SELECT equipment,
           ts AS start_ts,
           lead(ts) OVER (PARTITION BY equipment ORDER BY ts) AS end_ts,
           state
      FROM changes
),
on_intervals AS (
    SELECT equipment, start_ts, end_ts
      FROM intervals
     WHERE state IS TRUE
       AND end_ts IS NOT NULL
       AND end_ts > start_ts
),
fert_map(fert_relay, flush_relay, zone_path, serves_zones, schedule_id, expected_fert_min, expected_flush_min) AS (
    VALUES
        ('drip_wall_fert', 'drip_wall', 'wall_shared', ARRAY['south','west']::text[], 'wall_shared', 6, 2),
        ('mister_south_fert', 'mister_south', 'south_wall_mister', ARRAY['south']::text[], 'wall_shared', 6, 2),
        ('mister_west_fert', 'mister_west', 'west_wall_mister', ARRAY['west']::text[], 'wall_shared', 6, 2),
        ('drip_center_fert', 'drip_center', 'center', ARRAY['center']::text[], 'center', 6, 2)
),
fert_jobs AS (
    SELECT
        f.fert_relay,
        f.flush_relay,
        f.zone_path,
        f.serves_zones,
        f.schedule_id,
        COALESCE(s.fert_duration_min, f.expected_fert_min) AS expected_fert_min,
        COALESCE(s.flush_min, f.expected_flush_min) AS expected_flush_min,
        oi.start_ts AS fert_start,
        oi.end_ts AS fert_end
      FROM on_intervals oi
      JOIN fert_map f ON f.fert_relay = oi.equipment
      LEFT JOIN v_irrigation_schedule_current s ON s.schedule_id = f.schedule_id
),
paired AS (
    SELECT fj.*,
           fl.start_ts AS flush_start,
           fl.end_ts AS flush_end
      FROM fert_jobs fj
      LEFT JOIN LATERAL (
          SELECT oi.start_ts, oi.end_ts
            FROM on_intervals oi
           WHERE oi.equipment = fj.flush_relay
             AND oi.start_ts >= fj.fert_end - interval '30 seconds'
             AND oi.start_ts <= fj.fert_end + interval '10 minutes'
           ORDER BY oi.start_ts
           LIMIT 1
      ) fl ON true
),
measured AS (
    SELECT
        p.*,
        COALESCE(p.flush_end, p.fert_end) AS run_end,
        master.overlap_min AS fert_master_overlap_min,
        water.overlap_min AS water_flowing_overlap_min,
        meter.samples AS meter_samples,
        meter.min_total_gal,
        meter.max_total_gal,
        CASE
          WHEN meter.samples >= 2
          THEN greatest(0.0, meter.max_total_gal - meter.min_total_gal)
        END AS meter_delta_gal,
        meter.avg_flow_gpm,
        meter.max_flow_gpm
      FROM paired p
      LEFT JOIN LATERAL (
          SELECT round((sum(extract(epoch FROM least(m.end_ts, p.fert_end) - greatest(m.start_ts, p.fert_start))) / 60.0)::numeric, 2)::double precision AS overlap_min
            FROM on_intervals m
           WHERE m.equipment = 'fert_master_valve'
             AND m.start_ts < p.fert_end
             AND m.end_ts > p.fert_start
      ) master ON true
      LEFT JOIN LATERAL (
          SELECT round((sum(extract(epoch FROM least(w.end_ts, COALESCE(p.flush_end, p.fert_end)) - greatest(w.start_ts, p.fert_start))) / 60.0)::numeric, 2)::double precision AS overlap_min
            FROM on_intervals w
           WHERE w.equipment = 'water_flowing'
             AND w.start_ts < COALESCE(p.flush_end, p.fert_end)
             AND w.end_ts > p.fert_start
      ) water ON true
      LEFT JOIN LATERAL (
          SELECT stats.samples,
                 first_sample.water_total_gal AS min_total_gal,
                 last_sample.water_total_gal AS max_total_gal,
                 stats.avg_flow_gpm,
                 stats.max_flow_gpm
            FROM (
                SELECT count(*)::int AS samples,
                       avg(c.flow_gpm) AS avg_flow_gpm,
                       max(c.flow_gpm) AS max_flow_gpm
                  FROM climate c
                 WHERE c.ts >= p.fert_start - interval '30 seconds'
                   AND c.ts <= COALESCE(p.flush_end, p.fert_end) + interval '90 seconds'
                   AND c.water_total_gal IS NOT NULL
                   AND c.water_total_gal > 0
            ) stats
            LEFT JOIN LATERAL (
                SELECT c.water_total_gal
                  FROM climate c
                 WHERE c.ts >= p.fert_start - interval '30 seconds'
                   AND c.ts <= COALESCE(p.flush_end, p.fert_end) + interval '90 seconds'
                   AND c.water_total_gal IS NOT NULL
                   AND c.water_total_gal > 0
                 ORDER BY c.ts ASC
                 LIMIT 1
            ) first_sample ON true
            LEFT JOIN LATERAL (
                SELECT c.water_total_gal
                  FROM climate c
                 WHERE c.ts >= p.fert_start - interval '30 seconds'
                   AND c.ts <= COALESCE(p.flush_end, p.fert_end) + interval '90 seconds'
                   AND c.water_total_gal IS NOT NULL
                   AND c.water_total_gal > 0
                 ORDER BY c.ts DESC
                 LIMIT 1
            ) last_sample ON true
      ) meter ON true
),
flagged AS (
    SELECT
        md5(fert_relay || '|' || fert_start::text) AS run_id,
        (fert_start AT TIME ZONE 'America/Denver')::date AS day,
        zone_path,
        serves_zones,
        schedule_id,
        fert_relay,
        flush_relay,
        fert_start,
        fert_end,
        round((extract(epoch FROM (fert_end - fert_start)) / 60.0)::numeric, 2)::double precision AS fert_duration_min,
        flush_start,
        flush_end,
        CASE WHEN flush_start IS NOT NULL AND flush_end IS NOT NULL
             THEN round((extract(epoch FROM (flush_end - flush_start)) / 60.0)::numeric, 2)::double precision
        END AS flush_duration_min,
        fert_start AS run_start,
        run_end,
        round((extract(epoch FROM (run_end - fert_start)) / 60.0)::numeric, 2)::double precision AS total_duration_min,
        expected_fert_min,
        expected_flush_min,
        COALESCE(fert_master_overlap_min, 0.0) AS fert_master_overlap_min,
        COALESCE(water_flowing_overlap_min, 0.0) AS water_flowing_overlap_min,
        meter_samples,
        min_total_gal,
        max_total_gal,
        meter_delta_gal,
        avg_flow_gpm,
        max_flow_gpm,
        array_remove(ARRAY[
            CASE WHEN flush_start IS NULL THEN 'missing_flush' END,
            CASE WHEN COALESCE(fert_master_overlap_min, 0.0) < greatest(0.5, (extract(epoch FROM (fert_end - fert_start)) / 60.0) * 0.95)
                 THEN 'low_master_overlap' END,
            CASE WHEN meter_samples IS NULL OR meter_samples < 2 THEN 'meter_sparse' END,
            CASE WHEN meter_samples >= 2 AND COALESCE(meter_delta_gal, 0.0) <= 0.0 THEN 'zero_meter_delta' END
        ], NULL) AS quality_flags
      FROM measured
)
SELECT *,
       CASE WHEN array_length(quality_flags, 1) IS NULL
            THEN 'ok'
            ELSE array_to_string(quality_flags, ',')
       END AS quality_flag
  FROM flagged
 ORDER BY fert_start DESC, fert_relay;

COMMENT ON VIEW v_irrigation_fertigation_runs IS
'Equipment-derived fertigation runs. Compresses duplicate relay state rows, pairs fert relays with clean flush relays, measures fertilizer master overlap, and computes per-run water-meter delta.';

DROP VIEW IF EXISTS v_irrigation_log;

CREATE VIEW v_irrigation_log AS
SELECT
    (('x' || substr(run_id, 1, 8))::bit(32)::integer & 2147483647) AS id,
    run_start AS ts,
    zone_path AS zone,
    NULL::integer AS schedule_id,
    (run_start AT TIME ZONE 'America/Denver')::time AS scheduled_time,
    run_start AS actual_start,
    run_end AS actual_end,
    round(COALESCE(meter_delta_gal, 0)::numeric, 2)::numeric(8,2) AS volume_gal,
    'equipment_state'::text AS source,
    concat_ws(
        ' ',
        'retired compatibility row reconstructed from v_irrigation_fertigation_runs;',
        'run_id=' || run_id,
        'fert_relay=' || fert_relay,
        'flush_relay=' || flush_relay,
        'quality=' || quality_flag
    ) AS notes,
    run_end AS created_at,
    'vallery'::text AS greenhouse_id,
    false::boolean AS weather_skip,
    true::boolean AS fertigation,
    'water_total_gal'::text AS metering_method,
    round(total_duration_min * 60)::integer AS duration_s
  FROM v_irrigation_fertigation_runs
 ORDER BY run_start DESC, fert_relay;

COMMENT ON VIEW v_irrigation_log IS
'Retired compatibility view reconstructed from v_irrigation_fertigation_runs. Do not read irrigation_log for canonical irrigation/fertigation events.';

CREATE OR REPLACE VIEW v_irrigation_program_daily AS
SELECT
    day AS date,
    count(*) AS fertigation_events,
    round(sum(total_duration_min)::numeric, 1)::double precision AS runtime_min,
    round(sum(fert_duration_min)::numeric, 1)::double precision AS fert_runtime_min,
    round(sum(COALESCE(flush_duration_min, 0))::numeric, 1)::double precision AS flush_runtime_min,
    round(sum(fert_master_overlap_min)::numeric, 1)::double precision AS fert_master_overlap_min,
    round(sum(COALESCE(meter_delta_gal, 0))::numeric, 2)::double precision AS meter_delta_gal,
    count(*) FILTER (WHERE quality_flag <> 'ok') AS flagged_events,
    max(run_end) AS latest_event
  FROM v_irrigation_fertigation_runs
 GROUP BY day
 ORDER BY day DESC;

COMMENT ON VIEW v_irrigation_program_daily IS
'Daily rollup of equipment-derived irrigation/fertigation runs.';

-- Dashboard/API reads in this database are short OLTP-style Timescale queries.
-- PostgreSQL JIT compilation can dominate latency for these plans even when the
-- runtime scan is small, so keep it disabled for the application role.
ALTER ROLE verdify SET jit = off;

CREATE OR REPLACE VIEW v_irrigation_sensor_feedback_status AS
WITH latest AS (
    SELECT ts,
           soil_moisture_south_1,
           soil_temp_south_1,
           soil_ec_south_1,
           soil_moisture_south_2,
           soil_temp_south_2,
           moisture_center,
           ph_runoff_center,
           ec_runoff_center
      FROM climate
     ORDER BY ts DESC
     LIMIT 1
),
windowed AS (
    SELECT
        count(*) FILTER (WHERE ts >= now() - interval '24 hours') AS samples_24h,
        count(*) FILTER (WHERE ts >= now() - interval '24 hours' AND soil_moisture_south_1 IS NOT NULL) AS south_1_samples_24h,
        count(*) FILTER (WHERE ts >= now() - interval '24 hours' AND soil_moisture_south_1 > 0 AND soil_moisture_south_1 <= 100) AS south_1_positive_24h,
        min(soil_moisture_south_1) FILTER (WHERE ts >= now() - interval '24 hours') AS south_1_min_24h,
        max(soil_moisture_south_1) FILTER (WHERE ts >= now() - interval '24 hours') AS south_1_max_24h,
        max(ts) FILTER (WHERE soil_moisture_south_1 > 0 AND soil_moisture_south_1 <= 100) AS south_1_moisture_last_positive_ts,
        max(ts) FILTER (WHERE soil_ec_south_1 > 0) AS south_1_ec_last_positive_ts,
        count(*) FILTER (WHERE ts >= now() - interval '24 hours' AND soil_moisture_south_2 IS NOT NULL) AS south_2_samples_24h,
        count(*) FILTER (WHERE ts >= now() - interval '24 hours' AND soil_moisture_south_2 > 0 AND soil_moisture_south_2 <= 100) AS south_2_positive_24h,
        min(soil_moisture_south_2) FILTER (WHERE ts >= now() - interval '24 hours') AS south_2_min_24h,
        max(soil_moisture_south_2) FILTER (WHERE ts >= now() - interval '24 hours') AS south_2_max_24h,
        max(ts) FILTER (WHERE soil_moisture_south_2 > 0 AND soil_moisture_south_2 <= 100) AS south_2_last_positive_ts,
        max(ts) FILTER (WHERE moisture_center >= 0 AND moisture_center <= 100) AS center_moisture_last_valid_ts,
        max(ts) FILTER (WHERE ph_runoff_center >= 0 AND ph_runoff_center <= 14) AS center_ph_last_valid_ts,
        max(ts) FILTER (WHERE ec_runoff_center >= 0) AS center_ec_last_valid_ts
      FROM climate
)
SELECT
    'south_soil_probe_1'::text AS feedback_key,
    'south'::text AS zone,
    'soil_moisture_south_1'::text AS signal,
    latest.ts AS last_sample_ts,
    latest.soil_moisture_south_1 AS latest_value,
    CASE
      WHEN windowed.south_1_samples_24h = 0 THEN 'missing'
      WHEN windowed.south_1_positive_24h = 0 THEN 'stuck_zero'
      ELSE 'ok'
    END AS status,
    jsonb_build_object(
      'samples_24h', windowed.south_1_samples_24h,
      'positive_samples_24h', windowed.south_1_positive_24h,
      'min_24h', windowed.south_1_min_24h,
      'max_24h', windowed.south_1_max_24h,
      'last_positive_ts', windowed.south_1_moisture_last_positive_ts,
      'soil_temp_south_1', latest.soil_temp_south_1,
      'soil_ec_south_1', latest.soil_ec_south_1,
      'soil_ec_south_1_last_positive_ts', windowed.south_1_ec_last_positive_ts,
      'south_2_reference_samples_24h', windowed.south_2_samples_24h,
      'south_2_reference_positive_samples_24h', windowed.south_2_positive_24h,
      'south_2_reference_min_24h', windowed.south_2_min_24h,
      'south_2_reference_max_24h', windowed.south_2_max_24h,
      'south_2_reference_last_positive_ts', windowed.south_2_last_positive_ts,
      'soil_moisture_south_2_reference', latest.soil_moisture_south_2,
      'soil_temp_south_2_reference', latest.soil_temp_south_2
    ) AS details,
    CASE
      WHEN windowed.south_1_positive_24h = 0
       AND windowed.south_2_positive_24h > 0
       AND latest.soil_temp_south_1 IS NOT NULL
        THEN 'Repair or replace south SEN0601/address-7 probe; south_1 temperature and nearby south_2 moisture are updating, so prioritize probe/media contact or channel failure over shared ingestion. Last positive timestamps are in details.'
      ELSE 'Repair or replace south SEN0601/address-7 probe; verify nonzero response after irrigation. Last positive timestamps are in details.'
    END::text AS required_action
  FROM latest CROSS JOIN windowed
UNION ALL
SELECT
    'center_root_zone_moisture'::text,
    'center'::text,
    'moisture_center'::text,
    windowed.center_moisture_last_valid_ts,
    latest.moisture_center,
    CASE
      WHEN latest.moisture_center IS NOT NULL AND NOT (latest.moisture_center >= 0 AND latest.moisture_center <= 100) THEN 'invalid'
      WHEN windowed.center_moisture_last_valid_ts IS NULL THEN 'missing'
      WHEN windowed.center_moisture_last_valid_ts < now() - interval '24 hours' THEN 'stale'
      ELSE 'ok'
    END,
    jsonb_build_object('last_valid_sample_ts', windowed.center_moisture_last_valid_ts, 'latest_raw_value', latest.moisture_center),
    'Install/map center root-zone moisture sensor.'::text
  FROM latest CROSS JOIN windowed
UNION ALL
SELECT
    'center_runoff_ph'::text,
    'center'::text,
    'ph_runoff_center'::text,
    windowed.center_ph_last_valid_ts,
    latest.ph_runoff_center,
    CASE
      WHEN latest.ph_runoff_center IS NOT NULL AND NOT (latest.ph_runoff_center >= 0 AND latest.ph_runoff_center <= 14) THEN 'invalid'
      WHEN windowed.center_ph_last_valid_ts IS NULL THEN 'missing'
      WHEN windowed.center_ph_last_valid_ts < now() - interval '7 days' THEN 'stale'
      ELSE 'ok'
    END,
    jsonb_build_object('last_valid_sample_ts', windowed.center_ph_last_valid_ts, 'latest_raw_value', latest.ph_runoff_center),
    'Install/map center runoff pH feedback.'::text
  FROM latest CROSS JOIN windowed
UNION ALL
SELECT
    'center_runoff_ec'::text,
    'center'::text,
    'ec_runoff_center'::text,
    windowed.center_ec_last_valid_ts,
    latest.ec_runoff_center,
    CASE
      WHEN latest.ec_runoff_center IS NOT NULL AND latest.ec_runoff_center < 0 THEN 'invalid'
      WHEN windowed.center_ec_last_valid_ts IS NULL THEN 'missing'
      WHEN windowed.center_ec_last_valid_ts < now() - interval '7 days' THEN 'stale'
      ELSE 'ok'
    END,
    jsonb_build_object('last_valid_sample_ts', windowed.center_ec_last_valid_ts, 'latest_raw_value', latest.ec_runoff_center),
    'Install/map center runoff EC feedback.'::text
  FROM latest CROSS JOIN windowed
ORDER BY zone, signal;

COMMENT ON VIEW v_irrigation_sensor_feedback_status IS
'Operational status for physical irrigation feedback gaps: south soil probe 1 and center root-zone/runoff instrumentation.';

INSERT INTO maintenance_log (ts, equipment, service_type, description, technician, next_due, notes, greenhouse_id)
SELECT now(),
       'south_soil_probe_1',
       'repair',
       'Repair or replace south soil probe 1; current moisture/EC are stuck at zero.',
       'operator',
       (now() AT TIME ZONE 'America/Denver')::date,
       'Field evidence: v_irrigation_sensor_feedback_status.details reports last_positive_ts and soil_ec_south_1_last_positive_ts; south_1 temperature and south_2 reference moisture distinguish this from shared ingestion. After repair, run make irrigation-feedback-discover and make irrigation-feedback-check.',
       'vallery'
WHERE NOT EXISTS (
    SELECT 1 FROM maintenance_log
     WHERE equipment = 'south_soil_probe_1'
       AND service_type = 'repair'
       AND description ILIKE 'Repair or replace south soil probe 1%'
       AND greenhouse_id = 'vallery'
);

INSERT INTO maintenance_log (ts, equipment, service_type, description, technician, next_due, notes, greenhouse_id)
SELECT now(),
       'center_root_zone_runoff_feedback',
       'install',
       'Install center root-zone moisture and center runoff pH/EC feedback.',
       'operator',
       (now() AT TIME ZONE 'America/Denver')::date,
       'Field evidence: climate columns, HA/MQTT/ESPHome candidate maps, and sensor_registry targets are ready; hardware/entities must produce moisture_center, ph_runoff_center, and ec_runoff_center. After install, run make irrigation-feedback-discover and make irrigation-feedback-check.',
       'vallery'
WHERE NOT EXISTS (
    SELECT 1 FROM maintenance_log
     WHERE equipment = 'center_root_zone_runoff_feedback'
       AND service_type = 'install'
       AND description ILIKE 'Install center root-zone moisture%'
       AND greenhouse_id = 'vallery'
);

UPDATE maintenance_log
   SET notes = 'Field evidence: v_irrigation_sensor_feedback_status.details reports last_positive_ts and soil_ec_south_1_last_positive_ts; south_1 temperature and south_2 reference moisture distinguish this from shared ingestion. After repair, run make irrigation-feedback-discover and make irrigation-feedback-check.'
 WHERE equipment = 'south_soil_probe_1'
   AND service_type = 'repair'
   AND description ILIKE 'Repair or replace south soil probe 1%'
   AND greenhouse_id = 'vallery';

UPDATE maintenance_log
   SET notes = 'Field evidence: climate columns, HA/MQTT/ESPHome candidate maps, and sensor_registry targets are ready; hardware/entities must produce moisture_center, ph_runoff_center, and ec_runoff_center. After install, run make irrigation-feedback-discover and make irrigation-feedback-check.'
 WHERE equipment = 'center_root_zone_runoff_feedback'
   AND service_type = 'install'
   AND description ILIKE 'Install center root-zone moisture%'
   AND greenhouse_id = 'vallery';

WITH relays(equipment) AS (
    SELECT unnest(ARRAY[
        'drip_wall',
        'drip_center',
        'mister_south',
        'mister_west',
        'mister_center',
        'drip_wall_fert',
        'drip_center_fert',
        'mister_south_fert',
        'mister_west_fert',
        'fert_master_valve'
    ]::text[])
),
raw AS (
    SELECT e.equipment,
           e.ts,
           e.state,
           lag(e.state) OVER (PARTITION BY e.equipment ORDER BY e.ts) AS prev_state
      FROM equipment_state e
      JOIN relays r USING (equipment)
     WHERE e.ts >= (now() AT TIME ZONE 'America/Denver')::date - interval '90 days'
),
changes AS (
    SELECT equipment, ts, state
      FROM raw
     WHERE prev_state IS NULL OR prev_state IS DISTINCT FROM state
),
intervals AS (
    SELECT equipment,
           ts AS start_ts,
           lead(ts) OVER (PARTITION BY equipment ORDER BY ts) AS end_ts,
           state
      FROM changes
),
runtime AS (
    SELECT (start_ts AT TIME ZONE 'America/Denver')::date AS date,
           equipment,
           sum(extract(epoch FROM (end_ts - start_ts)) / 3600.0) AS runtime_h,
           count(*) AS cycles
      FROM intervals
     WHERE state IS TRUE
       AND end_ts IS NOT NULL
       AND end_ts > start_ts
     GROUP BY 1, equipment
),
pivoted AS (
    SELECT date,
           sum(runtime_h) FILTER (WHERE equipment = 'drip_wall_fert') AS runtime_drip_wall_fert_h,
           sum(runtime_h) FILTER (WHERE equipment = 'drip_center_fert') AS runtime_drip_center_fert_h,
           sum(runtime_h) FILTER (WHERE equipment = 'mister_south_fert') AS runtime_mister_south_fert_h,
           sum(runtime_h) FILTER (WHERE equipment = 'mister_west_fert') AS runtime_mister_west_fert_h,
           sum(runtime_h) FILTER (WHERE equipment = 'fert_master_valve') AS runtime_fert_master_h,
           sum(runtime_h) FILTER (WHERE equipment IN ('drip_wall','drip_center','mister_south','mister_west','mister_center')) AS runtime_irrigation_clean_h,
           sum(runtime_h) FILTER (WHERE equipment IN ('drip_wall_fert','drip_center_fert','mister_south_fert','mister_west_fert')) AS runtime_irrigation_fert_h,
           sum(cycles) FILTER (WHERE equipment = 'drip_wall_fert') AS cycles_drip_wall_fert,
           sum(cycles) FILTER (WHERE equipment = 'drip_center_fert') AS cycles_drip_center_fert,
           sum(cycles) FILTER (WHERE equipment = 'mister_south_fert') AS cycles_mister_south_fert,
           sum(cycles) FILTER (WHERE equipment = 'mister_west_fert') AS cycles_mister_west_fert,
           sum(cycles) FILTER (WHERE equipment = 'fert_master_valve') AS cycles_fert_master
      FROM runtime
     GROUP BY date
),
dates AS (
    SELECT ds.date
      FROM daily_summary ds
     WHERE ds.date >= (now() AT TIME ZONE 'America/Denver')::date - interval '90 days'
)
UPDATE daily_summary ds
   SET runtime_drip_wall_fert_h = round(COALESCE(p.runtime_drip_wall_fert_h, 0)::numeric, 3)::double precision,
       runtime_drip_center_fert_h = round(COALESCE(p.runtime_drip_center_fert_h, 0)::numeric, 3)::double precision,
       runtime_mister_south_fert_h = round(COALESCE(p.runtime_mister_south_fert_h, 0)::numeric, 3)::double precision,
       runtime_mister_west_fert_h = round(COALESCE(p.runtime_mister_west_fert_h, 0)::numeric, 3)::double precision,
       runtime_fert_master_h = round(COALESCE(p.runtime_fert_master_h, 0)::numeric, 3)::double precision,
       runtime_irrigation_clean_h = round(COALESCE(p.runtime_irrigation_clean_h, 0)::numeric, 3)::double precision,
       runtime_irrigation_fert_h = round(COALESCE(p.runtime_irrigation_fert_h, 0)::numeric, 3)::double precision,
       runtime_irrigation_total_h = round((COALESCE(p.runtime_irrigation_clean_h, 0) + COALESCE(p.runtime_irrigation_fert_h, 0))::numeric, 3)::double precision,
       cycles_drip_wall_fert = COALESCE(p.cycles_drip_wall_fert, 0)::integer,
       cycles_drip_center_fert = COALESCE(p.cycles_drip_center_fert, 0)::integer,
       cycles_mister_south_fert = COALESCE(p.cycles_mister_south_fert, 0)::integer,
       cycles_mister_west_fert = COALESCE(p.cycles_mister_west_fert, 0)::integer,
       cycles_fert_master = COALESCE(p.cycles_fert_master, 0)::integer,
       irrigation_water_gal = COALESCE(ipd.meter_delta_gal, 0),
       fertigation_water_gal = COALESCE(ipd.meter_delta_gal, 0)
  FROM dates d
  LEFT JOIN pivoted p ON p.date = d.date
  LEFT JOIN v_irrigation_program_daily ipd ON ipd.date = d.date
 WHERE ds.date = d.date;

CREATE OR REPLACE VIEW v_irrigation_accountability AS
SELECT
    day AS date,
    zone_path AS zone,
    count(*) AS events,
    round(sum(total_duration_min)::numeric, 1) AS runtime_min,
    round(sum(COALESCE(meter_delta_gal, 0))::numeric, 2) AS volume_gal,
    count(*) FILTER (WHERE meter_delta_gal IS NULL) AS missing_volume_events,
    0::bigint AS weather_skip_events,
    count(*) AS fertigation_events,
    max(run_start) AS latest_event
  FROM v_irrigation_fertigation_runs
 GROUP BY day, zone_path
 ORDER BY date DESC, zone;

COMMENT ON VIEW v_irrigation_accountability IS
'Compatibility accountability view backed by equipment-derived fertigation runs instead of retired irrigation_log rows.';

CREATE OR REPLACE VIEW v_water_budget AS
SELECT
    ds.date,
    ds.water_used_gal AS total_gal,
    ds.mister_water_gal AS mister_gal,
    COALESCE(ipd.meter_delta_gal, ds.irrigation_water_gal, drip.drip_runtime_gal, 0) AS drip_gal,
    ds.water_used_gal
      - COALESCE(ds.mister_water_gal, 0)
      - COALESCE(ipd.meter_delta_gal, ds.irrigation_water_gal, drip.drip_runtime_gal, 0) AS unaccounted_gal,
    CASE WHEN ds.stress_hours_vpd_high > 0
      THEN ROUND((COALESCE(ds.mister_water_gal, 0) / ds.stress_hours_vpd_high)::numeric, 1)
    END AS gal_per_vpd_stress_hour,
    COALESCE(ipd.meter_delta_gal, ds.fertigation_water_gal, 0) AS fertigation_gal,
    COALESCE(ds.runtime_irrigation_clean_h, drip.clean_runtime_h, 0) AS clean_runtime_h,
    COALESCE(ds.runtime_irrigation_fert_h, drip.fert_runtime_h, 0) AS fert_runtime_h,
    COALESCE(ds.runtime_fert_master_h, 0) AS fert_master_runtime_h
  FROM daily_summary ds
  LEFT JOIN v_irrigation_program_daily ipd ON ipd.date = ds.date
  LEFT JOIN LATERAL (
      SELECT
          (COALESCE(ds.runtime_drip_wall_h, 0) + COALESCE(ds.runtime_drip_center_h, 0)) * 60 * 2.0
            + (
                COALESCE(ds.runtime_drip_wall_fert_h, 0)
                + COALESCE(ds.runtime_drip_center_fert_h, 0)
                + COALESCE(ds.runtime_mister_south_fert_h, 0)
                + COALESCE(ds.runtime_mister_west_fert_h, 0)
              ) * 60 * 2.0 AS drip_runtime_gal,
          COALESCE(ds.runtime_drip_wall_h, 0)
            + COALESCE(ds.runtime_drip_center_h, 0)
            + COALESCE(ds.runtime_mister_south_h, 0)
            + COALESCE(ds.runtime_mister_west_h, 0)
            + COALESCE(ds.runtime_mister_center_h, 0) AS clean_runtime_h,
          COALESCE(ds.runtime_drip_wall_fert_h, 0)
            + COALESCE(ds.runtime_drip_center_fert_h, 0)
            + COALESCE(ds.runtime_mister_south_fert_h, 0)
            + COALESCE(ds.runtime_mister_west_fert_h, 0) AS fert_runtime_h
  ) drip ON true
 WHERE ds.water_used_gal IS NOT NULL AND ds.water_used_gal > 0;

COMMENT ON VIEW v_water_budget IS
'Daily water decomposition including equipment-derived fertigation gallons and fert/master relay runtime.';

CREATE OR REPLACE VIEW v_data_trust_ledger AS
SELECT 'climate_freshness' AS check_name,
       CASE WHEN age_s <= 300 THEN 'ok' ELSE 'fail' END AS status,
       age_s::numeric AS metric_value,
       300::numeric AS threshold_value,
       source || ' age seconds' AS details
FROM v_data_pipeline_health
WHERE source = 'climate'
UNION ALL
SELECT 'forecast_freshness',
       CASE WHEN age_s <= 21600 THEN 'ok' ELSE 'fail' END,
       age_s::numeric,
       21600::numeric,
       'weather_forecast fetched_at age seconds'
FROM v_data_pipeline_health
WHERE source = 'forecast'
UNION ALL
SELECT 'required_sensor_coverage',
       CASE WHEN count(*) FILTER (WHERE coverage_status <> 'ok') = 0 THEN 'ok' ELSE 'warn' END,
       count(*) FILTER (WHERE coverage_status <> 'ok')::numeric,
       0::numeric,
       'required configured sensors not ok'
FROM v_required_sensor_coverage
UNION ALL
SELECT 'alert_lifecycle_mismatch',
       CASE WHEN count(*) = 0 THEN 'ok' ELSE 'warn' END,
       count(*)::numeric,
       0::numeric,
       'alerts with resolved_at set but disposition not resolved'
FROM alert_log
WHERE resolved_at IS NOT NULL
  AND disposition <> 'resolved'
UNION ALL
SELECT 'open_critical_or_high_alerts',
       CASE WHEN count(*) = 0 THEN 'ok' ELSE 'fail' END,
       count(*)::numeric,
       0::numeric,
       'open critical/high alerts'
FROM alert_log
WHERE disposition = 'open'
  AND severity IN ('critical', 'high')
UNION ALL
SELECT 'planner_trigger_sla_36h',
       CASE
         WHEN required_failure_count > 0 THEN 'fail'
         WHEN missed_expected_count > 0 OR overdue_delivered_count > 0 THEN 'warn'
         ELSE 'ok'
       END,
       (required_failure_count + missed_expected_count + overdue_delivered_count)::numeric,
       0::numeric,
       'unrecovered required planner trigger failures or currently overdue expected/delivered triggers in last 36h'
FROM v_planner_trigger_health
UNION ALL
SELECT 'data_gap_hours_24h',
       CASE WHEN COALESCE(sum(duration_s), 0) = 0 THEN 'ok' ELSE 'warn' END,
       round((COALESCE(sum(duration_s), 0) / 3600.0)::numeric, 2),
       0::numeric,
       'telemetry gap hours ending in the last 24h'
FROM data_gaps
WHERE end_ts > now() - interval '24 hours'
UNION ALL
SELECT 'water_accounting_14d',
       CASE WHEN count(*) FILTER (WHERE quality_flag IN ('missing_total','negative_total','mister_exceeds_total','negative_unaccounted')) = 0 THEN 'ok' ELSE 'warn' END,
       count(*) FILTER (WHERE quality_flag IN ('missing_total','negative_total','mister_exceeds_total','negative_unaccounted'))::numeric,
       0::numeric,
       'hard water-accounting failures in last 14 local days; unattributed water remains an instrumentation limitation'
FROM v_water_accountability
WHERE date >= (now() AT TIME ZONE 'America/Denver')::date - 14
UNION ALL
SELECT 'irrigation_logging_14d',
       CASE WHEN expected.starts_14d = 0 OR logged.logs_14d >= expected.starts_14d THEN 'ok' ELSE 'warn' END,
       GREATEST(expected.starts_14d - logged.logs_14d, 0)::numeric,
       0::numeric,
       'fertigation starts in equipment_state without canonical run rows in last 14 days'
FROM (
    WITH ordered AS (
        SELECT
            ts,
            equipment,
            state,
            lag(state) OVER (PARTITION BY equipment ORDER BY ts) AS prev_state
        FROM equipment_state
        WHERE equipment IN ('drip_wall_fert', 'mister_south_fert', 'mister_west_fert', 'drip_center_fert')
          AND ts >= now() - interval '14 days'
    )
    SELECT count(*) AS starts_14d
    FROM ordered
    WHERE state = true
      AND COALESCE(prev_state, false) = false
) expected
CROSS JOIN (
    SELECT count(DISTINCT run_id) AS logs_14d
    FROM v_irrigation_fertigation_runs
    WHERE run_start >= now() - interval '14 days'
) logged
UNION ALL
SELECT 'energy_reconciliation_14d',
       CASE WHEN count(*) FILTER (WHERE quality_flag <> 'ok') = 0 THEN 'ok' ELSE 'warn' END,
       count(*) FILTER (WHERE quality_flag <> 'ok')::numeric,
       0::numeric,
       'daily_summary measured kWh sync mismatches in last 14 local days'
FROM v_energy_estimate_reconciliation
WHERE date >= (now() AT TIME ZONE 'America/Denver')::date - 14
UNION ALL
SELECT 'forecast_action_outcomes_7d',
       CASE WHEN count(*) FILTER (WHERE outcome IS NULL OR outcome = 'pending') = 0 THEN 'ok' ELSE 'warn' END,
       count(*) FILTER (WHERE outcome IS NULL OR outcome = 'pending')::numeric,
       0::numeric,
       'forecast action rows past follow-up window without evaluated outcome in last 7 days'
FROM forecast_action_log
WHERE triggered_at > now() - interval '7 days'
  AND triggered_at <= now() - interval '6 hours'
  AND action_taken <> 'evaluated_ok'
UNION ALL
SELECT 'crop_lifecycle_completeness',
       CASE WHEN sum(missing_count) FILTER (WHERE is_active) = 0 THEN 'ok' ELSE 'warn' END,
       COALESCE(sum(missing_count) FILTER (WHERE is_active), 0)::numeric,
       0::numeric,
       'missing active crop lifecycle fields'
FROM v_crop_lifecycle_completeness
UNION ALL
SELECT 'daily_plan_archive_self_check',
       CASE WHEN count(*) FILTER (WHERE stale) = 0 THEN 'ok' ELSE 'warn' END,
       count(*) FILTER (WHERE stale)::numeric,
       0::numeric,
       'completed generated daily plan pages stale or unaudited'
FROM v_daily_plan_archive_self_check
WHERE date >= (now() AT TIME ZONE 'America/Denver')::date - 14
  AND date < (now() AT TIME ZONE 'America/Denver')::date;

COMMENT ON VIEW v_data_trust_ledger IS
'Owner-facing public health checks spanning freshness, coverage, gaps, recovered planner trigger SLA, water hard failures, canonical fertigation logging, measured energy sync, forecasts, crop completeness, and generated archives. Future instrumentation requirements are reported separately from live-system health.';
