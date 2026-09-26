-- #371 bounded diagnostic projection. Requires 244/245; no metric backfill.
CREATE FUNCTION public.fn_observed_minute_diagnostic(p_day date, p_greenhouse text DEFAULT 'vallery')
RETURNS TABLE (
    day date, greenhouse_id text, served_at timestamptz,
    revision_id bigint, recorded_at timestamptz, capture_schema text,
    unavailable_reason text, diagnostic jsonb
) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $function$
BEGIN
    IF p_day IS NULL OR p_greenhouse IS DISTINCT FROM 'vallery' THEN
        RAISE EXCEPTION 'one explicit day and supported greenhouse required' USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
    WITH latest AS (
        -- Select the newest record BEFORE checking its validity. Never fall
        -- back to an older valid revision after deletion, mutation or damage.
        SELECT r.* FROM public.daily_climate_metric_revisions r
        WHERE r.greenhouse_id = p_greenhouse AND r.day = p_day
        ORDER BY r.revision_id DESC LIMIT 1
    ), snapshot AS (
        SELECT d.climate_observed_minute_metrics AS payload, r.revision_id,
               r.recorded_at, r.capture_schema,
               CASE
                 WHEN d.date IS NULL THEN 'daily_row_missing'
                 WHEN d.climate_observed_minute_metrics IS NULL THEN 'not_computed'
                 WHEN r.revision_id IS NULL OR r.capture_schema <> 'daily-summary-capture-v2'
                   OR r.operation NOT IN ('insert', 'after_update')
                   OR r.metrics->'observed_minute_diagnostic'
                      IS DISTINCT FROM d.climate_observed_minute_metrics THEN 'revision_mismatch'
                 ELSE NULL
               END AS reason
        FROM (SELECT 1) singleton
        LEFT JOIN public.daily_summary d ON d.date = p_day AND d.greenhouse_id = p_greenhouse
        LEFT JOIN latest r ON true
    )
    SELECT p_day, p_greenhouse, statement_timestamp(), s.revision_id,
           s.recorded_at, s.capture_schema, s.reason,
           CASE WHEN s.reason IS NULL THEN s.payload ELSE NULL END
    FROM snapshot s;
END;
$function$;

DO $owner$
DECLARE owner_name text;
BEGIN
    SELECT pg_get_userbyid(datdba) INTO owner_name
      FROM pg_database WHERE datname = current_database();
    IF owner_name IN ('verdify_api_runtime', 'verdify_ingestor_runtime',
                      'verdify_api_runtime_login', 'verdify_ingestor_runtime_login') THEN
        RAISE EXCEPTION 'runtime role must not own the diagnostic reader';
    END IF;
    EXECUTE format('ALTER FUNCTION public.fn_observed_minute_diagnostic(date,text) OWNER TO %I', owner_name);
END;
$owner$;
REVOKE ALL ON FUNCTION public.fn_observed_minute_diagnostic(date,text) FROM PUBLIC,
    verdify_api_runtime, verdify_ingestor_runtime, verdify_api_runtime_login, verdify_ingestor_runtime_login;
GRANT EXECUTE ON FUNCTION public.fn_observed_minute_diagnostic(date,text) TO verdify_api_runtime;
COMMENT ON FUNCTION public.fn_observed_minute_diagnostic(date,text) IS
'One-day captured observed-minute diagnostic, not a live or qualified crop/experiment endpoint. '
'No older-valid fallback, raw climate/setpoint export or new raw-table grants. Consumers must '
'validate the diagnostic structure and expose its evaluated window and revision.';
