-- 304 (#1283): v_water_meter_daily ends each day at the next America/Denver midnight, not at day + 1 day.
-- `day` is the local midnight as a timestamptz. The view measured the day's end as `day + interval '1 day'`, which
-- adds 24 hours in production's UTC sessions (SHOW timezone = UTC), so the 25-hour fall-back day (2026-11-01 local,
-- 06:00Z to 07:00Z the next day) could read complete with its last hour missing, and every 23-hour spring-forward
-- day (2026-03-08) read incomplete even when every sample was there. The end is now
-- ((day AT TIME ZONE 'America/Denver')::date + 1)::timestamp AT TIME ZONE 'America/Denver', and the local date
-- (today, partial_day) is read in America/Denver too, so neither depends on the session time zone. Nothing else
-- changes: the same columns in the same order and types, the same 10-minute edge tolerance, 300 s gap limit,
-- ledger watermark and quality order. The view keeps its oid, owner, ACL (verdify, and SELECT for the dashboard
-- duty) and options. v_water_daily and v_water_attribution_daily read it by name, so their text is unchanged.
-- No runtime seal moves: neither ordinary login, an MCP role nor PUBLIC holds an entry on the view, and the
-- relations they reach keep their text and columns. Both blocks pin 302's seals.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF current_user <> 'verdify'
        OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
            AND seq=302 AND filename='db/migrations/302-planner-section-reads.sql'
            AND sha256='a18aeb0588b5f4e56b7a78ce3dea7c592873b9fae54f0701c45f6d9bbbd8f3bf'
            AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>302)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'f0a0d02a96692609b37d8429085163f4ce2668431a1840f7b1635856d846a547'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '533b81f25b05bb544d4ecffe62d4c16c51644acff1190bbde2a85dc49130687e'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM 'af3f7ef2493b94b11e9e7c3f5ead6bf61c8542e477d26da117f3930b0530d873'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
        OR to_regclass('public.v_water_meter_daily') IS NULL
        OR encode(sha256(convert_to(pg_get_viewdef('public.v_water_meter_daily'::regclass,true),'UTF8')),'hex')
            IS DISTINCT FROM 'b6c1bafbac812137514276fe7f1e3f6b3c9ff7bc64294ec3a62a0df0d42cf338'
    THEN RAISE EXCEPTION '304 refuses an unqualified predecessor'; END IF;
END $predecessor$;

CREATE OR REPLACE VIEW public.v_water_meter_daily AS
WITH raw_ordered AS (
    SELECT
        COALESCE(c.greenhouse_id, 'vallery') AS greenhouse_id,
        c.ts,
        lag(c.ts) OVER (
            PARTITION BY COALESCE(c.greenhouse_id, 'vallery'),
                         (c.ts AT TIME ZONE 'America/Denver')::date
            ORDER BY c.ts
        ) AS prior_ts
    FROM public.climate c
    WHERE c.water_total_gal IS NOT NULL
), raw_daily AS (
    SELECT
        ((ts AT TIME ZONE 'America/Denver')::date::timestamp
            AT TIME ZONE 'America/Denver') AS day,
        greenhouse_id,
        count(*)::bigint AS raw_sample_count,
        min(ts) AS first_raw_ts,
        max(ts) AS last_raw_ts,
        max(extract(epoch FROM (ts - prior_ts)))::double precision AS max_raw_gap_seconds
    FROM raw_ordered
    GROUP BY 1, greenhouse_id
), event_daily AS (
    SELECT
        ((e.ts AT TIME ZONE 'America/Denver')::date::timestamp
            AT TIME ZONE 'America/Denver') AS day,
        e.greenhouse_id,
        e.meter_id,
        round(COALESCE(sum(e.delta_gal) FILTER (
            WHERE e.event_type = 'delta' AND e.quality_flag = 'ok'
        ), 0)::numeric, 3)::double precision AS used_gal,
        count(*) FILTER (WHERE e.event_type = 'delta' AND e.quality_flag = 'ok') AS delta_events,
        count(*) FILTER (WHERE e.event_type = 'reset') AS reset_events,
        count(*) FILTER (WHERE e.event_type = 'phantom_zero') AS phantom_zero_events,
        count(*) FILTER (WHERE e.quality_flag <> 'ok') AS quality_events,
        count(*) FILTER (WHERE e.event_type = 'gap') AS gap_events,
        count(*) FILTER (WHERE e.event_type = 'delta' AND e.prior_ts IS NULL) AS missing_interval_events
    FROM public.water_meter_events e
    GROUP BY 1, e.greenhouse_id, e.meter_id
), combined AS (
    SELECT
        COALESCE(e.day, r.day) AS day,
        COALESCE(e.greenhouse_id, r.greenhouse_id) AS greenhouse_id,
        COALESCE(e.meter_id, 'main_pulse') AS meter_id,
        COALESCE(e.used_gal, 0)::double precision AS used_gal,
        COALESCE(e.delta_events, 0)::bigint AS delta_events,
        COALESCE(e.reset_events, 0)::bigint AS reset_events,
        COALESCE(e.phantom_zero_events, 0)::bigint AS phantom_zero_events,
        COALESCE(e.quality_events, 0)::bigint AS quality_events,
        COALESCE(e.gap_events, 0)::bigint AS gap_events,
        COALESCE(e.missing_interval_events, 0)::bigint AS missing_interval_events,
        COALESCE(r.raw_sample_count, 0)::bigint AS raw_sample_count,
        r.first_raw_ts,
        r.last_raw_ts,
        r.max_raw_gap_seconds
    FROM event_daily e
    FULL JOIN raw_daily r USING (day, greenhouse_id)
), bounded AS (
    -- The local day and its end: the next America/Denver midnight (23, 24 or 25 hours later).
    SELECT
        c.*,
        (c.day AT TIME ZONE 'America/Denver')::date AS local_day,
        ((c.day AT TIME ZONE 'America/Denver')::date + 1)::timestamp AT TIME ZONE 'America/Denver' AS day_end
    FROM combined c
)
SELECT
    c.day,
    c.greenhouse_id,
    c.meter_id,
    c.used_gal,
    c.delta_events,
    c.reset_events,
    c.phantom_zero_events,
    c.quality_events,
    c.gap_events,
    c.raw_sample_count,
    c.first_raw_ts,
    c.last_raw_ts,
    c.max_raw_gap_seconds,
    c.missing_interval_events,
    c.local_day < (now() AT TIME ZONE 'America/Denver')::date
      AND c.first_raw_ts <= c.day + interval '10 minutes'
      AND c.last_raw_ts >= c.day_end - interval '10 minutes'
      AND COALESCE(c.max_raw_gap_seconds, 0) <= 300
      AND s.last_source_ts >= c.last_raw_ts AS is_complete_day,
    CASE
        WHEN c.local_day = (now() AT TIME ZONE 'America/Denver')::date THEN 'partial_day'
        WHEN c.raw_sample_count = 0 THEN 'unavailable'
        WHEN s.last_source_ts IS NULL OR s.last_source_ts < c.last_raw_ts
          THEN 'ledger_incomplete'
        WHEN c.gap_events > 0 OR COALESCE(c.max_raw_gap_seconds, 0) > 300
          OR c.quality_events > 0 OR c.missing_interval_events > 0 THEN 'discontinuous'
        WHEN c.first_raw_ts > c.day + interval '10 minutes'
          OR c.last_raw_ts < c.day_end - interval '10 minutes' THEN 'incomplete'
        ELSE 'ok'
    END AS quality,
    c.local_day < (now() AT TIME ZONE 'America/Denver')::date
      AND c.first_raw_ts <= c.day + interval '10 minutes'
      AND c.last_raw_ts >= c.day_end - interval '10 minutes'
      AND COALESCE(c.max_raw_gap_seconds, 0) <= 300
      AND s.last_source_ts >= c.last_raw_ts
      AND c.quality_events = 0
      AND c.missing_interval_events = 0 AS available_for_scoring,
    s.last_source_ts AS materialized_through_ts,
    s.last_source_ts IS NOT NULL
      AND s.last_source_ts >= c.last_raw_ts AS ledger_covers_day
FROM bounded c
LEFT JOIN public.water_meter_materializer_state s
  ON s.greenhouse_id = c.greenhouse_id
 AND s.source = 'climate.water_total_gal'
 AND s.meter_id = c.meter_id
ORDER BY c.day DESC;

COMMENT ON VIEW public.v_water_meter_daily IS
'Quality-filtered ledger gallons with raw-source coverage, reset/gap counts, interval completeness, and an explicit materializer watermark. A complete raw day is scoring-ineligible until the ledger watermark covers its final raw sample; no raw max-minus-min fallback is permitted. '
'A day runs from its America/Denver midnight to the next one (23, 24 or 25 hours; 304, #1283), whatever the session time zone.';

DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'f0a0d02a96692609b37d8429085163f4ce2668431a1840f7b1635856d846a547'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '533b81f25b05bb544d4ecffe62d4c16c51644acff1190bbde2a85dc49130687e'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM 'af3f7ef2493b94b11e9e7c3f5ead6bf61c8542e477d26da117f3930b0530d873'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
        OR (SELECT relowner FROM pg_class WHERE oid='public.v_water_meter_daily'::regclass)
            IS DISTINCT FROM 'verdify'::regrole::oid
        OR (SELECT reloptions FROM pg_class WHERE oid='public.v_water_meter_daily'::regclass) IS NOT NULL
        OR (SELECT array_agg(pg_get_userbyid(a.grantee) || ':' || a.privilege_type
                             ORDER BY pg_get_userbyid(a.grantee), a.privilege_type)
              FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
             WHERE c.oid='public.v_water_meter_daily'::regclass AND a.grantee <> c.relowner)
            IS DISTINCT FROM ARRAY['verdify_dashboard_runtime:SELECT']
    THEN RAISE EXCEPTION '304 changed a runtime boundary'; END IF;
END $successor$;
