-- Read-only function/ACL fixture for migration 234.
BEGIN;

DO $assertions$
DECLARE
    v_definition text;
BEGIN
    SELECT pg_get_functiondef(
               'public.fn_experiment_v2_direct_proof_resolve_startup_rollover(uuid,uuid,text,text)'::regprocedure)
      INTO v_definition;
    IF v_definition IS NULL OR
       position('recovery.parent_work_id = v_aggressive_work_id' in v_definition) = 0 OR
       position('recovery.work_id <> v_baseline_before_work_id' in v_definition) = 0 OR
       position('recovered.recorded_at > v_failed_at' in v_definition) = 0 OR
       position('count(DISTINCT receipt.receipt_id)::integer' in v_definition) = 0 OR
       position('closure.exposure_id IS NULL' in v_definition) = 0 OR
       position('lease_generation = lease_generation + 1' in v_definition) = 0 OR
       has_function_privilege(
           'public',
           'public.fn_experiment_v2_direct_proof_resolve_startup_rollover(uuid,uuid,text,text)',
           'EXECUTE') OR
       NOT has_function_privilege(
           'verdify_experiment_lifecycle',
           'public.fn_experiment_v2_direct_proof_resolve_startup_rollover(uuid,uuid,text,text)',
           'EXECUTE') THEN
        RAISE EXCEPTION
            'migration 234 startup-rollover evidence gate/ACL is not exact';
    END IF;
END
$assertions$;

ROLLBACK;
