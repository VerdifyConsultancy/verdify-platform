-- 220-experiment-v2-direct-randomized-launch.sql
--
-- One-study direct randomized launch path authorized in #581/#642 on
-- 2026-08-27.  This is additive: the ordinary protocol-v2 transition and
-- design-lock functions retain the staged shadow/commissioning/A-A contract.
-- A direct lock is possible only for the exact predeclared study, after one
-- completed supervised baseline -> aggressive -> baseline proof, and records
-- every waived stage plus the residual 27/48 compiled-coverage boundary.

CREATE TABLE IF NOT EXISTS public.experiment_v2_direct_launch_waivers (
    waiver_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id uuid NOT NULL UNIQUE
        REFERENCES public.control_experiments(experiment_id)
        CHECK (experiment_id = '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid),
    revision_bundle_sha256 text NOT NULL
        REFERENCES public.experiment_v2_candidate_revisions(revision_bundle_sha256)
        CHECK (revision_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    issue_number integer NOT NULL CHECK (issue_number = 642),
    authorization_ref text NOT NULL CHECK (length(authorization_ref) > 0),
    qualification_artifact_sha256 text NOT NULL
        CHECK (qualification_artifact_sha256 ~ '^[0-9a-f]{64}$'),
    profile_artifact_sha256 text NOT NULL
        CHECK (profile_artifact_sha256 ~ '^[0-9a-f]{64}$'),
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
    supervisor_role text NOT NULL CHECK (length(supervisor_role) > 0),
    rescue_owner_role text NOT NULL CHECK (length(rescue_owner_role) > 0),
    compiled_qualified_fields integer NOT NULL CHECK (compiled_qualified_fields = 27),
    compiled_unqualified_fields integer NOT NULL CHECK (compiled_unqualified_fields = 21),
    waived_stages text[] NOT NULL CHECK (waived_stages = ARRAY[
        'device_dark_shadow', 'separate_commissioning_canaries',
        'aa_48_hours', 'compiled_hil_remaining_21_fields',
        'minimum_joint_power_0_80', 'fixed_pair_count_150_to_30']::text[]),
    design_lock_sha256 text NOT NULL CHECK (design_lock_sha256 ~ '^[0-9a-f]{64}$'),
    source_git_sha text NOT NULL CHECK (source_git_sha ~ '^[0-9a-f]{40}$'),
    recorded_by text NOT NULL CHECK (length(recorded_by) > 0),
    recorded_at timestamptz NOT NULL
);

REVOKE ALL PRIVILEGES ON TABLE public.experiment_v2_direct_launch_waivers
    FROM PUBLIC CASCADE;
ALTER TABLE public.experiment_v2_direct_launch_waivers
    OWNER TO verdify_experiment_v2_owner;

DROP TRIGGER IF EXISTS trg_experiment_v2_direct_launch_waivers_immutable
    ON public.experiment_v2_direct_launch_waivers;
CREATE TRIGGER trg_experiment_v2_direct_launch_waivers_immutable
    BEFORE UPDATE OR DELETE ON public.experiment_v2_direct_launch_waivers
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_immutable();

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_launch_lock(
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
    p_authorization_ref text,
    p_qualification_artifact_sha256 text,
    p_profile_artifact_sha256 text,
    p_baseline_before_evidence_sha256 text,
    p_aggressive_evidence_sha256 text,
    p_baseline_after_evidence_sha256 text,
    p_proof_valid_range tstzrange,
    p_supervisor_role text,
    p_rescue_owner_role text,
    p_actor text DEFAULT current_user
) RETURNS public.control_experiments
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_waiver public.experiment_v2_direct_launch_waivers%ROWTYPE;
    v_now timestamptz := clock_timestamp();
    v_start_at timestamptz;
    v_offset_count integer;
    v_required_hash text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-launch-' || p_experiment_id::text));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_waiver FROM public.experiment_v2_direct_launch_waivers
     WHERE experiment_id = p_experiment_id;

    IF v_exp.experiment_id IS NULL OR v_exp.protocol_version <> 2 THEN
        RAISE EXCEPTION 'direct launch requires one configured protocol-v2 experiment';
    END IF;
    IF (v_exp.experiment_id, v_exp.study_id, v_exp.greenhouse_id, v_exp.timezone)
       IS DISTINCT FROM (
           '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid,
           'verdify-confirmed-component-switchback-v2-2026-08'::text,
           'vallery'::text, 'America/Denver'::text) THEN
        RAISE EXCEPTION 'direct launch is sealed to the exact #642 Vallery study identity';
    END IF;

    -- A lost response is retry-safe only when the complete lock and physical
    -- proof tuple is byte-for-byte identical to the immutable first result.
    IF v_exp.status = 'locked' THEN
        IF v_waiver.waiver_id IS NOT NULL AND
           (v_exp.execution_phase, v_exp.admission_state, v_exp.component_enabled,
            v_exp.study_start_local_date, v_exp.randomized_pair_count,
            v_exp.selector_context_cutoff_local, v_exp.design_lock_sha256,
            v_exp.source_git_sha, v_exp.schedule_schema_sha256,
            v_exp.selector_identity_sha256, v_exp.selector_artifact_sha256,
            v_exp.context_schema_sha256, v_exp.endpoint_artifact_sha256,
            v_exp.outcome_schema_sha256, v_exp.analyzer_environment_sha256,
            v_exp.power_artifact_sha256,
            v_waiver.authorization_ref, v_waiver.qualification_artifact_sha256,
            v_waiver.profile_artifact_sha256,
            v_waiver.baseline_before_evidence_sha256,
            v_waiver.aggressive_evidence_sha256,
            v_waiver.baseline_after_evidence_sha256,
            v_waiver.proof_valid_range, v_waiver.supervisor_role,
            v_waiver.rescue_owner_role) IS NOT DISTINCT FROM
           ('randomized'::text, 'closed'::text, false,
            p_study_start_local_date, p_randomized_pair_count,
            p_selector_context_cutoff_local, p_design_lock_sha256,
            p_source_git_sha, p_schedule_schema_sha256,
            p_selector_identity_sha256, p_selector_artifact_sha256,
            p_context_schema_sha256, p_endpoint_artifact_sha256,
            p_outcome_schema_sha256, p_analyzer_environment_sha256,
            p_power_artifact_sha256,
            p_authorization_ref, p_qualification_artifact_sha256,
            p_profile_artifact_sha256,
            p_baseline_before_evidence_sha256,
            p_aggressive_evidence_sha256,
            p_baseline_after_evidence_sha256,
            p_proof_valid_range, p_supervisor_role, p_rescue_owner_role) THEN
            RETURN v_exp;
        END IF;
        RAISE EXCEPTION 'direct-launch design lock is immutable and exact replay differs';
    END IF;

    FOREACH v_required_hash IN ARRAY ARRAY[
        p_design_lock_sha256, p_schedule_schema_sha256,
        p_selector_identity_sha256, p_selector_artifact_sha256,
        p_context_schema_sha256, p_endpoint_artifact_sha256,
        p_outcome_schema_sha256, p_analyzer_environment_sha256,
        p_power_artifact_sha256, p_qualification_artifact_sha256,
        p_profile_artifact_sha256, p_baseline_before_evidence_sha256,
        p_aggressive_evidence_sha256, p_baseline_after_evidence_sha256
    ] LOOP
        IF v_required_hash IS NULL OR v_required_hash !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'direct launch requires every exact lowercase artifact SHA-256';
        END IF;
    END LOOP;
    IF p_source_git_sha IS NULL OR p_source_git_sha !~ '^[0-9a-f]{40}$' OR
       p_schedule_schema_sha256 <>
           'fc73d212f58db91bd55bb70e3faa1431172b4339ae3b22a11d404ba95147b794' OR
       p_selector_context_cutoff_local IS NULL OR
       p_study_start_local_date IS NULL OR p_randomized_pair_count IS NULL OR
       p_randomized_pair_count <> 30 OR
       p_power_artifact_sha256 <>
           '4d751a76465d03dc2e75034dcb398d25dc39b375d9976671bd8fffb018d237a2' OR
       p_profile_artifact_sha256 <>
           'c185909cfd2a097c7dc3c7b820f4ebc4609b1261a555b7af8ed6294669ee1ea1' OR
       p_authorization_ref IS NULL OR
       length(p_authorization_ref) = 0 OR p_actor IS NULL OR length(p_actor) = 0 THEN
        RAISE EXCEPTION 'direct launch requires the exact accepted-risk 30-pair power/profile lock, source, authorization, and actor';
    END IF;
    IF p_proof_valid_range IS NULL OR isempty(p_proof_valid_range) OR
       lower_inf(p_proof_valid_range) OR upper_inf(p_proof_valid_range) OR
       NOT lower_inc(p_proof_valid_range) OR upper_inc(p_proof_valid_range) OR
       upper(p_proof_valid_range) > v_now OR
       upper(p_proof_valid_range) - lower(p_proof_valid_range) < interval '3 minutes' OR
       upper(p_proof_valid_range) - lower(p_proof_valid_range) > interval '12 hours' OR
       p_supervisor_role IS NULL OR length(p_supervisor_role) = 0 OR
       p_rescue_owner_role IS NULL OR length(p_rescue_owner_role) = 0 THEN
        RAISE EXCEPTION 'direct launch requires one completed 3-minute-to-12-hour supervised proof window';
    END IF;
    IF p_baseline_before_evidence_sha256 = p_aggressive_evidence_sha256 OR
       p_baseline_before_evidence_sha256 = p_baseline_after_evidence_sha256 OR
       p_aggressive_evidence_sha256 = p_baseline_after_evidence_sha256 THEN
        RAISE EXCEPTION 'baseline-before, aggressive, and baseline-after evidence must be distinct';
    END IF;

    v_start_at := p_study_start_local_date::timestamp AT TIME ZONE v_exp.timezone;
    IF v_start_at <= v_now THEN
        RAISE EXCEPTION 'direct-launch design start must remain strictly in the future';
    END IF;
    SELECT count(DISTINCT (
        (p_study_start_local_date + i)::timestamp -
        (((p_study_start_local_date + i)::timestamp AT TIME ZONE v_exp.timezone)
            AT TIME ZONE 'UTC')))
      INTO v_offset_count
      FROM generate_series(0, p_randomized_pair_count * 2) i;
    IF v_offset_count <> 1 THEN
        RAISE EXCEPTION 'protocol v2 forbids a UTC-offset crossing in the locked local-day window';
    END IF;

    IF v_exp.status <> 'draft' OR v_exp.execution_phase <> 'shadow' OR
       v_exp.admission_state <> 'closed' OR v_exp.component_enabled OR
       v_exp.design_lock_sha256 IS NOT NULL OR v_waiver.waiver_id IS NOT NULL OR
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_candidate_revisions revision
            WHERE revision.experiment_id = p_experiment_id
              AND revision.revision_bundle_sha256 = v_exp.revision_bundle_sha256) OR
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
        RAISE EXCEPTION 'direct launch requires closed feature-off draft, three frozen profiles, current revision, and no exposure';
    END IF;

    INSERT INTO public.experiment_v2_direct_launch_waivers
        (experiment_id, revision_bundle_sha256, issue_number,
         authorization_ref, qualification_artifact_sha256,
         profile_artifact_sha256, baseline_before_evidence_sha256,
         aggressive_evidence_sha256, baseline_after_evidence_sha256,
         proof_valid_range, supervisor_role, rescue_owner_role,
         compiled_qualified_fields, compiled_unqualified_fields,
         waived_stages, design_lock_sha256, source_git_sha,
         recorded_by, recorded_at)
    VALUES
        (p_experiment_id, v_exp.revision_bundle_sha256, 642,
         p_authorization_ref, p_qualification_artifact_sha256,
         p_profile_artifact_sha256, p_baseline_before_evidence_sha256,
         p_aggressive_evidence_sha256, p_baseline_after_evidence_sha256,
         p_proof_valid_range, p_supervisor_role, p_rescue_owner_role,
         27, 21, ARRAY['device_dark_shadow', 'separate_commissioning_canaries',
                       'aa_48_hours', 'compiled_hil_remaining_21_fields',
                       'minimum_joint_power_0_80',
                       'fixed_pair_count_150_to_30']::text[],
         p_design_lock_sha256, p_source_git_sha, p_actor, v_now)
    RETURNING * INTO v_waiver;

    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET status = 'locked', execution_phase = 'randomized',
           component_enabled = false, admission_state = 'closed',
           study_start_local_date = p_study_start_local_date,
           randomized_pair_count = p_randomized_pair_count,
           selector_context_cutoff_local = p_selector_context_cutoff_local,
           design_lock_sha256 = p_design_lock_sha256,
           source_git_sha = p_source_git_sha,
           schedule_schema_sha256 = p_schedule_schema_sha256,
           selector_identity_sha256 = p_selector_identity_sha256,
           selector_artifact_sha256 = p_selector_artifact_sha256,
           context_schema_sha256 = p_context_schema_sha256,
           endpoint_artifact_sha256 = p_endpoint_artifact_sha256,
           outcome_schema_sha256 = p_outcome_schema_sha256,
           analyzer_environment_sha256 = p_analyzer_environment_sha256,
           power_artifact_sha256 = p_power_artifact_sha256,
           lease_generation = lease_generation + 1,
           locked_at = v_now, updated_at = v_now
     WHERE experiment_id = p_experiment_id
     RETURNING * INTO v_exp;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES
        (p_experiment_id, 'state_transition', 'warning', p_actor,
         jsonb_build_object(
             'v2_status', 'locked', 'v2_phase', 'randomized',
             'launch_path', 'direct_randomized_2026_08_27',
             'direct_launch_waiver_id', v_waiver.waiver_id,
             'qualification_artifact_sha256', p_qualification_artifact_sha256,
             'compiled_qualified_fields', 27,
             'compiled_unqualified_fields', 21,
             'waived_stages', v_waiver.waived_stages), v_now);
    RETURN v_exp;
END;
$body$;

-- Once the randomizer has made its single internal draw, derive the existing
-- day-1 approval row from the immutable waiver.  No new human approval or
-- caller-selected artifact is accepted here.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_launch_approve_day1(
    p_experiment_id uuid,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_approvals
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_waiver public.experiment_v2_direct_launch_waivers%ROWTYPE;
    v_existing public.experiment_v2_approvals%ROWTYPE;
    v_row public.experiment_v2_approvals%ROWTYPE;
    v_ref text;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_waiver FROM public.experiment_v2_direct_launch_waivers
     WHERE experiment_id = p_experiment_id;
    v_ref := 'direct-launch-waiver:' || v_waiver.waiver_id::text;
    SELECT * INTO v_existing FROM public.experiment_v2_approvals
     WHERE experiment_id = p_experiment_id
       AND revision_bundle_sha256 = v_exp.revision_bundle_sha256
       AND approval_kind = 'randomized_day_1' AND scope_name = 'day1';
    IF FOUND THEN
        IF v_existing.issue_number <> 642 OR
           v_existing.approval_ref IS DISTINCT FROM v_ref OR
           v_existing.artifact_sha256 IS DISTINCT FROM
               v_waiver.qualification_artifact_sha256 THEN
            RAISE EXCEPTION 'direct-launch day-1 approval is immutable and differs';
        END IF;
        RETURN v_existing;
    END IF;
    IF v_exp.experiment_id IS NULL OR v_waiver.waiver_id IS NULL OR
       v_exp.experiment_id <> '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid OR
       v_exp.status <> 'armed' OR v_exp.execution_phase <> 'randomized' OR
       v_exp.admission_state <> 'closed' OR NOT v_exp.component_enabled OR
       p_actor IS NULL OR length(p_actor) = 0 OR NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_randomization randomization
            WHERE randomization.experiment_id = p_experiment_id
              AND randomization.design_lock_sha256 = v_exp.design_lock_sha256) OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_exposures exposure
           LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
            WHERE exposure.experiment_id = p_experiment_id
              AND closure.exposure_id IS NULL) THEN
        RAISE EXCEPTION 'direct-launch day 1 requires finalized armed design, immutable waiver, closed admission, and no exposure';
    END IF;
    INSERT INTO public.experiment_v2_approvals
        (experiment_id, approval_kind, scope_name, issue_number, approval_ref,
         artifact_sha256, revision_bundle_sha256, valid_range, expires_at,
         supervisor_role, rescue_owner_role, approved_by, approved_at)
    VALUES
        (p_experiment_id, 'randomized_day_1', 'day1', 642, v_ref,
         v_waiver.qualification_artifact_sha256, v_exp.revision_bundle_sha256,
         NULL, NULL, NULL, NULL, p_actor, v_now)
    RETURNING * INTO v_row;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES
        (p_experiment_id, 'approval_recorded', 'warning', p_actor,
         jsonb_build_object(
             'approval_kind', 'randomized_day_1',
             'launch_path', 'direct_randomized_2026_08_27',
             'direct_launch_waiver_id', v_waiver.waiver_id), v_now);
    RETURN v_row;
END;
$body$;

-- Preserve every original approval-insert rule; only the day-1 predecessor is
-- broadened from combined_physical to combined_physical OR the exact direct
-- waiver.  The dedicated function above is the only ordinary role allowed to
-- manufacture the derived direct approval.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_approval_insert_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = NEW.experiment_id;
    IF v_exp.protocol_version <> 2 OR
       NEW.revision_bundle_sha256 <> v_exp.revision_bundle_sha256 THEN
        RAISE EXCEPTION 'approval must bind the current protocol-v2 candidate revision';
    END IF;
    IF NEW.approval_kind = 'scoped_probe' AND (
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'commissioning' OR
       NEW.artifact_sha256 <> (SELECT state_content_sha256
          FROM public.experiment_v2_state_artifacts
         WHERE experiment_id = NEW.experiment_id
           AND revision_bundle_sha256 = NEW.revision_bundle_sha256
           AND profile = 'commissioning_probe')) THEN
        RAISE EXCEPTION 'scoped #641 approval must bind the frozen diagnostic probe in draft commissioning';
    ELSIF NEW.approval_kind = 'combined_physical' AND (
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'commissioning' OR
       (SELECT count(*) FROM public.experiment_v2_approvals
         WHERE experiment_id = NEW.experiment_id
           AND revision_bundle_sha256 = NEW.revision_bundle_sha256
           AND approval_kind = 'scoped_probe') <> 1 OR
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_work w
           JOIN public.experiment_v2_work_events ev USING (experiment_id, work_id)
            WHERE w.experiment_id = NEW.experiment_id
              AND w.revision_bundle_sha256 = NEW.revision_bundle_sha256
              AND w.operation_kind = 'commissioning_probe'
              AND ev.event_kind = 'completed')) THEN
        RAISE EXCEPTION 'combined #641 approval requires exactly one earlier scoped probe decision';
    ELSIF NEW.approval_kind = 'randomized_day_1' AND (
       v_exp.status <> 'armed' OR v_exp.execution_phase <> 'randomized' OR
       NOT EXISTS (SELECT 1 FROM public.experiment_v2_randomization r
                    WHERE r.experiment_id = NEW.experiment_id) OR
       NOT (
           EXISTS (SELECT 1 FROM public.experiment_v2_approvals a
                    WHERE a.experiment_id = NEW.experiment_id
                      AND a.revision_bundle_sha256 = NEW.revision_bundle_sha256
                      AND a.approval_kind = 'combined_physical') OR
           EXISTS (SELECT 1 FROM public.experiment_v2_direct_launch_waivers waiver
                    WHERE waiver.experiment_id = NEW.experiment_id
                      AND waiver.revision_bundle_sha256 = NEW.revision_bundle_sha256)
       )) THEN
        RAISE EXCEPTION '#642 approval requires finalized armed randomized design after #641 or its immutable direct-launch waiver';
    END IF;
    RETURN NEW;
END;
$body$;

ALTER FUNCTION public.fn_experiment_v2_direct_launch_lock(
    uuid,date,integer,time without time zone,
    text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,
    tstzrange,text,text,text) OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_launch_approve_day1(uuid,text)
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_approval_insert_binding()
    OWNER TO verdify_experiment_v2_owner;

REVOKE ALL PRIVILEGES ON FUNCTION public.fn_experiment_v2_direct_launch_lock(
    uuid,date,integer,time without time zone,
    text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,
    tstzrange,text,text,text) FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_direct_launch_approve_day1(uuid,text)
    FROM PUBLIC CASCADE;

DO $security$
DECLARE
    fn regprocedure;
BEGIN
    FOREACH fn IN ARRAY ARRAY[
        'public.fn_experiment_v2_direct_launch_lock(uuid,date,integer,time without time zone,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,tstzrange,text,text,text)'::regprocedure,
        'public.fn_experiment_v2_direct_launch_approve_day1(uuid,text)'::regprocedure
    ] LOOP
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %s TO verdify_experiment_lifecycle', fn);
    END LOOP;
END
$security$;
