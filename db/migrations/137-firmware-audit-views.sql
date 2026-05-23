-- 137-firmware-audit-views.sql
--
-- Read-only firmware audit rollups used after replay and during post-OTA
-- validation. These views make the weekly audit repeatable without ad hoc SQL.

CREATE OR REPLACE VIEW public.v_fan_balance_7d AS
WITH bounds AS (
    SELECT now() - interval '7 days' AS start_ts,
           now() AS end_ts
),
fan_set AS (
    SELECT DISTINCT es.greenhouse_id, es.equipment
      FROM public.equipment_state es
     WHERE es.equipment IN ('fan1', 'fan2')
),
seed AS (
    SELECT DISTINCT ON (fs.greenhouse_id, fs.equipment)
           fs.greenhouse_id,
           fs.equipment,
           b.start_ts AS ts,
           COALESCE(es.state, false) AS state
      FROM fan_set fs
      CROSS JOIN bounds b
      LEFT JOIN public.equipment_state es
        ON es.greenhouse_id = fs.greenhouse_id
       AND es.equipment = fs.equipment
       AND es.ts <= b.start_ts
     ORDER BY fs.greenhouse_id, fs.equipment, es.ts DESC NULLS LAST
),
events AS (
    SELECT greenhouse_id, equipment, ts, state FROM seed
    UNION ALL
    SELECT es.greenhouse_id, es.equipment, es.ts, es.state
      FROM public.equipment_state es
      CROSS JOIN bounds b
     WHERE es.equipment IN ('fan1', 'fan2')
       AND es.ts > b.start_ts
       AND es.ts <= b.end_ts
),
ordered AS (
    SELECT e.*,
           lag(e.state) OVER (PARTITION BY e.greenhouse_id, e.equipment ORDER BY e.ts) AS prev_state,
           lead(e.ts) OVER (PARTITION BY e.greenhouse_id, e.equipment ORDER BY e.ts) AS next_ts
      FROM events e
),
rollup AS (
    SELECT o.greenhouse_id,
           o.equipment,
           sum(EXTRACT(epoch FROM least(COALESCE(o.next_ts, b.end_ts), b.end_ts)
                            - greatest(o.ts, b.start_ts)) / 60.0)
               FILTER (WHERE o.state IS TRUE) AS on_minutes,
           count(*) FILTER (
               WHERE o.state IS TRUE
                 AND COALESCE(o.prev_state, false) IS FALSE
                 AND o.ts > b.start_ts
           ) AS cycles
      FROM ordered o
      CROSS JOIN bounds b
     WHERE o.ts < b.end_ts
       AND COALESCE(o.next_ts, b.end_ts) > b.start_ts
     GROUP BY o.greenhouse_id, o.equipment
)
SELECT COALESCE(f1.greenhouse_id, f2.greenhouse_id) AS greenhouse_id,
       b.start_ts AS window_start,
       b.end_ts AS window_end,
       round(COALESCE(f1.on_minutes, 0)::numeric, 1) AS fan1_minutes,
       round(COALESCE(f2.on_minutes, 0)::numeric, 1) AS fan2_minutes,
       COALESCE(f1.cycles, 0) AS fan1_cycles,
       COALESCE(f2.cycles, 0) AS fan2_cycles,
       round(abs(COALESCE(f1.on_minutes, 0) - COALESCE(f2.on_minutes, 0))::numeric, 1) AS imbalance_minutes,
       round(
           (100.0 * abs(COALESCE(f1.on_minutes, 0) - COALESCE(f2.on_minutes, 0))
            / NULLIF(greatest(COALESCE(f1.on_minutes, 0), COALESCE(f2.on_minutes, 0)), 0))::numeric,
           1
       ) AS imbalance_pct,
       CASE
           WHEN COALESCE(f1.on_minutes, 0) <= COALESCE(f2.on_minutes, 0) THEN 'fan1'
           ELSE 'fan2'
       END AS lower_runtime_fan,
       abs(COALESCE(f1.on_minutes, 0) - COALESCE(f2.on_minutes, 0)) > 10.0 AS rebalance_needed
  FROM bounds b
  LEFT JOIN rollup f1 ON f1.equipment = 'fan1'
  LEFT JOIN rollup f2 ON f2.equipment = 'fan2'
                    AND f2.greenhouse_id = f1.greenhouse_id;

COMMENT ON VIEW public.v_fan_balance_7d IS
'Rolling seven-day fan runtime/cycle balance from equipment_state, clipped at the exact window bounds. Used to audit runtime-aware lead selection.';

CREATE OR REPLACE VIEW public.v_heat_in_band_7d AS
WITH samples AS (
    SELECT bt.*,
           lead(bt.ts) OVER (PARTITION BY bt.greenhouse_id ORDER BY bt.ts) AS next_ts,
           public.fn_equip_at('heat1', bt.ts) AS heat1_on,
           public.fn_equip_at('heat2', bt.ts) AS heat2_on
      FROM public.fn_band_timeline(now() - interval '7 days', now(), interval '5 minutes', 'vallery') bt
     WHERE bt.timeline_phase = 'actual'
),
classified AS (
    SELECT s.*,
           greatest(0.0, least(EXTRACT(epoch FROM COALESCE(s.next_ts, now()) - s.ts), 300.0)) / 60.0 AS sample_minutes,
           s.indoor_temp_f >= s.firmware_temp_low
               AND s.indoor_temp_f <= s.firmware_temp_high AS temp_in_band,
           s.indoor_temp_f < s.firmware_temp_low AS temp_below_band,
           s.indoor_temp_f > s.firmware_temp_high AS temp_above_band
      FROM samples s
     WHERE s.indoor_temp_f IS NOT NULL
       AND s.firmware_temp_low IS NOT NULL
       AND s.firmware_temp_high IS NOT NULL
)
SELECT greenhouse_id,
       min(ts) AS window_start,
       max(ts) AS window_end,
       count(*) AS samples,
       round(sum(sample_minutes) FILTER (WHERE heat1_on)::numeric, 1) AS heat1_minutes,
       round(sum(sample_minutes) FILTER (WHERE heat2_on)::numeric, 1) AS heat2_minutes,
       round(sum(sample_minutes) FILTER (WHERE heat1_on AND temp_in_band)::numeric, 1) AS heat1_in_band_minutes,
       round(sum(sample_minutes) FILTER (WHERE heat2_on AND temp_in_band)::numeric, 1) AS heat2_in_band_minutes,
       round(sum(sample_minutes) FILTER (WHERE heat1_on AND temp_below_band)::numeric, 1) AS heat1_below_band_minutes,
       round(sum(sample_minutes) FILTER (WHERE heat2_on AND temp_below_band)::numeric, 1) AS heat2_below_band_minutes,
       round(sum(sample_minutes) FILTER (WHERE (heat1_on OR heat2_on) AND temp_above_band)::numeric, 1) AS heat_above_band_minutes,
       round((100.0 * sum(sample_minutes) FILTER (WHERE (heat1_on OR heat2_on) AND temp_in_band)
              / NULLIF(sum(sample_minutes) FILTER (WHERE heat1_on OR heat2_on), 0))::numeric, 1) AS heat_runtime_in_band_pct
  FROM classified
 GROUP BY greenhouse_id;

COMMENT ON VIEW public.v_heat_in_band_7d IS
'Rolling seven-day sampled heat runtime classified against fn_band_timeline crop-band thresholds. Highlights resource use while already inside band.';

CREATE OR REPLACE VIEW public.v_setpoint_effective_drift_7d AS
WITH ranked AS (
    SELECT c.*,
           row_number() OVER (PARTITION BY c.greenhouse_id, c.parameter ORDER BY c.ts DESC) AS rn
      FROM public.setpoint_clamps c
     WHERE c.ts > now() - interval '7 days'
)
SELECT greenhouse_id,
       parameter,
       count(*) AS drift_events,
       count(*) FILTER (WHERE status = 'guardrailed') AS guardrailed_events,
       count(*) FILTER (WHERE status = 'held_by_guardrail') AS held_events,
       count(*) FILTER (WHERE status = 'rejected') AS rejected_events,
       round(avg(abs(requested - applied))::numeric, 3) AS avg_abs_delta,
       round(max(abs(requested - applied))::numeric, 3) AS max_abs_delta,
       min(ts) AS first_seen,
       max(ts) AS last_seen,
       max(requested) FILTER (WHERE rn = 1) AS latest_requested,
       max(applied) FILTER (WHERE rn = 1) AS latest_effective,
       max(reason) FILTER (WHERE rn = 1) AS latest_reason,
       max(status) FILTER (WHERE rn = 1) AS latest_status,
       max(plan_id) FILTER (WHERE rn = 1) AS latest_plan_id
  FROM ranked
 GROUP BY greenhouse_id, parameter
 ORDER BY count(*) DESC, parameter;

COMMENT ON VIEW public.v_setpoint_effective_drift_7d IS
'Rolling seven-day planner requested vs dispatcher effective/applied drift from setpoint_clamps. Used to find hidden clamp or retired-param pressure.';

CREATE OR REPLACE VIEW public.v_vent_mist_assist_7d AS
WITH bounds AS (
    SELECT now() - interval '7 days' AS start_ts,
           now() AS end_ts
),
diag AS (
    SELECT d.greenhouse_id,
           d.ts,
           d.vent_mist_assist_active,
           lead(d.ts) OVER (PARTITION BY d.greenhouse_id ORDER BY d.ts) AS next_ts
      FROM public.diagnostics d
      CROSS JOIN bounds b
     WHERE d.ts > b.start_ts - interval '10 minutes'
       AND d.ts <= b.end_ts
),
assist AS (
    SELECT d.greenhouse_id,
           greatest(d.ts, b.start_ts) AS ts,
           least(COALESCE(d.next_ts, b.end_ts), b.end_ts) AS next_ts
      FROM diag d
      CROSS JOIN bounds b
     WHERE d.vent_mist_assist_active = 1
       AND d.ts < b.end_ts
       AND COALESCE(d.next_ts, b.end_ts) > b.start_ts
),
enriched AS (
    SELECT a.greenhouse_id,
           a.ts,
           EXTRACT(epoch FROM a.next_ts - a.ts) / 60.0 AS minutes,
           COALESCE(state.value, 'unknown') AS greenhouse_state,
           COALESCE(reason.value, 'vent_mist_assist_active') AS mode_reason,
           public.fn_equip_at('vent', a.ts) AS vent_on,
           public.fn_equip_at('fog', a.ts) AS fog_on,
           public.fn_equip_at('mister_south', a.ts)
               OR public.fn_equip_at('mister_west', a.ts)
               OR public.fn_equip_at('mister_center', a.ts) AS any_mister_on,
           c.temp_avg,
           c.vpd_avg,
           c.outdoor_temp_f,
           c.outdoor_rh_pct
      FROM assist a
      LEFT JOIN LATERAL (
          SELECT ss.value
            FROM public.system_state ss
           WHERE ss.greenhouse_id = a.greenhouse_id
             AND ss.entity = 'greenhouse_state'
             AND ss.ts <= a.ts
           ORDER BY ss.ts DESC
           LIMIT 1
      ) state ON true
      LEFT JOIN LATERAL (
          SELECT ss.value
            FROM public.system_state ss
           WHERE ss.greenhouse_id = a.greenhouse_id
             AND ss.entity = 'mode_reason'
             AND ss.ts <= a.ts
           ORDER BY ss.ts DESC
           LIMIT 1
      ) reason ON true
      LEFT JOIN LATERAL (
          SELECT c.temp_avg, c.vpd_avg, c.outdoor_temp_f, c.outdoor_rh_pct
            FROM public.climate c
           WHERE c.greenhouse_id = a.greenhouse_id
             AND c.ts <= a.ts
           ORDER BY c.ts DESC
           LIMIT 1
      ) c ON true
     WHERE a.next_ts > a.ts
)
SELECT greenhouse_id,
       greenhouse_state,
       mode_reason,
       count(*) AS samples,
       round(sum(minutes)::numeric, 1) AS assist_minutes,
       round(sum(minutes) FILTER (WHERE vent_on)::numeric, 1) AS vent_open_minutes,
       round(sum(minutes) FILTER (WHERE fog_on)::numeric, 1) AS fog_minutes,
       round(sum(minutes) FILTER (WHERE any_mister_on)::numeric, 1) AS mister_minutes,
       round(avg(temp_avg)::numeric, 1) AS avg_temp_f,
       round(avg(vpd_avg)::numeric, 2) AS avg_vpd_kpa,
       round(avg(outdoor_temp_f)::numeric, 1) AS avg_outdoor_temp_f,
       round(avg(outdoor_rh_pct)::numeric, 1) AS avg_outdoor_rh_pct
  FROM enriched
 GROUP BY greenhouse_id, greenhouse_state, mode_reason
 ORDER BY assist_minutes DESC;

COMMENT ON VIEW public.v_vent_mist_assist_7d IS
'Rolling seven-day open-vent moisture assist runtime by controller state/reason, using diagnostics.vent_mist_assist_active and equipment_state.';

CREATE OR REPLACE VIEW public.v_mister_fairness_7d AS
WITH bounds AS (
    SELECT now() - interval '7 days' AS start_ts,
           now() AS end_ts
),
zone_map AS (
    SELECT * FROM (VALUES
        ('mister_south'::text, 'south'::text),
        ('mister_west'::text, 'west'::text),
        ('mister_center'::text, 'center'::text)
    ) AS z(equipment, zone)
),
mister_set AS (
    SELECT DISTINCT es.greenhouse_id, z.equipment, z.zone
      FROM public.equipment_state es
      JOIN zone_map z ON z.equipment = es.equipment
),
seed AS (
    SELECT DISTINCT ON (ms.greenhouse_id, ms.equipment)
           ms.greenhouse_id,
           ms.equipment,
           ms.zone,
           b.start_ts AS ts,
           COALESCE(es.state, false) AS state
      FROM mister_set ms
      CROSS JOIN bounds b
      LEFT JOIN public.equipment_state es
        ON es.greenhouse_id = ms.greenhouse_id
       AND es.equipment = ms.equipment
       AND es.ts <= b.start_ts
     ORDER BY ms.greenhouse_id, ms.equipment, es.ts DESC NULLS LAST
),
events AS (
    SELECT greenhouse_id, equipment, zone, ts, state FROM seed
    UNION ALL
    SELECT es.greenhouse_id, es.equipment, z.zone, es.ts, es.state
      FROM public.equipment_state es
      JOIN zone_map z ON z.equipment = es.equipment
      CROSS JOIN bounds b
     WHERE es.ts > b.start_ts
       AND es.ts <= b.end_ts
),
ordered AS (
    SELECT e.*,
           lag(e.state) OVER (PARTITION BY e.greenhouse_id, e.equipment ORDER BY e.ts) AS prev_state,
           lead(e.ts) OVER (PARTITION BY e.greenhouse_id, e.equipment ORDER BY e.ts) AS next_ts
      FROM events e
),
rollup AS (
    SELECT o.greenhouse_id,
           o.equipment,
           o.zone,
           sum(EXTRACT(epoch FROM least(COALESCE(o.next_ts, b.end_ts), b.end_ts)
                            - greatest(o.ts, b.start_ts)) / 60.0)
               FILTER (WHERE o.state IS TRUE) AS runtime_min,
           count(*) FILTER (
               WHERE o.state IS TRUE
                 AND COALESCE(o.prev_state, false) IS FALSE
                 AND o.ts > b.start_ts
           ) AS cycles
      FROM ordered o
      CROSS JOIN bounds b
     WHERE o.ts < b.end_ts
       AND COALESCE(o.next_ts, b.end_ts) > b.start_ts
     GROUP BY o.greenhouse_id, o.equipment, o.zone
),
totals AS (
    SELECT greenhouse_id,
           sum(COALESCE(runtime_min, 0)) AS total_runtime_min,
           sum(COALESCE(cycles, 0)) AS total_cycles
      FROM rollup
     GROUP BY greenhouse_id
),
effect AS (
    SELECT 'vallery'::text AS greenhouse_id,
           zone,
           count(*) AS effect_samples,
           round(avg(zone_vpd_delta)::numeric, 3) AS avg_vpd_delta_kpa
      FROM public.v_mister_zone_effectiveness
     WHERE on_ts > now() - interval '7 days'
     GROUP BY greenhouse_id, zone
),
fairness AS (
    SELECT greenhouse_id,
           COALESCE(sum(mister_fairness_overrides_today), 0) AS fairness_overrides
      FROM public.daily_summary
     WHERE date >= ((now() AT TIME ZONE 'America/Denver')::date - 6)
     GROUP BY greenhouse_id
)
SELECT r.greenhouse_id,
       r.zone,
       r.equipment,
       round(COALESCE(r.runtime_min, 0)::numeric, 1) AS runtime_minutes,
       COALESCE(r.cycles, 0) AS cycles,
       round((100.0 * COALESCE(r.runtime_min, 0) / NULLIF(t.total_runtime_min, 0))::numeric, 1) AS runtime_share_pct,
       round((100.0 * COALESCE(r.cycles, 0) / NULLIF(t.total_cycles, 0))::numeric, 1) AS cycle_share_pct,
       e.effect_samples,
       e.avg_vpd_delta_kpa,
       COALESCE(f.fairness_overrides, 0) AS fairness_overrides_7d
  FROM rollup r
  JOIN totals t ON t.greenhouse_id = r.greenhouse_id
  LEFT JOIN effect e ON e.greenhouse_id = r.greenhouse_id AND e.zone = r.zone
  LEFT JOIN fairness f ON f.greenhouse_id = r.greenhouse_id
 ORDER BY r.greenhouse_id, r.zone;

COMMENT ON VIEW public.v_mister_fairness_7d IS
'Rolling seven-day mister zone runtime, cycle share, effectiveness, and firmware fairness override count for resource-balance audits.';
