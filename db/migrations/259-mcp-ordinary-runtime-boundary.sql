-- #371: cut MCP's database authority to an ordinary, source-attested login.
-- Phase 258 reserved stable role OIDs. This successor has no password material;
-- the PreSync bootstrap installs a SCRAM verifier from the KSOPS Secret.
SET LOCAL search_path = pg_catalog, public, pg_temp;
LOCK TABLE public.schema_migrations,
           public.runtime_ordinary_login_attestation_receipts
    IN SHARE ROW EXCLUSIVE MODE;

DO $preflight$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migrations
         WHERE source = 'db/migrations'
           AND filename = 'db/migrations/258-mcp-runtime-inert-roles.sql'
           AND seq = 258
           AND sha256 = '5558c4be0d3624ccd5821d2e0231b4625feaa64a6657e5fe53ca67b79d55e78e'
           AND stamp_method = 'runner'
    ) OR EXISTS (
        SELECT 1 FROM public.schema_migrations
         WHERE source = 'db/migrations' AND seq >= 259
    ) OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2
      OR EXISTS (
          SELECT 1 FROM (VALUES
              ('verdify_api_runtime_login',
               '444063bd61ccb69f02888ede5f2c2338d7882b954af7141e267cdb53b4ed9c7e'),
              ('verdify_ingestor_runtime_login',
               '86660529322d02ce6e735d329f6c5e320eeb53f9a8b2890a9a285eaf852f88f5')
          ) expected(login_name, digest)
          LEFT JOIN public.runtime_ordinary_login_attestation_receipts receipt
            ON receipt.login_name = expected.login_name
         WHERE encode(receipt.boundary_sha256, 'hex') IS DISTINCT FROM expected.digest
            OR encode(public.fn_runtime_ordinary_boundary_digest(expected.login_name), 'hex')
               IS DISTINCT FROM expected.digest
      )
    THEN
        RAISE EXCEPTION 'MCP ordinary boundary requires exact committed phase 258';
    END IF;
    IF (SELECT count(*) FROM pg_catalog.pg_roles
         WHERE rolname IN ('verdify_mcp_runtime', 'verdify_mcp_runtime_login')
           AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper
           AND NOT rolcreatedb AND NOT rolcreaterole
           AND NOT rolreplication AND NOT rolbypassrls) <> 2
       OR EXISTS (
           SELECT 1 FROM pg_catalog.pg_auth_members m
           JOIN pg_catalog.pg_roles granted ON granted.oid = m.roleid
           JOIN pg_catalog.pg_roles member ON member.oid = m.member
           WHERE granted.rolname IN ('verdify_mcp_runtime', 'verdify_mcp_runtime_login')
              OR member.rolname IN ('verdify_mcp_runtime', 'verdify_mcp_runtime_login')
       ) THEN
        RAISE EXCEPTION 'phase-258 MCP roles are no longer inert';
    END IF;
END;
$preflight$;

ALTER ROLE verdify_mcp_runtime_login
    LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT verdify_mcp_runtime TO verdify_mcp_runtime_login
    WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;
GRANT USAGE ON SCHEMA public TO verdify_mcp_runtime;
COMMENT ON ROLE verdify_mcp_runtime IS
    'NOLOGIN MCP duty with explicit tool-read/write grants and no experiment arm table access.';
COMMENT ON ROLE verdify_mcp_runtime_login IS
    'Ordinary MCP login; its verifier is installed by the dedicated PreSync bootstrap.';

-- Preserve the blinded assignment firewall while retaining the legacy writer
-- demotion decision. No treatment, arm, allocation, or policy vector is returned.
CREATE FUNCTION public.fn_mcp_armed_assignment_context()
RETURNS TABLE (experiment_id uuid, greenhouse_id text, assignment_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
    SELECT e.experiment_id, e.greenhouse_id, a.assignment_id
      FROM public.control_experiments e
      LEFT JOIN public.control_assignments a
        ON a.experiment_id = e.experiment_id
       AND a.status = 'active'
       AND pg_catalog.now() <@ a.valid_range
     WHERE e.status IN ('armed', 'running')
     ORDER BY e.armed_at DESC NULLS LAST
     LIMIT 1
$body$;
REVOKE ALL ON FUNCTION public.fn_mcp_armed_assignment_context() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.fn_mcp_armed_assignment_context()
    TO verdify_mcp_runtime;

-- Read surface used by all 23 registered tools, Slack operations, and the
-- invoker helper closure. No SELECT on control_experiments or assignments.
GRANT SELECT ON
    public.alert_log, public.climate, public.climate_action_log,
    public.crop_catalog, public.crop_events, public.crop_tasks, public.crops,
    public.daily_summary, public.diagnostics, public.dli_validity_intervals,
    public.energy, public.equipment_state, public.harvests,
    public.mv_daily_kpi, public.mv_equipment_runtime_daily,
    public.nutrient_recipes, public.observations,
    public.plan_delivery_log, public.plan_journal, public.planner_lessons,
    public.planner_trigger_ledger, public.setpoint_changes,
    public.setpoint_clamps, public.setpoint_plan, public.setpoint_snapshot,
    public.slack_ai_work_items, public.slack_alert_actions,
    public.slack_alert_runbooks, public.slack_command_audit, public.slack_confirmation_requests,
    public.slack_user_roles, public.system_state, public.treatments,
    public.verdify_embeddings, public.weather_forecast, public.zones,
    public.v_active_plan, public.v_crop_history, public.v_crop_lifecycle,
    public.v_dli_daily, public.v_energy_estimate_reconciliation,
    public.v_equipment_runtime_daily, public.v_greenhouse_now,
    public.v_plan_guardrail_scorecard, public.v_plan_window_scorecard,
    public.v_position_current, public.v_scorecard_climate_diagnostics,
    public.v_sensor_staleness, public.v_slack_crop_tasks_due,
    public.v_slack_forecast_triage, public.v_slack_guardrail_summary,
    public.v_slack_public_ops_log, public.v_topology_tree,
    public.v_water_attribution_daily
TO verdify_mcp_runtime;

GRANT INSERT, UPDATE ON
    public.alert_log, public.crop_tasks, public.crops,
    public.plan_delivery_log, public.plan_journal, public.planner_lessons,
    public.planner_trigger_ledger, public.setpoint_plan,
    public.slack_command_audit, public.slack_confirmation_requests,
    public.treatments
TO verdify_mcp_runtime;
GRANT INSERT ON
    public.crop_events, public.harvests, public.observations,
    public.slack_ai_work_items, public.slack_alert_actions
TO verdify_mcp_runtime;
GRANT USAGE ON SEQUENCE
    public.alert_log_id_seq, public.crop_events_id_seq,
    public.crop_tasks_id_seq, public.crops_id_seq,
    public.harvests_id_seq, public.observations_id_seq,
    public.plan_delivery_log_id_seq, public.planner_lessons_id_seq,
    public.planner_trigger_ledger_id_seq,
    public.slack_alert_actions_id_seq,
    public.slack_command_audit_id_seq, public.treatments_id_seq
TO verdify_mcp_runtime;

-- The ingestor's required-cycle neutral fallback records this one additional
-- terminal ledger fact. Keep its ordinary role's UPDATE scope column-bound.
GRANT UPDATE (had_required_failure) ON public.planner_trigger_ledger
TO verdify_ingestor_runtime;

GRANT EXECUTE ON FUNCTION
    public.fn_band_setpoints(timestamptz),
    public.fn_dli_proxy_lesson_invalid(text,text),
    public.fn_dli_validity(timestamptz,text),
    public.fn_equip_at(text,timestamptz),
    public.fn_house_vpd_control_band(timestamptz),
    public.fn_observed_minute_diagnostic(date,text),
    public.fn_plan_anchor_score(text),
    public.fn_planner_scorecard(date),
    public.fn_realized_solar_night_dryout(date,date,text),
    public.fn_route_only_crop_band_diagnostic(date,text),
    public.fn_search_embeddings(vector,integer,text[]),
    public.fn_setpoint_at(text,timestamptz),
    public.fn_setpoint_at(text,text,timestamptz),
    public.fn_submit_policy_proposal(text,text,uuid,jsonb,text,jsonb,text,text,uuid,uuid,tstzrange,text,text)
TO verdify_mcp_runtime;

-- Seal exact post-grant API/ingestor successors after the phase-258 role OIDs
-- have been committed. These literals are filled from a rollback-only probe.
UPDATE public.runtime_ordinary_login_attestation_receipts
   SET boundary_sha256 = CASE login_name
       WHEN 'verdify_api_runtime_login' THEN decode('edb663118ffc9c5fc2a6e00a9525433fdec92faebf071943c5e5feed4bbc5524', 'hex')
       WHEN 'verdify_ingestor_runtime_login' THEN decode('9349738c72983658a23f17ba1435c2fc42e2392ac58c35c34365a93c96345915', 'hex')
   END
 WHERE login_name IN ('verdify_api_runtime_login',
                      'verdify_ingestor_runtime_login');

-- The MCP receipt covers the managed role posture, exact memberships,
-- database/schema ACLs, every direct managed relation/column/function/default
-- grant, explicit PUBLIC grants, and definitions of exposed views/functions.
-- A new direct MCP or explicit PUBLIC grant changes the digest and fails
-- startup/readiness; effective sensitive-table denials are checked separately.
CREATE FUNCTION public.fn_mcp_runtime_boundary_digest()
RETURNS bytea
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
WITH managed(oid) AS (
    SELECT oid FROM pg_catalog.pg_roles
     WHERE rolname IN ('verdify_mcp_runtime', 'verdify_mcp_runtime_login')
), entries(entry) AS (
    SELECT pg_catalog.format(
        'role|%s|%s|%s|%s|%s|%s|%s|%s|%s',
        r.rolname, r.rolcanlogin, r.rolinherit, r.rolsuper,
        r.rolcreatedb, r.rolcreaterole, r.rolreplication,
        r.rolbypassrls, coalesce(r.rolconfig::text, ''))
      FROM pg_catalog.pg_roles r WHERE r.oid IN (SELECT oid FROM managed)
    UNION ALL
    SELECT pg_catalog.format('member|%s|%s|%s|%s|%s|%s',
        m.roleid, m.member, m.grantor,
        m.admin_option, m.inherit_option, m.set_option)
      FROM pg_catalog.pg_auth_members m
     WHERE m.roleid IN (SELECT oid FROM managed)
        OR m.member IN (SELECT oid FROM managed)
    UNION ALL
    SELECT pg_catalog.format('database|%s|%s', d.datdba, d.datacl::text)
      FROM pg_catalog.pg_database d WHERE d.datname = pg_catalog.current_database()
    UNION ALL
    SELECT pg_catalog.format('schema|%s|%s', n.nspowner, n.nspacl::text)
      FROM pg_catalog.pg_namespace n WHERE n.nspname = 'public'
    UNION ALL
    SELECT pg_catalog.format('relation|%s|%s|%s|%s|%s|%s',
        n.nspname, c.relname, c.relkind, c.relowner,
        c.reloptions::text,
        CASE WHEN c.relkind IN ('v','m')
             THEN pg_catalog.pg_get_viewdef(c.oid, true) ELSE '' END)
      FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
       AND (c.relowner IN (SELECT oid FROM managed)
            OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(c.relacl) a
                        WHERE a.grantee = 0
                           OR a.grantee IN (SELECT oid FROM managed)))
    UNION ALL
    SELECT pg_catalog.format('relation-acl|%s|%s|%s|%s|%s',
        c.oid::regclass, a.grantee, a.grantor, a.privilege_type, a.is_grantable)
      FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) a
     WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
       AND (a.grantee = 0 OR a.grantee IN (SELECT oid FROM managed))
    UNION ALL
    SELECT pg_catalog.format('column-acl|%s|%s|%s|%s|%s|%s',
        a.attrelid::regclass, a.attname, x.grantee, x.grantor,
        x.privilege_type, x.is_grantable)
      FROM pg_catalog.pg_attribute a
      JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) x
     WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
       AND a.attnum > 0 AND NOT a.attisdropped
       AND (x.grantee = 0 OR x.grantee IN (SELECT oid FROM managed))
    UNION ALL
    SELECT pg_catalog.format('function|%s|%s|%s',
        p.oid::regprocedure, p.proowner, pg_catalog.pg_get_functiondef(p.oid))
      FROM pg_catalog.pg_proc p
      JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
       AND (p.proowner IN (SELECT oid FROM managed)
            OR p.oid = 'public.fn_mcp_runtime_boundary_digest()'::regprocedure
            OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(p.proacl) a
                        WHERE a.grantee = 0
                           OR a.grantee IN (SELECT oid FROM managed)))
    UNION ALL
    SELECT pg_catalog.format('function-acl|%s|%s|%s|%s|%s',
        p.oid::regprocedure, a.grantee, a.grantor, a.privilege_type, a.is_grantable)
      FROM pg_catalog.pg_proc p
      JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
      CROSS JOIN LATERAL pg_catalog.aclexplode(p.proacl) a
     WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
       AND (a.grantee = 0 OR a.grantee IN (SELECT oid FROM managed))
    UNION ALL
    SELECT pg_catalog.format('default-acl|%s|%s|%s|%s|%s|%s',
        d.defaclrole, d.defaclnamespace, d.defaclobjtype,
        a.grantee, a.privilege_type, a.is_grantable)
      FROM pg_catalog.pg_default_acl d
      CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) a
     WHERE a.grantee = 0 OR a.grantee IN (SELECT oid FROM managed)
)
SELECT public.digest(
    pg_catalog.convert_to(coalesce(pg_catalog.string_agg(entry, E'\n' ORDER BY entry), ''), 'UTF8'),
    'sha256')
  FROM entries
$body$;
REVOKE ALL ON FUNCTION public.fn_mcp_runtime_boundary_digest() FROM PUBLIC;

CREATE TABLE public.mcp_runtime_boundary_receipt (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    boundary_sha256 bytea NOT NULL CHECK (pg_catalog.octet_length(boundary_sha256) = 32)
);
REVOKE ALL ON TABLE public.mcp_runtime_boundary_receipt FROM PUBLIC;

CREATE FUNCTION public.fn_mcp_runtime_attest_ordinary_login()
RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    expected bytea;
BEGIN
    IF session_user <> 'verdify_mcp_runtime_login'
       OR (SELECT count(*) FROM pg_catalog.pg_roles
            WHERE rolname = 'verdify_mcp_runtime_login'
              AND rolcanlogin AND rolinherit AND NOT rolsuper
              AND NOT rolcreatedb AND NOT rolcreaterole
              AND NOT rolreplication AND NOT rolbypassrls) <> 1
       OR (SELECT count(*) FROM pg_catalog.pg_roles
            WHERE rolname = 'verdify_mcp_runtime'
              AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper
              AND NOT rolcreatedb AND NOT rolcreaterole
              AND NOT rolreplication AND NOT rolbypassrls) <> 1
       OR (SELECT count(*) FROM pg_catalog.pg_auth_members m
            JOIN pg_catalog.pg_roles duty ON duty.oid = m.roleid
            JOIN pg_catalog.pg_roles login ON login.oid = m.member
           WHERE duty.rolname = 'verdify_mcp_runtime'
             AND login.rolname = 'verdify_mcp_runtime_login'
             AND NOT m.admin_option AND m.inherit_option AND m.set_option) <> 1
       OR (SELECT d.datdba = (SELECT oid FROM pg_catalog.pg_roles
                              WHERE rolname = 'verdify_mcp_runtime_login')
             FROM pg_catalog.pg_database d
            WHERE d.datname = pg_catalog.current_database())
       OR pg_catalog.has_database_privilege(
              'verdify_mcp_runtime_login', pg_catalog.current_database(), 'CREATE')
       OR pg_catalog.has_schema_privilege(
              'verdify_mcp_runtime_login', 'public', 'CREATE')
       OR pg_catalog.has_table_privilege(
              'verdify_mcp_runtime_login', 'public.control_experiments',
              'SELECT,INSERT,UPDATE,DELETE')
       OR pg_catalog.has_table_privilege(
              'verdify_mcp_runtime_login', 'public.control_assignments',
              'SELECT,INSERT,UPDATE,DELETE')
       OR EXISTS (
           SELECT 1 FROM pg_catalog.pg_class c
            WHERE c.relnamespace = 'public'::regnamespace
              AND c.relname LIKE 'experiment_v2_%'
              AND c.relkind IN ('r','p','v','m')
              AND pg_catalog.has_table_privilege(
                  'verdify_mcp_runtime_login', c.oid,
                  'SELECT,INSERT,UPDATE,DELETE')
       )
    THEN
        RETURN false;
    END IF;
    SELECT r.boundary_sha256 INTO expected
      FROM public.mcp_runtime_boundary_receipt r WHERE r.singleton;
    RETURN expected IS NOT NULL
       AND expected = public.fn_mcp_runtime_boundary_digest();
EXCEPTION WHEN OTHERS THEN
    RETURN false;
END;
$body$;
REVOKE ALL ON FUNCTION public.fn_mcp_runtime_attest_ordinary_login() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.fn_mcp_runtime_attest_ordinary_login()
    TO verdify_mcp_runtime;

INSERT INTO public.mcp_runtime_boundary_receipt (boundary_sha256)
VALUES (decode('c8b68f940995824e9dfbe6334f7c66ac38952fa2efd9423682dc2b9cd4542ba8', 'hex'));

DO $postflight$
BEGIN
    IF (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2
       OR EXISTS (
           SELECT 1 FROM (VALUES
               ('verdify_api_runtime_login', 'edb663118ffc9c5fc2a6e00a9525433fdec92faebf071943c5e5feed4bbc5524'),
               ('verdify_ingestor_runtime_login', '9349738c72983658a23f17ba1435c2fc42e2392ac58c35c34365a93c96345915')
           ) expected(login_name, digest)
           LEFT JOIN public.runtime_ordinary_login_attestation_receipts receipt
             ON receipt.login_name = expected.login_name
          WHERE encode(receipt.boundary_sha256, 'hex') IS DISTINCT FROM expected.digest
             OR encode(public.fn_runtime_ordinary_boundary_digest(expected.login_name), 'hex')
                IS DISTINCT FROM expected.digest
       )
       OR encode(public.fn_mcp_runtime_boundary_digest(), 'hex')
          IS DISTINCT FROM 'c8b68f940995824e9dfbe6334f7c66ac38952fa2efd9423682dc2b9cd4542ba8'
       OR pg_catalog.has_table_privilege('verdify_mcp_runtime_login',
              'public.control_assignments', 'SELECT')
    THEN
        RAISE EXCEPTION 'MCP ordinary boundary successor failed exact postflight';
    END IF;
END;
$postflight$;
