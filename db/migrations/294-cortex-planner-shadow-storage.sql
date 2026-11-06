-- 294 (#1350, #1366): storage and read surface for the Cortex planner's shadow runs, a retained
-- weather_forecast archive, and the heater roles in equipment_assets.
-- Jason, 2026-11-05 ~21:45Z, accepted the cortex-planner report's defaults: one deterministic call
-- to Cortex llm.primary.longctx per plan trigger, every pack and attempt stored, its own Deployment
-- (verdify_planner_runtime duty), shadow first. Shadow writes only the two planner tables below and
-- the planner_failed alert; it never writes plan_journal, setpoint_plan or setpoint_changes.
-- 1. planner_pack: one row per pack (gzip of the exact text sent, gzip of the inputs it was built
--    from, per-section sha256 and Qwen-token counts). About 30-60 KB a pack, 8-10 packs a day,
--    so roughly 0.2 GB a year; kept indefinitely (A/B attribution and ablation replays need it).
-- 2. planner_inference_runs (207, 0 rows, verdify-only ACL) gains the per-attempt columns: trigger,
--    pack/request/response sha256, status, finish reason, latency, reasoning tokens, attempt and
--    kind, pack variant, mode, dispatched (false in shadow), the validated proposal and the raw
--    response body. Its experiment_id/assignment_id/scheduled_in_arm columns already fit #1360.
-- 3. weather_forecast_archive: the ingestor purges weather_forecast after 30 days, so a pack's
--    forecast cannot be rebuilt later. Each fetch's rows up to 7 days ahead are copied once,
--    now (the current ~30 days) and then daily by a TimescaleDB job; the archive is never purged.
--    Projection: ~24 fetches a day x 168 rows ~ 1.5 M rows and ~0.45 GB a year with its key.
--    A full export of weather_forecast was taken out of band on 2026-11-05, before this archive
--    existed; rows older than the archive's first fetch exist only in that export.
-- 4. verdify_planner_runtime (268's duty schema) gains two views (equipment_state, active crops)
--    and four owner-run functions: the hourly served band and house VPD envelope series, and the
--    planner_failed alert raise/resolve. The duty gets SELECT/INSERT on the two planner tables
--    and SELECT on the archive, and nothing else.
-- 5. equipment_assets notes: heat1 (electric) is the PRIMARY heater and heat2 (gas) the BACKUP
--    (Jason, 2026-11-05); the old notes had them swapped.
-- Seal-preserving, like 288: no object here carries an api, ingestor, MCP or PUBLIC privilege,
-- none is owned by those roles, and no relation they can reach changes shape or ACL (the two
-- tables they can read, equipment_assets and alert_log, change only in rows). Both blocks pin
-- exact293's seals. Its predecessor is exact293 (#1387); 294 and 296 ship in one PR, in that order.
-- Both blocks carry 293's measured seals (its native qualification at 291, #1387);
-- tests/fixtures/294-cortex-planner-shadow-storage-native-qualification.json records 294's own.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF current_user <> 'verdify'
        OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
            AND seq=293 AND filename='db/migrations/293-crop-plantings.sql'
            AND sha256='fe591976baeb81eb7676b6ff3198a9f370ba6bb7db8f69218c1d5f47ddbd48b7'
            AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>293)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'f0a0d02a96692609b37d8429085163f4ce2668431a1840f7b1635856d846a547'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '533b81f25b05bb544d4ecffe62d4c16c51644acff1190bbde2a85dc49130687e'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '2f5f7ccadf8006a351353c4fce8aec9a59f634b605adf2bcda5e6e61dbe254b9'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
        OR to_regclass('public.planner_pack') IS NOT NULL
        OR to_regclass('public.weather_forecast_archive') IS NOT NULL
        OR to_regnamespace('verdify_planner_runtime') IS NULL
        OR (SELECT count(*) FROM public.planner_inference_runs)<>0
    THEN RAISE EXCEPTION '294 refuses an unqualified predecessor'; END IF;
END $predecessor$;

CREATE TABLE public.planner_pack (
    pack_sha256     text PRIMARY KEY CHECK (pack_sha256 ~ '^[0-9a-f]{64}$'),
    trigger_id      uuid,
    as_of           timestamptz NOT NULL,
    pack_version    text NOT NULL,
    pack_variant    text NOT NULL CHECK (pack_variant IN ('v1', 'minimum')),
    builder_git_sha text NOT NULL,
    token_method    text NOT NULL,
    sections        jsonb NOT NULL,
    total_tokens    integer NOT NULL CHECK (total_tokens > 0),
    body_gzip       bytea NOT NULL,
    inputs_gzip     bytea NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX planner_pack_trigger_idx ON public.planner_pack (trigger_id);
COMMENT ON TABLE public.planner_pack IS
    'Cortex planner packs (294, #1366): the exact text sent (body_gzip), the inputs it was built from (inputs_gzip, '
    'rebuilds the pack), per-section sha256 and Qwen-token counts. Kept indefinitely.';

ALTER TABLE public.planner_inference_runs
    ADD COLUMN trigger_id uuid,
    ADD COLUMN event_type text,
    ADD COLUMN pack_sha256 text REFERENCES public.planner_pack (pack_sha256),
    ADD COLUMN request_sha256 text CHECK (request_sha256 IS NULL OR request_sha256 ~ '^[0-9a-f]{64}$'),
    ADD COLUMN response_sha256 text CHECK (response_sha256 IS NULL OR response_sha256 ~ '^[0-9a-f]{64}$'),
    ADD COLUMN status text CHECK (status IS NULL OR status IN ('ok', 'invalid', 'transport_error', 'failed')),
    ADD COLUMN finish_reason text,
    ADD COLUMN latency_ms integer,
    ADD COLUMN reasoning_tokens bigint,
    ADD COLUMN attempt smallint CHECK (attempt IS NULL OR attempt BETWEEN 1 AND 3),
    ADD COLUMN attempt_kind text CHECK (attempt_kind IS NULL OR attempt_kind IN ('primary', 'retry', 'repair')),
    ADD COLUMN pack_variant text,
    ADD COLUMN mode text CHECK (mode IS NULL OR mode IN ('shadow', 'live')),
    ADD COLUMN dispatched boolean NOT NULL DEFAULT false,
    ADD COLUMN proposal jsonb,
    ADD COLUMN response_body jsonb,
    ADD COLUMN error text,
    ADD CONSTRAINT planner_inference_runs_shadow_not_dispatched CHECK (mode IS DISTINCT FROM 'shadow' OR NOT dispatched);
CREATE UNIQUE INDEX planner_inference_runs_trigger_attempt_key
    ON public.planner_inference_runs (trigger_id, attempt) WHERE trigger_id IS NOT NULL;
COMMENT ON COLUMN public.planner_inference_runs.request_sha256 IS
    'sha256 of the call identity (model, sampling, seed, reasoning effort, response format, schema, pack version, '
    'tokenizer): with pack_version, the treatment identity for #1360. The pack itself is pack_sha256.';

CREATE TABLE public.weather_forecast_archive (
    fetched_at             timestamptz NOT NULL,
    ts                     timestamptz NOT NULL,
    temp_f                 double precision,
    rh_pct                 double precision,
    wind_speed_mph         double precision,
    wind_dir_deg           double precision,
    cloud_cover_pct        double precision,
    precip_prob_pct        double precision,
    solar_w_m2             double precision,
    dew_point_f            double precision,
    feels_like_f           double precision,
    vpd_kpa                double precision,
    precip_in              double precision,
    rain_in                double precision,
    snow_in                double precision,
    wind_gust_mph          double precision,
    uv_index               double precision,
    et0_mm                 double precision,
    direct_radiation_w_m2  double precision,
    diffuse_radiation_w_m2 double precision,
    sunshine_duration_s    double precision,
    weather_code           integer,
    cloud_cover_low_pct    double precision,
    cloud_cover_high_pct   double precision,
    surface_pressure_hpa   double precision,
    soil_temp_f            double precision,
    visibility_m           double precision,
    greenhouse_id          text,
    archived_at            timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fetched_at, ts)
);
COMMENT ON TABLE public.weather_forecast_archive IS
    'Retained copy of weather_forecast (294): every fetch, rows up to 7 days ahead, copied daily by '
    'fn_archive_weather_forecast; never purged. weather_forecast itself keeps 30 days. A full export of '
    'weather_forecast was taken out of band on 2026-11-05; earlier fetches exist only there.';

CREATE FUNCTION public.fn_archive_weather_forecast() RETURNS integer
LANGUAGE sql VOLATILE SET search_path=pg_catalog,public,pg_temp AS $archive$
    WITH copied AS (
        INSERT INTO public.weather_forecast_archive (
            fetched_at, ts, temp_f, rh_pct, wind_speed_mph, wind_dir_deg, cloud_cover_pct, precip_prob_pct, solar_w_m2,
            dew_point_f, feels_like_f, vpd_kpa, precip_in, rain_in, snow_in, wind_gust_mph, uv_index, et0_mm,
            direct_radiation_w_m2, diffuse_radiation_w_m2, sunshine_duration_s, weather_code, cloud_cover_low_pct,
            cloud_cover_high_pct, surface_pressure_hpa, soil_temp_f, visibility_m, greenhouse_id)
        SELECT f.fetched_at, f.ts, f.temp_f, f.rh_pct, f.wind_speed_mph, f.wind_dir_deg, f.cloud_cover_pct,
               f.precip_prob_pct, f.solar_w_m2, f.dew_point_f, f.feels_like_f, f.vpd_kpa, f.precip_in, f.rain_in,
               f.snow_in, f.wind_gust_mph, f.uv_index, f.et0_mm, f.direct_radiation_w_m2, f.diffuse_radiation_w_m2,
               f.sunshine_duration_s, f.weather_code, f.cloud_cover_low_pct, f.cloud_cover_high_pct,
               f.surface_pressure_hpa, f.soil_temp_f, f.visibility_m, f.greenhouse_id
          FROM public.weather_forecast f
         WHERE f.fetched_at > coalesce((SELECT max(a.fetched_at) FROM public.weather_forecast_archive a), '-infinity')
                              - interval '2 days'
           AND f.ts < f.fetched_at + interval '7 days'
        ON CONFLICT (fetched_at, ts) DO NOTHING
        RETURNING 1)
    SELECT count(*)::integer FROM copied
$archive$;
REVOKE ALL ON FUNCTION public.fn_archive_weather_forecast() FROM PUBLIC;
CREATE PROCEDURE public.archive_weather_forecast_job(job_id integer, config jsonb)
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $job$ BEGIN PERFORM public.fn_archive_weather_forecast(); END $job$;
REVOKE ALL ON PROCEDURE public.archive_weather_forecast_job(integer, jsonb) FROM PUBLIC;
SELECT public.fn_archive_weather_forecast();
SELECT public.add_job('public.archive_weather_forecast_job', interval '1 day', initial_start => now() + interval '1 hour');

CREATE VIEW verdify_planner_runtime.equipment_state WITH(security_barrier=true) AS
    SELECT "ts", "equipment", "state", "greenhouse_id" FROM public.equipment_state;
REVOKE ALL ON verdify_planner_runtime.equipment_state FROM PUBLIC;
GRANT SELECT ON verdify_planner_runtime.equipment_state TO verdify_planner_runtime;

CREATE VIEW verdify_planner_runtime.crops WITH(security_barrier=true) AS
    SELECT "id", "name", "variety", "position", "zone", "stage", "count", "planted_date", "greenhouse_id"
      FROM public.crops WHERE is_active;
REVOKE ALL ON verdify_planner_runtime.crops FROM PUBLIC;
GRANT SELECT ON verdify_planner_runtime.crops TO verdify_planner_runtime;

CREATE FUNCTION verdify_planner_runtime.band_series(p_from timestamptz, p_to timestamptz)
RETURNS TABLE(ts timestamptz, temp_low double precision, temp_high double precision, vpd_low double precision,
              vpd_high double precision, temp_target double precision, vpd_target double precision,
              house_vpd_low double precision, house_vpd_high double precision)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $band$
    SELECT g.h, b.temp_low, b.temp_high, b.vpd_low, b.vpd_high, b.temp_target, b.vpd_target,
           e.house_vpd_low, e.house_vpd_high
      FROM generate_series(date_trunc('hour', p_from), p_to, interval '1 hour') AS g(h)
     CROSS JOIN LATERAL public.fn_band_setpoints(g.h + interval '30 minutes') AS b
     CROSS JOIN LATERAL public.fn_house_vpd_control_band(g.h + interval '30 minutes') AS e
     WHERE p_to > p_from AND p_to - p_from <= interval '20 days'
$band$;
REVOKE ALL ON FUNCTION verdify_planner_runtime.band_series(timestamptz, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION verdify_planner_runtime.band_series(timestamptz, timestamptz) TO verdify_planner_runtime;

CREATE FUNCTION verdify_planner_runtime.raise_planner_failed(p_trigger_id uuid, p_message text, p_details jsonb)
RETURNS integer LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $raise$
DECLARE
    v_id integer;
BEGIN
    SELECT a.id INTO v_id FROM public.alert_log a
     WHERE a.alert_type = 'planner_failed' AND a.source = 'cortex_planner'
       AND a.disposition IN ('open', 'acknowledged') AND a.resolved_at IS NULL
     ORDER BY a.id DESC LIMIT 1;
    IF v_id IS NOT NULL THEN
        UPDATE public.alert_log a
           SET details = coalesce(a.details, '{}'::jsonb) || jsonb_build_object(
                   'last_trigger_id', p_trigger_id, 'last_message', left(p_message, 500),
                   'last_details', p_details, 'last_at', now(),
                   'repeat_count', coalesce((a.details ->> 'repeat_count')::integer, 1) + 1)
         WHERE a.id = v_id;
        RETURN v_id;
    END IF;
    INSERT INTO public.alert_log (alert_type, severity, category, sensor_id, message, details, source)
    VALUES ('planner_failed', 'warning', 'system', 'planner.cortex', left(p_message, 500),
            jsonb_build_object('trigger_id', p_trigger_id, 'details', p_details, 'repeat_count', 1), 'cortex_planner')
    RETURNING id INTO v_id;
    RETURN v_id;
END
$raise$;
REVOKE ALL ON FUNCTION verdify_planner_runtime.raise_planner_failed(uuid, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION verdify_planner_runtime.raise_planner_failed(uuid, text, jsonb) TO verdify_planner_runtime;

CREATE FUNCTION verdify_planner_runtime.resolve_planner_failed(p_resolution text)
RETURNS integer LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $resolve$
DECLARE
    v_count integer;
BEGIN
    UPDATE public.alert_log a
       SET disposition = 'resolved', resolved_at = now(), resolved_by = 'cortex_planner',
           resolution = left(p_resolution, 500)
     WHERE a.alert_type = 'planner_failed' AND a.source = 'cortex_planner'
       AND a.disposition IN ('open', 'acknowledged') AND a.resolved_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END
$resolve$;
REVOKE ALL ON FUNCTION verdify_planner_runtime.resolve_planner_failed(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION verdify_planner_runtime.resolve_planner_failed(text) TO verdify_planner_runtime;

GRANT SELECT, INSERT ON public.planner_pack TO verdify_planner_runtime;
GRANT SELECT, INSERT ON public.planner_inference_runs TO verdify_planner_runtime;
GRANT SELECT ON public.weather_forecast_archive TO verdify_planner_runtime;

UPDATE public.equipment_assets
   SET notes = 'PRIMARY heater (Jason, 2026-11-05): electric, thermostat-cycled; firmware stage HEAT_S1 runs it first.'
 WHERE equipment = 'heat1';
UPDATE public.equipment_assets
   SET notes = 'BACKUP heater (Jason, 2026-11-05): gas furnace for deep cold, firmware stage HEAT_S2 below the band floor.'
 WHERE equipment = 'heat2';

DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'f0a0d02a96692609b37d8429085163f4ce2668431a1840f7b1635856d846a547'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '533b81f25b05bb544d4ecffe62d4c16c51644acff1190bbde2a85dc49130687e'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '2f5f7ccadf8006a351353c4fce8aec9a59f634b605adf2bcda5e6e61dbe254b9'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
        OR (SELECT count(*) FROM public.equipment_assets WHERE equipment IN ('heat1', 'heat2')
            AND notes LIKE '%(Jason, 2026-11-05)%')<>2
    THEN RAISE EXCEPTION '294 changed a runtime boundary'; END IF;
END $successor$;
