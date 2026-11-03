-- 284 (VD-74, #1233), dashboard migration M2b: the dashboard login's grants on existing objects.
-- 282 created verdify_dashboard_runtime with grants only on its own objects, and 283 added the
-- irrigation tables, the line state, 194's per-event attribution and the slot sequence. This file
-- grants the rest of what the operator API reads and writes (architecture 7 and 9), so /v1/now,
-- /v1/plan and /v1/equipment stop answering 503. Every grant is direct, with no 268-style wrapper:
-- 268's wrappers kept shared ACLs out of the seals, this file re-seals instead, the services read
-- the public relations by name, and no view listed here calls an invoker function the login lacks.
-- Not here: v_greenhouse_now (its DLI and health-score helpers run as the caller), any write on
-- setpoint_changes or setpoint_plan, anything on the experiment and policy tables the attestor
-- denies (the login sees an armed experiment only through the definer function), and
-- mv_zone_band_grade (M6's scorecard view reads it with its owner's rights). fn_solar_phase beneath
-- the band functions keeps PUBLIC's EXECUTE (217's transitive helpers). M6 grants its own
-- v_daily_scorecard.
-- The grants move both ordinary seals: fn_runtime_ordinary_boundary_digest hashes every grantee, by
-- OID, on each relation and helper the api or ingestor login can reach. fn_mcp_runtime_boundary_digest
-- hashes only PUBLIC's and the MCP duty's entries, and each hypertable grant reaches its chunks, so
-- the MCP seal and receipt stay 283's. Its predecessor is exact283.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
        AND seq=283 AND filename='db/migrations/283-irrigation-tables.sql'
        AND sha256='0b07a5042edc29cb90da4c800da6514e47517240d765c0da0217b68cc7fe92c2'
        AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=284)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '030df35ca3b0bc8b94de5f394f96c2292674b2df58aea2d46990c53ad3bd1ad7' OR
        encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM 'c03d427cbbf327c8843496c744d718e1fca70c1e6d98963022ac87e308954096' OR
        encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login' AND boundary_sha256=decode('030df35ca3b0bc8b94de5f394f96c2292674b2df58aea2d46990c53ad3bd1ad7','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login' AND boundary_sha256=decode('c03d427cbbf327c8843496c744d718e1fca70c1e6d98963022ac87e308954096','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton AND boundary_sha256=decode('6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e','hex'))
        OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='verdify_dashboard_runtime' AND NOT rolcanlogin)
        OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='verdify_dashboard_runtime_login' AND rolcanlogin)
    THEN RAISE EXCEPTION '284 refuses an unqualified predecessor'; END IF;
END $predecessor$;

-- What the services read today: Now, plan and equipment (reads.py), settings and band writes
-- (settings.py), alerts and their runbooks (alerts.py), the activity log (activity.py), and
-- the graph tables (series.py): daily_summary and data_gaps. VD-57's forecast keys read
-- weather_forecast, so its release needs no second re-seal.
GRANT SELECT ON
    public.activity_log, public.alert_log, public.climate, public.crop_band_anchors, public.daily_summary,
    public.data_gaps, public.desired_state, public.diagnostics, public.equipment_state, public.plan_journal,
    public.planner_trigger_ledger, public.setpoint_changes, public.setpoint_clamps, public.setpoint_plan,
    public.setpoint_snapshot, public.slack_alert_runbooks, public.system_state, public.water_meter_events,
    public.weather_forecast
    TO verdify_dashboard_runtime;

-- The views: the plan in force, the desired rows, the open alerts and M4's minutes and coverage,
-- with v_water_attribution_daily for API-21, plus the other sources architecture 7 and the
-- screens name for freshness, equipment, planner health and water.
GRANT SELECT ON
    public.v_active_plan, public.v_data_pipeline_health, public.v_desired_state_current,
    public.v_equipment_relay_map, public.v_irrigation_day_coverage, public.v_irrigation_minutes_daily,
    public.v_open_alerts, public.v_planner_trigger_health, public.v_sensor_staleness,
    public.v_water_attribution_daily, public.v_water_meter_daily, public.v_water_relay_runs
    TO verdify_dashboard_runtime;

-- The functions: whether an experiment is armed (no SELECT on control_experiments), M4's bounded
-- minutes reader, each lane's seed (fn_equip_at), the band reads and, because those run as the
-- caller, fn_crop_band_value and fn_current_season beneath them, as 261 found for MCP.
GRANT EXECUTE ON FUNCTION
    public.fn_band_setpoints(timestamptz),
    public.fn_crop_band_value(text,text,timestamptz,text,text,text),
    public.fn_current_season(),
    public.fn_equip_at(text,timestamptz),
    public.fn_irrigation_minutes_daily(date,date),
    public.fn_mcp_armed_assignment_context(),
    public.fn_zone_vpd_targets(timestamptz)
    TO verdify_dashboard_runtime;

-- The writes: setting writes and their activity rows are append-only, each with its sequence; an
-- ack or a resolve sets only these alert columns; a crop-band write sets only these three anchor
-- columns. The updated_at triggers on both tables need no column grant.
GRANT INSERT ON public.desired_state, public.activity_log TO verdify_dashboard_runtime;
GRANT USAGE ON SEQUENCE public.desired_state_id_seq, public.activity_log_id_seq TO verdify_dashboard_runtime;
GRANT UPDATE (disposition, acknowledged_at, acknowledged_by, notes, resolved_at, resolved_by, resolution)
    ON public.alert_log TO verdify_dashboard_runtime;
GRANT UPDATE (value, width_below, width_above) ON public.crop_band_anchors TO verdify_dashboard_runtime;

-- Successor seals: the grants above move the api and ingestor digests. Both hash the dashboard
-- duty's OID, so only a rolled-back native qualification run once 282 is applied can measure them
-- (tests/fixtures/284-dashboard-runtime-grants-native-qualification.json). The MCP digest and
-- receipt must still equal 283's, so this block re-seals only the two ordinary receipts.
DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '03d2a7ce5efd2eecee965672175585bad43194b5fea85d99739efe70e01a81a8' OR
        encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM 'c571ff1bdc212ca84815718406aaa6b7242ba9f86a2727d3855168dcd4ed81ad' OR
        encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
    THEN RAISE EXCEPTION '284 refuses an unqualified successor'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('03d2a7ce5efd2eecee965672175585bad43194b5fea85d99739efe70e01a81a8','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_api_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '284 missing api receipt'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('c571ff1bdc212ca84815718406aaa6b7242ba9f86a2727d3855168dcd4ed81ad','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_ingestor_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '284 missing ingestor receipt'; END IF;
END $successor$;
