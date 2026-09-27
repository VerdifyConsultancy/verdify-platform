-- RESEARCH DRAFT, NOT FOR MIGRATION DELIVERY: the frozen study baseline was
-- proven incompatible with Jason's selected all-five-layer desired policy on
-- 2026-09-27 (17 encoded mismatches; eight off-grid values; four crop VPD
-- commands outside the experiment vector). Preserve for analysis only.
-- One exact recovery-only successor for the sealed #641 Gate R state. It does
-- not arm an aggressive profile or rewrite any historical failed work event.
-- The two entry points are lifecycle-duty only; applying this migration is inert.

CREATE TABLE public.experiment_v2_recovery_only_handoffs (
    experiment_id uuid PRIMARY KEY REFERENCES public.control_experiments(experiment_id),
    work_id uuid NOT NULL UNIQUE REFERENCES public.experiment_v2_work(work_id),
    source_fault_report_id uuid NOT NULL,
    gate_r_resolution_id uuid NOT NULL,
    authorization_ref text NOT NULL CHECK (length(authorization_ref) > 0),
    baseline_state_content_sha256 text NOT NULL CHECK
        (baseline_state_content_sha256 ~ '^[0-9a-f]{64}$'),
    revision_bundle_sha256 text NOT NULL CHECK
        (revision_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    recovery_lease_generation bigint NOT NULL,
    ordinary_lease_generation bigint,
    began_at timestamptz NOT NULL,
    recovered_at timestamptz,
    closed_at timestamptz,
    CHECK ((recovered_at IS NULL AND closed_at IS NULL AND ordinary_lease_generation IS NULL)
        OR (recovered_at IS NOT NULL AND closed_at IS NOT NULL
            AND ordinary_lease_generation IS NOT NULL))
);
ALTER TABLE public.experiment_v2_recovery_only_handoffs
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL ON public.experiment_v2_recovery_only_handoffs FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_recovery_only_begin(
    p_experiment_id uuid,
    p_expected_fault_report_id uuid,
    p_gate_r_resolution_id uuid,
    p_authorization_ref text,
    p_valid_range tstzrange,
    p_actor text
) RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_baseline public.experiment_v2_state_artifacts%ROWTYPE;
    v_work uuid;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('experiment-v2-recovery-only-' || p_experiment_id::text));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF p_experiment_id IS DISTINCT FROM '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid
       OR p_expected_fault_report_id IS DISTINCT FROM
          '725306a0-9b4b-4083-a3ba-8912308e1d55'::uuid
       OR p_gate_r_resolution_id IS DISTINCT FROM
          'ace2d26c-539d-4007-ad9c-d25ad812644f'::uuid
       OR p_authorization_ref IS NULL OR length(btrim(p_authorization_ref)) = 0
       OR p_actor IS NULL OR length(btrim(p_actor)) = 0
       OR p_valid_range IS NULL OR isempty(p_valid_range)
       OR lower_inf(p_valid_range) OR upper_inf(p_valid_range)
       OR NOT lower_inc(p_valid_range) OR upper_inc(p_valid_range)
       OR NOT v_now <@ p_valid_range
       OR upper(p_valid_range) - lower(p_valid_range) NOT BETWEEN
          interval '3 minutes' AND interval '30 minutes'
       OR EXISTS (SELECT 1 FROM public.experiment_v2_recovery_only_handoffs
                   WHERE experiment_id = p_experiment_id)
       OR NOT public.fn_experiment_v2_gate_p_sealed_recovery_target(p_experiment_id)
       OR v_exp.status <> 'draft' OR v_exp.execution_phase <> 'shadow'
       OR v_exp.admission_state <> 'closed' OR v_exp.component_enabled
       OR v_exp.lease_generation <> 17
       OR v_exp.revision_bundle_sha256 <>
          '3e26a2da1863bd14d255d58fe00d8e94aae9226f9b588ddd9d4f993e6bcb7016'
       OR EXISTS (
           SELECT 1 FROM public.experiment_v2_work w
           WHERE w.experiment_id = p_experiment_id
             AND NOT EXISTS (SELECT 1 FROM public.experiment_v2_work_events t
                             WHERE t.work_id = w.work_id AND t.event_kind IN
                               ('completed','failed','recovered','cancelled','superseded')))
    THEN
        RAISE EXCEPTION 'recovery-only begin requires exact sealed, idle Gate R authority';
    END IF;
    SELECT * INTO v_baseline FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = p_experiment_id
       AND revision_bundle_sha256 = v_exp.revision_bundle_sha256
       AND profile = 'baseline';
    IF v_baseline.state_artifact_id IS NULL OR
       octet_length(v_baseline.wire_vector) <> 178 THEN
        RAISE EXCEPTION 'recovery-only begin requires frozen canonical 48-field baseline';
    END IF;

    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET execution_phase = 'commissioning', component_enabled = true,
           admission_state = 'baseline_recovery',
           lease_generation = lease_generation + 1, updated_at = v_now
     WHERE experiment_id = p_experiment_id RETURNING * INTO v_exp;
    v_work := public.fn_experiment_v2_request_recovery_at(
        p_experiment_id, NULL, p_valid_range, upper(p_valid_range),
        'recovery-only-after-sealed-gate-r', v_now, p_actor);
    IF NOT EXISTS (SELECT 1 FROM public.experiment_v2_work w
                   WHERE w.work_id = v_work AND w.experiment_id = p_experiment_id
                     AND w.parent_work_id IS NULL
                     AND w.execution_phase = 'commissioning'
                     AND w.operation_kind = 'baseline_recovery'
                     AND w.target_profile = 'baseline'
                     AND w.target_state_content_sha256 = v_baseline.state_content_sha256
                     AND w.lease_generation = v_exp.lease_generation) THEN
        RAISE EXCEPTION 'recovery-only work did not bind the exact baseline';
    END IF;
    INSERT INTO public.experiment_v2_recovery_only_handoffs
        (experiment_id, work_id, source_fault_report_id, gate_r_resolution_id,
         authorization_ref, baseline_state_content_sha256,
         revision_bundle_sha256, recovery_lease_generation, began_at)
    VALUES (p_experiment_id, v_work, p_expected_fault_report_id,
            p_gate_r_resolution_id, p_authorization_ref,
            v_baseline.state_content_sha256, v_exp.revision_bundle_sha256,
            v_exp.lease_generation, v_now);
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail)
    VALUES (p_experiment_id, 'state_transition', 'warning', p_actor,
            jsonb_build_object('v2_event', 'recovery_only_begin',
                               'work_id', v_work,
                               'source_fault_report_id', p_expected_fault_report_id,
                               'gate_r_resolution_id', p_gate_r_resolution_id,
                               'authorization_ref', p_authorization_ref));
    RETURN v_work;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_recovery_only_finish(
    p_experiment_id uuid, p_work_id uuid, p_authorization_ref text, p_actor text
) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_handoff public.experiment_v2_recovery_only_handoffs%ROWTYPE;
    v_recovered_at timestamptz;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('experiment-v2-recovery-only-' || p_experiment_id::text));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_handoff FROM public.experiment_v2_recovery_only_handoffs
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF v_handoff.work_id IS DISTINCT FROM p_work_id
       OR v_handoff.authorization_ref IS DISTINCT FROM p_authorization_ref
       OR p_actor IS NULL OR length(btrim(p_actor)) = 0 THEN
        RAISE EXCEPTION 'recovery-only finish identity mismatch';
    END IF;
    IF v_handoff.closed_at IS NOT NULL THEN
        RETURN v_exp.status = 'draft' AND v_exp.execution_phase = 'shadow'
           AND v_exp.admission_state = 'closed' AND NOT v_exp.component_enabled
           AND v_exp.lease_generation = v_handoff.ordinary_lease_generation
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_runtime_faults later
                WHERE later.experiment_id = p_experiment_id
                  AND later.recorded_at >= v_handoff.recovered_at)
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_exposures x
               LEFT JOIN public.experiment_v2_exposure_closures c
                 USING (exposure_id)
                WHERE x.experiment_id = p_experiment_id
                  AND c.exposure_id IS NULL);
    END IF;
    SELECT recovered.recorded_at INTO v_recovered_at
      FROM public.experiment_v2_work_events recovered
     WHERE recovered.experiment_id = p_experiment_id
       AND recovered.work_id = p_work_id AND recovered.event_kind = 'recovered'
     ORDER BY recovered.recorded_at DESC LIMIT 1;
    IF v_exp.status <> 'draft' OR v_exp.execution_phase <> 'commissioning'
       OR v_exp.admission_state <> 'baseline_recovery' OR NOT v_exp.component_enabled
       OR v_exp.lease_generation <> v_handoff.recovery_lease_generation
       OR v_exp.revision_bundle_sha256 <> v_handoff.revision_bundle_sha256
       OR v_recovered_at IS NULL OR v_recovered_at <= v_handoff.began_at
       OR EXISTS (SELECT 1 FROM public.experiment_v2_runtime_faults f
                   WHERE f.experiment_id = p_experiment_id
                     AND f.recorded_at >= v_handoff.began_at)
       OR EXISTS (SELECT 1 FROM public.experiment_v2_exposures x
                   LEFT JOIN public.experiment_v2_exposure_closures c
                     USING (exposure_id)
                   WHERE x.experiment_id = p_experiment_id AND c.exposure_id IS NULL)
       OR EXISTS (SELECT 1 FROM public.experiment_v2_work w
                   WHERE w.experiment_id = p_experiment_id
                     AND w.created_at >= v_handoff.began_at AND w.work_id <> p_work_id)
       OR NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_observation_receipts r1
           JOIN public.experiment_v2_observation_epochs e1 USING (source_epoch_id)
           JOIN public.experiment_v2_state_artifacts baseline
             ON baseline.experiment_id = r1.experiment_id
            AND baseline.revision_bundle_sha256 = v_handoff.revision_bundle_sha256
            AND baseline.profile = 'baseline'
            AND baseline.state_content_sha256 = v_handoff.baseline_state_content_sha256
           JOIN public.experiment_v2_observation_receipts r2
             ON r2.work_id = r1.work_id AND r2.bundle_id = r1.bundle_id
            AND r2.source_epoch_id <> r1.source_epoch_id
           JOIN public.experiment_v2_observation_epochs e2
             ON e2.source_epoch_id = r2.source_epoch_id
           JOIN LATERAL (
               SELECT g.runtime_instance_id, g.writer_generation,
                      g.connection_generation
                 FROM public.experiment_v2_runtime_generations g
                WHERE g.experiment_id = p_experiment_id
                  AND g.device_id = 'esp32-vallery'
                ORDER BY g.generation_event_id DESC LIMIT 1
           ) current_generation ON true
           WHERE r1.work_id = p_work_id AND r2.work_id = p_work_id
             AND r1.experiment_id = p_experiment_id
             AND r2.experiment_id = p_experiment_id
             AND r1.policy_state_content_sha256 = v_handoff.baseline_state_content_sha256
             AND r2.policy_state_content_sha256 = r1.policy_state_content_sha256
             AND e1.runtime_instance_id = current_generation.runtime_instance_id
             AND e2.runtime_instance_id = current_generation.runtime_instance_id
             AND e1.writer_generation = current_generation.writer_generation
             AND e2.writer_generation = current_generation.writer_generation
             AND e1.connection_generation = current_generation.connection_generation
             AND e2.connection_generation = current_generation.connection_generation
             AND octet_length(e1.wire_vector) = 178
             AND octet_length(e2.wire_vector) = 178
             AND e1.wire_vector = baseline.wire_vector
             AND e2.wire_vector = baseline.wire_vector
             AND jsonb_array_length(e1.observations) = 48
             AND jsonb_array_length(e2.observations) = 48
             AND e1.last_observed_at >= v_handoff.began_at
             AND e2.last_observed_at - e1.last_observed_at >= interval '30 seconds'
             AND e2.last_observed_at <= v_recovered_at)
    THEN
        RAISE EXCEPTION 'recovery-only finish requires fresh complete physical baseline and unchanged authority';
    END IF;
    PERFORM public.fn_experiment_v2_set_admission(
        p_experiment_id, 'closed', p_actor, 'recovery-only-two-epoch-baseline');
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET execution_phase = 'shadow', component_enabled = false,
           lease_generation = lease_generation + 1, updated_at = v_now
     WHERE experiment_id = p_experiment_id RETURNING * INTO v_exp;
    UPDATE public.experiment_v2_recovery_only_handoffs
       SET recovered_at = v_recovered_at, closed_at = v_now,
           ordinary_lease_generation = v_exp.lease_generation
     WHERE experiment_id = p_experiment_id;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail)
    VALUES (p_experiment_id, 'state_transition', 'info', p_actor,
            jsonb_build_object('v2_event', 'recovery_only_baseline_confirmed',
                               'work_id', p_work_id,
                               'recovered_at', v_recovered_at));
    RETURN true;
END;
$body$;

ALTER FUNCTION public.fn_experiment_v2_recovery_only_begin(uuid,uuid,uuid,text,tstzrange,text)
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_recovery_only_finish(uuid,uuid,text,text)
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL ON FUNCTION
    public.fn_experiment_v2_recovery_only_begin(uuid,uuid,uuid,text,tstzrange,text),
    public.fn_experiment_v2_recovery_only_finish(uuid,uuid,text,text)
    FROM PUBLIC CASCADE;
GRANT EXECUTE ON FUNCTION
    public.fn_experiment_v2_recovery_only_begin(uuid,uuid,uuid,text,tstzrange,text),
    public.fn_experiment_v2_recovery_only_finish(uuid,uuid,text,text)
    TO verdify_experiment_lifecycle;

-- The 98 August recovery jobs with truthful failed events remain in history.
-- The old startup projection counts each one as pending forever. Override its
-- hold only after this exact successor has physically recovered a newer full
-- baseline and returned ordinary ownership. A later runtime fault or new work
-- immediately restores the old fail-closed result.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_safe_startup_after_recovery_only(
    p_device_id text, p_experiment_id uuid DEFAULT NULL
) RETURNS TABLE (
    attested_at timestamptz, device_id text, requested_experiment_id uuid,
    scoped_experiment_id uuid, scope_resolved boolean,
    current_lease_generation bigint, active_experiment_count integer,
    open_exposure_count integer, recovery_pending_count integer,
    experiment_authority_active boolean, facility_authority_yielded boolean,
    hold_required boolean, attestation_reason text
)
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $body$
    WITH old_result AS (
        SELECT * FROM public.fn_experiment_v2_safe_startup_attestation(
            p_device_id, p_experiment_id)
    ), cleared AS (
        SELECT EXISTS (
            SELECT 1
              FROM public.experiment_v2_recovery_only_handoffs h
              JOIN public.control_experiments e USING (experiment_id)
              JOIN public.experiment_v2_work_events recovered
                ON recovered.experiment_id = h.experiment_id
               AND recovered.work_id = h.work_id
               AND recovered.event_kind = 'recovered'
               AND recovered.recorded_at = h.recovered_at
              JOIN old_result old ON old.scoped_experiment_id = h.experiment_id
             WHERE p_device_id = 'esp32-vallery'
               AND h.experiment_id =
                   '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid
               AND (p_experiment_id IS NULL OR p_experiment_id = h.experiment_id)
               AND old.scope_resolved AND old.active_experiment_count = 0
               AND old.open_exposure_count = 0
               AND NOT old.facility_authority_yielded
               AND h.closed_at IS NOT NULL AND h.recovered_at IS NOT NULL
               AND h.recovered_at > h.began_at
               AND e.protocol_version = 2 AND e.status = 'draft'
               AND e.execution_phase = 'shadow'
               AND e.admission_state = 'closed' AND NOT e.component_enabled
               AND e.revision_bundle_sha256 = h.revision_bundle_sha256
               AND e.lease_generation = h.ordinary_lease_generation
               AND NOT EXISTS (
                   SELECT 1 FROM public.experiment_v2_runtime_faults fault
                    WHERE fault.experiment_id = h.experiment_id
                      AND fault.recorded_at >= h.recovered_at)
               AND NOT EXISTS (
                   SELECT 1 FROM public.experiment_v2_exposures exposure
                   LEFT JOIN public.experiment_v2_exposure_closures closure
                     USING (exposure_id)
                    WHERE exposure.experiment_id = h.experiment_id
                      AND closure.exposure_id IS NULL)
               AND NOT EXISTS (
                   SELECT 1 FROM public.experiment_v2_work work
                    WHERE work.experiment_id = h.experiment_id
                      AND work.created_at >= h.began_at
                      AND work.work_id <> h.work_id
                      AND NOT EXISTS (
                          SELECT 1 FROM public.experiment_v2_work_events terminal
                           WHERE terminal.work_id = work.work_id
                             AND terminal.event_kind IN
                               ('completed','failed','recovered','cancelled','superseded')))
        ) AS safe
    )
    SELECT old.attested_at, old.device_id, old.requested_experiment_id,
           old.scoped_experiment_id, old.scope_resolved,
           old.current_lease_generation, old.active_experiment_count,
           old.open_exposure_count,
           old.recovery_pending_count,
           old.experiment_authority_active, old.facility_authority_yielded,
           CASE WHEN cleared.safe THEN false ELSE old.hold_required END,
           CASE WHEN cleared.safe THEN 'recovery_only_baseline_confirmed'
                ELSE old.attestation_reason END
      FROM old_result old CROSS JOIN cleared;
$body$;
ALTER FUNCTION public.fn_experiment_v2_safe_startup_after_recovery_only(text,uuid)
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL ON FUNCTION
    public.fn_experiment_v2_safe_startup_after_recovery_only(text,uuid)
    FROM PUBLIC CASCADE;
GRANT EXECUTE ON FUNCTION
    public.fn_experiment_v2_safe_startup_after_recovery_only(text,uuid)
    TO verdify_experiment_component_executor;

-- A successor Gate P may use this as recovery-handoff evidence only. The
-- original sealed-target predicate remains a pre-recovery check and must turn
-- false after a genuine newer recovered event. This projection grants no
-- experiment, proof, admission, or device authority.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_gate_p_recovery_handoff_ready(
    p_experiment_id uuid
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $body$
    SELECT p_experiment_id = '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid
       AND EXISTS (
           SELECT 1
             FROM public.experiment_v2_recovery_only_handoffs h
             JOIN public.control_experiments e USING (experiment_id)
             JOIN public.experiment_v2_runtime_faults original
               ON original.fault_report_id = h.source_fault_report_id
             JOIN public.experiment_v2_work_events recovered
               ON recovered.experiment_id = h.experiment_id
              AND recovered.work_id = h.work_id
              AND recovered.event_kind = 'recovered'
              AND recovered.recorded_at = h.recovered_at
             JOIN public.fn_experiment_v2_ops_status() ops
               ON ops.experiment_id = h.experiment_id
            WHERE h.experiment_id = p_experiment_id
              AND h.source_fault_report_id =
                  '725306a0-9b4b-4083-a3ba-8912308e1d55'::uuid
              AND h.gate_r_resolution_id =
                  'ace2d26c-539d-4007-ad9c-d25ad812644f'::uuid
              AND h.closed_at IS NOT NULL AND h.recovered_at > h.began_at
              AND h.recovered_at > original.recorded_at
              AND e.protocol_version = 2 AND e.status = 'draft'
              AND e.execution_phase = 'shadow' AND e.admission_state = 'closed'
              AND NOT e.component_enabled
              AND e.revision_bundle_sha256 = h.revision_bundle_sha256
              AND e.lease_generation = h.ordinary_lease_generation
              AND ops.safety_state = 'shadow_closed'
              AND ops.alert_reason IS NULL
              AND ops.open_exposure_count = 0
              AND ops.observation_truth = 'no_current_work'
              AND NOT EXISTS (
                  SELECT 1 FROM public.experiment_v2_runtime_faults later
                   WHERE later.experiment_id = h.experiment_id
                     AND later.recorded_at >= h.recovered_at)
              AND NOT EXISTS (
                  SELECT 1 FROM public.experiment_v2_exposures x
                  LEFT JOIN public.experiment_v2_exposure_closures c
                    USING (exposure_id)
                   WHERE x.experiment_id = h.experiment_id
                     AND c.exposure_id IS NULL));
$body$;
ALTER FUNCTION public.fn_experiment_v2_gate_p_recovery_handoff_ready(uuid)
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL ON FUNCTION
    public.fn_experiment_v2_gate_p_recovery_handoff_ready(uuid)
    FROM PUBLIC CASCADE;
GRANT EXECUTE ON FUNCTION
    public.fn_experiment_v2_gate_p_recovery_handoff_ready(uuid)
    TO verdify_experiment_lifecycle;

-- Migration 254 pinned the ordinary-login boundary. These new functions belong
-- to lifecycle and component-executor duty roles, not either ordinary login.
-- Preserve both immutable ordinary receipts and reject privilege widening.
DO $boundary$
BEGIN
    IF (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2
       OR (SELECT encode(boundary_sha256, 'hex')
             FROM public.runtime_ordinary_login_attestation_receipts
            WHERE login_name = 'verdify_api_runtime_login') IS DISTINCT FROM
          '9b5e6841cebc95cff6020f1a899e65c1f504d927844f66f7045ecfb1eef23451'
       OR (SELECT encode(boundary_sha256, 'hex')
             FROM public.runtime_ordinary_login_attestation_receipts
            WHERE login_name = 'verdify_ingestor_runtime_login') IS DISTINCT FROM
          '52c1d03192df977396e4e61075616ee18ecb66c6b771005261c510980e97adc3'
       OR encode(public.fn_runtime_ordinary_boundary_digest(
           'verdify_api_runtime_login'), 'hex') IS DISTINCT FROM
          '9b5e6841cebc95cff6020f1a899e65c1f504d927844f66f7045ecfb1eef23451'
       OR encode(public.fn_runtime_ordinary_boundary_digest(
           'verdify_ingestor_runtime_login'), 'hex') IS DISTINCT FROM
          '52c1d03192df977396e4e61075616ee18ecb66c6b771005261c510980e97adc3'
       OR pg_catalog.has_function_privilege('verdify_api_runtime_login',
           'public.fn_experiment_v2_safe_startup_after_recovery_only(text,uuid)', 'EXECUTE')
       OR pg_catalog.has_function_privilege('verdify_ingestor_runtime_login',
           'public.fn_experiment_v2_safe_startup_after_recovery_only(text,uuid)', 'EXECUTE')
       OR pg_catalog.has_function_privilege('verdify_ingestor_runtime_login',
           'public.fn_experiment_v2_recovery_only_begin(uuid,uuid,uuid,text,tstzrange,text)',
           'EXECUTE')
       OR pg_catalog.has_function_privilege('verdify_ingestor_runtime_login',
           'public.fn_experiment_v2_recovery_only_finish(uuid,uuid,text,text)', 'EXECUTE')
       OR pg_catalog.has_function_privilege('verdify_api_runtime_login',
           'public.fn_experiment_v2_gate_p_recovery_handoff_ready(uuid)', 'EXECUTE')
       OR pg_catalog.has_function_privilege('verdify_ingestor_runtime_login',
           'public.fn_experiment_v2_gate_p_recovery_handoff_ready(uuid)', 'EXECUTE')
    THEN
        RAISE EXCEPTION 'recovery-only ordinary-login boundary preflight failed';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
         WHERE r.boundary_sha256 IS DISTINCT FROM
               public.fn_runtime_ordinary_boundary_digest(r.login_name))
       OR NOT pg_catalog.has_function_privilege(
           'verdify_experiment_v2_component_executor_login',
           'public.fn_experiment_v2_safe_startup_after_recovery_only(text,uuid)',
           'EXECUTE')
    THEN
        RAISE EXCEPTION 'recovery-only ordinary-login boundary postflight failed';
    END IF;
END;
$boundary$;
