-- 228-experiment-v2-recovery-generation-guard.sql
--
-- A replacement writer may not have registered its generation when a
-- PostSync proof runner first connects. Migration 227's age test could then
-- read the predecessor pod's already-old row. Guard every appended bounded
-- recovery at the table boundary: the generation must be both newer than the
-- failed predecessor resolution and old enough to have drained startup hold.

CREATE OR REPLACE FUNCTION
    public.fn_experiment_v2_direct_proof_recovery_generation_guard()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_predecessor_recorded_at timestamptz;
    v_generation public.experiment_v2_runtime_generations%ROWTYPE;
    v_now timestamptz := clock_timestamp();
BEGIN
    IF NEW.resolution_kind <> 'bounded_baseline_recovery' OR
       NEW.recovery_attempt_number <= 1 THEN
        RETURN NEW;
    END IF;

    SELECT predecessor.recorded_at INTO v_predecessor_recorded_at
      FROM public.experiment_v2_direct_proof_emergency_resolutions predecessor
     WHERE predecessor.authorization_id = NEW.authorization_id
       AND predecessor.recovery_attempt_number =
           NEW.recovery_attempt_number - 1;
    SELECT generation.* INTO v_generation
      FROM public.experiment_v2_runtime_generations generation
     WHERE generation.experiment_id = NEW.experiment_id
     ORDER BY generation.generation_event_id DESC
     LIMIT 1;

    IF v_predecessor_recorded_at IS NULL THEN
        RAISE EXCEPTION
            'direct-proof emergency recovery retry requires its exact predecessor generation';
    END IF;
    IF v_generation.generation_event_id IS NULL OR
       v_generation.recorded_at <= v_predecessor_recorded_at OR
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

DROP TRIGGER IF EXISTS
    trg_direct_proof_recovery_generation_guard
    ON public.experiment_v2_direct_proof_emergency_resolutions;
CREATE TRIGGER trg_direct_proof_recovery_generation_guard
    BEFORE INSERT
    ON public.experiment_v2_direct_proof_emergency_resolutions
    FOR EACH ROW EXECUTE FUNCTION
        public.fn_experiment_v2_direct_proof_recovery_generation_guard();
