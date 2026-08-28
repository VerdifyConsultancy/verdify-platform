-- 233-experiment-v2-direct-proof-zero-exposure-seal.sql
--
-- Baseline-after is baseline recovery work.  The executor records its two
-- advancing exact receipts and recovered event without opening a treatment
-- exposure, exactly as it does for emergency recovery.  Migration 225 retained
-- the legacy requirement for one open baseline-after exposure, making the
-- physically confirmed executor state impossible to seal.  Require zero open
-- exposure and retain every exact-attempt, evidence, timing, and ACL boundary.

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_finish(
    p_experiment_id uuid,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_direct_proof_receipts
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_auth public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_existing public.experiment_v2_direct_proof_receipts%ROWTYPE;
    v_row public.experiment_v2_direct_proof_receipts%ROWTYPE;
    v_aggressive_work uuid;
    v_aggressive_at timestamptz;
    v_before_work uuid;
    v_before_at timestamptz;
    v_after_work uuid;
    v_after_at timestamptz;
    v_before_count integer;
    v_aggressive_count integer;
    v_after_count integer;
    v_before_hash text;
    v_aggressive_hash text;
    v_after_hash text;
    v_receipt_hash text;
    v_proof_range tstzrange;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-proof-' || p_experiment_id::text));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_existing FROM public.experiment_v2_direct_proof_receipts
     WHERE experiment_id = p_experiment_id;
    IF v_existing.proof_receipt_id IS NOT NULL THEN
        RETURN v_existing;
    END IF;
    SELECT authz.* INTO v_auth
      FROM public.experiment_v2_direct_proof_authorizations authz
     WHERE authz.experiment_id = p_experiment_id
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_receipts receipt
            WHERE receipt.authorization_id = authz.authorization_id)
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_attempt_events terminal
            WHERE terminal.authorization_id = authz.authorization_id
              AND terminal.event_kind IN ('failed', 'superseded'))
     ORDER BY authz.attempt_number DESC LIMIT 1;
    SELECT
        max(mapped.work_id::text) FILTER
            (WHERE mapped.stage = 'baseline_before')::uuid,
        max(mapped.work_id::text) FILTER
            (WHERE mapped.stage = 'aggressive')::uuid,
        max(mapped.work_id::text) FILTER
            (WHERE mapped.stage = 'baseline_after')::uuid
      INTO v_before_work, v_aggressive_work, v_after_work
      FROM public.experiment_v2_direct_proof_attempt_work mapped
     WHERE mapped.authorization_id = v_auth.authorization_id;
    IF v_exp.experiment_id IS NULL OR
       v_auth.authorization_id IS NULL OR
       v_before_work IS NULL OR v_aggressive_work IS NULL OR v_after_work IS NULL OR
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'commissioning' OR
       v_exp.admission_state <> 'baseline_recovery' OR
       NOT v_exp.component_enabled OR
       p_actor IS NULL OR length(p_actor) = 0 OR
       NOT v_now <@ v_auth.proof_valid_range OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_attempt_work mapped
             JOIN public.experiment_v2_work work USING (work_id)
            WHERE mapped.authorization_id = v_auth.authorization_id
              AND (work.experiment_id <> p_experiment_id OR
                   work.revision_bundle_sha256 <> v_exp.revision_bundle_sha256 OR
                   work.lease_generation <> v_exp.lease_generation)) THEN
        RAISE EXCEPTION
            'direct proof finishes only from three exact work rows of its active attended attempt';
    END IF;
    SELECT recorded_at INTO v_before_at
      FROM public.experiment_v2_work_events
     WHERE experiment_id = p_experiment_id AND work_id = v_before_work
       AND event_kind = 'recovered';
    SELECT recorded_at INTO v_aggressive_at
      FROM public.experiment_v2_work_events
     WHERE experiment_id = p_experiment_id AND work_id = v_aggressive_work
       AND event_kind = 'completed';
    SELECT recorded_at INTO v_after_at
      FROM public.experiment_v2_work_events
     WHERE experiment_id = p_experiment_id AND work_id = v_after_work
       AND event_kind = 'recovered';
    IF v_before_at IS NULL OR v_aggressive_at IS NULL OR v_after_at IS NULL OR
       NOT (v_before_at < v_aggressive_at AND v_aggressive_at < v_after_at) OR
       NOT v_before_at <@ v_auth.proof_valid_range OR
       NOT v_aggressive_at <@ v_auth.proof_valid_range OR
       NOT v_after_at <@ v_auth.proof_valid_range THEN
        RAISE EXCEPTION
            'direct proof requires exact-attempt baseline-before, aggressive, baseline-after terminal order';
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
            'direct proof requires zero open exposure before sealing';
    END IF;
    SELECT count(*)::integer,
           encode(digest(convert_to(
               'verdify-direct-proof-evidence-v1|' || v_before_work::text || '|' ||
               string_agg(receipt.observation_receipt_sha256, '|'
                          ORDER BY receipt.persisted_at, receipt.receipt_id),
               'UTF8'), 'sha256'), 'hex')
      INTO v_before_count, v_before_hash
      FROM public.experiment_v2_observation_receipts receipt
     WHERE receipt.experiment_id = p_experiment_id
       AND receipt.work_id = v_before_work;
    SELECT count(*)::integer,
           encode(digest(convert_to(
               'verdify-direct-proof-evidence-v1|' || v_aggressive_work::text || '|' ||
               string_agg(receipt.observation_receipt_sha256, '|'
                          ORDER BY receipt.persisted_at, receipt.receipt_id),
               'UTF8'), 'sha256'), 'hex')
      INTO v_aggressive_count, v_aggressive_hash
      FROM public.experiment_v2_observation_receipts receipt
     WHERE receipt.experiment_id = p_experiment_id
       AND receipt.work_id = v_aggressive_work;
    SELECT count(*)::integer,
           encode(digest(convert_to(
               'verdify-direct-proof-evidence-v1|' || v_after_work::text || '|' ||
               string_agg(receipt.observation_receipt_sha256, '|'
                          ORDER BY receipt.persisted_at, receipt.receipt_id),
               'UTF8'), 'sha256'), 'hex')
      INTO v_after_count, v_after_hash
      FROM public.experiment_v2_observation_receipts receipt
     WHERE receipt.experiment_id = p_experiment_id
       AND receipt.work_id = v_after_work;
    IF v_before_count < 2 OR v_aggressive_count < 2 OR v_after_count < 2 OR
       v_before_hash = v_aggressive_hash OR
       v_before_hash = v_after_hash OR v_aggressive_hash = v_after_hash THEN
        RAISE EXCEPTION
            'direct proof requires distinct receipt-bound two-epoch evidence for all three exact-attempt states';
    END IF;
    v_proof_range := tstzrange(
        v_before_at, v_after_at + interval '1 microsecond', '[)');
    IF NOT v_proof_range <@ v_auth.proof_valid_range OR
       upper(v_proof_range) > v_now OR
       upper(v_proof_range) - lower(v_proof_range) < interval '3 minutes' OR
       upper(v_proof_range) - lower(v_proof_range) > interval '12 hours' THEN
        RAISE EXCEPTION
            'direct proof evidence must span one completed 3-minute-to-12-hour interval inside the active attempt authorization';
    END IF;
    v_receipt_hash := encode(digest(convert_to(
        'verdify-direct-proof-receipt-v1|' || v_auth.authorization_id::text || '|' ||
        p_experiment_id::text || '|' || v_exp.revision_bundle_sha256 || '|' ||
        v_proof_range::text || '|' || v_auth.supervisor_role || '|' ||
        v_auth.rescue_owner_role || '|' ||
        v_before_work::text || '|' || v_before_hash || '|' ||
        v_aggressive_work::text || '|' || v_aggressive_hash || '|' ||
        v_after_work::text || '|' || v_after_hash,
        'UTF8'), 'sha256'), 'hex');
    INSERT INTO public.experiment_v2_direct_proof_receipts
        (authorization_id, experiment_id, revision_bundle_sha256,
         baseline_before_work_id, aggressive_work_id, baseline_after_work_id,
         baseline_before_evidence_sha256, aggressive_evidence_sha256,
         baseline_after_evidence_sha256, proof_valid_range, proof_receipt_sha256,
         recorded_by, recorded_at)
    VALUES
        (v_auth.authorization_id, p_experiment_id, v_exp.revision_bundle_sha256,
         v_before_work, v_aggressive_work, v_after_work,
         v_before_hash, v_aggressive_hash, v_after_hash, v_proof_range,
         v_receipt_hash, p_actor, v_now)
    RETURNING * INTO v_row;
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
             'direct_proof_authorization_id', v_auth.authorization_id,
             'direct_proof_attempt_number', v_auth.attempt_number,
             'direct_proof_receipt_id', v_row.proof_receipt_id,
             'proof_receipt_sha256', v_row.proof_receipt_sha256), v_now);
    RETURN v_row;
END;
$body$;

ALTER FUNCTION public.fn_experiment_v2_direct_proof_finish(uuid, text)
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_direct_proof_finish(uuid, text)
    FROM PUBLIC CASCADE;
GRANT EXECUTE ON FUNCTION
    public.fn_experiment_v2_direct_proof_finish(uuid, text)
    TO verdify_experiment_lifecycle;
