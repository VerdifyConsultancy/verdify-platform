-- 302 (#1361, #1360): the dashboard's Planner section reads the experiment records and the planner's cost.
-- The dashboard duty already reads plan_journal and planner_trigger_ledger (284). It cannot read the experiment
-- tables (control_experiments, control_assignments, experiment_events: api, ingestor and the experiment owner
-- only) or planner_inference_runs (207: verdify only). 302 adds four read views, owned by verdify, with SELECT for
-- verdify_dashboard_runtime and nobody else, so API-25/26/27 can show plan cost, the experiment list (the closed
-- 2026-08-28 draft with the event that closed it) and a study's arm calendar:
--   v_planner_experiments          one row per experiment, with its newest closing event and deviation count;
--   v_planner_assignments          one row per assigned study day (arm, pair, valid range, status);
--   v_planner_experiment_events    each event's kind, severity, time and assignment (no detail payload);
--   v_planner_inference_runs       one row per planner call: model, tokens, energy, timing, arm and experiment, plus
--                                  `attempt`, the whole row as jsonb without its bulky or free-text columns. The
--                                  whole-row reference takes in columns added later, so the Cortex attempt columns
--                                  (status, latency_ms, attempt, mode, dispatched; #1350, migration 294) appear in
--                                  `attempt` once they exist, without another migration: the API checks for them.
-- No runtime seal moves: the views carry no api, ingestor, MCP or PUBLIC privilege and no runtime role owns them;
-- the underlying tables keep their ACLs and shape. Both blocks pin the predecessor's seals: 301's (296's api and
-- ingestor, 301's MCP). 302 follows 301 in the same change; both are qualified together.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF current_user <> 'verdify'
        OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
            AND seq=301 AND filename='db/migrations/301-greenhouse-brief.sql'
            AND sha256='24945842499e5d907d2c49ef4cbf4e57ada2f29b297c9744edd055421f7d0e1d'
            AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>301)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'f0a0d02a96692609b37d8429085163f4ce2668431a1840f7b1635856d846a547'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '533b81f25b05bb544d4ecffe62d4c16c51644acff1190bbde2a85dc49130687e'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM 'af3f7ef2493b94b11e9e7c3f5ead6bf61c8542e477d26da117f3930b0530d873'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
        OR to_regclass('public.control_experiments') IS NULL
        OR to_regclass('public.control_assignments') IS NULL
        OR to_regclass('public.experiment_events') IS NULL
        OR to_regclass('public.planner_inference_runs') IS NULL
        OR to_regclass('public.v_planner_experiments') IS NOT NULL
        OR to_regclass('public.v_planner_inference_runs') IS NOT NULL
        OR to_regrole('verdify_dashboard_runtime') IS NULL
    THEN RAISE EXCEPTION '302 refuses an unqualified predecessor'; END IF;
END $predecessor$;

CREATE VIEW public.v_planner_experiments WITH (security_barrier=true) AS
SELECT e.experiment_id,
       e.kind,
       e.status,
       e.name,
       e.study_id,
       e.execution_phase,
       e.admission_state,
       e.component_enabled,
       e.timezone,
       e.study_start_local_date,
       e.randomized_pair_count,
       e.schedule_sha256,
       e.firmware_revision,
       e.created_at,
       e.locked_at,
       e.armed_at,
       e.started_at,
       e.ended_at,
       e.updated_at,
       c.event_kind AS closing_event_kind,
       c.recorded_at AS closing_event_at,
       coalesce(c.detail ->> 'v2_event', c.detail ->> 'reason') AS closing_event_reason,
       (SELECT count(*) FROM public.experiment_events d
         WHERE d.experiment_id = e.experiment_id AND d.severity IN ('warning', 'critical'))::integer AS deviation_events
  FROM public.control_experiments e
  LEFT JOIN LATERAL (
        SELECT x.event_kind, x.recorded_at, x.detail
          FROM public.experiment_events x
         WHERE x.experiment_id = e.experiment_id
           AND x.event_kind IN ('emergency_action', 'state_transition')
         ORDER BY x.recorded_at DESC, x.event_id DESC
         LIMIT 1) c ON true
 WHERE e.greenhouse_id = 'vallery';
COMMENT ON VIEW public.v_planner_experiments IS
    'Planner section (302, #1361): one row per experiment record with its newest emergency_action or '
    'state_transition event (kind, time and its v2_event or reason) and its warning/critical event count. '
    'Dashboard duty only.';

CREATE VIEW public.v_planner_assignments WITH (security_barrier=true) AS
SELECT a.assignment_id,
       a.experiment_id,
       a.pair_index,
       a.block_index,
       a.arm_label,
       a.operation_kind,
       a.status,
       lower(a.valid_range) AS starts_at,
       upper(a.valid_range) AS ends_at,
       a.reason,
       a.locked_at,
       a.created_at
  FROM public.control_assignments a
 WHERE a.greenhouse_id = 'vallery';
COMMENT ON VIEW public.v_planner_assignments IS
    'Planner section (302, #1361): one row per assigned study day, its arm and pair, for the arm calendar. '
    'Dashboard duty only.';

CREATE VIEW public.v_planner_experiment_events WITH (security_barrier=true) AS
SELECT x.event_id,
       x.experiment_id,
       x.assignment_id,
       x.event_kind,
       x.severity,
       x.recorded_at
  FROM public.experiment_events x;
COMMENT ON VIEW public.v_planner_experiment_events IS
    'Planner section (302, #1361): each experiment event without its detail payload, for the calendar''s '
    'deviation marks. Dashboard duty only.';

CREATE VIEW public.v_planner_inference_runs WITH (security_barrier=true) AS
SELECT r.run_id,
       r.experiment_id,
       r.assignment_id,
       r.scheduled_in_arm,
       r.provider,
       r.model_id,
       r.started_at,
       r.ended_at,
       r.retries,
       r.input_tokens,
       r.output_tokens,
       r.cache_read_tokens,
       r.energy_wh,
       r.billed_cost_usd,
       r.created_at,
       to_jsonb(r) - ARRAY['proposal', 'response_body', 'error', 'price_snapshot', 'prompt_sha256',
                           'tool_manifest_sha256'] AS attempt
  FROM public.planner_inference_runs r;
COMMENT ON VIEW public.v_planner_inference_runs IS
    'Planner section (302, #1361): one row per planner call with its model, tokens, energy and timing. '
    '`attempt` is the whole row as jsonb without its bulky or free-text columns, so columns added later '
    '(the Cortex attempt columns of #1350) appear there without another migration. Dashboard duty only.';

REVOKE ALL ON public.v_planner_experiments, public.v_planner_assignments, public.v_planner_experiment_events,
    public.v_planner_inference_runs FROM PUBLIC;
GRANT SELECT ON public.v_planner_experiments, public.v_planner_assignments, public.v_planner_experiment_events,
    public.v_planner_inference_runs TO verdify_dashboard_runtime;

DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'f0a0d02a96692609b37d8429085163f4ce2668431a1840f7b1635856d846a547'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '533b81f25b05bb544d4ecffe62d4c16c51644acff1190bbde2a85dc49130687e'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM 'af3f7ef2493b94b11e9e7c3f5ead6bf61c8542e477d26da117f3930b0530d873'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
        OR EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
            WHERE c.oid IN ('public.v_planner_experiments'::regclass, 'public.v_planner_assignments'::regclass,
                            'public.v_planner_experiment_events'::regclass, 'public.v_planner_inference_runs'::regclass)
              AND (pg_get_userbyid(c.relowner) <> 'verdify'
                   OR a.grantee NOT IN (c.relowner, 'verdify_dashboard_runtime'::regrole::oid)
                   OR (a.grantee = 'verdify_dashboard_runtime'::regrole::oid AND a.privilege_type <> 'SELECT')))
        OR (SELECT count(*) FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
            WHERE c.oid IN ('public.v_planner_experiments'::regclass, 'public.v_planner_assignments'::regclass,
                            'public.v_planner_experiment_events'::regclass, 'public.v_planner_inference_runs'::regclass)
              AND a.grantee = 'verdify_dashboard_runtime'::regrole::oid AND a.privilege_type = 'SELECT')<>4
    THEN RAISE EXCEPTION '302 changed a runtime boundary'; END IF;
END $successor$;
