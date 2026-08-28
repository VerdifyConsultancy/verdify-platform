-- 224-experiment-v2-direct-launch-runtime.sql
--
-- Complete the one-study accepted-risk path without broadening any ordinary
-- protocol-v2 approval or admission surface.  Every randomized day receives
-- the same baseline interposition row before its hidden treatment is known to
-- the device executor.  One exact lifecycle entrypoint prepares the draw,
-- closes the preceding exposure at the next selector cutoff, advances the day
-- boundary, and opens randomized admission only after confirmed recovery.

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_interposition_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_baseline public.experiment_v2_state_artifacts%ROWTYPE;
    v_upper timestamptz;
BEGIN
    IF NEW.operation_kind <> 'randomized_assignment' OR
       NEW.experiment_id <> '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid THEN
        RETURN NEW;
    END IF;
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = NEW.experiment_id;
    IF v_exp.protocol_version <> 2 OR v_exp.execution_phase <> 'randomized' OR
       v_exp.status NOT IN ('armed', 'running') OR
       v_exp.revision_bundle_sha256 <> NEW.revision_bundle_sha256 OR
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_launch_waivers waiver
            WHERE waiver.experiment_id = NEW.experiment_id
              AND waiver.revision_bundle_sha256 = NEW.revision_bundle_sha256
              AND waiver.issue_number = 642) THEN
        RAISE EXCEPTION 'direct interposition requires the exact armed accepted-risk study';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.experiment_v2_work recovery
         WHERE recovery.parent_work_id = NEW.work_id
           AND recovery.operation_kind = 'baseline_recovery') THEN
        RETURN NEW;
    END IF;
    SELECT * INTO v_baseline FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = NEW.experiment_id
       AND revision_bundle_sha256 = NEW.revision_bundle_sha256
       AND profile = 'baseline';
    IF v_baseline.state_artifact_id IS NULL THEN
        RAISE EXCEPTION 'direct interposition requires the frozen baseline state';
    END IF;
    v_upper := least(lower(NEW.valid_range) + interval '15 minutes',
                     upper(NEW.valid_range));
    INSERT INTO public.experiment_v2_work
        (experiment_id, parent_work_id, execution_phase, operation_kind,
         target_profile, target_state_content_sha256, revision_bundle_sha256,
         firmware_revision, config_revision, registry_revision, grid_revision,
         lease_generation, valid_range, expires_at, created_by, created_at)
    VALUES
        (NEW.experiment_id, NEW.work_id, 'randomized', 'baseline_recovery',
         'baseline', v_baseline.state_content_sha256,
         NEW.revision_bundle_sha256, NEW.firmware_revision,
         NEW.config_revision, NEW.registry_revision, NEW.grid_revision,
         NEW.lease_generation,
         tstzrange(lower(NEW.valid_range), v_upper, '[)'), v_upper,
         NEW.created_by, NEW.created_at);
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_direct_interposition_insert
    ON public.experiment_v2_work;
CREATE TRIGGER trg_experiment_v2_direct_interposition_insert
    AFTER INSERT ON public.experiment_v2_work
    FOR EACH ROW EXECUTE FUNCTION
        public.fn_experiment_v2_direct_interposition_insert();

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_open_randomized(
    p_experiment_id uuid,
    p_actor text DEFAULT current_user
) RETURNS public.control_experiments
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF v_exp.experiment_id <> '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid OR
       v_exp.protocol_version <> 2 OR v_exp.status <> 'running' OR
       v_exp.execution_phase <> 'randomized' OR NOT v_exp.component_enabled OR
       v_exp.admission_state NOT IN ('closed', 'baseline_recovery') OR
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_launch_waivers waiver
            WHERE waiver.experiment_id = p_experiment_id
              AND waiver.revision_bundle_sha256 = v_exp.revision_bundle_sha256
              AND waiver.issue_number = 642) OR
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_approvals approval
            WHERE approval.experiment_id = p_experiment_id
              AND approval.revision_bundle_sha256 = v_exp.revision_bundle_sha256
              AND approval.approval_kind = 'randomized_day_1') OR
       (SELECT count(*)
          FROM public.experiment_v2_work work
          JOIN public.control_assignments assignment
            ON assignment.experiment_id = work.experiment_id
           AND assignment.assignment_id = work.assignment_id
         WHERE work.experiment_id = p_experiment_id
           AND work.operation_kind = 'randomized_assignment'
           AND assignment.operation_kind = 'randomized_day'
           AND assignment.status = 'active'
           AND work.revision_bundle_sha256 = v_exp.revision_bundle_sha256
           AND work.lease_generation = v_exp.lease_generation
           AND v_now < work.expires_at AND v_now <@ work.valid_range
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_work_events terminal
                WHERE terminal.experiment_id = work.experiment_id
                  AND terminal.work_id = work.work_id
                  AND terminal.event_kind IN
                      ('completed', 'failed', 'recovered', 'cancelled', 'superseded'))
           AND EXISTS (
               SELECT 1 FROM public.experiment_v2_work recovery
               JOIN public.experiment_v2_work_events recovered
                 ON recovered.experiment_id = recovery.experiment_id
                AND recovered.work_id = recovery.work_id
                AND recovered.event_kind = 'recovered'
                WHERE recovery.experiment_id = work.experiment_id
                  AND recovery.parent_work_id = work.work_id
                  AND recovery.operation_kind = 'baseline_recovery'
                  AND recovery.revision_bundle_sha256 = work.revision_bundle_sha256
                  AND recovery.lease_generation = work.lease_generation)) <> 1 OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_exposures exposure
           LEFT JOIN public.experiment_v2_exposure_closures closure
             USING (exposure_id)
            WHERE exposure.experiment_id = p_experiment_id
              AND closure.exposure_id IS NULL) THEN
        RAISE EXCEPTION 'direct randomized admission requires exact current work after confirmed baseline recovery';
    END IF;
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET admission_state = 'open', updated_at = v_now
     WHERE experiment_id = p_experiment_id RETURNING * INTO v_exp;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES (p_experiment_id, 'state_transition', 'warning', p_actor,
            jsonb_build_object(
                'v2_admission', 'open',
                'launch_path', 'direct_randomized_2026_08_27',
                'reason', 'confirmed-universal-baseline-interposition'), v_now);
    RETURN v_exp;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_launch_cycle(
    p_experiment_id uuid,
    p_actor text DEFAULT current_user
) RETURNS TABLE (
    action text,
    subject_id uuid,
    lifecycle_status text,
    admission_state text,
    resolved_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_next public.control_assignments%ROWTYPE;
    v_current public.control_assignments%ROWTYPE;
    v_current_work public.experiment_v2_work%ROWTYPE;
    v_recovery public.experiment_v2_work%ROWTYPE;
    v_exposure_id uuid;
    v_handoff_recovery uuid;
    v_cutoff timestamptz;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-launch-cycle-' || p_experiment_id::text));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF v_exp.experiment_id <> '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid OR
       v_exp.protocol_version <> 2 OR v_exp.execution_phase <> 'randomized' OR
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_launch_waivers waiver
            WHERE waiver.experiment_id = p_experiment_id
              AND waiver.revision_bundle_sha256 = v_exp.revision_bundle_sha256
              AND waiver.issue_number = 642) THEN
        RAISE EXCEPTION 'direct launch cycle is restricted to the exact accepted-risk study';
    END IF;

    IF v_exp.status = 'locked' THEN
        PERFORM * FROM public.fn_experiment_v2_finalize_randomization(
            p_experiment_id, p_actor);
        SELECT * INTO v_exp FROM public.control_experiments
         WHERE experiment_id = p_experiment_id FOR UPDATE;
    END IF;
    IF v_exp.status = 'armed' AND NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_approvals approval
         WHERE approval.experiment_id = p_experiment_id
           AND approval.revision_bundle_sha256 = v_exp.revision_bundle_sha256
           AND approval.approval_kind = 'randomized_day_1') THEN
        PERFORM public.fn_experiment_v2_direct_launch_approve_day1(
            p_experiment_id, p_actor);
    END IF;

    -- Fifteen minutes before the next day, close the preceding exposure and
    -- request a deterministic parentless baseline handoff.  Only after that
    -- exact recovery is confirmed does admission close for the treatment-blind
    -- selector window; no physical treatment continues outside its exposure.
    SELECT assignment.* INTO v_next
      FROM public.control_assignments assignment
      JOIN public.experiment_v2_outcomes outcome
        USING (assignment_id, experiment_id)
      LEFT JOIN public.experiment_v2_selector_choices choice
        USING (assignment_id, experiment_id)
     WHERE assignment.experiment_id = p_experiment_id
       AND assignment.operation_kind = 'randomized_day'
       AND assignment.status = 'active'
       AND choice.assignment_id IS NULL
       AND v_now >= (((outcome.assigned_local_date - 1)::date +
                      v_exp.selector_context_cutoff_local)
                     AT TIME ZONE v_exp.timezone)
       AND v_now < lower(assignment.valid_range)
     ORDER BY lower(assignment.valid_range), assignment.assignment_id LIMIT 1;
    IF v_next.assignment_id IS NOT NULL THEN
        SELECT (((outcome.assigned_local_date - 1)::date +
                 v_exp.selector_context_cutoff_local)
                AT TIME ZONE v_exp.timezone)
          INTO v_cutoff
          FROM public.experiment_v2_outcomes outcome
         WHERE outcome.experiment_id = p_experiment_id
           AND outcome.assignment_id = v_next.assignment_id;
        IF v_exp.status = 'running' AND v_exp.admission_state = 'open' THEN
            FOR v_exposure_id IN
                SELECT exposure.exposure_id
                  FROM public.experiment_v2_exposures exposure
                  LEFT JOIN public.experiment_v2_exposure_closures closure
                    USING (exposure_id)
                 WHERE exposure.experiment_id = p_experiment_id
                   AND closure.exposure_id IS NULL
                 ORDER BY exposure.opened_at, exposure.exposure_id
            LOOP
                PERFORM public.fn_experiment_v2_close_exposure(
                    v_exposure_id, 'baseline_recovery', p_actor);
            END LOOP;
            v_handoff_recovery := public.fn_experiment_v2_request_recovery_at(
                p_experiment_id, NULL,
                tstzrange(v_cutoff, lower(v_next.valid_range), '[)'),
                lower(v_next.valid_range), 'next selector cutoff baseline handoff',
                v_now, p_actor);
            PERFORM public.fn_experiment_v2_set_admission(
                p_experiment_id, 'baseline_recovery', p_actor,
                'next-selector-cutoff');
            SELECT * INTO v_exp FROM public.control_experiments
             WHERE experiment_id = p_experiment_id FOR UPDATE;
        END IF;
        IF v_exp.status = 'running' AND
           v_exp.admission_state = 'baseline_recovery' THEN
            SELECT recovery.work_id INTO v_handoff_recovery
              FROM public.experiment_v2_work recovery
             WHERE recovery.experiment_id = p_experiment_id
               AND recovery.parent_work_id IS NULL
               AND recovery.operation_kind = 'baseline_recovery'
               AND recovery.execution_phase = 'randomized'
               AND recovery.lease_generation = v_exp.lease_generation
               AND recovery.valid_range =
                   tstzrange(v_cutoff, lower(v_next.valid_range), '[)')
               AND EXISTS (
                   SELECT 1 FROM public.experiment_v2_work_events recovered
                    WHERE recovered.experiment_id = recovery.experiment_id
                      AND recovered.work_id = recovery.work_id
                      AND recovered.event_kind = 'recovered');
            IF v_handoff_recovery IS NOT NULL THEN
                PERFORM public.fn_experiment_v2_set_admission(
                    p_experiment_id, 'closed', p_actor,
                    'next-selector-baseline-confirmed');
                SELECT * INTO v_exp FROM public.control_experiments
                 WHERE experiment_id = p_experiment_id FOR UPDATE;
            END IF;
        END IF;
        SELECT * INTO v_exp FROM public.control_experiments
         WHERE experiment_id = p_experiment_id FOR UPDATE;
        RETURN QUERY SELECT
            CASE WHEN v_exp.admission_state = 'closed'
                 THEN 'selector_window_ready'
                 ELSE 'selector_baseline_pending' END,
            v_next.assignment_id, v_exp.status, v_exp.admission_state, v_now;
        RETURN;
    END IF;

    -- Close any elapsed exposure before the ordinary immutable assignment
    -- finalizer runs.  At most one exposure can be open for the greenhouse.
    FOR v_exposure_id IN
        SELECT exposure.exposure_id
          FROM public.experiment_v2_exposures exposure
          JOIN public.control_assignments assignment
            ON assignment.assignment_id = exposure.assignment_id
          LEFT JOIN public.experiment_v2_exposure_closures closure
            USING (exposure_id)
         WHERE exposure.experiment_id = p_experiment_id
           AND closure.exposure_id IS NULL
           AND v_now >= upper(assignment.valid_range)
         ORDER BY exposure.opened_at, exposure.exposure_id
    LOOP
        PERFORM public.fn_experiment_v2_close_exposure(
            v_exposure_id, 'boundary', p_actor);
    END LOOP;
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF v_exp.admission_state = 'open' AND NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_exposures exposure
        LEFT JOIN public.experiment_v2_exposure_closures closure
          USING (exposure_id)
         WHERE exposure.experiment_id = p_experiment_id
           AND closure.exposure_id IS NULL) THEN
        PERFORM public.fn_experiment_v2_set_admission(
            p_experiment_id, 'closed', p_actor, 'assignment-boundary');
        SELECT * INTO v_exp FROM public.control_experiments
         WHERE experiment_id = p_experiment_id FOR UPDATE;
    END IF;
    IF v_exp.status = 'running' THEN
        PERFORM * FROM public.fn_experiment_v2_boundary_cycle(
            p_experiment_id, p_actor);
    END IF;

    SELECT assignment.* INTO v_current
      FROM public.control_assignments assignment
     WHERE assignment.experiment_id = p_experiment_id
       AND assignment.operation_kind = 'randomized_day'
       AND assignment.status = 'active'
       AND v_now <@ assignment.valid_range
     ORDER BY lower(assignment.valid_range), assignment.assignment_id LIMIT 1;
    IF v_current.assignment_id IS NULL THEN
        RETURN QUERY SELECT 'waiting_for_boundary'::text, NULL::uuid,
            v_exp.status, v_exp.admission_state, v_now;
        RETURN;
    END IF;
    SELECT * INTO v_current_work FROM public.experiment_v2_work
     WHERE experiment_id = p_experiment_id
       AND assignment_id = v_current.assignment_id
       AND operation_kind = 'randomized_assignment';
    IF v_current_work.work_id IS NULL THEN
        RETURN QUERY SELECT 'waiting_for_selector'::text,
            v_current.assignment_id, v_exp.status, v_exp.admission_state, v_now;
        RETURN;
    END IF;
    SELECT * INTO v_recovery FROM public.experiment_v2_work
     WHERE experiment_id = p_experiment_id
       AND parent_work_id = v_current_work.work_id
       AND operation_kind = 'baseline_recovery';
    IF v_recovery.work_id IS NULL THEN
        RAISE EXCEPTION 'direct randomized work is missing universal interposition';
    END IF;

    IF v_exp.status = 'armed' THEN
        PERFORM public.fn_experiment_v2_transition(
            p_experiment_id, 'running', NULL, p_actor,
            'direct day-1 boundary after immutable selector choice');
        SELECT * INTO v_exp FROM public.control_experiments
         WHERE experiment_id = p_experiment_id FOR UPDATE;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.experiment_v2_work_events recovered
         WHERE recovered.experiment_id = p_experiment_id
           AND recovered.work_id = v_recovery.work_id
           AND recovered.event_kind = 'recovered') THEN
        IF v_exp.admission_state <> 'open' THEN
            v_exp := public.fn_experiment_v2_direct_open_randomized(
                p_experiment_id, p_actor);
        END IF;
        RETURN QUERY SELECT 'randomized_admission_open'::text,
            v_current.assignment_id, v_exp.status, v_exp.admission_state, v_now;
        RETURN;
    END IF;
    IF v_exp.admission_state = 'closed' THEN
        PERFORM public.fn_experiment_v2_set_admission(
            p_experiment_id, 'baseline_recovery', p_actor,
            'universal-day-boundary-interposition');
        SELECT * INTO v_exp FROM public.control_experiments
         WHERE experiment_id = p_experiment_id FOR UPDATE;
    END IF;
    RETURN QUERY SELECT 'baseline_recovery_pending'::text,
        v_current.assignment_id, v_exp.status, v_exp.admission_state, v_now;
END;
$body$;

ALTER FUNCTION public.fn_experiment_v2_direct_interposition_insert()
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_open_randomized(uuid,text)
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_launch_cycle(uuid,text)
    OWNER TO verdify_experiment_v2_owner;

REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_direct_interposition_insert()
    FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_direct_open_randomized(uuid,text)
    FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_direct_launch_cycle(uuid,text)
    FROM PUBLIC CASCADE;
DO $security$
DECLARE
    fn regprocedure;
BEGIN
    FOREACH fn IN ARRAY ARRAY[
        'public.fn_experiment_v2_direct_launch_cycle(uuid,text)'::regprocedure
    ] LOOP
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %s TO verdify_experiment_shadow_scheduler',
            fn);
    END LOOP;
END
$security$;
