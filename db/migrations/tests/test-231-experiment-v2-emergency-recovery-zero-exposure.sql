-- Read-only function/ACL fixture for migration 231.
BEGIN;

DO $assertions$
DECLARE
    v_definition text;
BEGIN
    SELECT pg_get_functiondef(
               'public.fn_experiment_v2_direct_proof_finish_emergency_recovery(uuid,uuid,text)'::regprocedure)
      INTO v_definition;
    IF v_definition IS NULL OR
       position('recovered.event_kind = ''recovered''' in v_definition) = 0 OR
       position('v_receipt_count < 2' in v_definition) = 0 OR
       position('closure.exposure_id IS NULL' in v_definition) = 0 OR
       position('requires zero open exposure before sealing' in v_definition) = 0 OR
       position('fn_experiment_v2_close_exposure' in v_definition) <> 0 OR
       has_function_privilege(
           'public',
           'public.fn_experiment_v2_direct_proof_finish_emergency_recovery(uuid,uuid,text)',
           'EXECUTE') OR
       NOT has_function_privilege(
           'verdify_experiment_lifecycle',
           'public.fn_experiment_v2_direct_proof_finish_emergency_recovery(uuid,uuid,text)',
           'EXECUTE') THEN
        RAISE EXCEPTION
            'migration 231 emergency recovery zero-exposure boundary/ACL is not exact';
    END IF;
END
$assertions$;

ROLLBACK;
