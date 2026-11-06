 WITH intervals AS (
         WITH checked AS (
                 SELECT e.ts,
                    e.greenhouse_id,
                    e.watts_total,
                    e.ch0_source_ts,
                    e.ch1_source_ts,
                    COALESCE((e.measurement_revision = ANY (ARRAY['ha_shelly_power_v1'::text, 'shelly_modbus_source_v2'::text])) AND e.ch0_quality = 'ok'::text AND e.ch1_quality = 'ok'::text AND e.ch0_entity_id = 'sensor.shellyproem50_ac15186daafc_energy_meter_0_power'::text AND e.ch1_entity_id = 'sensor.shellyproem50_ac15186daafc_energy_meter_1_power'::text AND e.ch0_source_ts <= e.ts AND e.ch1_source_ts <= e.ts AND e.ch0_source_ts > (e.ts - '00:05:00'::interval) AND e.ch1_source_ts > (e.ts - '00:05:00'::interval) AND e.watts_total > '-Infinity'::double precision AND e.watts_total < 'Infinity'::double precision AND e.ch0_power_w > '-Infinity'::double precision AND e.ch0_power_w < 'Infinity'::double precision AND e.ch1_power_w > '-Infinity'::double precision AND e.ch1_power_w < 'Infinity'::double precision AND abs(e.watts_total::numeric - e.ch0_power_w::numeric - e.ch1_power_w::numeric) <= (GREATEST(abs(e.watts_total::numeric), abs(e.ch0_power_w::numeric), abs(e.ch1_power_w::numeric), 1::numeric) * 0.00000000000001), false) AS qualified
                   FROM energy e
                  WHERE isfinite(e.ts)
                ), grouped AS (
                 SELECT COALESCE(checked.greenhouse_id, 'vallery'::text) AS greenhouse_id,
                    checked.ts,
                    count(*) = 1 AND bool_and(checked.qualified) AS qualified,
                    min(checked.watts_total) AS watts_total,
                    min(LEAST(checked.ch0_source_ts, checked.ch1_source_ts)) AS source_through_ts
                   FROM checked
                  GROUP BY (COALESCE(checked.greenhouse_id, 'vallery'::text)), checked.ts
                ), sequenced AS (
                 SELECT grouped.greenhouse_id,
                    grouped.ts,
                    grouped.qualified,
                    grouped.watts_total,
                    grouped.source_through_ts,
                    lead(grouped.ts) OVER (PARTITION BY grouped.greenhouse_id ORDER BY grouped.ts) AS next_ts
                   FROM grouped
                )
         SELECT sequenced.greenhouse_id,
            sequenced.ts,
            sequenced.qualified,
            sequenced.watts_total,
            sequenced.source_through_ts,
                CASE
                    WHEN sequenced.qualified THEN GREATEST(sequenced.ts, LEAST(COALESCE(sequenced.next_ts, sequenced.ts), sequenced.ts + '00:05:00'::interval, sequenced.source_through_ts + '00:05:00'::interval))
                    ELSE sequenced.ts
                END AS end_ts
           FROM sequenced
        ), split AS (
         SELECT i.greenhouse_id,
            i.ts,
            i.qualified,
            i.watts_total,
            i.source_through_ts,
            i.end_ts,
            d.local_day::date AS local_date,
            (d.local_day AT TIME ZONE 'America/Denver'::text) AS day_start,
            ((d.local_day + '1 day'::interval) AT TIME ZONE 'America/Denver'::text) AS day_end
           FROM intervals i
             CROSS JOIN LATERAL generate_series((i.ts AT TIME ZONE 'America/Denver'::text)::date::timestamp without time zone, (i.end_ts AT TIME ZONE 'America/Denver'::text)::date::timestamp without time zone, '1 day'::interval) d(local_day)
        ), durations AS (
         SELECT split.greenhouse_id,
            split.ts,
            split.qualified,
            split.watts_total,
            split.source_through_ts,
            split.end_ts,
            split.local_date,
            split.day_start,
            split.day_end,
            GREATEST(EXTRACT(epoch FROM LEAST(split.end_ts, split.day_end) - GREATEST(split.ts, split.day_start)), 0::numeric) AS observed_seconds
           FROM split
          WHERE split.ts < split.day_end AND (split.end_ts > split.day_start OR split.ts = split.end_ts)
        )
 SELECT local_date AS date,
    round(sum(watts_total::numeric * observed_seconds / 3600000::numeric) FILTER (WHERE observed_seconds > 0::numeric), 3) AS measured_kwh,
    round(sum(watts_total::numeric * observed_seconds) FILTER (WHERE observed_seconds > 0::numeric) / NULLIF(sum(observed_seconds), 0::numeric), 1) AS avg_watts,
    round(max(watts_total::numeric) FILTER (WHERE observed_seconds > 0::numeric), 1) AS peak_watts,
    count(*) FILTER (WHERE observed_seconds > 0::numeric) AS sample_count,
    round(sum(observed_seconds) / 3600::numeric, 3)::double precision AS observed_hours,
    round(100::numeric * sum(observed_seconds) / EXTRACT(epoch FROM day_end - day_start), 1)::double precision AS meter_coverage_pct,
    'partial_shelly_two_channels'::text AS measured_scope,
        CASE
            WHEN sum(observed_seconds) = 0::numeric THEN 'unverified_source'::text
            WHEN local_date >= (now() AT TIME ZONE 'America/Denver'::text)::date THEN 'partial_day'::text
            WHEN (sum(observed_seconds) / EXTRACT(epoch FROM day_end - day_start)) < 0.9 THEN 'low_coverage'::text
            ELSE 'uncommissioned'::text
        END AS measured_quality,
    false AS available_for_scoring,
    greenhouse_id
   FROM durations
  GROUP BY local_date, day_start, day_end, greenhouse_id
  ORDER BY local_date;
