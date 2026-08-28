-- Read-only function/ACL fixture for migration 235.
BEGIN;

DO $assertions$
DECLARE
    v_definition text;
BEGIN
    SELECT pg_get_functiondef(
               'public.fn_experiment_v2_direct_proof_resolve_startup_rollover(uuid,uuid,text,text)'::regprocedure)
      INTO v_definition;
    IF v_definition IS NULL OR
       position('fault.fault_source = ''raw_reset_epoch''' in v_definition) = 0 OR
       position('fault.reported_fault_kind = ''reboot''' in v_definition) = 0 OR
       position('recovery.parent_work_id IS NULL' in v_definition) = 0 OR
       position('v_opened_at < v_fault_at AND v_fault_at < v_recovered_at' in v_definition) = 0 OR
       position('count(DISTINCT receipt.receipt_id)::integer' in v_definition) = 0 OR
       position('closure.exposure_id IS NULL' in v_definition) = 0 OR
       position('startup_raw_reset_before_aggressive_claim' in v_definition) = 0 OR
       has_function_privilege(
           'public',
           'public.fn_experiment_v2_direct_proof_resolve_startup_rollover(uuid,uuid,text,text)',
           'EXECUTE') OR
       NOT has_function_privilege(
           'verdify_experiment_lifecycle',
           'public.fn_experiment_v2_direct_proof_resolve_startup_rollover(uuid,uuid,text,text)',
           'EXECUTE') THEN
        RAISE EXCEPTION
            'migration 235 raw-reset rollover evidence gate/ACL is not exact';
    END IF;
END
$assertions$;

ROLLBACK;
