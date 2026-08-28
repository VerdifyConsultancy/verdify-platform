-- 227-experiment-v2-emergency-recovery-retry.sql
--
-- A freshly rolled singleton writer can truthfully yield an already-admitted
-- bounded recovery while it drains its startup authority hold. Preserve that
-- failed recovery work and append one exact successor only after the current
-- writer generation has remained stable. The ordinary runtime fault fence is
-- unchanged and remains the authority that failed the predecessor.

ALTER TABLE public.experiment_v2_direct_proof_emergency_resolutions
    ADD COLUMN IF NOT EXISTS recovery_attempt_number integer NOT NULL DEFAULT 1;

-- PostgreSQL preserves the _authorization_id_key suffix when it truncates the
-- auto-generated name, so discover the exact single-column constraint instead
-- of relying on a pre-truncated identifier.
DO $authorization_unique$
DECLARE
    v_constraint_name name;
BEGIN
    SELECT constraint_row.conname INTO v_constraint_name
      FROM pg_constraint constraint_row
      JOIN pg_attribute column_row
        ON column_row.attrelid = constraint_row.conrelid
       AND column_row.attname = 'authorization_id'
     WHERE constraint_row.conrelid =
           'public.experiment_v2_direct_proof_emergency_resolutions'::regclass
       AND constraint_row.contype = 'u'
       AND constraint_row.conkey = ARRAY[column_row.attnum]::smallint[];
    IF v_constraint_name IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE public.experiment_v2_direct_proof_emergency_resolutions DROP CONSTRAINT %I',
            v_constraint_name);
    END IF;
END
$authorization_unique$;

DO $constraint$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid =
               'public.experiment_v2_direct_proof_emergency_resolutions'::regclass
           AND conname =
               'experiment_v2_direct_proof_emergency_recovery_attempt_number_check'
    ) THEN
        ALTER TABLE public.experiment_v2_direct_proof_emergency_resolutions
            ADD CONSTRAINT
                experiment_v2_direct_proof_emergency_recovery_attempt_number_check
            CHECK (recovery_attempt_number >= 1);
    END IF;
END
$constraint$;

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_experiment_v2_direct_proof_emergency_recovery_attempt
    ON public.experiment_v2_direct_proof_emergency_resolutions
       (authorization_id, recovery_attempt_number);

CREATE TABLE IF NOT EXISTS
    public.experiment_v2_direct_proof_emergency_recovery_attempt_events (
        recovery_attempt_event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        authorization_id uuid NOT NULL
            REFERENCES public.experiment_v2_direct_proof_authorizations(
                authorization_id),
        experiment_id uuid NOT NULL
            REFERENCES public.control_experiments(experiment_id),
        failed_resolution_id uuid NOT NULL UNIQUE
            REFERENCES public.experiment_v2_direct_proof_emergency_resolutions(
                resolution_id),
        successor_resolution_id uuid NOT NULL UNIQUE
            REFERENCES public.experiment_v2_direct_proof_emergency_resolutions(
                resolution_id),
        reason text NOT NULL CHECK (length(reason) > 0),
        recorded_by text NOT NULL CHECK (length(recorded_by) > 0),
        recorded_at timestamptz NOT NULL,
        CHECK (failed_resolution_id <> successor_resolution_id)
    );

ALTER TABLE public.experiment_v2_direct_proof_emergency_recovery_attempt_events
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON TABLE
    public.experiment_v2_direct_proof_emergency_recovery_attempt_events
    FROM PUBLIC CASCADE;

DROP TRIGGER IF EXISTS
    trg_direct_proof_emergency_recovery_attempt_events_immutable
    ON public.experiment_v2_direct_proof_emergency_recovery_attempt_events;
CREATE TRIGGER trg_direct_proof_emergency_recovery_attempt_events_immutable
    BEFORE UPDATE OR DELETE
    ON public.experiment_v2_direct_proof_emergency_recovery_attempt_events
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_immutable();

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
       v_exp.admission_state <> 'emergency_hold' OR
       v_exp.component_enabled OR
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
            'direct-proof emergency recovery retry requires the exact yielded failed predecessor and matching revision/lease';
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
    PERFORM public.fn_experiment_v2_set_admission(
        p_experiment_id, 'baseline_recovery', p_actor,
        p_facility_authorization_ref);
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

-- A failed proof attempt is resolved when its latest bounded emergency
-- recovery has a receipt. Earlier failed recovery attempts remain immutable
-- evidence and no longer prevent the successor proof attempt.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_begin(
    p_experiment_id uuid,
    p_authorization_ref text,
    p_proof_valid_range tstzrange,
    p_supervisor_role text,
    p_rescue_owner_role text,
    p_actor text DEFAULT current_user
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_auth public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_previous public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_state public.experiment_v2_state_artifacts%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_recovery_work_id uuid;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-proof-' || p_experiment_id::text));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
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
     ORDER BY authz.attempt_number DESC
     LIMIT 1;
    IF v_auth.authorization_id IS NOT NULL THEN
        IF (v_auth.authorization_ref, v_auth.proof_valid_range,
            v_auth.supervisor_role, v_auth.rescue_owner_role,
            v_auth.authorized_by) IS DISTINCT FROM
           (p_authorization_ref, p_proof_valid_range,
            p_supervisor_role, p_rescue_owner_role, p_actor) THEN
            RAISE EXCEPTION
                'direct-proof authorization is immutable and exact replay differs';
        END IF;
        SELECT work.* INTO v_work
          FROM public.experiment_v2_direct_proof_attempt_work mapped
          JOIN public.experiment_v2_work work USING (work_id)
         WHERE mapped.authorization_id = v_auth.authorization_id
           AND mapped.stage = 'aggressive';
        IF v_work.work_id IS NULL THEN
            RAISE EXCEPTION
                'direct-proof begin replay conflicts with exact attempt/work binding';
        END IF;
        RETURN v_work.work_id;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.experiment_v2_direct_proof_receipts receipt
         WHERE receipt.experiment_id = p_experiment_id) THEN
        RAISE EXCEPTION 'direct proof is already complete and cannot be retried';
    END IF;
    SELECT * INTO v_previous
      FROM public.experiment_v2_direct_proof_authorizations
     WHERE experiment_id = p_experiment_id
     ORDER BY attempt_number DESC
     LIMIT 1;
    IF v_previous.authorization_id IS NOT NULL AND (
       NOT EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_emergency_resolutions resolution
            WHERE resolution.authorization_id = v_previous.authorization_id) OR
       NOT EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_attempt_events failed
            WHERE failed.authorization_id = v_previous.authorization_id
              AND failed.event_kind = 'failed') OR
       (EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_emergency_resolutions resolution
            WHERE resolution.authorization_id = v_previous.authorization_id
              AND resolution.resolution_kind = 'bounded_baseline_recovery') AND
        NOT EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_emergency_recovery_receipts receipt
            WHERE receipt.authorization_id = v_previous.authorization_id)) OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_attempt_events superseded
            WHERE superseded.authorization_id = v_previous.authorization_id
              AND superseded.event_kind = 'superseded')) THEN
        RAISE EXCEPTION
            'direct-proof retry requires one resolved, not-yet-superseded failed attempt';
    END IF;
    IF v_exp.experiment_id IS NULL OR v_exp.protocol_version <> 2 OR
       (v_exp.experiment_id, v_exp.study_id, v_exp.greenhouse_id, v_exp.timezone)
       IS DISTINCT FROM (
           '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid,
           'verdify-confirmed-component-switchback-v2-2026-08'::text,
           'vallery'::text, 'America/Denver'::text) OR
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'shadow' OR
       v_exp.admission_state <> 'closed' OR v_exp.component_enabled OR
       v_exp.design_lock_sha256 IS NOT NULL OR
       (SELECT count(DISTINCT state.profile)
          FROM public.experiment_v2_state_artifacts state
         WHERE state.experiment_id = p_experiment_id
           AND state.revision_bundle_sha256 = v_exp.revision_bundle_sha256
           AND state.profile IN ('baseline', 'moderate', 'aggressive')) <> 3 OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_exposures exposure
           LEFT JOIN public.experiment_v2_exposure_closures closure
             USING (exposure_id)
            WHERE exposure.experiment_id = p_experiment_id
              AND closure.exposure_id IS NULL) THEN
        RAISE EXCEPTION
            'direct proof requires the exact closed feature-off draft and three frozen profiles';
    END IF;
    IF p_authorization_ref IS NULL OR length(p_authorization_ref) = 0 OR
       p_actor IS NULL OR length(p_actor) = 0 OR
       p_supervisor_role IS DISTINCT FROM 'Jason Vallery' OR
       p_rescue_owner_role IS DISTINCT FROM 'Jason Vallery' OR
       p_proof_valid_range IS NULL OR isempty(p_proof_valid_range) OR
       lower_inf(p_proof_valid_range) OR upper_inf(p_proof_valid_range) OR
       NOT lower_inc(p_proof_valid_range) OR upper_inc(p_proof_valid_range) OR
       NOT v_now <@ p_proof_valid_range OR
       upper(p_proof_valid_range) - lower(p_proof_valid_range) <
           interval '3 minutes' OR
       upper(p_proof_valid_range) - lower(p_proof_valid_range) >
           interval '12 hours' THEN
        RAISE EXCEPTION
            'direct proof requires one active 3-minute-to-12-hour attended window with Jason Vallery in both facility roles';
    END IF;
    SELECT * INTO STRICT v_state
      FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = p_experiment_id
       AND revision_bundle_sha256 = v_exp.revision_bundle_sha256
       AND profile = 'aggressive';
    INSERT INTO public.experiment_v2_direct_proof_authorizations
        (experiment_id, revision_bundle_sha256, issue_number,
         authorization_ref, proof_valid_range, supervisor_role,
         rescue_owner_role, authorized_by, authorized_at, attempt_number)
    VALUES
        (p_experiment_id, v_exp.revision_bundle_sha256, 641,
         p_authorization_ref, p_proof_valid_range, p_supervisor_role,
         p_rescue_owner_role, p_actor, v_now,
         coalesce(v_previous.attempt_number + 1, 1))
    RETURNING * INTO v_auth;
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET execution_phase = 'commissioning', component_enabled = true,
           admission_state = 'baseline_recovery',
           lease_generation = lease_generation + 1, updated_at = v_now
     WHERE experiment_id = p_experiment_id
     RETURNING * INTO v_exp;
    INSERT INTO public.experiment_v2_work
        (experiment_id, execution_phase, operation_kind, target_profile,
         target_state_content_sha256, revision_bundle_sha256,
         firmware_revision, config_revision, registry_revision, grid_revision,
         lease_generation, valid_range, expires_at, created_by, created_at)
    VALUES
        (p_experiment_id, 'commissioning', 'commissioning_canary', 'aggressive',
         v_state.state_content_sha256, v_exp.revision_bundle_sha256,
         v_exp.firmware_revision, v_exp.config_revision,
         v_exp.registry_revision, v_exp.grid_revision,
         v_exp.lease_generation, p_proof_valid_range,
         upper(p_proof_valid_range), p_actor, v_now)
    RETURNING * INTO v_work;
    INSERT INTO public.experiment_v2_direct_proof_attempt_work
        (authorization_id, experiment_id, stage, work_id, bound_at)
    VALUES (v_auth.authorization_id, p_experiment_id, 'aggressive',
            v_work.work_id, v_now);
    v_recovery_work_id := public.fn_experiment_v2_request_recovery_at(
        p_experiment_id, v_work.work_id, p_proof_valid_range,
        upper(p_proof_valid_range), 'direct-proof-baseline-before',
        v_now, p_actor);
    INSERT INTO public.experiment_v2_direct_proof_attempt_work
        (authorization_id, experiment_id, stage, work_id, bound_at)
    VALUES (v_auth.authorization_id, p_experiment_id, 'baseline_before',
            v_recovery_work_id, v_now);
    IF v_previous.authorization_id IS NOT NULL THEN
        INSERT INTO public.experiment_v2_direct_proof_attempt_events
            (authorization_id, experiment_id, event_kind,
             successor_authorization_id, reason, recorded_by, recorded_at)
        VALUES (v_previous.authorization_id, p_experiment_id, 'superseded',
                v_auth.authorization_id,
                'facility-authorized direct-proof retry', p_actor, v_now);
    END IF;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES
        (p_experiment_id, 'state_transition', 'warning', p_actor,
         jsonb_build_object(
             'v2_phase', 'commissioning',
             'launch_path', 'direct_randomized_2026_08_27',
             'direct_proof_authorization_id', v_auth.authorization_id,
             'direct_proof_attempt_number', v_auth.attempt_number,
             'aggressive_work_id', v_work.work_id,
             'baseline_before_work_id', v_recovery_work_id,
             'v2_admission', 'baseline_recovery'), v_now);
    RETURN v_work.work_id;
END;
$body$;

ALTER FUNCTION
    public.fn_experiment_v2_direct_proof_retry_emergency_recovery(
        uuid,uuid,uuid,text,bigint,tstzrange,text,text,text)
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_proof_attempt_status(uuid)
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_proof_begin(
    uuid,text,tstzrange,text,text,text)
    OWNER TO verdify_experiment_v2_owner;

REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_direct_proof_retry_emergency_recovery(
        uuid,uuid,uuid,text,bigint,tstzrange,text,text,text)
    FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_direct_proof_attempt_status(uuid)
    FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_direct_proof_begin(
        uuid,text,tstzrange,text,text,text)
    FROM PUBLIC CASCADE;

DO $security$
DECLARE
    fn regprocedure;
BEGIN
    FOREACH fn IN ARRAY ARRAY[
        'public.fn_experiment_v2_direct_proof_retry_emergency_recovery(uuid,uuid,uuid,text,bigint,tstzrange,text,text,text)'::regprocedure,
        'public.fn_experiment_v2_direct_proof_attempt_status(uuid)'::regprocedure,
        'public.fn_experiment_v2_direct_proof_begin(uuid,text,tstzrange,text,text,text)'::regprocedure
    ] LOOP
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %s TO verdify_experiment_lifecycle', fn);
    END LOOP;
END
$security$;

-- Preserve the function's return type while making every consumer see only
-- the newest append-only recovery resolution for the newest proof attempt.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_attempt_status(
    p_experiment_id uuid
) RETURNS TABLE (
    authorization_id uuid,
    attempt_number integer,
    revision_bundle_sha256 text,
    proof_valid_range tstzrange,
    aggressive_work_id uuid,
    baseline_after_work_id uuid,
    attempt_failed boolean,
    attempt_superseded boolean,
    resolution_id uuid,
    resolution_kind text,
    recovery_work_id uuid,
    recovery_valid_range tstzrange,
    emergency_recovery_complete boolean,
    proof_receipt_id uuid
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
    SELECT authz.authorization_id,
           authz.attempt_number,
           authz.revision_bundle_sha256,
           authz.proof_valid_range,
           aggressive.work_id,
           baseline_after.work_id,
           EXISTS (
               SELECT 1
                 FROM public.experiment_v2_direct_proof_attempt_events event
                WHERE event.authorization_id = authz.authorization_id
                  AND event.event_kind = 'failed'),
           EXISTS (
               SELECT 1
                 FROM public.experiment_v2_direct_proof_attempt_events event
                WHERE event.authorization_id = authz.authorization_id
                  AND event.event_kind = 'superseded'),
           resolution.resolution_id,
           resolution.resolution_kind,
           resolution.recovery_work_id,
           resolution.recovery_valid_range,
           recovery_receipt.resolution_id IS NOT NULL,
           proof_receipt.proof_receipt_id
      FROM public.experiment_v2_direct_proof_authorizations authz
      LEFT JOIN public.experiment_v2_direct_proof_attempt_work aggressive
        ON aggressive.authorization_id = authz.authorization_id
       AND aggressive.stage = 'aggressive'
      LEFT JOIN public.experiment_v2_direct_proof_attempt_work baseline_after
        ON baseline_after.authorization_id = authz.authorization_id
       AND baseline_after.stage = 'baseline_after'
      LEFT JOIN LATERAL (
          SELECT candidate.*
            FROM public.experiment_v2_direct_proof_emergency_resolutions candidate
           WHERE candidate.authorization_id = authz.authorization_id
           ORDER BY candidate.recovery_attempt_number DESC
           LIMIT 1
      ) resolution ON true
      LEFT JOIN public.experiment_v2_direct_proof_emergency_recovery_receipts recovery_receipt
        ON recovery_receipt.resolution_id = resolution.resolution_id
      LEFT JOIN public.experiment_v2_direct_proof_receipts proof_receipt
        ON proof_receipt.authorization_id = authz.authorization_id
     WHERE authz.experiment_id = p_experiment_id
     ORDER BY authz.attempt_number DESC
     LIMIT 1
$body$;
