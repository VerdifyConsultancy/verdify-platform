-- The migration creates an explicit, one-use data-repair entry point. Applying
-- the migration itself does not change work history. After the Gate R receipt
-- is sealed, the lifecycle operator may terminalize only the five Aug 28 root
-- baseline-recovery jobs that expired without a claim, delivery, observation,
-- exposure or event. Their old lease generations make the ordinary work-event
-- helper correctly reject them. This repair records failure, never recovery.

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_terminalize_legacy_expired_recovery(
    p_experiment_id uuid,
    p_expected_resolution_id uuid,
    p_expected_lease_generation bigint,
    p_actor text
) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_ids uuid[] := ARRAY[
        'baadbd8b-49da-486b-9cfc-a54f534239ba'::uuid,
        '019c9b7c-c8fd-4d28-b7dc-c6bf739be506'::uuid,
        '55c353fc-f894-4179-8341-47af364eacad'::uuid,
        '48a66c19-7a2e-43c8-91d1-ba545535ff0a'::uuid,
        'b803563f-1813-4ccf-b21b-6de8ee096a4d'::uuid
    ];
    v_now timestamptz := clock_timestamp();
    v_matching_work integer;
    v_existing_events integer;
    v_matching_events integer;
    v_matching_event_work integer;
    v_unresolved integer;
    v_inserted integer := 0;
    v_ops record;
BEGIN
    IF p_experiment_id IS DISTINCT FROM
           '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid OR
       p_expected_resolution_id IS DISTINCT FROM
           'ace2d26c-539d-4007-ad9c-d25ad812644f'::uuid OR
       p_expected_lease_generation IS DISTINCT FROM 17 OR
       p_actor IS NULL OR length(btrim(p_actor)) = 0 THEN
        RAISE EXCEPTION 'legacy recovery terminalization binding is not exact';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-legacy-expired-recovery-' || p_experiment_id::text));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF v_exp.experiment_id IS NULL OR
       v_exp.protocol_version <> 2 OR
       v_exp.kind <> 'randomized' OR
       v_exp.status <> 'draft' OR
       v_exp.execution_phase <> 'shadow' OR
       v_exp.admission_state <> 'closed' OR
       v_exp.component_enabled OR
       v_exp.lease_generation <> p_expected_lease_generation OR
       v_exp.revision_bundle_sha256 <>
           '3e26a2da1863bd14d255d58fe00d8e94aae9226f9b588ddd9d4f993e6bcb7016' OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_exposures exposure
           LEFT JOIN public.experiment_v2_exposure_closures closure
             USING (exposure_id)
           WHERE exposure.experiment_id = p_experiment_id
             AND closure.exposure_id IS NULL) OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_receipts proof
           WHERE proof.experiment_id = p_experiment_id) OR
       NOT EXISTS (
           SELECT 1
           FROM public.experiment_v2_direct_proof_emergency_recovery_receipts receipt
           JOIN public.experiment_v2_direct_proof_emergency_resolutions resolution
             ON resolution.resolution_id = receipt.resolution_id
           WHERE receipt.resolution_id = p_expected_resolution_id
             AND receipt.experiment_id = p_experiment_id
             AND receipt.authorization_id =
                 'd00304d1-74f9-4872-857e-6944de53ac46'::uuid
             AND receipt.recovery_work_id =
                 '7093f8c3-a36e-49f2-8b4b-443d32a9a51b'::uuid
             AND receipt.recovery_evidence_sha256 =
                 '0fa6d172de87cf2008d5908ff4a3517eeca1d1cd4811e86461fc349c25f41b91'
             AND resolution.resolution_kind = 'bounded_baseline_recovery') THEN
        RAISE EXCEPTION 'legacy recovery terminalization requires the sealed zero-exposure Gate R state';
    END IF;

    WITH expected(work_id, lease_generation, created_at, expires_at) AS (
        VALUES
        ('baadbd8b-49da-486b-9cfc-a54f534239ba'::uuid, 1::bigint,
         '2026-08-28 04:01:56.513732+00'::timestamptz,
         '2026-08-28 04:06:56.513732+00'::timestamptz),
        ('019c9b7c-c8fd-4d28-b7dc-c6bf739be506'::uuid, 2::bigint,
         '2026-08-28 06:31:05.062609+00'::timestamptz,
         '2026-08-28 06:36:05.062609+00'::timestamptz),
        ('55c353fc-f894-4179-8341-47af364eacad'::uuid, 3::bigint,
         '2026-08-28 07:33:54.741947+00'::timestamptz,
         '2026-08-28 07:38:54.741947+00'::timestamptz),
        ('48a66c19-7a2e-43c8-91d1-ba545535ff0a'::uuid, 4::bigint,
         '2026-08-28 08:19:47.095558+00'::timestamptz,
         '2026-08-28 08:24:47.095558+00'::timestamptz),
        ('b803563f-1813-4ccf-b21b-6de8ee096a4d'::uuid, 6::bigint,
         '2026-08-28 11:31:11.080946+00'::timestamptz,
         '2026-08-28 11:36:11.080946+00'::timestamptz)
    )
    SELECT count(*) INTO v_matching_work
      FROM expected
      JOIN public.experiment_v2_work work USING (work_id)
     WHERE work.experiment_id = p_experiment_id
       AND work.lease_generation = expected.lease_generation
       AND work.created_at = expected.created_at
       AND work.valid_range = tstzrange(
           expected.created_at, expected.expires_at, '[)')
       AND work.expires_at = expected.expires_at
       AND work.parent_work_id IS NULL
       AND work.operation_kind = 'baseline_recovery'
       AND work.target_profile = 'baseline'
       AND work.created_by = 'verdify-component-executor-v2'
       AND work.revision_bundle_sha256 = v_exp.revision_bundle_sha256
       AND work.target_state_content_sha256 =
           '4fbbab8b067c978500f1b45878d7fd11ae2a36c25feeff26dd2e7334b2c2b574'
       AND expected.expires_at < v_now
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_delivery_bundles bundle
           WHERE bundle.work_id = work.work_id)
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_observation_receipts observation
           WHERE observation.work_id = work.work_id)
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_exposures exposure
           WHERE exposure.work_id = work.work_id)
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_runtime_faults fault
           WHERE fault.recovery_work_id = work.work_id);
    IF v_matching_work <> 5 THEN
        RAISE EXCEPTION 'legacy recovery work identity or absence-of-delivery proof changed';
    END IF;

    SELECT count(*) INTO v_existing_events
      FROM public.experiment_v2_work_events event
     WHERE event.experiment_id = p_experiment_id
       AND event.work_id = ANY(v_ids);
    SELECT count(*) INTO v_matching_events
      FROM public.experiment_v2_work_events event
     WHERE event.experiment_id = p_experiment_id
       AND event.work_id = ANY(v_ids)
       AND event.event_kind = 'failed'
       AND event.claim_expires_at IS NULL
       AND event.worker_ref = p_actor
       AND event.detail = jsonb_build_object(
           'reason', 'historical_expired_unclaimed_baseline_recovery',
           'gate_r_resolution_id', p_expected_resolution_id,
           'source_migration', 251);
    SELECT count(DISTINCT event.work_id) INTO v_matching_event_work
      FROM public.experiment_v2_work_events event
     WHERE event.experiment_id = p_experiment_id
       AND event.work_id = ANY(v_ids)
       AND event.event_kind = 'failed'
       AND event.claim_expires_at IS NULL
       AND event.worker_ref = p_actor
       AND event.detail = jsonb_build_object(
           'reason', 'historical_expired_unclaimed_baseline_recovery',
           'gate_r_resolution_id', p_expected_resolution_id,
           'source_migration', 251);
    IF v_existing_events <> 0 AND
       (v_existing_events <> 5 OR v_matching_events <> 5 OR
        v_matching_event_work <> 5) THEN
        RAISE EXCEPTION 'legacy recovery work has partial or conflicting event history';
    END IF;

    SELECT count(*) INTO v_unresolved
      FROM public.experiment_v2_work work
     WHERE work.experiment_id = p_experiment_id
       AND (v_now >= work.expires_at OR v_now >= upper(work.valid_range))
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_work_events terminal
           WHERE terminal.experiment_id = work.experiment_id
             AND terminal.work_id = work.work_id
             AND terminal.event_kind IN
                 ('completed', 'failed', 'recovered', 'cancelled', 'superseded'));
    IF (v_existing_events = 0 AND v_unresolved <> 5) OR
       (v_existing_events = 5 AND v_unresolved <> 0) THEN
        RAISE EXCEPTION 'legacy recovery terminalization refuses other expired work';
    END IF;

    IF v_existing_events = 0 THEN
        INSERT INTO public.experiment_v2_work_events
            (experiment_id, work_id, event_kind, worker_ref,
             claim_expires_at, detail, recorded_at)
        SELECT p_experiment_id, target.work_id, 'failed', p_actor,
               NULL,
               jsonb_build_object(
                   'reason', 'historical_expired_unclaimed_baseline_recovery',
                   'gate_r_resolution_id', p_expected_resolution_id,
                   'source_migration', 251),
               v_now
          FROM unnest(v_ids) AS target(work_id);
        GET DIAGNOSTICS v_inserted = ROW_COUNT;
        IF v_inserted <> 5 THEN
            RAISE EXCEPTION 'legacy recovery terminalization did not append five failures';
        END IF;
    END IF;

    SELECT count(*) INTO v_unresolved
      FROM public.experiment_v2_work work
     WHERE work.experiment_id = p_experiment_id
       AND (v_now >= work.expires_at OR v_now >= upper(work.valid_range))
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_work_events terminal
           WHERE terminal.experiment_id = work.experiment_id
             AND terminal.work_id = work.work_id
             AND terminal.event_kind IN
                 ('completed', 'failed', 'recovered', 'cancelled', 'superseded'));
    SELECT * INTO v_ops FROM public.fn_experiment_v2_ops_status()
     WHERE experiment_id = p_experiment_id;
    IF v_ops.experiment_id IS NULL OR v_unresolved <> 0 OR
       v_ops.safety_state <> 'runtime_fault' OR
       v_ops.alert_reason <> 'runtime_fault_requires_recovery' OR
       v_ops.open_exposure_count <> 0 OR
       v_ops.observation_truth <> 'no_current_work' THEN
        RAISE EXCEPTION 'legacy recovery terminalization did not preserve truthful runtime-fault hold';
    END IF;
    RETURN v_inserted;
END;
$body$;

ALTER FUNCTION public.fn_experiment_v2_terminalize_legacy_expired_recovery(
    uuid, uuid, bigint, text) OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_terminalize_legacy_expired_recovery(
        uuid, uuid, bigint, text) FROM PUBLIC CASCADE;
GRANT EXECUTE ON FUNCTION
    public.fn_experiment_v2_terminalize_legacy_expired_recovery(
        uuid, uuid, bigint, text) TO verdify_experiment_lifecycle;
