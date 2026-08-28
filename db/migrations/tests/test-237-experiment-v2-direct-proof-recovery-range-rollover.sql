-- Read-only function/ACL fixture for migration 237.
BEGIN;

DO $assertions$
DECLARE
    v_definition text;
BEGIN
    SELECT pg_get_functiondef(
               'public.fn_experiment_v2_direct_proof_resolve_startup_rollover(uuid,uuid,text,text)'::regprocedure)
      INTO v_definition;
    IF v_definition IS NULL OR
       position('fault.recorded_at <@ recovery.valid_range' in v_definition) = 0 OR
       position('recovered.recorded_at <@ recovery.valid_range' in v_definition) = 0 OR
       position('recovery.created_at >= v_aggressive_created_at' in v_definition) = 0 OR
       position('recovery.expires_at = upper(recovery.valid_range)' in v_definition) = 0 OR
       position('interval ''5 minutes''' in v_definition) = 0 OR
       position('recovery.lease_generation = v_exp.lease_generation' in v_definition) = 0 OR
       position('exposure.work_id = v_aggressive_work_id' in v_definition) = 0 OR
       position('closure.exposure_id IS NULL' in v_definition) = 0 OR
       position('verdify-direct-proof-startup-raw-reset-v3|' in v_definition) = 0 OR
       position('fault.recorded_at <@ v_auth.proof_valid_range' in v_definition) <> 0 OR
       position('recovered.recorded_at <@ v_auth.proof_valid_range' in v_definition) <> 0 OR
       has_function_privilege(
           'public',
           'public.fn_experiment_v2_direct_proof_resolve_startup_rollover(uuid,uuid,text,text)',
           'EXECUTE') OR
       NOT has_function_privilege(
           'verdify_experiment_lifecycle',
           'public.fn_experiment_v2_direct_proof_resolve_startup_rollover(uuid,uuid,text,text)',
           'EXECUTE') THEN
        RAISE EXCEPTION
            'migration 237 recovery-range rollover evidence gate or ACL is not exact';
    END IF;
END
$assertions$;

ROLLBACK;
