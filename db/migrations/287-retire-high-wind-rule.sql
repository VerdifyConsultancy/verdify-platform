-- 287: retire the high_wind forecast rule that 063 seeds. Jason, 2026-11-03: it is not needed,
-- "it's an indoor greenhouse". The row stays, disabled, as history, so a rebuild from migrations
-- cannot resurrect the rule. Production's row was disabled by hand at 04:33:22Z, so this matches
-- no row there. Data only: no runtime seal moves and no receipt is re-sealed; both blocks pin
-- exact286's seals, and tests/fixtures/287-retire-high-wind-rule-native-qualification.json
-- records the rolled-back native qualification.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF current_user <> 'verdify'
        OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
            AND seq=286 AND filename='db/migrations/286-lighting-policy-ignores-manual.sql'
            AND sha256='6bacb419d3aa6d1d16f05c5a1d451f78001db1cedf3315539853f69bf3144eee'
            AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>286)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '6b43af2ab8da8871469a379b1c322ade82481a92a7d32300381ea5e60fd2dd8c'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '6b0c82221c3d826a83b8c7a5cd674c23d4bc39ee9c9912a9aaa5bae0d90dbdcc'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
    THEN RAISE EXCEPTION '287 refuses an unqualified predecessor'; END IF;
END $predecessor$;

UPDATE public.forecast_action_rules SET enabled = false, updated_at = now() WHERE name = 'high_wind' AND enabled;

DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '6b43af2ab8da8871469a379b1c322ade82481a92a7d32300381ea5e60fd2dd8c'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '6b0c82221c3d826a83b8c7a5cd674c23d4bc39ee9c9912a9aaa5bae0d90dbdcc'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
    THEN RAISE EXCEPTION '287 changed a runtime boundary'; END IF;
END $successor$;
