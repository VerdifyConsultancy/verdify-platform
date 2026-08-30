-- test-239-experiment-v2-orphaned-preclaim-recovery.sql
-- Transactionally rolled-back function/ACL and restored production-lineage
-- fixture.  The final block exercises the exact unsealed production-shaped
-- attempt, then rolls every append and authority transition back.
BEGIN;

DO $assertions$
DECLARE
    v_definition text;
BEGIN
    SELECT pg_get_functiondef(
               'public.fn_experiment_v2_direct_proof_resolve_startup_rollover(uuid,uuid,text,text)'::regprocedure)
      INTO v_definition;
    IF v_definition IS NULL OR
       position('recovery.created_at > upper(v_auth.proof_valid_range)' in
                v_definition) = 0 OR
       position('recovery.lease_generation = v_aggressive.lease_generation' in
                v_definition) = 0 OR
       position('v_exp.lease_generation - 1' in v_definition) = 0 OR
       position('public.experiment_v2_runtime_faults fault' in v_definition) = 0 OR
       position('fault.recovery_work_id = recovery.work_id' in v_definition) = 0 OR
       position('verdify-direct-proof-orphaned-preclaim-recovery-v1|' in
                v_definition) = 0 OR
       position('runtime_fault_receipt'', ''absent' in v_definition) = 0 OR
       position('recovery_cause'', ''unproven' in v_definition) = 0 OR
       position('admission_state = ''closed''' in v_definition) = 0 OR
       position('fn_experiment_v2_set_admission' in v_definition) <> 0 OR
       position('verdify-direct-proof-startup-raw-reset-' in v_definition) <> 0 OR
       has_function_privilege(
           'public',
           'public.fn_experiment_v2_direct_proof_resolve_startup_rollover(uuid,uuid,text,text)',
           'EXECUTE') OR
       NOT has_function_privilege(
           'verdify_experiment_lifecycle',
           'public.fn_experiment_v2_direct_proof_resolve_startup_rollover(uuid,uuid,text,text)',
           'EXECUTE') THEN
        RAISE EXCEPTION
            'migration 239 orphaned-preclaim recovery evidence gate or ACL is not exact';
    END IF;
END
$assertions$;

DO $live_shaped_recovery$
DECLARE
    v_candidate_count integer;
    v_outside_predecessor_count integer;
    v_missing_fault_receipt_count integer;
    v_exact_receipt_count integer;
BEGIN
    WITH candidates AS (
        SELECT auth.authorization_id,
               auth.proof_valid_range,
               aggressive.work_id AS aggressive_work_id,
               aggressive.created_at AS aggressive_created_at,
               aggressive.lease_generation AS aggressive_lease_generation,
               recovery.work_id AS recovery_work_id,
               recovery.created_at AS recovery_created_at,
               recovery.valid_range AS recovery_valid_range,
               recovery.lease_generation AS recovery_lease_generation,
               recovered.recorded_at AS recovered_at,
               (SELECT count(DISTINCT observation.receipt_id)::integer
                  FROM public.experiment_v2_observation_receipts observation
                 WHERE observation.experiment_id = auth.experiment_id
                   AND observation.work_id = recovery.work_id) AS receipt_count,
               NOT EXISTS (
                   SELECT 1
                     FROM public.experiment_v2_runtime_faults fault
                    WHERE fault.experiment_id = auth.experiment_id
                      AND fault.recovery_work_id = recovery.work_id) AS fault_receipt_absent,
               (SELECT encode(digest(convert_to(
                           'verdify-direct-proof-orphaned-preclaim-recovery-v1|' ||
                           auth.authorization_id::text || '|' ||
                           aggressive.work_id::text || '|' ||
                           recovery.work_id::text || '|' ||
                           string_agg(observation.observation_receipt_sha256, '|'
                                      ORDER BY observation.persisted_at,
                                               observation.receipt_id),
                           'UTF8'), 'sha256'), 'hex')
                  FROM public.experiment_v2_observation_receipts observation
                 WHERE observation.experiment_id = auth.experiment_id
                   AND observation.work_id = recovery.work_id) AS evidence_sha256
          FROM public.experiment_v2_direct_proof_authorizations auth
          JOIN public.experiment_v2_direct_proof_attempt_work mapped
            ON mapped.authorization_id = auth.authorization_id
           AND mapped.stage = 'aggressive'
          JOIN public.experiment_v2_work aggressive
            ON aggressive.experiment_id = auth.experiment_id
           AND aggressive.work_id = mapped.work_id
          JOIN public.experiment_v2_work recovery
            ON recovery.experiment_id = auth.experiment_id
           AND recovery.created_at > aggressive.created_at
           AND recovery.created_at > upper(auth.proof_valid_range)
           AND recovery.lease_generation = aggressive.lease_generation
          JOIN public.experiment_v2_work_events recovered
            ON recovered.experiment_id = recovery.experiment_id
           AND recovered.work_id = recovery.work_id
           AND recovered.event_kind = 'recovered'
         WHERE auth.experiment_id =
                   '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid
           AND recovery.parent_work_id IS NULL
           AND recovery.operation_kind = 'baseline_recovery'
           AND recovery.target_profile = 'baseline'
           AND recovery.created_by = 'verdify-component-executor-v2'
           AND recovery.revision_bundle_sha256 = auth.revision_bundle_sha256
           AND NOT lower_inf(recovery.valid_range)
           AND NOT upper_inf(recovery.valid_range)
           AND lower_inc(recovery.valid_range)
           AND NOT upper_inc(recovery.valid_range)
           AND upper(recovery.valid_range) - lower(recovery.valid_range) =
               interval '5 minutes'
           AND recovery.expires_at = upper(recovery.valid_range)
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
                 AND recovery_created_at > upper(proof_valid_range)
                 AND recovered_at > upper(proof_valid_range)),
           count(*) FILTER (
               WHERE receipt_count >= 2 AND fault_receipt_absent),
           count(*) FILTER (
               WHERE receipt_count >= 2
                 AND EXISTS (
                     SELECT 1
                       FROM public.experiment_v2_direct_proof_emergency_recovery_receipts sealed
                      WHERE sealed.authorization_id = candidates.authorization_id
                        AND sealed.recovery_work_id = candidates.recovery_work_id
                        AND sealed.recovery_evidence_sha256 =
                            candidates.evidence_sha256))
      INTO v_candidate_count, v_outside_predecessor_count,
           v_missing_fault_receipt_count, v_exact_receipt_count
      FROM candidates;

    IF v_candidate_count < 1 OR
       v_outside_predecessor_count < 1 OR
       v_missing_fault_receipt_count < 1 THEN
        RAISE EXCEPTION
            'restored production-shaped migration 239 orphaned-preclaim recovery lineage is absent, causalized without evidence, or still predecessor-range bound';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM public.experiment_v2_direct_proof_emergency_recovery_receipts sealed
          JOIN public.experiment_v2_direct_proof_authorizations auth
            USING (authorization_id)
          JOIN public.experiment_v2_direct_proof_attempt_work mapped
            ON mapped.authorization_id = auth.authorization_id
           AND mapped.stage = 'aggressive'
          JOIN public.experiment_v2_work aggressive
            ON aggressive.experiment_id = auth.experiment_id
           AND aggressive.work_id = mapped.work_id
          JOIN public.experiment_v2_work recovery
            ON recovery.experiment_id = auth.experiment_id
           AND recovery.work_id = sealed.recovery_work_id
           AND recovery.created_at > upper(auth.proof_valid_range)
           AND recovery.lease_generation = aggressive.lease_generation
         WHERE auth.experiment_id =
                   '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid
           AND NOT EXISTS (
               SELECT 1
                 FROM public.experiment_v2_runtime_faults fault
                WHERE fault.experiment_id = recovery.experiment_id
                  AND fault.recovery_work_id = recovery.work_id)) AND
       v_exact_receipt_count < 1 THEN
        RAISE EXCEPTION
            'migration 239 sealed orphaned-recovery receipt does not reproduce from its exact observation receipts';
    END IF;
END
$live_shaped_recovery$;

DO $execute_unsealed_recovery$
DECLARE
    c_experiment_id constant uuid :=
        '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid;
    c_authorization_id constant uuid :=
        'd00304d1-74f9-4872-857e-6944de53ac46'::uuid;
    c_aggressive_work_id constant uuid :=
        '7aa1f560-a309-4d17-b9ad-57a20574f05d'::uuid;
    c_recovery_work_id constant uuid :=
        '7093f8c3-a36e-49f2-8b4b-443d32a9a51b'::uuid;
    c_evidence_sha256 constant text :=
        '0fa6d172de87cf2008d5908ff4a3517eeca1d1cd4811e86461fc349c25f41b91';
    v_before public.control_experiments%ROWTYPE;
    v_after public.control_experiments%ROWTYPE;
    v_receipt public.experiment_v2_direct_proof_emergency_recovery_receipts%ROWTYPE;
    v_reproduced_sha256 text;
BEGIN
    -- A later rehearsal of an already-sealed dump is covered by the immutable
    -- receipt reproduction above.  Exercise the mutation path only while this
    -- exact production-shaped authorization remains unsealed.
    IF NOT EXISTS (
        SELECT 1
          FROM public.experiment_v2_direct_proof_authorizations auth
         WHERE auth.authorization_id = c_authorization_id
           AND auth.experiment_id = c_experiment_id) OR
       EXISTS (
        SELECT 1
          FROM public.experiment_v2_direct_proof_emergency_recovery_receipts sealed
         WHERE sealed.authorization_id = c_authorization_id) THEN
        RETURN;
    END IF;

    SELECT * INTO STRICT v_before
      FROM public.control_experiments
     WHERE experiment_id = c_experiment_id;
    IF v_before.status <> 'draft' OR
       v_before.execution_phase <> 'commissioning' OR
       v_before.admission_state NOT IN ('closed', 'emergency_hold',
                                        'baseline_recovery') OR
       v_before.component_enabled <> (v_before.admission_state =
                                      'baseline_recovery') THEN
        RAISE EXCEPTION
            'migration 239 execution fixture did not start from an exact fail-closed commissioning state';
    END IF;

    IF v_before.admission_state <> 'baseline_recovery' THEN
        PERFORM public.fn_experiment_v2_set_admission(
            c_experiment_id, 'baseline_recovery',
            'migration-239-restored-fixture',
            'fixture://migration-239/orphaned-preclaim-recovery');
    END IF;
    SELECT * INTO STRICT v_receipt
      FROM public.fn_experiment_v2_direct_proof_resolve_startup_rollover(
          c_experiment_id, c_authorization_id,
          'fixture://migration-239/orphaned-preclaim-recovery',
          'migration-239-restored-fixture');
    SELECT * INTO STRICT v_after
      FROM public.control_experiments
     WHERE experiment_id = c_experiment_id;

    SELECT encode(digest(convert_to(
               'verdify-direct-proof-orphaned-preclaim-recovery-v1|' ||
               c_authorization_id::text || '|' ||
               c_aggressive_work_id::text || '|' ||
               c_recovery_work_id::text || '|' ||
               string_agg(observation.observation_receipt_sha256, '|'
                          ORDER BY observation.persisted_at,
                                   observation.receipt_id),
               'UTF8'), 'sha256'), 'hex')
      INTO v_reproduced_sha256
      FROM public.experiment_v2_observation_receipts observation
     WHERE observation.experiment_id = c_experiment_id
       AND observation.work_id = c_recovery_work_id;

    IF v_receipt.recovery_work_id <> c_recovery_work_id OR
       v_receipt.recovery_evidence_sha256 <> c_evidence_sha256 OR
       v_reproduced_sha256 <> c_evidence_sha256 OR
       v_after.execution_phase <> 'shadow' OR
       v_after.admission_state <> 'closed' OR
       v_after.component_enabled OR
       v_after.lease_generation <> v_before.lease_generation + 1 OR
       (SELECT count(*)
          FROM public.experiment_v2_direct_proof_emergency_resolutions resolution
         WHERE resolution.authorization_id = c_authorization_id
           AND resolution.recovery_work_id = c_recovery_work_id
           AND resolution.reason =
               'Orphaned preclaim recovery; runtime-fault receipt absent and cause unproven') <> 1 OR
       (SELECT count(*)
          FROM public.experiment_v2_direct_proof_emergency_recovery_receipts receipt
         WHERE receipt.authorization_id = c_authorization_id
           AND receipt.recovery_work_id = c_recovery_work_id
           AND receipt.recovery_evidence_sha256 = c_evidence_sha256) <> 1 OR
       (SELECT count(*)
          FROM public.experiment_v2_work_events terminal
         WHERE terminal.experiment_id = c_experiment_id
           AND terminal.work_id = c_aggressive_work_id
           AND terminal.event_kind = 'failed'
           AND terminal.detail->>'runtime_fault_receipt' = 'absent'
           AND terminal.detail->>'reason' =
               'orphaned_preclaim_recovery_cause_unproven') <> 1 OR
       (SELECT count(*)
          FROM public.experiment_v2_direct_proof_attempt_events terminal
         WHERE terminal.authorization_id = c_authorization_id
           AND terminal.event_kind = 'failed'
           AND terminal.reason =
               'orphaned preclaim recovery sealed; runtime cause unproven') <> 1 OR
       EXISTS (
          SELECT 1
            FROM public.experiment_v2_direct_proof_receipts proof
           WHERE proof.authorization_id = c_authorization_id) THEN
        RAISE EXCEPTION
            'migration 239 exact orphaned-preclaim execution did not seal one non-proof receipt and atomically fence authority';
    END IF;
END
$execute_unsealed_recovery$;

ROLLBACK;
