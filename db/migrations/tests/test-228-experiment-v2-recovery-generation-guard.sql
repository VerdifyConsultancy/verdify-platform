-- Read-only catalog/ACL fixture for migration 228.
BEGIN;

DO $assertions$
BEGIN
    IF to_regprocedure(
           'public.fn_experiment_v2_direct_proof_recovery_generation_guard()')
           IS NULL OR
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
              AND NOT tgisinternal
              AND (tgtype & 2) = 2
              AND (tgtype & 4) = 4) THEN
        RAISE EXCEPTION 'migration 228 generation guard/ACL is not exact';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.experiment_v2_direct_proof_emergency_recovery_attempt_events event
          JOIN public.experiment_v2_direct_proof_emergency_resolutions predecessor
            ON predecessor.resolution_id = event.failed_resolution_id
          JOIN public.experiment_v2_direct_proof_emergency_resolutions successor
            ON successor.resolution_id = event.successor_resolution_id
         WHERE successor.recovery_attempt_number <>
               predecessor.recovery_attempt_number + 1) THEN
        RAISE EXCEPTION 'migration 228 found a malformed historical recovery chain';
    END IF;
END
$assertions$;

ROLLBACK;
