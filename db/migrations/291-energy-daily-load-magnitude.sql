-- 291 (#1362): v_energy_daily integrates load magnitude, so a day is never negative; backfill daily_summary.
-- Root cause (read-only on production, 2026-11-05): no counter is involved. The view integrates the Shelly Pro EM-50's
-- signed active power, watts_total = ch0_power_w + ch1_power_w. The 1500 W electric heater (heat1) reads about
-- -1,480 W on ch1: in the last 10 days ch1 was below -1,000 W in 2,498 of 2,512 samples taken while heat1 was on,
-- and in 1 of 312 while it was off. The first negative sample (2026-09-27 10:33Z) is the first heat1 run after the
-- two-channel rows began (2026-09-26 22:43Z); heat1 has run most nights since, which is why the daily total fell
-- to about -25 kWh. The heater's conductor is measured with the opposite sign to the other loads (a reversed CT or
-- the opposite leg against the single voltage reference); other ch1 loads read positive.
-- The greenhouse circuit has no generation, so a negative channel reading is a sign error, not export. Each
-- qualified sample now counts |ch0| + |ch1|. That is a lower bound on the draw: a channel carrying the heater and an
-- opposite-sign load at once still nets them. signed_kwh keeps the old signed integral and sign_corrected_samples
-- counts the samples with a negative channel, so a corrected day is visible, not silent. Every other guard (source
-- revision, entity, quality, clock, finite, signed-total identity, 300 s hold, Denver day split, coverage label,
-- available_for_scoring=false) is unchanged. The view keeps its owner, ACL and options; the two appended columns
-- and its new text move the api and ingestor seals (the relation entry carries both), so both receipts are re-sealed.
-- The MCP seal is unchanged: no MCP role or PUBLIC holds an entry on the view.
-- daily_summary.kwh_total and peak_kw are refreshed from the view for 2026-09-27 through today, the same mapping as
-- ingestor/partial_energy.py. Only those two columns and captured_at move; the climate revision trigger ignores them.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF current_user <> 'verdify'
        OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
            AND seq=290 AND filename='db/migrations/290-band-compliance.sql'
            AND sha256='557d8d03a61a284e05e1b9bc26db7e3748898531ea7b80f0a6b4e458c922e6be'
            AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>290)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'a752a89a07b5b90ecbe6ccaf44358492aa8da6c562d608c8880d24d77088af7e'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM 'b17c9da67fe6456c1fd470ac5dc82dfdd20320b3d808c482162b11952bd57011'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login' AND boundary_sha256=decode('a752a89a07b5b90ecbe6ccaf44358492aa8da6c562d608c8880d24d77088af7e','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login' AND boundary_sha256=decode('b17c9da67fe6456c1fd470ac5dc82dfdd20320b3d808c482162b11952bd57011','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton AND boundary_sha256=decode('6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e','hex'))
        OR to_regclass('public.v_energy_daily') IS NULL
        OR encode(sha256(convert_to(pg_get_viewdef('public.v_energy_daily'::regclass,true),'UTF8')),'hex')
            IS DISTINCT FROM 'b2fe9fcd01f0098cb1b0019a8b79bdb6ae0c030682b7afb06b32a86be5ae21f5'
        OR (SELECT relowner FROM pg_class WHERE oid='public.v_energy_daily'::regclass)
            IS DISTINCT FROM (SELECT datdba FROM pg_database WHERE datname=current_database())
    THEN RAISE EXCEPTION '291 refuses an unqualified predecessor'; END IF;
END $predecessor$;

DO $view$
DECLARE
    before jsonb;
BEGIN
    SELECT jsonb_build_object('oid',c.oid,'kind',c.relkind,'owner',c.relowner,'acl',c.relacl,'options',c.reloptions)
      INTO before FROM pg_class c WHERE c.oid='public.v_energy_daily'::regclass;
    EXECUTE $ddl$
CREATE OR REPLACE VIEW public.v_energy_daily AS
 WITH intervals AS (
         WITH checked AS (
                 SELECT e.ts,
                    e.greenhouse_id,
                    e.watts_total,
                    e.ch0_power_w,
                    e.ch1_power_w,
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
                    min(abs(checked.ch0_power_w::numeric) + abs(checked.ch1_power_w::numeric)) AS load_watts,
                    bool_or(checked.ch0_power_w < 0::double precision OR checked.ch1_power_w < 0::double precision) AS sign_corrected,
                    min(LEAST(checked.ch0_source_ts, checked.ch1_source_ts)) AS source_through_ts
                   FROM checked
                  GROUP BY (COALESCE(checked.greenhouse_id, 'vallery'::text)), checked.ts
                ), sequenced AS (
                 SELECT grouped.greenhouse_id,
                    grouped.ts,
                    grouped.qualified,
                    grouped.watts_total,
                    grouped.load_watts,
                    grouped.sign_corrected,
                    grouped.source_through_ts,
                    lead(grouped.ts) OVER (PARTITION BY grouped.greenhouse_id ORDER BY grouped.ts) AS next_ts
                   FROM grouped
                )
         SELECT sequenced.greenhouse_id,
            sequenced.ts,
            sequenced.qualified,
            sequenced.watts_total,
            sequenced.load_watts,
            sequenced.sign_corrected,
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
            i.load_watts,
            i.sign_corrected,
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
            split.load_watts,
            split.sign_corrected,
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
    round(sum(load_watts * observed_seconds / 3600000::numeric) FILTER (WHERE observed_seconds > 0::numeric), 3) AS measured_kwh,
    round(sum(load_watts * observed_seconds) FILTER (WHERE observed_seconds > 0::numeric) / NULLIF(sum(observed_seconds), 0::numeric), 1) AS avg_watts,
    round(max(load_watts) FILTER (WHERE observed_seconds > 0::numeric), 1) AS peak_watts,
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
    greenhouse_id,
    round(sum(watts_total::numeric * observed_seconds / 3600000::numeric) FILTER (WHERE observed_seconds > 0::numeric), 3) AS signed_kwh,
    count(*) FILTER (WHERE observed_seconds > 0::numeric AND sign_corrected) AS sign_corrected_samples
   FROM durations
  GROUP BY local_date, day_start, day_end, greenhouse_id
  ORDER BY local_date
$ddl$;
    IF (SELECT jsonb_build_object('oid',c.oid,'kind',c.relkind,'owner',c.relowner,'acl',c.relacl,'options',c.reloptions)
          FROM pg_class c WHERE c.oid='public.v_energy_daily'::regclass) IS DISTINCT FROM before
        OR encode(sha256(convert_to(pg_get_viewdef('public.v_energy_daily'::regclass,true),'UTF8')),'hex')
            IS DISTINCT FROM '68a12689cf96a6d27b2ff601b692d4bd34f2e25899a4d535aba534ef147c7da8'
    THEN RAISE EXCEPTION '291 changed the view identity, ownership, grants or options, or its text'; END IF;
END $view$;

COMMENT ON VIEW public.v_energy_daily IS
    'Source-qualified HA two-channel diagnostic integration, not commissioned measurement. Denver local-day splits and actual 23/24/25-hour denominator; max 300-second source hold, no null bridging or trailing extrapolation. Missing/legacy energy remains NULL. 90% is an operational coverage label only; scoring remains false until a separately reviewed endpoint is commissioned. '
    'measured_kwh, avg_watts and peak_watts integrate |ch0| + |ch1| per sample (291, #1362): the circuit has no generation, and the 1500 W heater reads negative on ch1, so this is a lower bound on draw. signed_kwh is the signed integral; sign_corrected_samples counts samples with a negative channel.';

-- Backfill: the partial_energy.refresh_partial_energy mapping (a day with no view row is cleared) for every day
-- since the first negative one. The view is read once: a date filter cannot reach below its window function, so a
-- correlated read would scan the whole energy history once per row.
WITH ed AS MATERIALIZED (
    SELECT date, greenhouse_id, measured_kwh, peak_watts FROM public.v_energy_daily WHERE date >= DATE '2026-09-27'
)
UPDATE public.daily_summary ds
   SET (kwh_total,peak_kw) = (
       SELECT ed.measured_kwh::double precision,(ed.peak_watts/1000.0)::double precision
       FROM ed WHERE ed.date=ds.date AND ed.greenhouse_id=ds.greenhouse_id
   ), captured_at=now()
 WHERE ds.date >= DATE '2026-09-27' AND ds.date <= (now() AT TIME ZONE 'America/Denver')::date;

DO $backfill$
BEGIN
    IF EXISTS(SELECT 1 FROM public.daily_summary WHERE kwh_total < 0 OR peak_kw < 0)
        OR EXISTS(SELECT 1 FROM public.v_energy_daily WHERE measured_kwh < 0 OR peak_watts < 0 OR avg_watts < 0)
    THEN RAISE EXCEPTION '291 left a negative energy day'; END IF;
END $backfill$;

-- Successor seals: the view's new text and columns move the api and ingestor digests and re-seal both receipts;
-- the MCP digest and receipt must still equal 290's (which are 289's).
DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'b4b8499b426ef1c7abb2c6c996dee098d9aec968ddc3292d210cca70c2e77b76'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '2dcc7c99cd7cb2e80745ee1f2f05af9f5fa3dcdf0f6bbdd34e9cf1b870692806'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
    THEN RAISE EXCEPTION '291 refuses an unqualified successor'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('b4b8499b426ef1c7abb2c6c996dee098d9aec968ddc3292d210cca70c2e77b76','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_api_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '291 missing api receipt'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('2dcc7c99cd7cb2e80745ee1f2f05af9f5fa3dcdf0f6bbdd34e9cf1b870692806','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_ingestor_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '291 missing ingestor receipt'; END IF;
END $successor$;
