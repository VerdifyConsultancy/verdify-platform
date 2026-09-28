-- #498 forward ACL repair for the deployed admin-only outcome_kpi reader.
-- Migration 260 restored fn_climate_action_daily_scorecard, but the ordinary
-- MCP login still cannot execute invoker helpers beneath the band resolver.
-- Grant only the three transitive helpers and their two read-only data sources.
-- This migration is transactional and advances all affected runtime receipts.
SET LOCAL search_path = pg_catalog, public, pg_temp;
LOCK TABLE public.schema_migrations, public.mcp_runtime_boundary_receipt,
           public.runtime_ordinary_login_attestation_receipts
    IN SHARE ROW EXCLUSIVE MODE;

DO $preflight$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migrations
         WHERE source = 'db/migrations'
           AND filename = 'db/migrations/260-restore-climate-action-daily-scorecard.sql'
           AND seq = 260
           AND sha256 = '3f1b1d0c7c055db75f008ab171ec505665ec2df14e918c2845dfbce3ff98cb80'
           AND stamp_method = 'runner'
    ) OR EXISTS (
        SELECT 1 FROM public.schema_migrations
         WHERE source = 'db/migrations' AND seq >= 261
    ) OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt) <> 1
      OR (SELECT encode(boundary_sha256, 'hex')
            FROM public.mcp_runtime_boundary_receipt WHERE singleton)
         IS DISTINCT FROM 'c61838a50ec877274a1d6a90a4a0b0f7e6d7c879e3f08a6421cefac56aa21fb5'
      OR encode(public.fn_mcp_runtime_boundary_digest(), 'hex')
         IS DISTINCT FROM 'c61838a50ec877274a1d6a90a4a0b0f7e6d7c879e3f08a6421cefac56aa21fb5'
      OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts
            WHERE (login_name = 'verdify_api_runtime_login'
                   AND encode(boundary_sha256, 'hex') = 'edb663118ffc9c5fc2a6e00a9525433fdec92faebf071943c5e5feed4bbc5524')
               OR (login_name = 'verdify_ingestor_runtime_login'
                   AND encode(boundary_sha256, 'hex') = '9349738c72983658a23f17ba1435c2fc42e2392ac58c35c34365a93c96345915')) <> 2
      OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'), 'hex')
         IS DISTINCT FROM 'edb663118ffc9c5fc2a6e00a9525433fdec92faebf071943c5e5feed4bbc5524'
      OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'), 'hex')
         IS DISTINCT FROM '9349738c72983658a23f17ba1435c2fc42e2392ac58c35c34365a93c96345915'
      OR pg_catalog.has_function_privilege('verdify_mcp_runtime_login',
             'public.fn_crop_band_value(text,text,timestamptz,text,text,text)', 'EXECUTE')
      OR pg_catalog.has_function_privilege('verdify_mcp_runtime_login',
             'public.fn_current_season()', 'EXECUTE')
      OR pg_catalog.has_function_privilege('verdify_mcp_runtime_login',
             'public.fn_zone_vpd_targets(timestamptz)', 'EXECUTE')
      OR pg_catalog.has_table_privilege('verdify_mcp_runtime_login',
             'public.crop_band_anchors', 'SELECT')
      OR pg_catalog.has_table_privilege('verdify_mcp_runtime_login',
             'public.crop_target_profiles', 'SELECT')
    THEN
        RAISE EXCEPTION 'outcome KPI ACL repair requires exact phase-260 boundaries and denied dependencies';
    END IF;
END;
$preflight$;

GRANT EXECUTE ON FUNCTION
    public.fn_crop_band_value(text,text,timestamptz,text,text,text),
    public.fn_current_season(),
    public.fn_zone_vpd_targets(timestamptz)
TO verdify_mcp_runtime;

GRANT SELECT ON TABLE public.crop_band_anchors, public.crop_target_profiles
TO verdify_mcp_runtime;

UPDATE public.mcp_runtime_boundary_receipt
   SET boundary_sha256 = decode('116b10bdf81496423026f3c467a9c10aa6b2767867cb428e03271c71a34393fe', 'hex')
 WHERE singleton;

UPDATE public.runtime_ordinary_login_attestation_receipts
   SET boundary_sha256 = CASE login_name
       WHEN 'verdify_api_runtime_login' THEN decode('d259673dee68b8eefc6e4b164f82412c78587ea5043715e410ea199db4a553c2', 'hex')
       WHEN 'verdify_ingestor_runtime_login' THEN decode('2fe7dfba3f23e1c1b053b8f5d245319072546d93f40f6643902f1bdf4c7e2a97', 'hex')
   END
 WHERE login_name IN ('verdify_api_runtime_login',
                      'verdify_ingestor_runtime_login');

DO $postflight$
BEGIN
    IF encode(public.fn_mcp_runtime_boundary_digest(), 'hex')
       IS DISTINCT FROM '116b10bdf81496423026f3c467a9c10aa6b2767867cb428e03271c71a34393fe'
       OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'), 'hex')
          IS DISTINCT FROM 'd259673dee68b8eefc6e4b164f82412c78587ea5043715e410ea199db4a553c2'
       OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'), 'hex')
          IS DISTINCT FROM '2fe7dfba3f23e1c1b053b8f5d245319072546d93f40f6643902f1bdf4c7e2a97'
       OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt
            WHERE singleton AND encode(boundary_sha256, 'hex') =
                  '116b10bdf81496423026f3c467a9c10aa6b2767867cb428e03271c71a34393fe') <> 1
       OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts
            WHERE (login_name = 'verdify_api_runtime_login' AND encode(boundary_sha256, 'hex') =
                   'd259673dee68b8eefc6e4b164f82412c78587ea5043715e410ea199db4a553c2')
               OR (login_name = 'verdify_ingestor_runtime_login' AND encode(boundary_sha256, 'hex') =
                   '2fe7dfba3f23e1c1b053b8f5d245319072546d93f40f6643902f1bdf4c7e2a97')) <> 2
       OR NOT pg_catalog.has_function_privilege('verdify_mcp_runtime_login',
              'public.fn_crop_band_value(text,text,timestamptz,text,text,text)', 'EXECUTE')
       OR NOT pg_catalog.has_function_privilege('verdify_mcp_runtime_login',
              'public.fn_current_season()', 'EXECUTE')
       OR NOT pg_catalog.has_function_privilege('verdify_mcp_runtime_login',
              'public.fn_zone_vpd_targets(timestamptz)', 'EXECUTE')
       OR NOT pg_catalog.has_table_privilege('verdify_mcp_runtime_login',
              'public.crop_band_anchors', 'SELECT')
       OR NOT pg_catalog.has_table_privilege('verdify_mcp_runtime_login',
              'public.crop_target_profiles', 'SELECT')
       OR pg_catalog.has_table_privilege('verdify_mcp_runtime_login',
              'public.control_experiments', 'SELECT')
       OR pg_catalog.has_table_privilege('verdify_mcp_runtime_login',
              'public.control_assignments', 'SELECT')
    THEN
        RAISE EXCEPTION 'outcome KPI ACL repair failed exact runtime attestation';
    END IF;
END;
$postflight$;
