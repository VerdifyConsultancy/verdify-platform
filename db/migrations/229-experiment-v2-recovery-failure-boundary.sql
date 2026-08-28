-- 229-experiment-v2-recovery-failure-boundary.sql
--
-- The causal boundary for a successor recovery is the predecessor recovery
-- work's immutable terminal failure, not the earlier resolution admission.
-- A writer can register between those events and still be the writer that
-- failed. Replace migration 228's guard definition with the exact boundary.

CREATE OR REPLACE FUNCTION
    public.fn_experiment_v2_direct_proof_recovery_generation_guard()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_predecessor_failed_at timestamptz;
    v_generation public.experiment_v2_runtime_generations%ROWTYPE;
    v_now timestamptz := clock_timestamp();
BEGIN
    IF NEW.resolution_kind <> 'bounded_baseline_recovery' OR
       NEW.recovery_attempt_number <= 1 THEN
        RETURN NEW;
    END IF;

    SELECT max(failed_work.recorded_at) INTO v_predecessor_failed_at
      FROM public.experiment_v2_direct_proof_emergency_resolutions predecessor
      JOIN public.experiment_v2_work_events failed_work
        ON failed_work.experiment_id = predecessor.experiment_id
       AND failed_work.work_id = predecessor.recovery_work_id
       AND failed_work.event_kind = 'failed'
     WHERE predecessor.authorization_id = NEW.authorization_id
       AND predecessor.recovery_attempt_number =
           NEW.recovery_attempt_number - 1;
    SELECT generation.* INTO v_generation
      FROM public.experiment_v2_runtime_generations generation
     WHERE generation.experiment_id = NEW.experiment_id
     ORDER BY generation.generation_event_id DESC
     LIMIT 1;

    IF v_predecessor_failed_at IS NULL THEN
        RAISE EXCEPTION
            'direct-proof emergency recovery retry requires its predecessor terminal failure';
    END IF;
    IF v_generation.generation_event_id IS NULL OR
       v_generation.recorded_at <= v_predecessor_failed_at OR
       v_generation.recorded_at > v_now - interval '4 minutes' OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_runtime_faults fault
            WHERE fault.experiment_id = NEW.experiment_id
              AND fault.recorded_at > v_now - interval '2 minutes') THEN
        RAISE EXCEPTION
            'current writer generation is not yet stable for direct-proof emergency recovery retry';
    END IF;
    RETURN NEW;
END;
$body$;

ALTER FUNCTION
    public.fn_experiment_v2_direct_proof_recovery_generation_guard()
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_direct_proof_recovery_generation_guard()
    FROM PUBLIC CASCADE;
