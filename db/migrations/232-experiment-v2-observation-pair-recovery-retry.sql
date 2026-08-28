-- 232-experiment-v2-observation-pair-recovery-retry.sql
--
-- A 15-second source cadence can make the two newest post-delivery epochs too
-- close for the immutable 30-second confirmation barrier even when an older,
-- still-fresh epoch forms a valid pair with the newest one.  Select the
-- earliest and latest fresh bundle epochs instead of the two newest.
--
-- A recovery that terminal-fails this barrier remains in baseline_recovery.
-- Permit the existing append-only emergency retry entrypoint to chain that
-- exact failed predecessor without requiring a synthetic authority transition.

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_read_observation_window(
    p_experiment_id uuid,
    p_work_id uuid,
    p_bundle_id uuid,
    p_device_id text,
    p_expected_lease_generation bigint
) RETURNS TABLE (
    window_kind text,
    sequence_index integer,
    source_epoch_id uuid,
    receipt_id uuid,
    policy_state_content_sha256 text,
    wire_vector bytea,
    observations jsonb,
    first_observed_at timestamptz,
    last_observed_at timestamptz,
    persisted_at timestamptz,
    bundle_finished_at timestamptz,
    firmware_revision text,
    config_revision text,
    registry_revision text,
    grid_revision text,
    runtime_instance_id uuid,
    writer_generation bigint,
    connection_generation bigint,
    is_current_generation boolean,
    is_fresh boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_bundle public.experiment_v2_delivery_bundles%ROWTYPE;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id;
    SELECT * INTO v_work FROM public.experiment_v2_work
     WHERE experiment_id = p_experiment_id AND work_id = p_work_id;
    SELECT * INTO v_bundle FROM public.experiment_v2_delivery_bundles
     WHERE experiment_id = p_experiment_id AND work_id = p_work_id
       AND bundle_id = p_bundle_id AND device_id = p_device_id;
    IF v_exp.protocol_version <> 2 OR v_work.work_id IS NULL OR
       v_bundle.bundle_id IS NULL OR
       v_exp.lease_generation <> p_expected_lease_generation OR
       v_work.lease_generation <> p_expected_lease_generation THEN
        RAISE EXCEPTION
            'observation window scope/lease is stale or unauthorized';
    END IF;
    RETURN QUERY
    WITH current_generation AS (
        SELECT g.writer_generation, g.connection_generation
          FROM public.experiment_v2_runtime_generations g
         WHERE g.experiment_id = p_experiment_id
           AND g.device_id = p_device_id
         ORDER BY g.generation_event_id DESC
         LIMIT 1
    ), current_epoch AS (
        SELECT e.source_epoch_id, 'current'::text AS kind, 0 AS seq
          FROM public.experiment_v2_observation_epochs e
          JOIN public.experiment_v2_delivery_bundles b
            ON b.bundle_id = e.bundle_id
         WHERE e.experiment_id = p_experiment_id
           AND b.device_id = p_device_id
         ORDER BY e.last_observed_at DESC
         LIMIT 1
    ), ranked_post_epochs AS (
        SELECT e.source_epoch_id, e.last_observed_at,
               row_number() OVER (
                   ORDER BY e.last_observed_at, e.source_epoch_id)::integer AS earliest_rank,
               row_number() OVER (
                   ORDER BY e.last_observed_at DESC, e.source_epoch_id DESC)::integer AS latest_rank
          FROM public.experiment_v2_observation_epochs e
         WHERE e.work_id = p_work_id
           AND e.bundle_id = p_bundle_id
           AND v_now - e.last_observed_at <= interval '90 seconds'
    ), post_epochs AS (
        SELECT ranked.source_epoch_id, 'post_delivery'::text AS kind,
               CASE WHEN ranked.earliest_rank = 1 THEN 1 ELSE 2 END AS seq
          FROM ranked_post_epochs ranked
         WHERE ranked.earliest_rank = 1 OR ranked.latest_rank = 1
    ), selected AS (
        SELECT * FROM current_epoch
        UNION
        SELECT * FROM post_epochs
    )
    SELECT s.kind, s.seq, e.source_epoch_id, r.receipt_id,
           r.policy_state_content_sha256, e.wire_vector, e.observations,
           e.first_observed_at, e.last_observed_at, e.persisted_at,
           completion.bundle_finished_at, e.firmware_revision,
           e.config_revision, e.registry_revision, e.grid_revision,
           e.runtime_instance_id, e.writer_generation,
           e.connection_generation,
           (e.writer_generation = g.writer_generation AND
            e.connection_generation = g.connection_generation),
           (v_now - e.last_observed_at <= interval '90 seconds')
      FROM selected s
      JOIN public.experiment_v2_observation_epochs e
        ON e.source_epoch_id = s.source_epoch_id
      JOIN public.experiment_v2_observation_receipts r
        ON r.source_epoch_id = e.source_epoch_id
       AND r.work_id = e.work_id
       AND r.bundle_id = e.bundle_id
      JOIN public.experiment_v2_delivery_bundle_completions completion
        ON completion.bundle_id = e.bundle_id
      CROSS JOIN current_generation g
     ORDER BY CASE s.kind WHEN 'current' THEN 0 ELSE 1 END, s.seq;
END;
$body$;

ALTER FUNCTION public.fn_experiment_v2_read_observation_window(
    uuid, uuid, uuid, text, bigint)
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_read_observation_window(
        uuid, uuid, uuid, text, bigint)
    FROM PUBLIC CASCADE;
GRANT EXECUTE ON FUNCTION
    public.fn_experiment_v2_read_observation_window(
        uuid, uuid, uuid, text, bigint)
    TO verdify_experiment_component_executor;

CREATE OR REPLACE FUNCTION
    public.fn_experiment_v2_direct_proof_retry_emergency_recovery(
        p_experiment_id uuid,
        p_authorization_id uuid,
        p_failed_resolution_id uuid,
        p_expected_revision_bundle_sha256 text,
        p_expected_emergency_lease_generation bigint,
        p_recovery_valid_range tstzrange,
        p_facility_authorization_ref text,
        p_reason text,
        p_actor text DEFAULT current_user
    ) RETURNS public.experiment_v2_direct_proof_emergency_resolutions
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_auth public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_failed public.experiment_v2_direct_proof_emergency_resolutions%ROWTYPE;
    v_latest public.experiment_v2_direct_proof_emergency_resolutions%ROWTYPE;
    v_existing public.experiment_v2_direct_proof_emergency_resolutions%ROWTYPE;
    v_row public.experiment_v2_direct_proof_emergency_resolutions%ROWTYPE;
    v_generation public.experiment_v2_runtime_generations%ROWTYPE;
    v_resolution_id uuid := gen_random_uuid();
    v_recovery_work_id uuid;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-proof-' || p_experiment_id::text));

    SELECT successor.* INTO v_existing
      FROM public.experiment_v2_direct_proof_emergency_recovery_attempt_events event
      JOIN public.experiment_v2_direct_proof_emergency_resolutions successor
        ON successor.resolution_id = event.successor_resolution_id
     WHERE event.failed_resolution_id = p_failed_resolution_id;
    IF v_existing.resolution_id IS NOT NULL THEN
        IF (v_existing.authorization_id, v_existing.experiment_id,
            v_existing.expected_revision_bundle_sha256,
            v_existing.expected_emergency_lease_generation,
            v_existing.recovery_valid_range,
            v_existing.facility_authorization_ref,
            v_existing.reason, v_existing.recorded_by) IS DISTINCT FROM
           (p_authorization_id, p_experiment_id,
            p_expected_revision_bundle_sha256,
            p_expected_emergency_lease_generation,
            p_recovery_valid_range, p_facility_authorization_ref,
            p_reason, p_actor) THEN
            RAISE EXCEPTION
                'direct-proof emergency recovery retry is immutable and exact replay differs';
        END IF;
        RETURN v_existing;
    END IF;

    SELECT * INTO v_exp
      FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_auth
      FROM public.experiment_v2_direct_proof_authorizations
     WHERE authorization_id = p_authorization_id
       AND experiment_id = p_experiment_id;
    SELECT * INTO v_failed
      FROM public.experiment_v2_direct_proof_emergency_resolutions
     WHERE resolution_id = p_failed_resolution_id
       AND authorization_id = p_authorization_id
       AND experiment_id = p_experiment_id;
    SELECT * INTO v_latest
      FROM public.experiment_v2_direct_proof_emergency_resolutions
     WHERE authorization_id = p_authorization_id
     ORDER BY recovery_attempt_number DESC
     LIMIT 1;
    SELECT * INTO v_generation
      FROM public.experiment_v2_runtime_generations
     WHERE experiment_id = p_experiment_id
     ORDER BY generation_event_id DESC
     LIMIT 1;

    IF v_exp.experiment_id IS NULL OR
       v_auth.authorization_id IS NULL OR
       v_failed.resolution_id IS NULL OR
       v_failed.resolution_kind <> 'bounded_baseline_recovery' OR
       v_latest.resolution_id <> v_failed.resolution_id OR
       v_failed.expected_revision_bundle_sha256 <>
           p_expected_revision_bundle_sha256 OR
       v_exp.revision_bundle_sha256 <>
           p_expected_revision_bundle_sha256 OR
       v_exp.lease_generation <>
           p_expected_emergency_lease_generation OR
       v_exp.status <> 'draft' OR
       v_exp.execution_phase <> 'commissioning' OR
       NOT ((v_exp.admission_state = 'emergency_hold' AND
             NOT v_exp.component_enabled) OR
            (v_exp.admission_state = 'baseline_recovery' AND
             v_exp.component_enabled)) OR
       p_recovery_valid_range IS NULL OR isempty(p_recovery_valid_range) OR
       lower_inf(p_recovery_valid_range) OR upper_inf(p_recovery_valid_range) OR
       NOT lower_inc(p_recovery_valid_range) OR upper_inc(p_recovery_valid_range) OR
       NOT v_now <@ p_recovery_valid_range OR
       upper(p_recovery_valid_range) - lower(p_recovery_valid_range) <
           interval '3 minutes' OR
       upper(p_recovery_valid_range) - lower(p_recovery_valid_range) >
           interval '30 minutes' OR
       p_facility_authorization_ref IS NULL OR
       length(p_facility_authorization_ref) = 0 OR
       p_reason IS NULL OR length(p_reason) = 0 OR
       p_actor IS NULL OR length(p_actor) = 0 OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_receipts receipt
            WHERE receipt.authorization_id = p_authorization_id) OR
       NOT EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_attempt_events failed_attempt
            WHERE failed_attempt.authorization_id = p_authorization_id
              AND failed_attempt.event_kind = 'failed') OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_attempt_events superseded
            WHERE superseded.authorization_id = p_authorization_id
              AND superseded.event_kind = 'superseded') OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_emergency_recovery_receipts receipt
            WHERE receipt.authorization_id = p_authorization_id) OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_emergency_recovery_attempt_events event
            WHERE event.failed_resolution_id = p_failed_resolution_id) OR
       NOT EXISTS (
           SELECT 1
             FROM public.experiment_v2_work_events failed_work
            WHERE failed_work.work_id = v_failed.recovery_work_id
              AND failed_work.event_kind = 'failed') OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_exposures exposure
             LEFT JOIN public.experiment_v2_exposure_closures closure
               USING (exposure_id)
            WHERE exposure.experiment_id = p_experiment_id
              AND closure.exposure_id IS NULL) THEN
        RAISE EXCEPTION
            'direct-proof emergency recovery retry requires the exact failed predecessor, matching authority, and no open exposure';
    END IF;

    IF v_generation.generation_event_id IS NULL OR
       v_generation.recorded_at > v_now - interval '4 minutes' OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_runtime_faults fault
            WHERE fault.experiment_id = p_experiment_id
              AND fault.recorded_at > v_now - interval '2 minutes') THEN
        RAISE EXCEPTION
            'current writer generation is not yet stable for direct-proof emergency recovery retry';
    END IF;

    v_recovery_work_id := public.fn_experiment_v2_request_recovery_at(
        p_experiment_id, NULL, p_recovery_valid_range,
        upper(p_recovery_valid_range), p_reason, v_now, p_actor);
    INSERT INTO public.experiment_v2_direct_proof_emergency_resolutions
        (resolution_id, authorization_id, experiment_id, resolution_kind,
         expected_revision_bundle_sha256,
         expected_emergency_lease_generation, facility_authorization_ref,
         safe_state_artifact_sha256, recovery_work_id, recovery_valid_range,
         reason, recorded_by, recorded_at, recovery_attempt_number)
    VALUES
        (v_resolution_id, p_authorization_id, p_experiment_id,
         'bounded_baseline_recovery', p_expected_revision_bundle_sha256,
         p_expected_emergency_lease_generation, p_facility_authorization_ref,
         NULL, v_recovery_work_id, p_recovery_valid_range,
         p_reason, p_actor, v_now, v_failed.recovery_attempt_number + 1)
    RETURNING * INTO v_row;
    INSERT INTO
        public.experiment_v2_direct_proof_emergency_recovery_attempt_events
        (authorization_id, experiment_id, failed_resolution_id,
         successor_resolution_id, reason, recorded_by, recorded_at)
    VALUES
        (p_authorization_id, p_experiment_id, p_failed_resolution_id,
         v_resolution_id, p_reason, p_actor, v_now);
    IF v_exp.admission_state = 'emergency_hold' THEN
        PERFORM public.fn_experiment_v2_set_admission(
            p_experiment_id, 'baseline_recovery', p_actor,
            p_facility_authorization_ref);
    END IF;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES
        (p_experiment_id, 'emergency_action', 'critical', p_actor,
         jsonb_build_object(
             'v2_event', 'direct_proof_bounded_emergency_recovery_retry',
             'authorization_id', p_authorization_id,
             'failed_resolution_id', p_failed_resolution_id,
             'successor_resolution_id', v_resolution_id,
             'recovery_attempt_number', v_row.recovery_attempt_number,
             'recovery_work_id', v_recovery_work_id,
             'facility_authorization_ref', p_facility_authorization_ref,
             'reason', p_reason), v_now);
    RETURN v_row;
END;
$body$;

ALTER FUNCTION
    public.fn_experiment_v2_direct_proof_retry_emergency_recovery(
        uuid, uuid, uuid, text, bigint, tstzrange, text, text, text)
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_direct_proof_retry_emergency_recovery(
        uuid, uuid, uuid, text, bigint, tstzrange, text, text, text)
    FROM PUBLIC CASCADE;
GRANT EXECUTE ON FUNCTION
    public.fn_experiment_v2_direct_proof_retry_emergency_recovery(
        uuid, uuid, uuid, text, bigint, tstzrange, text, text, text)
    TO verdify_experiment_lifecycle;
