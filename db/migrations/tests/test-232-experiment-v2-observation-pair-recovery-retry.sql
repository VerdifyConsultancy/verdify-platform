-- Read-only function/ACL fixture for migration 232.
BEGIN;

DO $assertions$
DECLARE
    v_window_definition text;
    v_retry_definition text;
BEGIN
    SELECT pg_get_functiondef(
               'public.fn_experiment_v2_read_observation_window(uuid,uuid,uuid,text,bigint)'::regprocedure)
      INTO v_window_definition;
    SELECT pg_get_functiondef(
               'public.fn_experiment_v2_direct_proof_retry_emergency_recovery(uuid,uuid,uuid,text,bigint,tstzrange,text,text,text)'::regprocedure)
      INTO v_retry_definition;
    IF v_window_definition IS NULL OR
       position('ranked_post_epochs' in v_window_definition) = 0 OR
       position('earliest_rank = 1 OR ranked.latest_rank = 1' in v_window_definition) = 0 OR
       position('v_now - e.last_observed_at <= interval ''90 seconds''' in v_window_definition) = 0 OR
       v_retry_definition IS NULL OR
       position('v_exp.admission_state = ''baseline_recovery''' in v_retry_definition) = 0 OR
       position('IF v_exp.admission_state = ''emergency_hold''' in v_retry_definition) = 0 OR
       position('failed_work.event_kind = ''failed''' in v_retry_definition) = 0 OR
       has_function_privilege(
           'public',
           'public.fn_experiment_v2_read_observation_window(uuid,uuid,uuid,text,bigint)',
           'EXECUTE') OR
       NOT has_function_privilege(
           'verdify_experiment_component_executor',
           'public.fn_experiment_v2_read_observation_window(uuid,uuid,uuid,text,bigint)',
           'EXECUTE') OR
       has_function_privilege(
           'public',
           'public.fn_experiment_v2_direct_proof_retry_emergency_recovery(uuid,uuid,uuid,text,bigint,tstzrange,text,text,text)',
           'EXECUTE') OR
       NOT has_function_privilege(
           'verdify_experiment_lifecycle',
           'public.fn_experiment_v2_direct_proof_retry_emergency_recovery(uuid,uuid,uuid,text,bigint,tstzrange,text,text,text)',
           'EXECUTE') THEN
        RAISE EXCEPTION
            'migration 232 observation pair/recovery retry boundary or ACL is not exact';
    END IF;
END
$assertions$;

ROLLBACK;
