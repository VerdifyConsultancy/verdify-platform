-- #424: additive public lineage reader. Legacy functions are NOT consumed truth.
-- No applied migration, table, row or legacy function is rewritten.
-- A snapshot timestamp is a database capture time, not raw sensor freshness.
-- SECURITY DEFINER returns only this public allowlist, with a fixed search_path.
CREATE OR REPLACE FUNCTION public.fn_public_band_trace_v2(
    p_start timestamptz, p_end timestamptz, p_greenhouse_id text DEFAULT 'vallery'
)
RETURNS TABLE (
    ts timestamptz, greenhouse_id text,
    temp_avg double precision, vpd_avg double precision,
    reconstructed_temp_low double precision, reconstructed_temp_high double precision,
    reconstructed_vpd_low double precision, reconstructed_vpd_high double precision,
    reconstructed_temp_in_band boolean, reconstructed_vpd_in_band boolean, reconstructed_both_in_band boolean,
    desired_temp_in_band boolean, desired_vpd_in_band boolean, desired_both_in_band boolean,
    lineage_contract_version integer, trace_quality_flag text, lineage jsonb
)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
BEGIN
    IF p_start IS NULL OR p_end IS NULL OR p_end <= p_start
       OR p_end - p_start > interval '168 hours'
       OR p_greenhouse_id IS DISTINCT FROM 'vallery' THEN
        RAISE EXCEPTION 'unsupported public band trace window or greenhouse' USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
    WITH samples AS (
        SELECT c.ts, c.greenhouse_id,
            CASE WHEN c.temp_avg > '-Infinity'::float8 AND c.temp_avg < 'Infinity'::float8
                 THEN c.temp_avg END AS temp_avg,
            CASE WHEN c.vpd_avg > '-Infinity'::float8 AND c.vpd_avg < 'Infinity'::float8
                 THEN c.vpd_avg END AS vpd_avg,
            c.house_temp_target_f, c.house_vpd_target
        FROM public.climate c
        WHERE c.greenhouse_id = p_greenhouse_id
          AND c.ts >= p_start AND c.ts < p_end
    ), joined AS (
        SELECT c.*,
            CASE WHEN crop.temp_low > '-Infinity'::float8 AND crop.temp_low < 'Infinity'::float8
                 THEN crop.temp_low END AS reconstructed_temp_low,
            CASE WHEN crop.temp_high > '-Infinity'::float8 AND crop.temp_high < 'Infinity'::float8
                 THEN crop.temp_high END AS reconstructed_temp_high,
            CASE WHEN crop.vpd_low > '-Infinity'::float8 AND crop.vpd_low < 'Infinity'::float8
                 THEN crop.vpd_low END AS reconstructed_vpd_low,
            CASE WHEN crop.vpd_high > '-Infinity'::float8 AND crop.vpd_high < 'Infinity'::float8
                 THEN crop.vpd_high END AS reconstructed_vpd_high,
            edges.edges, edges.desired_temp_low, edges.desired_temp_high,
            edges.desired_vpd_low, edges.desired_vpd_high,
            diag.band_source, diag.ts AS diagnostic_captured_at
        FROM samples c
        -- Existing resolver is greenhouse-global: restrict this reader to vallery.
        -- Recomputes current resolver at historical time; NOT an immutable crop definition.
        LEFT JOIN LATERAL public.fn_band_setpoints(c.ts) crop ON true
        LEFT JOIN LATERAL (
            SELECT jsonb_object_agg(p.parameter, jsonb_build_object(
                'unit', p.unit, 'raw_slug', p.raw_slug,
                'desired_value', d.value, 'desired_recorded_at', d.ts,
                'desired_conflict', coalesce(d.conflict, false),
                'cfg_snapshot_value', r.value, 'cfg_snapshot_captured_at', r.ts,
                'cfg_snapshot_conflict', coalesce(r.conflict, false),
                'numeric_comparison', CASE
                    WHEN d.value IS NULL OR r.value IS NULL THEN 'unavailable'
                    WHEN d.value = r.value THEN 'equal_numeric' ELSE 'different_numeric' END
            )) AS edges,
            max(d.value) FILTER (WHERE p.parameter = 'temp_low') AS desired_temp_low,
            max(d.value) FILTER (WHERE p.parameter = 'temp_high') AS desired_temp_high,
            max(d.value) FILTER (WHERE p.parameter = 'vpd_low') AS desired_vpd_low,
            max(d.value) FILTER (WHERE p.parameter = 'vpd_high') AS desired_vpd_high
            FROM (VALUES
                ('temp_low', '°F', 'cfg___temp_low___f_'),
                ('temp_high', '°F', 'cfg___temp_high___f_'),
                ('vpd_low', 'kPa', 'cfg___vpd_low__kpa_'),
                ('vpd_high', 'kPa', 'cfg___vpd_high__kpa_')
            ) p(parameter, unit, raw_slug)
            LEFT JOIN LATERAL (
                SELECT s.ts,
                    CASE WHEN count(DISTINCT s.value) = 1
                         AND bool_and(s.value > '-Infinity'::float8 AND s.value < 'Infinity'::float8)
                         THEN min(s.value) END AS value,
                    count(DISTINCT s.value) <> 1 AS conflict
                FROM public.setpoint_changes s
                WHERE s.greenhouse_id = c.greenhouse_id AND s.parameter = p.parameter
                  AND s.ts <= c.ts AND (s.expired_at IS NULL OR s.expired_at > c.ts)
                GROUP BY s.ts ORDER BY s.ts DESC LIMIT 1
            ) d ON true
            LEFT JOIN LATERAL (
                SELECT s.ts,
                    CASE WHEN count(DISTINCT s.value) = 1
                         AND bool_and(s.value > '-Infinity'::float8 AND s.value < 'Infinity'::float8)
                         THEN min(s.value) END AS value,
                    count(DISTINCT s.value) <> 1 AS conflict
                FROM public.setpoint_snapshot s
                WHERE s.greenhouse_id = c.greenhouse_id AND s.parameter = p.parameter
                  AND s.ts <= c.ts AND s.ts >= c.ts - interval '15 minutes'
                GROUP BY s.ts ORDER BY s.ts DESC LIMIT 1
            ) r ON true
        ) edges ON true
        LEFT JOIN LATERAL (
            SELECT d.ts, CASE WHEN count(DISTINCT d.band_source) = 1 THEN min(d.band_source) END AS band_source
            FROM public.diagnostics d
            WHERE d.greenhouse_id = c.greenhouse_id
              AND d.ts <= c.ts AND d.ts >= c.ts - interval '15 minutes'
            GROUP BY d.ts ORDER BY d.ts DESC LIMIT 1
        ) diag ON true
    ), comparisons AS (
        SELECT j.*,
            CASE WHEN j.reconstructed_temp_low > '-Infinity'::float8
                      AND j.reconstructed_temp_high < 'Infinity'::float8
                      AND j.reconstructed_temp_low <= j.reconstructed_temp_high
                 THEN j.temp_avg BETWEEN j.reconstructed_temp_low AND j.reconstructed_temp_high END AS ct,
            CASE WHEN j.reconstructed_vpd_low > '-Infinity'::float8
                      AND j.reconstructed_vpd_high < 'Infinity'::float8
                      AND j.reconstructed_vpd_low <= j.reconstructed_vpd_high
                 THEN j.vpd_avg BETWEEN j.reconstructed_vpd_low AND j.reconstructed_vpd_high END AS cv,
            CASE WHEN j.desired_temp_low <= j.desired_temp_high
                 THEN j.temp_avg BETWEEN j.desired_temp_low AND j.desired_temp_high END AS dt,
            CASE WHEN j.desired_vpd_low <= j.desired_vpd_high
                 THEN j.vpd_avg BETWEEN j.desired_vpd_low AND j.desired_vpd_high END AS dv
        FROM joined j
    )
    SELECT j.ts, j.greenhouse_id, j.temp_avg, j.vpd_avg,
        j.reconstructed_temp_low, j.reconstructed_temp_high, j.reconstructed_vpd_low, j.reconstructed_vpd_high,
        j.ct, j.cv, CASE WHEN j.ct IS NOT NULL AND j.cv IS NOT NULL THEN j.ct AND j.cv END,
        j.dt, j.dv, CASE WHEN j.dt IS NOT NULL AND j.dv IS NOT NULL THEN j.dt AND j.dv END,
        2, 'unobservable_consumed_band'::text,
        jsonb_build_object(
            'edges', j.edges,
            'band_source_snapshot', CASE WHEN j.band_source IN ('onchip_curve', 'dispatcher_legacy')
                                         THEN j.band_source ELSE NULL END,
            'diagnostic_captured_at', j.diagnostic_captured_at,
            'temp_target_snapshot', CASE WHEN j.house_temp_target_f > '-Infinity'::float8
                                            AND j.house_temp_target_f < 'Infinity'::float8
                                        THEN j.house_temp_target_f END,
            'vpd_target_snapshot', CASE WHEN j.house_vpd_target > '-Infinity'::float8
                                           AND j.house_vpd_target < 'Infinity'::float8
                                       THEN j.house_vpd_target END,
            'target_snapshot_captured_at', j.ts,
            'raw_observation_freshness_verified', false,
            'runtime_connection_identity_verified', false,
            'consumed_band_verified', false,
            'disposition', 'unobservable'
        )
    FROM comparisons j ORDER BY j.ts;
END;
$function$;
COMMENT ON FUNCTION public.fn_public_band_trace_v2(timestamptz,timestamptz,text) IS
    'Public lineage v2: current house-anchor reconstruction, desired history, bounded database snapshots; never device-consumed proof. Missing comparisons remain NULL; fractions are sample-weighted, not crop-study endpoints.';
REVOKE ALL ON FUNCTION public.fn_public_band_trace_v2(timestamptz,timestamptz,text) FROM PUBLIC;
DO $owner$
DECLARE owner_name text;
BEGIN
    SELECT r.rolname INTO owner_name FROM pg_catalog.pg_database d
    JOIN pg_catalog.pg_roles r ON r.oid = d.datdba WHERE d.datname = current_database();
    EXECUTE format('ALTER FUNCTION public.fn_public_band_trace_v2(timestamptz,timestamptz,text) OWNER TO %I', owner_name);
END;
$owner$;
GRANT EXECUTE ON FUNCTION public.fn_public_band_trace_v2(timestamptz,timestamptz,text)
TO verdify_api_runtime;
