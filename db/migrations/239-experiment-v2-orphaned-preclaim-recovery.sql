-- 239-experiment-v2-orphaned-preclaim-recovery.sql
--
-- Migration 237 expected a raw-reset runtime-fault receipt that the retained
-- production lineage does not contain.  The device recovery is real, but its
-- cause is not proven: the immutable record contains an unclaimed aggressive
-- work item followed by a distinct, root, five-minute baseline recovery with
-- two observation receipts and a recovered event.  Resolve that failed proof
-- attempt without manufacturing a runtime fault and without crediting physical
-- proof.  The historical recovery may lag current authority by exactly one
-- lease generation; the caller must still hold attended, current baseline-
-- recovery authority before this append-only resolution can run.

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_resolve_startup_rollover(
    p_experiment_id uuid,
    p_authorization_id uuid,
    p_facility_authorization_ref text,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_direct_proof_emergency_recovery_receipts
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_auth public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_resolution public.experiment_v2_direct_proof_emergency_resolutions%ROWTYPE;
    v_existing public.experiment_v2_direct_proof_emergency_recovery_receipts%ROWTYPE;
    v_receipt public.experiment_v2_direct_proof_emergency_recovery_receipts%ROWTYPE;
    v_aggressive public.experiment_v2_work%ROWTYPE;
    v_recovery public.experiment_v2_work%ROWTYPE;
    v_baseline_before_work_id uuid;
    v_recovered_at timestamptz;
    v_receipt_count integer;
    v_evidence_sha256 text;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-proof-' || p_experiment_id::text));

    SELECT receipt.* INTO v_existing
      FROM public.experiment_v2_direct_proof_emergency_recovery_receipts receipt
     WHERE receipt.authorization_id = p_authorization_id;
    IF v_existing.resolution_id IS NOT NULL THEN
        IF v_existing.experiment_id <> p_experiment_id OR
           v_existing.recorded_by <> p_actor THEN
            RAISE EXCEPTION
                'direct-proof orphaned recovery resolution is immutable and exact replay differs';
        END IF;
        RETURN v_existing;
    END IF;

    SELECT * INTO v_exp
      FROM public.control_experiments
     WHERE experiment_id = p_experiment_id
     FOR UPDATE;
    SELECT * INTO v_auth
      FROM public.experiment_v2_direct_proof_authorizations
     WHERE authorization_id = p_authorization_id
       AND experiment_id = p_experiment_id;
    SELECT aggressive.* INTO v_aggressive
      FROM public.experiment_v2_direct_proof_attempt_work mapped
      JOIN public.experiment_v2_work aggressive
        ON aggressive.experiment_id = p_experiment_id
       AND aggressive.work_id = mapped.work_id
     WHERE mapped.authorization_id = p_authorization_id
       AND mapped.stage = 'aggressive';
    SELECT mapped.work_id INTO v_baseline_before_work_id
      FROM public.experiment_v2_direct_proof_attempt_work mapped
     WHERE mapped.authorization_id = p_authorization_id
       AND mapped.stage = 'baseline_before';

    -- Select only the latest recovered root baseline which has no causal
    -- runtime-fault receipt.  This records an orphaned recovery, not a reboot.
    SELECT recovery.* INTO v_recovery
      FROM public.experiment_v2_work recovery
      JOIN public.experiment_v2_work_events recovered
        ON recovered.experiment_id = recovery.experiment_id
       AND recovered.work_id = recovery.work_id
       AND recovered.event_kind = 'recovered'
     WHERE recovery.experiment_id = p_experiment_id
       AND recovery.created_at > v_aggressive.created_at
       AND recovery.created_at > upper(v_auth.proof_valid_range)
       AND NOT lower_inf(recovery.valid_range)
       AND NOT upper_inf(recovery.valid_range)
       AND lower_inc(recovery.valid_range)
       AND NOT upper_inc(recovery.valid_range)
       AND upper(recovery.valid_range) - lower(recovery.valid_range) =
           interval '5 minutes'
       AND recovery.expires_at = upper(recovery.valid_range)
       AND recovery.parent_work_id IS NULL
       AND recovery.work_id <> v_baseline_before_work_id
       AND recovery.operation_kind = 'baseline_recovery'
       AND recovery.target_profile = 'baseline'
       AND recovery.created_by = 'verdify-component-executor-v2'
       AND recovery.revision_bundle_sha256 = v_exp.revision_bundle_sha256
       AND recovery.lease_generation = v_aggressive.lease_generation
       AND recovery.lease_generation IN
           (v_exp.lease_generation, v_exp.lease_generation - 1)
       AND recovered.recorded_at <@ recovery.valid_range
       AND recovered.worker_ref = 'verdify-component-executor-v2'
       AND (recovered.detail->>'confirmed_at')::timestamptz <@
           recovery.valid_range
       AND NOT EXISTS (
           SELECT 1
             FROM public.experiment_v2_runtime_faults fault
            WHERE fault.experiment_id = recovery.experiment_id
              AND fault.recovery_work_id = recovery.work_id)
       AND NOT EXISTS (
           SELECT 1
             FROM public.experiment_v2_work_events terminal
            WHERE terminal.experiment_id = recovery.experiment_id
              AND terminal.work_id = recovery.work_id
              AND terminal.event_kind IN
                  ('failed', 'cancelled', 'superseded'))
       AND (SELECT count(DISTINCT observation.receipt_id)
              FROM public.experiment_v2_observation_receipts observation
             WHERE observation.experiment_id = recovery.experiment_id
               AND observation.work_id = recovery.work_id) >= 2
     ORDER BY recovery.created_at DESC, recovered.recorded_at DESC
     LIMIT 1;
    SELECT recovered.recorded_at INTO v_recovered_at
      FROM public.experiment_v2_work_events recovered
     WHERE recovered.experiment_id = p_experiment_id
       AND recovered.work_id = v_recovery.work_id
       AND recovered.event_kind = 'recovered'
     ORDER BY recovered.recorded_at DESC
     LIMIT 1;

    IF v_exp.experiment_id IS NULL OR
       v_exp.experiment_id <>
           '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid OR
       v_auth.authorization_id IS NULL OR
       v_auth.revision_bundle_sha256 <> v_exp.revision_bundle_sha256 OR
       v_exp.status <> 'draft' OR
       v_exp.execution_phase <> 'commissioning' OR
       v_exp.admission_state <> 'baseline_recovery' OR
       NOT v_exp.component_enabled OR
       v_aggressive.work_id IS NULL OR
       v_baseline_before_work_id IS NULL OR
       v_recovery.work_id IS NULL OR
       v_recovered_at IS NULL OR
       p_facility_authorization_ref IS NULL OR
       length(p_facility_authorization_ref) = 0 OR
       p_actor IS NULL OR length(p_actor) = 0 OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_authorizations newer
            WHERE newer.experiment_id = p_experiment_id
              AND newer.attempt_number > v_auth.attempt_number) OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_receipts proof
            WHERE proof.authorization_id = p_authorization_id) OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_attempt_events terminal
            WHERE terminal.authorization_id = p_authorization_id) OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_emergency_resolutions resolution
            WHERE resolution.authorization_id = p_authorization_id) OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_work_events terminal
            WHERE terminal.experiment_id = p_experiment_id
              AND terminal.work_id = v_aggressive.work_id
              AND terminal.event_kind IN
                  ('completed', 'failed', 'recovered', 'cancelled', 'superseded')) OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_exposures exposure
            WHERE exposure.experiment_id = p_experiment_id
              AND exposure.work_id = v_aggressive.work_id) OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_exposures exposure
             LEFT JOIN public.experiment_v2_exposure_closures closure
               USING (exposure_id)
            WHERE exposure.experiment_id = p_experiment_id
              AND closure.exposure_id IS NULL) THEN
        RAISE EXCEPTION
            'direct-proof startup rollover resolution requires the latest active orphaned-preclaim attempt, its recovered root baseline without a runtime-fault receipt, and zero open exposure';
    END IF;

    SELECT count(DISTINCT observation.receipt_id)::integer,
           encode(digest(convert_to(
               'verdify-direct-proof-orphaned-preclaim-recovery-v1|' ||
               p_authorization_id::text || '|' ||
               v_aggressive.work_id::text || '|' ||
               v_recovery.work_id::text || '|' ||
               string_agg(observation.observation_receipt_sha256, '|'
                          ORDER BY observation.persisted_at,
                                   observation.receipt_id),
               'UTF8'), 'sha256'), 'hex')
      INTO v_receipt_count, v_evidence_sha256
      FROM public.experiment_v2_observation_receipts observation
     WHERE observation.experiment_id = p_experiment_id
       AND observation.work_id = v_recovery.work_id;
    IF v_receipt_count < 2 OR v_evidence_sha256 IS NULL THEN
        RAISE EXCEPTION
            'direct-proof orphaned recovery resolution requires two receipt-bound recovered baseline epochs';
    END IF;

    INSERT INTO public.experiment_v2_direct_proof_emergency_resolutions
        (authorization_id, experiment_id, resolution_kind,
         expected_revision_bundle_sha256,
         expected_emergency_lease_generation, facility_authorization_ref,
         recovery_work_id, recovery_valid_range, reason,
         recorded_by, recorded_at)
    VALUES
        (p_authorization_id, p_experiment_id, 'bounded_baseline_recovery',
         v_exp.revision_bundle_sha256, v_exp.lease_generation,
         p_facility_authorization_ref, v_recovery.work_id,
         v_recovery.valid_range,
         'Orphaned preclaim recovery; runtime-fault receipt absent and cause unproven',
         p_actor, v_now)
    RETURNING * INTO v_resolution;
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref,
         claim_expires_at, detail, recorded_at)
    SELECT p_experiment_id, v_aggressive.work_id, 'failed', p_actor,
           NULL,
           jsonb_build_object(
               'reason', 'orphaned_preclaim_recovery_cause_unproven',
               'direct_proof_authorization_id', p_authorization_id,
               'runtime_fault_receipt', 'absent',
               'recovery_work_id', v_recovery.work_id),
           v_now
     WHERE NOT EXISTS (
         SELECT 1
           FROM public.experiment_v2_work_events terminal
          WHERE terminal.experiment_id = p_experiment_id
            AND terminal.work_id = v_aggressive.work_id
            AND terminal.event_kind IN
                ('completed', 'failed', 'recovered', 'cancelled', 'superseded'));
    INSERT INTO public.experiment_v2_direct_proof_attempt_events
        (authorization_id, experiment_id, event_kind,
         successor_authorization_id, reason, recorded_by, recorded_at)
    VALUES
        (p_authorization_id, p_experiment_id, 'failed', NULL,
         'orphaned preclaim recovery sealed; runtime cause unproven',
         p_actor, v_now);
    INSERT INTO public.experiment_v2_direct_proof_emergency_recovery_receipts
        (resolution_id, authorization_id, experiment_id, recovery_work_id,
         recovery_evidence_sha256, recorded_by, recorded_at)
    VALUES
        (v_resolution.resolution_id, p_authorization_id, p_experiment_id,
         v_recovery.work_id, v_evidence_sha256, p_actor, v_now)
    RETURNING * INTO v_receipt;

    -- The retained recovered baseline is deliberately one lease behind current
    -- authority.  The generic admission helper only closes baseline recovery
    -- on same-lease evidence, so this exact repair performs the close and
    -- authority fence atomically after sealing the immutable receipt above.
    -- Do not manufacture a current recovery merely to satisfy that helper.
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET execution_phase = 'shadow', admission_state = 'closed',
           component_enabled = false,
           lease_generation = lease_generation + 1, updated_at = v_now
     WHERE experiment_id = p_experiment_id;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES
        (p_experiment_id, 'state_transition', 'warning', p_actor,
         jsonb_build_object(
             'v2_phase', 'shadow',
             'v2_admission', 'closed',
             'v2_event', 'direct_proof_orphaned_preclaim_recovery_sealed',
             'authorization_id', p_authorization_id,
             'emergency_resolution_id', v_resolution.resolution_id,
             'aggressive_work_id', v_aggressive.work_id,
             'runtime_fault_receipt', 'absent',
             'recovery_cause', 'unproven',
             'recovery_work_id', v_recovery.work_id,
             'recovery_evidence_sha256', v_evidence_sha256),
         v_now);
    RETURN v_receipt;
END;
$body$;

ALTER FUNCTION public.fn_experiment_v2_direct_proof_resolve_startup_rollover(
    uuid, uuid, text, text)
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_direct_proof_resolve_startup_rollover(
        uuid, uuid, text, text)
    FROM PUBLIC CASCADE;
GRANT EXECUTE ON FUNCTION
    public.fn_experiment_v2_direct_proof_resolve_startup_rollover(
        uuid, uuid, text, text)
    TO verdify_experiment_lifecycle;
