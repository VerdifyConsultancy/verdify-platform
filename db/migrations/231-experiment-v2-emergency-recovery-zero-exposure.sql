-- 231-experiment-v2-emergency-recovery-zero-exposure.sql
--
-- Baseline recovery deliberately records its two advancing confirmation
-- receipts and terminal recovered event without opening a treatment exposure.
-- The emergency finish function from migration 225 incorrectly required one
-- open exposure for that work and therefore rejected the exact executor state
-- after physical baseline confirmation. Seal only from the intended zero-open-
-- exposure boundary; any open interval remains a hard failure.

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_finish_emergency_recovery(
    p_experiment_id uuid,
    p_resolution_id uuid,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_direct_proof_emergency_recovery_receipts
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_resolution public.experiment_v2_direct_proof_emergency_resolutions%ROWTYPE;
    v_existing public.experiment_v2_direct_proof_emergency_recovery_receipts%ROWTYPE;
    v_row public.experiment_v2_direct_proof_emergency_recovery_receipts%ROWTYPE;
    v_receipt_count integer;
    v_evidence_sha256 text;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-proof-' || p_experiment_id::text));
    SELECT * INTO v_existing
      FROM public.experiment_v2_direct_proof_emergency_recovery_receipts
     WHERE resolution_id = p_resolution_id;
    IF v_existing.resolution_id IS NOT NULL THEN
        IF v_existing.experiment_id <> p_experiment_id OR
           v_existing.recorded_by <> p_actor THEN
            RAISE EXCEPTION
                'direct-proof emergency recovery receipt is immutable and exact replay differs';
        END IF;
        RETURN v_existing;
    END IF;
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_resolution
      FROM public.experiment_v2_direct_proof_emergency_resolutions
     WHERE resolution_id = p_resolution_id
       AND experiment_id = p_experiment_id;
    IF v_exp.experiment_id IS NULL OR
       v_resolution.resolution_id IS NULL OR
       v_resolution.resolution_kind <> 'bounded_baseline_recovery' OR
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'commissioning' OR
       v_exp.admission_state <> 'baseline_recovery' OR
       NOT v_exp.component_enabled OR
       v_exp.revision_bundle_sha256 <>
           v_resolution.expected_revision_bundle_sha256 OR
       v_exp.lease_generation <>
           v_resolution.expected_emergency_lease_generation OR
       p_actor IS NULL OR length(p_actor) = 0 OR
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_work recovery
           JOIN public.experiment_v2_work_events recovered
             USING (experiment_id, work_id)
            WHERE recovery.experiment_id = p_experiment_id
              AND recovery.work_id = v_resolution.recovery_work_id
              AND recovery.operation_kind = 'baseline_recovery'
              AND recovery.target_profile = 'baseline'
              AND recovery.parent_work_id IS NULL
              AND recovery.valid_range = v_resolution.recovery_valid_range
              AND recovery.revision_bundle_sha256 =
                  v_resolution.expected_revision_bundle_sha256
              AND recovery.lease_generation =
                  v_resolution.expected_emergency_lease_generation
              AND recovered.event_kind = 'recovered') THEN
        RAISE EXCEPTION
            'direct-proof emergency recovery finishes only from its exact current-lease recovered baseline work';
    END IF;
    SELECT count(*)::integer,
           encode(digest(convert_to(
               'verdify-direct-proof-emergency-recovery-v1|' ||
               v_resolution.resolution_id::text || '|' ||
               v_resolution.recovery_work_id::text || '|' ||
               string_agg(receipt.observation_receipt_sha256, '|'
                          ORDER BY receipt.persisted_at, receipt.receipt_id),
               'UTF8'), 'sha256'), 'hex')
      INTO v_receipt_count, v_evidence_sha256
      FROM public.experiment_v2_observation_receipts receipt
     WHERE receipt.experiment_id = p_experiment_id
       AND receipt.work_id = v_resolution.recovery_work_id;
    IF v_receipt_count < 2 OR v_evidence_sha256 IS NULL THEN
        RAISE EXCEPTION
            'direct-proof emergency recovery requires two receipt-bound baseline epochs';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM public.experiment_v2_exposures exposure
          LEFT JOIN public.experiment_v2_exposure_closures closure
            USING (exposure_id)
         WHERE exposure.experiment_id = p_experiment_id
           AND closure.exposure_id IS NULL
    ) THEN
        RAISE EXCEPTION
            'direct-proof emergency recovery requires zero open exposure before sealing';
    END IF;
    PERFORM public.fn_experiment_v2_set_admission(
        p_experiment_id, 'closed', p_actor,
        'direct-proof-emergency-recovery-complete');
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET execution_phase = 'shadow', component_enabled = false,
           lease_generation = lease_generation + 1, updated_at = v_now
     WHERE experiment_id = p_experiment_id;
    INSERT INTO public.experiment_v2_direct_proof_emergency_recovery_receipts
        (resolution_id, authorization_id, experiment_id, recovery_work_id,
         recovery_evidence_sha256, recorded_by, recorded_at)
    VALUES
        (v_resolution.resolution_id, v_resolution.authorization_id,
         p_experiment_id, v_resolution.recovery_work_id,
         v_evidence_sha256, p_actor, v_now)
    RETURNING * INTO v_row;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES
        (p_experiment_id, 'state_transition', 'warning', p_actor,
         jsonb_build_object(
             'v2_phase', 'shadow',
             'v2_admission', 'closed',
             'v2_event', 'direct_proof_emergency_recovery_complete',
             'emergency_resolution_id', v_resolution.resolution_id,
             'recovery_work_id', v_resolution.recovery_work_id,
             'recovery_evidence_sha256', v_evidence_sha256), v_now);
    RETURN v_row;
END;
$body$;

ALTER FUNCTION public.fn_experiment_v2_direct_proof_finish_emergency_recovery(
    uuid, uuid, text)
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_direct_proof_finish_emergency_recovery(
        uuid, uuid, text)
    FROM PUBLIC CASCADE;
GRANT EXECUTE ON FUNCTION
    public.fn_experiment_v2_direct_proof_finish_emergency_recovery(
        uuid, uuid, text)
    TO verdify_experiment_lifecycle;
