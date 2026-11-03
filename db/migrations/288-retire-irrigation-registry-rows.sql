-- 288: retire six sensor_registry rows whose feeds ended by design; each holds or would open a
-- false sensor_offline. v_sensor_staleness reads only active rows. The rows stay, inactive, as
-- history, so a rebuild from migrations cannot resurrect them.
-- Four equipment.irrigation_* rows: the engine firmware (2026.11.3.0435.5e24d07c, VD-47) removed
-- those config switches, so their equipment_state rows stopped on 2026-11-02 and nothing writes
-- them again. Each row allows 604800 s twice over, so each would open an alert about 2026-11-16.
-- climate.wet_bulb_temp_f and climate.feels_like_f: the Home Assistant feed was their only source
-- and was trimmed by design (d35d7984a, VD-48), so their values stopped on 2026-11-03. Each holds
-- an open alert. The alert monitor resolves both on its next cycle because neither key is raised
-- any more; the resolution text reads "probe reporting again", which here means only that.
-- climate.hydro_tds_ppm stays active: it comes back with the YINMIK repair.
-- A controller rolled back to pre-engine firmware keeps working: nothing reads these rows but the
-- staleness view. Data only: no runtime seal moves and no receipt is re-sealed; both blocks pin
-- exact287's seals.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF current_user <> 'verdify'
        OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
            AND seq=287 AND filename='db/migrations/287-retire-high-wind-rule.sql'
            AND sha256='c95444af3859465b89b6d3659de6aad2b91a00ade552ecfdca3072beec516933'
            AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>287)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '6b43af2ab8da8871469a379b1c322ade82481a92a7d32300381ea5e60fd2dd8c'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '6b0c82221c3d826a83b8c7a5cd674c23d4bc39ee9c9912a9aaa5bae0d90dbdcc'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
    THEN RAISE EXCEPTION '288 refuses an unqualified predecessor'; END IF;
END $predecessor$;

UPDATE public.sensor_registry SET active = false WHERE sensor_id IN ('equipment.irrigation_enabled', 'equipment.irrigation_wall_enabled', 'equipment.irrigation_center_enabled', 'equipment.irrigation_weather_skip', 'climate.wet_bulb_temp_f', 'climate.feels_like_f') AND active;

DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '6b43af2ab8da8871469a379b1c322ade82481a92a7d32300381ea5e60fd2dd8c'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '6b0c82221c3d826a83b8c7a5cd674c23d4bc39ee9c9912a9aaa5bae0d90dbdcc'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
    THEN RAISE EXCEPTION '288 changed a runtime boundary'; END IF;
END $successor$;
