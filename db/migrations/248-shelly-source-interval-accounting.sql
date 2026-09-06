-- #781: forward-only source-qualified partial power diagnostics.
-- This changes the ordinary-role boundary. The seven-file C0 transition does
-- NOT authorize it; require a separate reviewed exact-state transition before
-- delivery. No receipt refresh, calibration, physical or scientific approval.
SET LOCAL search_path = pg_catalog, public, pg_temp;
LOCK TABLE public.energy IN ACCESS EXCLUSIVE MODE;

DO $predecessor$
DECLARE
    owner_oid oid;
    relation_name text;
BEGIN
    SELECT datdba INTO owner_oid FROM pg_database WHERE datname=current_database();
    FOREACH relation_name IN ARRAY ARRAY['energy','v_runtime_energy_write',
        'v_energy_daily','v_energy_meter_health','v_energy_estimate_reconciliation'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_class WHERE oid=to_regclass('public.'||relation_name)
                       AND relowner=owner_oid) THEN
            RAISE EXCEPTION 'Shelly interval repair refuses missing or non-owner predecessor';
        END IF;
    END LOOP;
    IF regexp_replace(pg_get_viewdef('public.v_runtime_energy_write'::regclass,true),
                       '\s','','g') <>
       'SELECTts,watts_total,watts_heat,watts_fans,watts_other,kwh_todayFROMenergy;'
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='verdify_ingestor_runtime') THEN
        RAISE EXCEPTION 'Shelly interval repair refuses unknown write facade';
    END IF;
END;
$predecessor$;

ALTER TABLE public.energy
    ADD COLUMN measurement_revision text,
    ADD COLUMN ch0_power_w double precision,
    ADD COLUMN ch1_power_w double precision,
    ADD COLUMN ch0_source_ts timestamptz,
    ADD COLUMN ch1_source_ts timestamptz,
    ADD COLUMN ch0_entity_id text,
    ADD COLUMN ch1_entity_id text,
    ADD COLUMN ch0_quality text,
    ADD COLUMN ch1_quality text;

-- Append to the existing owner-sealed projection, preserving OID/old column ACLs.
CREATE OR REPLACE VIEW public.v_runtime_energy_write
WITH (security_barrier=true, security_invoker=false) AS
SELECT ts, watts_total, watts_heat, watts_fans, watts_other, kwh_today,
       measurement_revision, ch0_power_w, ch1_power_w, ch0_source_ts, ch1_source_ts,
       ch0_entity_id, ch1_entity_id, ch0_quality, ch1_quality
FROM public.energy;
GRANT INSERT (measurement_revision,ch0_power_w,ch1_power_w,ch0_source_ts,ch1_source_ts,
              ch0_entity_id,ch1_entity_id,ch0_quality,ch1_quality)
    ON public.v_runtime_energy_write TO verdify_ingestor_runtime;

-- Inline both copies into role-attested public views; no private callee or new SELECT grant.
CREATE OR REPLACE VIEW public.v_energy_daily AS
WITH intervals AS (
WITH checked AS (
    SELECT e.ts,e.greenhouse_id,e.watts_total,e.ch0_source_ts,e.ch1_source_ts,
        COALESCE(measurement_revision='ha_shelly_power_v1'
          AND ch0_quality='ok' AND ch1_quality='ok'
          AND ch0_entity_id='sensor.shellyproem50_ac15186daafc_energy_meter_0_power'
          AND ch1_entity_id='sensor.shellyproem50_ac15186daafc_energy_meter_1_power'
          AND ch0_source_ts <= ts AND ch1_source_ts <= ts
          AND ch0_source_ts > ts-interval '300 seconds'
          AND ch1_source_ts > ts-interval '300 seconds'
          AND watts_total > '-Infinity'::float8 AND watts_total < 'Infinity'::float8
          AND ch0_power_w > '-Infinity'::float8 AND ch0_power_w < 'Infinity'::float8
          AND ch1_power_w > '-Infinity'::float8 AND ch1_power_w < 'Infinity'::float8
          AND abs(watts_total::numeric-ch0_power_w::numeric-ch1_power_w::numeric)
              <= GREATEST(abs(watts_total::numeric),abs(ch0_power_w::numeric),abs(ch1_power_w::numeric),1)*1e-14,
          false) AS qualified
    FROM public.energy e
    WHERE isfinite(ts)
), grouped AS (
    -- Duplicate timestamps are ambiguous, not an arbitrary winner's interval.
    SELECT COALESCE(greenhouse_id,'vallery') AS greenhouse_id, ts,
           count(*)=1 AND bool_and(qualified) AS qualified,
           min(watts_total) AS watts_total,
           min(LEAST(ch0_source_ts,ch1_source_ts)) AS source_through_ts
    FROM checked GROUP BY COALESCE(greenhouse_id,'vallery'),ts
), sequenced AS (
    -- Keep invalid/null rows in LEAD: a missing poll is an actual boundary.
    SELECT *, lead(ts) OVER (PARTITION BY greenhouse_id ORDER BY ts) AS next_ts
    FROM grouped
)
SELECT greenhouse_id, ts, qualified, watts_total, source_through_ts,
       CASE WHEN qualified THEN GREATEST(ts, LEAST(COALESCE(next_ts,ts),
            ts+interval '300 seconds',source_through_ts+interval '300 seconds'))
            ELSE ts END AS end_ts
FROM sequenced
), split AS (
    SELECT i.*, d.local_day::date AS local_date,
        d.local_day AT TIME ZONE 'America/Denver' AS day_start,
        (d.local_day+interval '1 day') AT TIME ZONE 'America/Denver' AS day_end
    FROM intervals i
    CROSS JOIN LATERAL generate_series(
        (i.ts AT TIME ZONE 'America/Denver')::date::timestamp,
        (i.end_ts AT TIME ZONE 'America/Denver')::date::timestamp,
        interval '1 day') d(local_day)
), durations AS (
    SELECT *, GREATEST(extract(epoch FROM
        (LEAST(end_ts,day_end)-GREATEST(ts,day_start))),0) AS observed_seconds
    FROM split
    WHERE ts < day_end AND (end_ts > day_start OR ts=end_ts)
)
SELECT local_date AS date,
    round(sum(watts_total::numeric*observed_seconds/3600000)
        FILTER (WHERE observed_seconds>0),3) AS measured_kwh,
    round(sum(watts_total::numeric*observed_seconds) FILTER (WHERE observed_seconds>0)
        /NULLIF(sum(observed_seconds),0),1) AS avg_watts,
    round(max(watts_total::numeric) FILTER (WHERE observed_seconds>0),1) AS peak_watts,
    count(*) FILTER (WHERE observed_seconds>0)::bigint AS sample_count,
    round(sum(observed_seconds)/3600,3)::double precision AS observed_hours,
    round(100*sum(observed_seconds)/extract(epoch FROM (day_end-day_start)),1)::double precision
        AS meter_coverage_pct,
    'partial_shelly_two_channels'::text AS measured_scope,
    CASE WHEN sum(observed_seconds)=0 THEN 'unverified_source'
         WHEN local_date >= (now() AT TIME ZONE 'America/Denver')::date THEN 'partial_day'
         WHEN sum(observed_seconds)/extract(epoch FROM (day_end-day_start)) < 0.9 THEN 'low_coverage'
         ELSE 'uncommissioned' END AS measured_quality,
    false AS available_for_scoring,
    greenhouse_id
FROM durations
GROUP BY local_date,day_start,day_end,greenhouse_id
ORDER BY local_date;
COMMENT ON VIEW public.v_energy_daily IS
'Source-qualified HA two-channel diagnostic integration, not commissioned measurement. Denver local-day splits and actual 23/24/25-hour denominator; max 300-second source hold, no null bridging or trailing extrapolation. Missing/legacy energy remains NULL. 90% is an operational coverage label only; scoring remains false until a separately reviewed endpoint is commissioned.';

CREATE OR REPLACE VIEW public.v_energy_meter_health AS
WITH intervals AS (
WITH checked AS (
    SELECT e.ts,e.greenhouse_id,e.watts_total,e.ch0_source_ts,e.ch1_source_ts,
        COALESCE(measurement_revision='ha_shelly_power_v1'
          AND ch0_quality='ok' AND ch1_quality='ok'
          AND ch0_entity_id='sensor.shellyproem50_ac15186daafc_energy_meter_0_power'
          AND ch1_entity_id='sensor.shellyproem50_ac15186daafc_energy_meter_1_power'
          AND ch0_source_ts <= ts AND ch1_source_ts <= ts
          AND ch0_source_ts > ts-interval '300 seconds'
          AND ch1_source_ts > ts-interval '300 seconds'
          AND watts_total > '-Infinity'::float8 AND watts_total < 'Infinity'::float8
          AND ch0_power_w > '-Infinity'::float8 AND ch0_power_w < 'Infinity'::float8
          AND ch1_power_w > '-Infinity'::float8 AND ch1_power_w < 'Infinity'::float8
          AND abs(watts_total::numeric-ch0_power_w::numeric-ch1_power_w::numeric)
              <= GREATEST(abs(watts_total::numeric),abs(ch0_power_w::numeric),abs(ch1_power_w::numeric),1)*1e-14,
          false) AS qualified
    FROM public.energy e
    WHERE isfinite(ts)
), grouped AS (
    -- Duplicate timestamps are ambiguous, not an arbitrary winner's interval.
    SELECT COALESCE(greenhouse_id,'vallery') AS greenhouse_id, ts,
           count(*)=1 AND bool_and(qualified) AS qualified,
           min(watts_total) AS watts_total,
           min(LEAST(ch0_source_ts,ch1_source_ts)) AS source_through_ts
    FROM checked GROUP BY COALESCE(greenhouse_id,'vallery'),ts
), sequenced AS (
    -- Keep invalid/null rows in LEAD: a missing poll is an actual boundary.
    SELECT *, lead(ts) OVER (PARTITION BY greenhouse_id ORDER BY ts) AS next_ts
    FROM grouped
)
SELECT greenhouse_id, ts, qualified, watts_total, source_through_ts,
       CASE WHEN qualified THEN GREATEST(ts, LEAST(COALESCE(next_ts,ts),
            ts+interval '300 seconds',source_through_ts+interval '300 seconds'))
            ELSE ts END AS end_ts
FROM sequenced
), latest AS (
    SELECT greenhouse_id, max(source_through_ts) AS latest_ts,
        count(*) FILTER (WHERE source_through_ts >= now()-interval '10 minutes'
                         AND source_through_ts <= now())::bigint AS recent_sample_count
    FROM intervals WHERE qualified
    GROUP BY greenhouse_id
)
SELECT h.id AS greenhouse_id, l.latest_ts,
    round(extract(epoch FROM (now()-l.latest_ts))::numeric,1) AS sample_age_seconds,
    COALESCE(l.recent_sample_count,0)::bigint AS recent_sample_count,
    CASE WHEN l.latest_ts IS NULL THEN 'unavailable'
         WHEN l.latest_ts < now()-interval '10 minutes' THEN 'stale'
         WHEN l.latest_ts > now() THEN 'unavailable' ELSE 'fresh' END AS meter_status,
    COALESCE(l.latest_ts BETWEEN now()-interval '10 minutes' AND now(),false) AS fresh_for_observation,
    'partial_shelly_two_channels'::text AS measured_scope
FROM public.greenhouses h LEFT JOIN latest l ON l.greenhouse_id=h.id;

-- Keep every public column/type, but stop subtracting non-comparable scopes.
CREATE OR REPLACE VIEW public.v_energy_estimate_reconciliation AS
WITH days AS (
    SELECT date,greenhouse_id FROM public.v_runtime_energy_daily
    UNION SELECT date,greenhouse_id FROM public.v_energy_daily
)
SELECT d.date,r.modeled_kwh AS kwh_estimated,e.measured_kwh,
    NULL::numeric AS estimate_delta_kwh,
    CASE WHEN r.modeled_kwh IS NULL AND e.measured_kwh IS NULL THEN 'unavailable'
         WHEN r.modeled_kwh IS NULL THEN 'missing_runtime_model'
         WHEN e.measured_kwh IS NULL THEN 'missing_partial_measurement'
         ELSE 'scope_separated' END AS quality_flag,
    d.greenhouse_id,r.modeled_kwh_low,r.modeled_kwh_high,r.coefficient_revisions,
    r.modeled_scope,e.measured_scope,e.meter_coverage_pct,r.runtime_coverage_pct,
    r.model_quality,e.measured_quality,
    COALESCE(r.available_for_scoring,false) AS modeled_available_for_scoring,
    COALESCE(e.available_for_scoring,false) AS measured_available_for_scoring,
    r.runtime_evidence
FROM days d LEFT JOIN public.v_runtime_energy_daily r
    ON r.date=d.date AND r.greenhouse_id=d.greenhouse_id
LEFT JOIN public.v_energy_daily e ON e.date=d.date AND e.greenhouse_id=d.greenhouse_id;
COMMENT ON VIEW public.v_energy_estimate_reconciliation IS
'Scope-separated model and source-qualified partial measurement. Their difference is NULL because unlike scopes do not define waste or efficiency; uncommissioned measurement is scoring-ineligible.';
