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

-- Exercise the immutable production-shaped lineage retained in the restored
-- dump. This is intentionally read-only: it proves that the exact fault and
-- two-receipt recovery which migration 236 rejected on the expired predecessor
-- authorization are admitted by 237's current-work range instead. It accepts
-- either the unresolved pre-call shape or the append-only post-call shape.
DO $live_shaped_recovery$
DECLARE
    v_candidate_count integer;
    v_old_range_rejection_count integer;
    v_exact_receipt_count integer;
BEGIN
    WITH candidates AS (
        SELECT auth.authorization_id,
               auth.proof_valid_range,
               aggressive.work_id AS aggressive_work_id,
               aggressive.created_at AS aggressive_created_at,
               fault.recorded_at AS fault_at,
               recovery.work_id AS recovery_work_id,
               recovery.valid_range AS recovery_valid_range,
               recovered.recorded_at AS recovered_at,
               (SELECT count(DISTINCT receipt.receipt_id)::integer
                  FROM public.experiment_v2_observation_receipts receipt
                 WHERE receipt.experiment_id = auth.experiment_id
                   AND receipt.work_id = recovery.work_id) AS receipt_count,
               (SELECT encode(digest(convert_to(
                           'verdify-direct-proof-startup-raw-reset-v3|' ||
                           auth.authorization_id::text || '|' ||
                           aggressive.work_id::text || '|' ||
                           recovery.work_id::text || '|' ||
                           string_agg(receipt.observation_receipt_sha256, '|'
                                      ORDER BY receipt.persisted_at,
                                               receipt.receipt_id),
                           'UTF8'), 'sha256'), 'hex')
                  FROM public.experiment_v2_observation_receipts receipt
                 WHERE receipt.experiment_id = auth.experiment_id
                   AND receipt.work_id = recovery.work_id) AS evidence_sha256
          FROM public.experiment_v2_direct_proof_authorizations auth
          JOIN public.experiment_v2_direct_proof_attempt_work mapped
            ON mapped.authorization_id = auth.authorization_id
           AND mapped.stage = 'aggressive'
          JOIN public.experiment_v2_work aggressive
            ON aggressive.experiment_id = auth.experiment_id
           AND aggressive.work_id = mapped.work_id
          JOIN public.experiment_v2_runtime_faults fault
            ON fault.experiment_id = auth.experiment_id
           AND fault.fault_source = 'raw_reset_epoch'
           AND fault.reported_fault_kind = 'reboot'
           AND fault.admission_state_after = 'baseline_recovery'
           AND fault.authority_hold_required
           AND NOT fault.facility_authority_yielded
           AND fault.recorded_at > aggressive.created_at
          JOIN public.experiment_v2_work recovery
            ON recovery.experiment_id = fault.experiment_id
           AND recovery.work_id = fault.recovery_work_id
          JOIN public.experiment_v2_work_events recovered
            ON recovered.experiment_id = recovery.experiment_id
           AND recovered.work_id = recovery.work_id
           AND recovered.event_kind = 'recovered'
         WHERE auth.experiment_id =
                   '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid
           AND fault.recorded_at <@ recovery.valid_range
           AND recovered.recorded_at <@ recovery.valid_range
           AND recovery.created_at >= aggressive.created_at
           AND NOT lower_inf(recovery.valid_range)
           AND NOT upper_inf(recovery.valid_range)
           AND lower_inc(recovery.valid_range)
           AND NOT upper_inc(recovery.valid_range)
           AND upper(recovery.valid_range) - lower(recovery.valid_range) =
               interval '5 minutes'
           AND recovery.expires_at = upper(recovery.valid_range)
           AND recovery.parent_work_id IS NULL
           AND recovery.operation_kind = 'baseline_recovery'
           AND recovery.target_profile = 'baseline'
           AND NOT EXISTS (
               SELECT 1
                 FROM public.experiment_v2_exposures exposure
                WHERE exposure.experiment_id = auth.experiment_id
                  AND exposure.work_id = aggressive.work_id)
           AND NOT EXISTS (
               SELECT 1
                 FROM public.experiment_v2_direct_proof_receipts proof
                WHERE proof.authorization_id = auth.authorization_id)
    )
    SELECT count(*) FILTER (WHERE receipt_count >= 2),
           count(*) FILTER (
               WHERE receipt_count >= 2
                 AND NOT (
                     fault_at <@ proof_valid_range AND
                     recovered_at <@ proof_valid_range)),
           count(*) FILTER (
               WHERE receipt_count >= 2
                 AND EXISTS (
                     SELECT 1
                       FROM public.experiment_v2_direct_proof_emergency_recovery_receipts sealed
                      WHERE sealed.authorization_id = candidates.authorization_id
                        AND sealed.recovery_work_id = candidates.recovery_work_id
                        AND sealed.recovery_evidence_sha256 =
                            candidates.evidence_sha256))
      INTO v_candidate_count, v_old_range_rejection_count,
           v_exact_receipt_count
      FROM candidates;

    IF v_candidate_count < 1 OR v_old_range_rejection_count < 1 THEN
        RAISE EXCEPTION
            'restored production-shaped migration 237 recovery lineage is absent or still depends on predecessor authorization time';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM public.experiment_v2_direct_proof_emergency_recovery_receipts sealed
          JOIN public.experiment_v2_direct_proof_authorizations auth
            USING (authorization_id)
         WHERE auth.experiment_id =
                   '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid
           AND sealed.recorded_by LIKE 'gitops:%') AND
       v_exact_receipt_count < 1 THEN
        RAISE EXCEPTION
            'migration 237 sealed receipt does not reproduce from its exact two observation receipts';
    END IF;
END
$live_shaped_recovery$;

ROLLBACK;
