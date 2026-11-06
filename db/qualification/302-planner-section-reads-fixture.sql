-- Run only inside an explicit rollback-only transaction after exact302 (#1361, #1360), as the superuser owner:
-- scripts/qualify-migration-successor.py --native-fixture runs it at the end of its final session. The four
-- Planner-section views are verdify's with SELECT for the dashboard duty and nobody else; the dashboard login
-- attests and reads every column of each; the 2026-08-28 draft (45039c86) is listed with the facility safe
-- closure of 2026-09-30 as its closing event; each view answers within 5 s; the api login still reads none of
-- them. Nothing is written. The last line reports the result to the qualifier.
DO $catalog$
BEGIN
    IF EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
        WHERE c.oid IN ('public.v_planner_experiments'::regclass, 'public.v_planner_assignments'::regclass,
                        'public.v_planner_experiment_events'::regclass, 'public.v_planner_inference_runs'::regclass)
          AND (pg_get_userbyid(c.relowner) <> 'verdify'
               OR a.grantee NOT IN (c.relowner, 'verdify_dashboard_runtime'::regrole::oid)
               OR (a.grantee = 'verdify_dashboard_runtime'::regrole::oid AND a.privilege_type <> 'SELECT')))
       OR has_table_privilege('verdify_api_runtime_login', 'public.v_planner_experiments', 'SELECT')
       OR has_table_privilege('verdify_api_runtime_login', 'public.v_planner_inference_runs', 'SELECT')
       OR has_table_privilege('verdify_ingestor_runtime_login', 'public.v_planner_inference_runs', 'SELECT')
       OR has_table_privilege('verdify_dashboard_runtime_login', 'public.planner_inference_runs', 'SELECT')
       OR has_table_privilege('verdify_dashboard_runtime_login', 'public.control_experiments', 'SELECT')
    THEN RAISE EXCEPTION '302 planner section grants are not exactly the dashboard duty''s SELECT'; END IF;
END $catalog$;

-- SET SESSION AUTHORIZATION needs the superuser owner; the rollback undoes it. The login keeps its result in
-- transaction-local settings, so it needs no table of its own.
SET SESSION AUTHORIZATION verdify_dashboard_runtime_login;
DO $dashboard$
DECLARE
    started timestamptz := clock_timestamp();
    closing text;
BEGIN
    IF verdify_dashboard_runtime.fn_attest_login() IS NOT TRUE THEN
        RAISE EXCEPTION '302 dashboard login does not attest';
    END IF;
    -- Every column: a view's unused columns plan away, and with them the reads they make.
    PERFORM * FROM public.v_planner_experiments;
    IF NOT FOUND THEN RAISE EXCEPTION '302 dashboard login read no experiment'; END IF;
    SELECT closing_event_reason INTO closing FROM public.v_planner_experiments
     WHERE experiment_id = '45039c86-c1d9-52f6-a0a9-d94a17bc4b14';
    PERFORM * FROM public.v_planner_assignments;
    PERFORM * FROM public.v_planner_experiment_events WHERE recorded_at >= now() - interval '120 days';
    PERFORM * FROM public.v_planner_inference_runs WHERE started_at >= now() - interval '90 days';
    PERFORM set_config('planner302.closing', coalesce(closing, ''), true);
    PERFORM set_config('planner302.ms', round(extract(epoch FROM clock_timestamp() - started) * 1000)::text, true);
END $dashboard$;
RESET SESSION AUTHORIZATION;

DO $fixture$
BEGIN
    IF current_setting('planner302.closing') <> 'facility_safe_closure'
       OR current_setting('planner302.ms')::numeric > 5000
    THEN RAISE EXCEPTION '302 planner section native fixture failed'; END IF;
END $fixture$;

SELECT 'QUALIFY native_fixture ' || json_build_object(
    'checks', json_build_array('grants_dashboard_select_only', 'api_and_ingestor_read_nothing_new',
                               'dashboard_login_attests', 'dashboard_login_reads_every_column',
                               'old_draft_closing_event', 'read_within_5_s'),
    'closing_event_reason', current_setting('planner302.closing'),
    'read_ms', current_setting('planner302.ms')::numeric,
    'all_passed', true,
    'attested', json_build_object('dashboard', true))::text;
