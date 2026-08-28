-- Read-only catalog/ACL fixture for migration 229.
BEGIN;

DO $assertions$
DECLARE
    v_definition text;
BEGIN
    SELECT pg_get_functiondef(
               'public.fn_experiment_v2_direct_proof_recovery_generation_guard()'::regprocedure)
      INTO v_definition;
    IF v_definition IS NULL OR
       position('failed_work.event_kind = ''failed''' in v_definition) = 0 OR
       position('max(failed_work.recorded_at)' in v_definition) = 0 OR
       position('v_generation.recorded_at <= v_predecessor_failed_at' in
                v_definition) = 0 OR
       has_function_privilege(
           'public',
           'public.fn_experiment_v2_direct_proof_recovery_generation_guard()',
           'EXECUTE') OR
       NOT EXISTS (
           SELECT 1
             FROM pg_trigger
            WHERE tgrelid =
                  'public.experiment_v2_direct_proof_emergency_resolutions'::regclass
              AND tgname = 'trg_direct_proof_recovery_generation_guard'
              AND NOT tgisinternal) THEN
        RAISE EXCEPTION 'migration 229 terminal-failure generation guard is not exact';
    END IF;
END
$assertions$;

ROLLBACK;
