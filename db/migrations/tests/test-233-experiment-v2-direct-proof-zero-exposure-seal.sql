-- Read-only function/ACL fixture for migration 233.
BEGIN;

DO $assertions$
DECLARE
    v_definition text;
BEGIN
    SELECT pg_get_functiondef(
               'public.fn_experiment_v2_direct_proof_finish(uuid,text)'::regprocedure)
      INTO v_definition;
    IF v_definition IS NULL OR
       position('v_before_at < v_aggressive_at AND v_aggressive_at < v_after_at' in v_definition) = 0 OR
       position('v_before_count < 2 OR v_aggressive_count < 2 OR v_after_count < 2' in v_definition) = 0 OR
       position('closure.exposure_id IS NULL' in v_definition) = 0 OR
       position('requires zero open exposure before sealing' in v_definition) = 0 OR
       position('fn_experiment_v2_close_exposure' in v_definition) <> 0 OR
       has_function_privilege(
           'public',
           'public.fn_experiment_v2_direct_proof_finish(uuid,text)',
           'EXECUTE') OR
       NOT has_function_privilege(
           'verdify_experiment_lifecycle',
           'public.fn_experiment_v2_direct_proof_finish(uuid,text)',
           'EXECUTE') THEN
        RAISE EXCEPTION
            'migration 233 direct-proof zero-exposure seal/ACL is not exact';
    END IF;
END
$assertions$;

ROLLBACK;
