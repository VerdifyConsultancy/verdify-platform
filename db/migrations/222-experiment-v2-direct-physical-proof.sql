-- 222-experiment-v2-direct-physical-proof.sql
--
-- Exact one-study operator path for the #641/#642 attended
-- baseline -> aggressive -> baseline proof.  Migration 220 deliberately left
-- the ordinary staged lifecycle unchanged, but its direct lock required the
-- draft to remain in shadow while the only physical executor admitted work in
-- commissioning.  These functions bridge that gap without weakening the
-- ordinary shadow/commissioning/A-A path or manufacturing staged approvals.

CREATE TABLE IF NOT EXISTS public.experiment_v2_direct_proof_authorizations (
    authorization_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id uuid NOT NULL UNIQUE
        REFERENCES public.control_experiments(experiment_id)
        CHECK (experiment_id = '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid),
    revision_bundle_sha256 text NOT NULL
        REFERENCES public.experiment_v2_candidate_revisions(revision_bundle_sha256)
        CHECK (revision_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    issue_number integer NOT NULL CHECK (issue_number = 641),
    authorization_ref text NOT NULL CHECK (length(authorization_ref) > 0),
    proof_valid_range tstzrange NOT NULL CHECK (
        NOT isempty(proof_valid_range) AND lower_inc(proof_valid_range) AND
        NOT upper_inc(proof_valid_range) AND NOT lower_inf(proof_valid_range) AND
        NOT upper_inf(proof_valid_range)),
    supervisor_role text NOT NULL CHECK (length(supervisor_role) > 0),
    rescue_owner_role text NOT NULL CHECK (length(rescue_owner_role) > 0),
    authorized_by text NOT NULL CHECK (length(authorized_by) > 0),
    authorized_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_direct_proof_receipts (
    proof_receipt_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    authorization_id uuid NOT NULL UNIQUE
        REFERENCES public.experiment_v2_direct_proof_authorizations(authorization_id),
    experiment_id uuid NOT NULL UNIQUE
        REFERENCES public.control_experiments(experiment_id),
    revision_bundle_sha256 text NOT NULL
        REFERENCES public.experiment_v2_candidate_revisions(revision_bundle_sha256)
        CHECK (revision_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    baseline_before_work_id uuid NOT NULL
        REFERENCES public.experiment_v2_work(work_id),
    aggressive_work_id uuid NOT NULL
        REFERENCES public.experiment_v2_work(work_id),
    baseline_after_work_id uuid NOT NULL
        REFERENCES public.experiment_v2_work(work_id),
    baseline_before_evidence_sha256 text NOT NULL
        CHECK (baseline_before_evidence_sha256 ~ '^[0-9a-f]{64}$'),
    aggressive_evidence_sha256 text NOT NULL
        CHECK (aggressive_evidence_sha256 ~ '^[0-9a-f]{64}$'),
    baseline_after_evidence_sha256 text NOT NULL
        CHECK (baseline_after_evidence_sha256 ~ '^[0-9a-f]{64}$'),
    proof_valid_range tstzrange NOT NULL CHECK (
        NOT isempty(proof_valid_range) AND lower_inc(proof_valid_range) AND
        NOT upper_inc(proof_valid_range) AND NOT lower_inf(proof_valid_range) AND
        NOT upper_inf(proof_valid_range)),
    proof_receipt_sha256 text NOT NULL UNIQUE
        CHECK (proof_receipt_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_by text NOT NULL CHECK (length(recorded_by) > 0),
    recorded_at timestamptz NOT NULL,
    CHECK (baseline_before_work_id <> aggressive_work_id),
    CHECK (baseline_before_work_id <> baseline_after_work_id),
    CHECK (aggressive_work_id <> baseline_after_work_id),
    CHECK (baseline_before_evidence_sha256 <> aggressive_evidence_sha256),
    CHECK (baseline_before_evidence_sha256 <> baseline_after_evidence_sha256),
    CHECK (aggressive_evidence_sha256 <> baseline_after_evidence_sha256)
);

ALTER TABLE public.experiment_v2_direct_proof_authorizations
    OWNER TO verdify_experiment_v2_owner;
ALTER TABLE public.experiment_v2_direct_proof_receipts
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON TABLE public.experiment_v2_direct_proof_authorizations
    FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON TABLE public.experiment_v2_direct_proof_receipts
    FROM PUBLIC CASCADE;

DROP TRIGGER IF EXISTS trg_experiment_v2_direct_proof_authorizations_immutable
    ON public.experiment_v2_direct_proof_authorizations;
CREATE TRIGGER trg_experiment_v2_direct_proof_authorizations_immutable
    BEFORE UPDATE OR DELETE ON public.experiment_v2_direct_proof_authorizations
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_immutable();
DROP TRIGGER IF EXISTS trg_experiment_v2_direct_proof_receipts_immutable
    ON public.experiment_v2_direct_proof_receipts;
CREATE TRIGGER trg_experiment_v2_direct_proof_receipts_immutable
    BEFORE UPDATE OR DELETE ON public.experiment_v2_direct_proof_receipts
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_immutable();

-- Enter the exact attended window and create its single aggressive work row.
-- The ordinary phase transition remains unchanged and still requires shadow.
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
    v_state public.experiment_v2_state_artifacts%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_recovery_work_id uuid;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-proof-' || p_experiment_id::text));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_auth FROM public.experiment_v2_direct_proof_authorizations
     WHERE experiment_id = p_experiment_id;
    IF v_auth.authorization_id IS NOT NULL THEN
        IF (v_auth.authorization_ref, v_auth.proof_valid_range,
            v_auth.supervisor_role, v_auth.rescue_owner_role,
            v_auth.authorized_by) IS DISTINCT FROM
           (p_authorization_ref, p_proof_valid_range,
            p_supervisor_role, p_rescue_owner_role, p_actor) THEN
            RAISE EXCEPTION 'direct-proof authorization is immutable and exact replay differs';
        END IF;
        SELECT * INTO v_work FROM public.experiment_v2_work
         WHERE experiment_id = p_experiment_id
           AND revision_bundle_sha256 = v_auth.revision_bundle_sha256
           AND operation_kind = 'commissioning_canary'
           AND target_profile = 'aggressive';
        IF v_work.work_id IS NULL THEN
            RAISE EXCEPTION 'direct-proof begin replay conflicts with current lifecycle';
        END IF;
        RETURN v_work.work_id;
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
           LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
            WHERE exposure.experiment_id = p_experiment_id
              AND closure.exposure_id IS NULL) THEN
        RAISE EXCEPTION 'direct proof requires the exact closed feature-off draft and three frozen profiles';
    END IF;
    IF p_authorization_ref IS NULL OR length(p_authorization_ref) = 0 OR
       p_actor IS NULL OR length(p_actor) = 0 OR
       p_supervisor_role IS DISTINCT FROM 'Jason Vallery' OR
       p_rescue_owner_role IS DISTINCT FROM 'Jason Vallery' OR
       p_proof_valid_range IS NULL OR isempty(p_proof_valid_range) OR
       lower_inf(p_proof_valid_range) OR upper_inf(p_proof_valid_range) OR
       NOT lower_inc(p_proof_valid_range) OR upper_inc(p_proof_valid_range) OR
       NOT v_now <@ p_proof_valid_range OR
       upper(p_proof_valid_range) - lower(p_proof_valid_range) < interval '3 minutes' OR
       upper(p_proof_valid_range) - lower(p_proof_valid_range) > interval '12 hours' THEN
        RAISE EXCEPTION 'direct proof requires one active 3-minute-to-12-hour attended window with Jason Vallery in both facility roles';
    END IF;
    SELECT * INTO STRICT v_state FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = p_experiment_id
       AND revision_bundle_sha256 = v_exp.revision_bundle_sha256
       AND profile = 'aggressive';
    INSERT INTO public.experiment_v2_direct_proof_authorizations
        (experiment_id, revision_bundle_sha256, issue_number,
         authorization_ref, proof_valid_range, supervisor_role,
         rescue_owner_role, authorized_by, authorized_at)
    VALUES
        (p_experiment_id, v_exp.revision_bundle_sha256, 641,
         p_authorization_ref, p_proof_valid_range, p_supervisor_role,
         p_rescue_owner_role, p_actor, v_now)
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
    v_recovery_work_id := public.fn_experiment_v2_request_recovery_at(
        p_experiment_id, v_work.work_id, p_proof_valid_range,
        upper(p_proof_valid_range), 'direct-proof-baseline-before',
        v_now, p_actor);
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES
        (p_experiment_id, 'state_transition', 'warning', p_actor,
         jsonb_build_object(
             'v2_phase', 'commissioning',
             'launch_path', 'direct_randomized_2026_08_27',
             'direct_proof_authorization_id', v_auth.authorization_id,
             'aggressive_work_id', v_work.work_id,
             'baseline_before_work_id', v_recovery_work_id,
             'v2_admission', 'baseline_recovery'), v_now);
    RETURN v_work.work_id;
END;
$body$;

-- Open only the pre-created aggressive work after its linked full-baseline
-- recovery has two advancing exact receipts.  No ordinary commissioning or
-- A/A approval row is synthesized.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_open_aggressive(
    p_experiment_id uuid,
    p_aggressive_work_id uuid,
    p_actor text DEFAULT current_user
) RETURNS public.control_experiments
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_auth public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-proof-' || p_experiment_id::text));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_auth FROM public.experiment_v2_direct_proof_authorizations
     WHERE experiment_id = p_experiment_id;
    SELECT * INTO v_work FROM public.experiment_v2_work
     WHERE experiment_id = p_experiment_id AND work_id = p_aggressive_work_id;
    IF v_exp.admission_state = 'open' AND
       v_auth.authorization_id IS NOT NULL AND
       v_exp.status = 'draft' AND
       v_exp.execution_phase = 'commissioning' AND
       v_exp.component_enabled AND
       v_work.operation_kind = 'commissioning_canary' AND
       v_work.target_profile = 'aggressive' AND
       v_work.revision_bundle_sha256 = v_exp.revision_bundle_sha256 AND
       v_work.lease_generation = v_exp.lease_generation AND
       p_actor IS NOT NULL AND length(p_actor) > 0 THEN
        RETURN v_exp;
    END IF;
    IF v_exp.experiment_id IS NULL OR
       v_exp.experiment_id <> '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid OR
       v_auth.authorization_id IS NULL OR NOT v_now <@ v_auth.proof_valid_range OR
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'commissioning' OR
       v_exp.admission_state <> 'baseline_recovery' OR
       NOT v_exp.component_enabled OR
       v_work.operation_kind <> 'commissioning_canary' OR
       v_work.target_profile <> 'aggressive' OR
       v_work.execution_phase <> v_exp.execution_phase OR
       v_work.revision_bundle_sha256 <> v_exp.revision_bundle_sha256 OR
       v_work.lease_generation <> v_exp.lease_generation OR
       NOT v_now <@ v_work.valid_range OR v_now >= v_work.expires_at OR
       (p_actor IS NULL OR length(p_actor) = 0) OR
       (SELECT count(*)
          FROM public.experiment_v2_work recovery
          JOIN public.experiment_v2_work_events recovered
            USING (experiment_id, work_id)
         WHERE recovery.experiment_id = p_experiment_id
           AND recovery.parent_work_id = p_aggressive_work_id
           AND recovery.operation_kind = 'baseline_recovery'
           AND recovery.revision_bundle_sha256 = v_exp.revision_bundle_sha256
           AND recovery.lease_generation = v_exp.lease_generation
           AND recovered.event_kind = 'recovered'
           AND recovered.recorded_at <@ v_auth.proof_valid_range) <> 1 OR
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_work recovery
           JOIN public.experiment_v2_observation_receipts receipt
             USING (experiment_id, work_id)
            WHERE recovery.experiment_id = p_experiment_id
              AND recovery.parent_work_id = p_aggressive_work_id
              AND recovery.operation_kind = 'baseline_recovery'
              AND recovery.revision_bundle_sha256 = v_exp.revision_bundle_sha256
              AND recovery.lease_generation = v_exp.lease_generation
            GROUP BY recovery.work_id
           HAVING count(*) >= 2) OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_work_events terminal
            WHERE terminal.work_id = p_aggressive_work_id
              AND terminal.event_kind IN
                  ('completed', 'failed', 'recovered', 'cancelled', 'superseded')) OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_exposures exposure
           LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
            WHERE exposure.experiment_id = p_experiment_id
              AND closure.exposure_id IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM public.experiment_v2_work recovery
                   WHERE recovery.experiment_id = p_experiment_id
                     AND recovery.work_id = exposure.work_id
                     AND recovery.parent_work_id = p_aggressive_work_id
                     AND recovery.operation_kind = 'baseline_recovery')) THEN
        RAISE EXCEPTION 'direct aggressive admission requires its current attended authorization and recovered baseline';
    END IF;
    IF (SELECT count(*)
          FROM public.experiment_v2_exposures exposure
          LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
         WHERE exposure.experiment_id = p_experiment_id
           AND closure.exposure_id IS NULL) > 1 THEN
        RAISE EXCEPTION 'direct aggressive admission permits at most the single recovered baseline exposure';
    END IF;
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET admission_state = 'open', updated_at = v_now
     WHERE experiment_id = p_experiment_id
     RETURNING * INTO v_exp;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES
        (p_experiment_id, 'state_transition', 'warning', p_actor,
         jsonb_build_object(
             'v2_admission', 'open',
             'launch_path', 'direct_randomized_2026_08_27',
             'aggressive_work_id', p_aggressive_work_id), v_now);
    RETURN v_exp;
END;
$body$;

-- After aggressive evidence is terminal, close its exposure at the next
-- executor claim boundary and admit exactly one second linked baseline.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_begin_baseline_after(
    p_experiment_id uuid,
    p_aggressive_work_id uuid,
    p_actor text DEFAULT current_user
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_auth public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_aggressive_at timestamptz;
    v_before_at timestamptz;
    v_existing uuid;
    v_recovery_work_id uuid;
    v_recovery_range tstzrange;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-proof-' || p_experiment_id::text));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_auth FROM public.experiment_v2_direct_proof_authorizations
     WHERE experiment_id = p_experiment_id;
    SELECT * INTO v_work FROM public.experiment_v2_work
     WHERE experiment_id = p_experiment_id AND work_id = p_aggressive_work_id;
    SELECT completed.recorded_at INTO v_aggressive_at
      FROM public.experiment_v2_work_events completed
     WHERE completed.experiment_id = p_experiment_id
       AND completed.work_id = p_aggressive_work_id
       AND completed.event_kind = 'completed';
    SELECT recovered.recorded_at INTO v_before_at
      FROM public.experiment_v2_work recovery
      JOIN public.experiment_v2_work_events recovered
        USING (experiment_id, work_id)
     WHERE recovery.experiment_id = p_experiment_id
       AND recovery.parent_work_id = p_aggressive_work_id
       AND recovery.operation_kind = 'baseline_recovery'
       AND recovered.event_kind = 'recovered'
       AND recovered.recorded_at < v_aggressive_at
     ORDER BY recovered.recorded_at DESC LIMIT 1;
    SELECT recovery.work_id INTO v_existing
      FROM public.experiment_v2_work recovery
     WHERE recovery.experiment_id = p_experiment_id
       AND recovery.parent_work_id = p_aggressive_work_id
       AND recovery.operation_kind = 'baseline_recovery'
       AND recovery.created_at > v_aggressive_at
     ORDER BY recovery.created_at LIMIT 1;
    IF v_existing IS NOT NULL THEN
        RETURN v_existing;
    END IF;
    IF v_exp.experiment_id IS NULL OR
       v_exp.experiment_id <> '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid OR
       v_auth.authorization_id IS NULL OR NOT v_now <@ v_auth.proof_valid_range OR
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'commissioning' OR
       v_exp.admission_state <> 'open' OR NOT v_exp.component_enabled OR
       v_work.operation_kind <> 'commissioning_canary' OR
       v_work.target_profile <> 'aggressive' OR
       v_work.revision_bundle_sha256 <> v_exp.revision_bundle_sha256 OR
       v_work.lease_generation <> v_exp.lease_generation OR
       v_aggressive_at IS NULL OR NOT v_aggressive_at <@ v_auth.proof_valid_range OR
       v_before_at IS NULL OR v_now - v_before_at < interval '151 seconds' OR
       (p_actor IS NULL OR length(p_actor) = 0) OR
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_exposures exposure
           LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
            WHERE exposure.experiment_id = p_experiment_id
              AND exposure.work_id = p_aggressive_work_id
              AND closure.exposure_id IS NULL) OR
       (SELECT count(*)
          FROM public.experiment_v2_work recovery
         WHERE recovery.experiment_id = p_experiment_id
           AND recovery.parent_work_id = p_aggressive_work_id
           AND recovery.operation_kind = 'baseline_recovery') <> 1 THEN
        RAISE EXCEPTION 'direct baseline-after requires the completed current aggressive exposure, its single baseline-before, and enough elapsed proof duration';
    END IF;
    v_recovery_range := tstzrange(v_now, upper(v_auth.proof_valid_range), '[)');
    IF upper(v_recovery_range) - lower(v_recovery_range) < interval '90 seconds' THEN
        RAISE EXCEPTION 'direct baseline-after requires at least 90 seconds remaining in the attended window';
    END IF;
    v_recovery_work_id := public.fn_experiment_v2_request_recovery_at(
        p_experiment_id, p_aggressive_work_id, v_recovery_range,
        upper(v_recovery_range), 'direct-proof-baseline-after', v_now, p_actor);
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET admission_state = 'baseline_recovery', updated_at = v_now
     WHERE experiment_id = p_experiment_id;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES
        (p_experiment_id, 'state_transition', 'warning', p_actor,
         jsonb_build_object(
             'v2_admission', 'baseline_recovery',
             'launch_path', 'direct_randomized_2026_08_27',
             'aggressive_work_id', p_aggressive_work_id,
             'baseline_after_work_id', v_recovery_work_id), v_now);
    RETURN v_recovery_work_id;
END;
$body$;

-- Seal the three terminal work rows and their actual observation receipts,
-- then return the feature to closed shadow so migration 220's atomic lock can
-- consume only this immutable proof.  The one-way ordinary phase graph is not
-- changed.
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
    v_recovery_count integer;
    v_before_count integer;
    v_aggressive_count integer;
    v_after_count integer;
    v_before_hash text;
    v_aggressive_hash text;
    v_after_hash text;
    v_receipt_hash text;
    v_after_exposure uuid;
    v_proof_range tstzrange;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-proof-' || p_experiment_id::text));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_auth FROM public.experiment_v2_direct_proof_authorizations
     WHERE experiment_id = p_experiment_id;
    SELECT * INTO v_existing FROM public.experiment_v2_direct_proof_receipts
     WHERE experiment_id = p_experiment_id;
    IF v_existing.proof_receipt_id IS NOT NULL THEN
        RETURN v_existing;
    END IF;
    IF v_exp.experiment_id IS NULL OR
       v_exp.experiment_id <> '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid OR
       v_auth.authorization_id IS NULL OR
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'commissioning' OR
       v_exp.admission_state <> 'baseline_recovery' OR
       NOT v_exp.component_enabled OR
       (p_actor IS NULL OR length(p_actor) = 0) OR
       NOT v_now <@ v_auth.proof_valid_range THEN
        RAISE EXCEPTION 'direct proof finishes only after its attended baseline-after recovery';
    END IF;
    SELECT work.work_id, completed.recorded_at
      INTO v_aggressive_work, v_aggressive_at
      FROM public.experiment_v2_work work
      JOIN public.experiment_v2_work_events completed
        USING (experiment_id, work_id)
     WHERE work.experiment_id = p_experiment_id
       AND work.revision_bundle_sha256 = v_exp.revision_bundle_sha256
       AND work.lease_generation = v_exp.lease_generation
       AND work.operation_kind = 'commissioning_canary'
       AND work.target_profile = 'aggressive'
       AND completed.event_kind = 'completed';
    IF v_aggressive_work IS NULL OR NOT v_aggressive_at <@ v_auth.proof_valid_range THEN
        RAISE EXCEPTION 'direct proof requires one completed aggressive work row in the attended window';
    END IF;
    SELECT count(*)::integer INTO v_recovery_count
      FROM public.experiment_v2_work recovery
      JOIN public.experiment_v2_work_events recovered
        USING (experiment_id, work_id)
     WHERE recovery.experiment_id = p_experiment_id
       AND recovery.parent_work_id = v_aggressive_work
       AND recovery.operation_kind = 'baseline_recovery'
       AND recovery.revision_bundle_sha256 = v_exp.revision_bundle_sha256
       AND recovery.lease_generation = v_exp.lease_generation
       AND recovered.event_kind = 'recovered'
       AND recovered.recorded_at <@ v_auth.proof_valid_range;
    IF v_recovery_count <> 2 THEN
        RAISE EXCEPTION 'direct proof requires exactly two linked recovered baselines';
    END IF;
    SELECT recovery.work_id, recovered.recorded_at
      INTO v_before_work, v_before_at
      FROM public.experiment_v2_work recovery
      JOIN public.experiment_v2_work_events recovered
        USING (experiment_id, work_id)
     WHERE recovery.experiment_id = p_experiment_id
       AND recovery.parent_work_id = v_aggressive_work
       AND recovery.operation_kind = 'baseline_recovery'
       AND recovery.revision_bundle_sha256 = v_exp.revision_bundle_sha256
       AND recovery.lease_generation = v_exp.lease_generation
       AND recovered.event_kind = 'recovered'
       AND recovered.recorded_at < v_aggressive_at
     ORDER BY recovered.recorded_at DESC LIMIT 1;
    SELECT recovery.work_id, recovered.recorded_at
      INTO v_after_work, v_after_at
      FROM public.experiment_v2_work recovery
      JOIN public.experiment_v2_work_events recovered
        USING (experiment_id, work_id)
     WHERE recovery.experiment_id = p_experiment_id
       AND recovery.parent_work_id = v_aggressive_work
       AND recovery.operation_kind = 'baseline_recovery'
       AND recovery.revision_bundle_sha256 = v_exp.revision_bundle_sha256
       AND recovery.lease_generation = v_exp.lease_generation
       AND recovered.event_kind = 'recovered'
       AND recovered.recorded_at > v_aggressive_at
     ORDER BY recovered.recorded_at LIMIT 1;
    IF v_before_work IS NULL OR v_after_work IS NULL OR
       NOT (v_before_at < v_aggressive_at AND v_aggressive_at < v_after_at) THEN
        RAISE EXCEPTION 'direct proof requires baseline-before, aggressive, baseline-after terminal order';
    END IF;
    SELECT exposure.exposure_id INTO v_after_exposure
      FROM public.experiment_v2_exposures exposure
      LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
     WHERE exposure.experiment_id = p_experiment_id
       AND exposure.work_id = v_after_work
       AND closure.exposure_id IS NULL;
    IF v_after_exposure IS NULL OR
       (SELECT count(*)
          FROM public.experiment_v2_exposures exposure
          LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
         WHERE exposure.experiment_id = p_experiment_id
           AND closure.exposure_id IS NULL) <> 1 OR EXISTS (
        SELECT 1 FROM public.experiment_v2_exposures exposure
        LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
         WHERE exposure.experiment_id = p_experiment_id
           AND exposure.work_id <> v_after_work
           AND closure.exposure_id IS NULL) THEN
        RAISE EXCEPTION 'direct proof requires only the confirmed baseline-after exposure to remain open';
    END IF;
    SELECT count(*)::integer,
           encode(digest(convert_to(
               'verdify-direct-proof-evidence-v1|' || v_before_work::text || '|' ||
               string_agg(receipt.observation_receipt_sha256, '|'
                          ORDER BY receipt.persisted_at, receipt.receipt_id),
               'UTF8'), 'sha256'), 'hex')
      INTO v_before_count, v_before_hash
      FROM public.experiment_v2_observation_receipts receipt
     WHERE receipt.work_id = v_before_work;
    SELECT count(*)::integer,
           encode(digest(convert_to(
               'verdify-direct-proof-evidence-v1|' || v_aggressive_work::text || '|' ||
               string_agg(receipt.observation_receipt_sha256, '|'
                          ORDER BY receipt.persisted_at, receipt.receipt_id),
               'UTF8'), 'sha256'), 'hex')
      INTO v_aggressive_count, v_aggressive_hash
      FROM public.experiment_v2_observation_receipts receipt
     WHERE receipt.work_id = v_aggressive_work;
    SELECT count(*)::integer,
           encode(digest(convert_to(
               'verdify-direct-proof-evidence-v1|' || v_after_work::text || '|' ||
               string_agg(receipt.observation_receipt_sha256, '|'
                          ORDER BY receipt.persisted_at, receipt.receipt_id),
               'UTF8'), 'sha256'), 'hex')
      INTO v_after_count, v_after_hash
      FROM public.experiment_v2_observation_receipts receipt
     WHERE receipt.work_id = v_after_work;
    IF v_before_count < 2 OR v_aggressive_count < 2 OR v_after_count < 2 OR
       v_before_hash = v_aggressive_hash OR v_before_hash = v_after_hash OR
       v_aggressive_hash = v_after_hash THEN
        RAISE EXCEPTION 'direct proof requires distinct receipt-bound two-epoch evidence for all three states';
    END IF;
    v_proof_range := tstzrange(
        v_before_at, v_after_at + interval '1 microsecond', '[)');
    IF NOT v_proof_range <@ v_auth.proof_valid_range OR
       upper(v_proof_range) > v_now OR
       upper(v_proof_range) - lower(v_proof_range) < interval '3 minutes' OR
       upper(v_proof_range) - lower(v_proof_range) > interval '12 hours' THEN
        RAISE EXCEPTION 'direct proof evidence must span one completed 3-minute-to-12-hour interval inside the attended authorization';
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
         v_receipt_hash,
         p_actor, v_now)
    RETURNING * INTO v_row;
    PERFORM public.fn_experiment_v2_close_exposure(
        v_after_exposure, 'boundary', p_actor);
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
             'launch_path', 'direct_randomized_2026_08_27',
             'direct_proof_receipt_id', v_row.proof_receipt_id,
             'proof_receipt_sha256', v_row.proof_receipt_sha256), v_now);
    RETURN v_row;
END;
$body$;

-- The existing direct lock remains the single write of the immutable design.
-- This wrapper removes caller-selected physical hashes and roles: they come
-- only from the sealed proof receipt and authorization.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_launch_commit(
    p_experiment_id uuid,
    p_study_start_local_date date,
    p_randomized_pair_count integer,
    p_selector_context_cutoff_local time without time zone,
    p_design_lock_sha256 text,
    p_source_git_sha text,
    p_schedule_schema_sha256 text,
    p_selector_identity_sha256 text,
    p_selector_artifact_sha256 text,
    p_context_schema_sha256 text,
    p_endpoint_artifact_sha256 text,
    p_outcome_schema_sha256 text,
    p_analyzer_environment_sha256 text,
    p_power_artifact_sha256 text,
    p_actor text DEFAULT current_user
) RETURNS public.control_experiments
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_auth public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_receipt public.experiment_v2_direct_proof_receipts%ROWTYPE;
BEGIN
    SELECT * INTO v_auth FROM public.experiment_v2_direct_proof_authorizations
     WHERE experiment_id = p_experiment_id;
    SELECT * INTO v_receipt FROM public.experiment_v2_direct_proof_receipts
     WHERE experiment_id = p_experiment_id;
    IF v_auth.authorization_id IS NULL OR v_receipt.proof_receipt_id IS NULL OR
       v_receipt.authorization_id <> v_auth.authorization_id THEN
        RAISE EXCEPTION 'direct launch commit requires the immutable attended proof receipt';
    END IF;
    RETURN public.fn_experiment_v2_direct_launch_lock(
        p_experiment_id, p_study_start_local_date, p_randomized_pair_count,
        p_selector_context_cutoff_local, p_design_lock_sha256, p_source_git_sha,
        p_schedule_schema_sha256, p_selector_identity_sha256,
        p_selector_artifact_sha256, p_context_schema_sha256,
        p_endpoint_artifact_sha256, p_outcome_schema_sha256,
        p_analyzer_environment_sha256, p_power_artifact_sha256,
        v_auth.authorization_ref, v_receipt.proof_receipt_sha256,
        'c185909cfd2a097c7dc3c7b820f4ebc4609b1261a555b7af8ed6294669ee1ea1',
        v_receipt.baseline_before_evidence_sha256,
        v_receipt.aggressive_evidence_sha256,
        v_receipt.baseline_after_evidence_sha256,
        v_receipt.proof_valid_range, v_auth.supervisor_role,
        v_auth.rescue_owner_role, p_actor);
END;
$body$;

-- Prevent even the owner-side migration-220 function from accepting a caller
-- supplied proof tuple that was not sealed by the exact executor sequence.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_waiver_proof_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
DECLARE
    v_auth public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_receipt public.experiment_v2_direct_proof_receipts%ROWTYPE;
BEGIN
    SELECT * INTO v_auth FROM public.experiment_v2_direct_proof_authorizations
     WHERE experiment_id = NEW.experiment_id;
    SELECT * INTO v_receipt FROM public.experiment_v2_direct_proof_receipts
     WHERE experiment_id = NEW.experiment_id;
    IF v_auth.authorization_id IS NULL OR v_receipt.proof_receipt_id IS NULL OR
       v_receipt.authorization_id <> v_auth.authorization_id OR
       (NEW.revision_bundle_sha256, NEW.authorization_ref,
        NEW.qualification_artifact_sha256,
        NEW.baseline_before_evidence_sha256,
        NEW.aggressive_evidence_sha256,
        NEW.baseline_after_evidence_sha256,
        NEW.proof_valid_range, NEW.supervisor_role, NEW.rescue_owner_role)
       IS DISTINCT FROM
       (v_receipt.revision_bundle_sha256, v_auth.authorization_ref,
        v_receipt.proof_receipt_sha256,
        v_receipt.baseline_before_evidence_sha256,
        v_receipt.aggressive_evidence_sha256,
        v_receipt.baseline_after_evidence_sha256,
        v_receipt.proof_valid_range, v_auth.supervisor_role,
        v_auth.rescue_owner_role) THEN
        RAISE EXCEPTION 'direct-launch waiver must consume the exact sealed physical proof';
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_direct_waiver_proof_binding
    ON public.experiment_v2_direct_launch_waivers;
CREATE TRIGGER trg_experiment_v2_direct_waiver_proof_binding
    BEFORE INSERT ON public.experiment_v2_direct_launch_waivers
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_direct_waiver_proof_binding();

ALTER FUNCTION public.fn_experiment_v2_direct_proof_begin(
    uuid,text,tstzrange,text,text,text) OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_proof_open_aggressive(
    uuid,uuid,text) OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_proof_begin_baseline_after(
    uuid,uuid,text) OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_proof_finish(uuid,text)
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_launch_commit(
    uuid,date,integer,time without time zone,
    text,text,text,text,text,text,text,text,text,text,text)
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_waiver_proof_binding()
    OWNER TO verdify_experiment_v2_owner;

REVOKE ALL PRIVILEGES ON FUNCTION public.fn_experiment_v2_direct_proof_begin(
    uuid,text,tstzrange,text,text,text) FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION public.fn_experiment_v2_direct_proof_open_aggressive(
    uuid,uuid,text) FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION public.fn_experiment_v2_direct_proof_begin_baseline_after(
    uuid,uuid,text) FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION public.fn_experiment_v2_direct_proof_finish(
    uuid,text) FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION public.fn_experiment_v2_direct_launch_commit(
    uuid,date,integer,time without time zone,
    text,text,text,text,text,text,text,text,text,text,text)
    FROM PUBLIC CASCADE;

DO $security$
DECLARE
    fn regprocedure;
BEGIN
    FOREACH fn IN ARRAY ARRAY[
        'public.fn_experiment_v2_direct_proof_begin(uuid,text,tstzrange,text,text,text)'::regprocedure,
        'public.fn_experiment_v2_direct_proof_open_aggressive(uuid,uuid,text)'::regprocedure,
        'public.fn_experiment_v2_direct_proof_begin_baseline_after(uuid,uuid,text)'::regprocedure,
        'public.fn_experiment_v2_direct_proof_finish(uuid,text)'::regprocedure,
        'public.fn_experiment_v2_direct_launch_commit(uuid,date,integer,time without time zone,text,text,text,text,text,text,text,text,text,text,text)'::regprocedure
    ] LOOP
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %s TO verdify_experiment_lifecycle', fn);
    END LOOP;
END
$security$;
