-- Read-only function/ACL fixture for migration 230. The data-bearing fixture
-- in test-214 executes this function with a completion, receipt, and two
-- observation epochs so PostgreSQL also plans and runs the repaired joins.
BEGIN;

DO $assertions$
DECLARE
    v_definition text;
BEGIN
    SELECT pg_get_functiondef(
               'public.fn_experiment_v2_read_observation_window(uuid,uuid,uuid,text,bigint)'::regprocedure)
      INTO v_definition;
    IF v_definition IS NULL OR
       position('r.source_epoch_id = e.source_epoch_id' in v_definition) = 0 OR
       position('r.work_id = e.work_id' in v_definition) = 0 OR
       position('r.bundle_id = e.bundle_id' in v_definition) = 0 OR
       position('completion.bundle_id = e.bundle_id' in v_definition) = 0 OR
       position('USING (bundle_id)' in v_definition) <> 0 OR
       has_function_privilege(
           'public',
           'public.fn_experiment_v2_read_observation_window(uuid,uuid,uuid,text,bigint)',
           'EXECUTE') OR
       NOT has_function_privilege(
           'verdify_experiment_component_executor',
           'public.fn_experiment_v2_read_observation_window(uuid,uuid,uuid,text,bigint)',
           'EXECUTE') THEN
        RAISE EXCEPTION
            'migration 230 observation-window identity joins/ACL are not exact';
    END IF;
END
$assertions$;

ROLLBACK;
