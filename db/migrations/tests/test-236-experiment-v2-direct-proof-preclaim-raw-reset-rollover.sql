-- Read-only function/ACL fixture for migration 236.
BEGIN;

DO $assertions$
DECLARE
    v_definition text;
BEGIN
    SELECT pg_get_functiondef(
               'public.fn_experiment_v2_direct_proof_resolve_startup_rollover(uuid,uuid,text,text)'::regprocedure)
      INTO v_definition;
    IF v_definition IS NULL OR
       position('SELECT aggressive.created_at INTO v_aggressive_created_at' in v_definition) = 0 OR
       position('aggressive.work_id = v_aggressive_work_id' in v_definition) = 0 OR
       position('fault.recorded_at > v_aggressive_created_at' in v_definition) = 0 OR
       position('v_aggressive_created_at < v_fault_at AND v_fault_at < v_recovered_at' in v_definition) = 0 OR
       position('exposure.work_id = v_aggressive_work_id' in v_definition) = 0 OR
       position('closure.exposure_id IS NULL' in v_definition) = 0 OR
       position('count(DISTINCT receipt.receipt_id)::integer' in v_definition) = 0 OR
       position('verdify-direct-proof-startup-raw-reset-v2|' in v_definition) = 0 OR
       position('event.detail ->> ''v2_admission'' = ''open''' in v_definition) <> 0 OR
       has_function_privilege(
           'public',
           'public.fn_experiment_v2_direct_proof_resolve_startup_rollover(uuid,uuid,text,text)',
           'EXECUTE') OR
       NOT has_function_privilege(
           'verdify_experiment_lifecycle',
           'public.fn_experiment_v2_direct_proof_resolve_startup_rollover(uuid,uuid,text,text)',
           'EXECUTE') THEN
        RAISE EXCEPTION
            'migration 236 pre-claim raw-reset ordering/exposure gate or ACL is not exact';
    END IF;
END
$assertions$;

ROLLBACK;
