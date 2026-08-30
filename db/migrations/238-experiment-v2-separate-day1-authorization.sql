-- 238-experiment-v2-separate-day1-authorization.sql
--
-- Keep the one internal, insert-once OS-CSPRNG finalization introduced by
-- migration 214, but restore the independent #642 authorization boundary.
-- Migration 224 finalized the design and manufactured randomized_day_1
-- approval in the same scheduler transaction.  That made the approval an
-- implementation side effect instead of a distinct audited operator command.
--
-- This forward correction deliberately leaves finalized blinded assignments
-- untouched.  The replacement public scheduler entrypoint can finalize once,
-- then returns a treatment-free awaiting state until the authenticated API has
-- recorded the separate approval.  Additional insert guards prevent a
-- selector context, selector choice, or randomized work row from bypassing the
-- gate even if a narrower internal function is invoked directly.

ALTER FUNCTION public.fn_experiment_v2_direct_launch_cycle(uuid,text)
    RENAME TO fn_experiment_v2_direct_launch_cycle_pre238;

REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_direct_launch_cycle_pre238(uuid,text)
    FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_direct_launch_cycle_pre238(uuid,text)
    FROM verdify_experiment_shadow_scheduler CASCADE;

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

    -- Finalization remains internal and exactly once.  The function accepts no
    -- secret, RNG, replacement, or redraw input; exact replay returns the
    -- existing insert-once receipt from migration 214.
    IF v_exp.status = 'locked' THEN
        PERFORM * FROM public.fn_experiment_v2_finalize_randomization(
            p_experiment_id, p_actor);
        SELECT * INTO v_exp FROM public.control_experiments
         WHERE experiment_id = p_experiment_id FOR UPDATE;
    END IF;

    IF v_exp.status = 'aborted' THEN
        RETURN QUERY SELECT 'randomization_aborted'::text, NULL::uuid,
            v_exp.status, v_exp.admission_state, v_now;
        RETURN;
    END IF;
    IF v_exp.status NOT IN ('armed', 'running') THEN
        RAISE EXCEPTION 'direct launch finalization did not produce one armed randomized design';
    END IF;

    -- This is the intentional pause.  Only a later authenticated API command
    -- may record the separate API-mediated day-1 approval.  Do not create a
    -- selector context, choice, work row, exposure, or admission transition.
    IF NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_approvals approval
         WHERE approval.experiment_id = p_experiment_id
           AND approval.revision_bundle_sha256 = v_exp.revision_bundle_sha256
           AND approval.approval_kind = 'randomized_day_1'
           AND approval.issue_number = 642
           AND approval.approved_by LIKE 'verdify-api:%') THEN
        RETURN QUERY SELECT 'awaiting_separate_day1_approval'::text, NULL::uuid,
            v_exp.status, v_exp.admission_state, v_now;
        RETURN;
    END IF;

    RETURN QUERY SELECT *
      FROM public.fn_experiment_v2_direct_launch_cycle_pre238(
          p_experiment_id, p_actor);
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_require_day1_approval()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $body$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM public.control_experiments experiment
          JOIN public.experiment_v2_approvals approval
            ON approval.experiment_id = experiment.experiment_id
           AND approval.revision_bundle_sha256 = experiment.revision_bundle_sha256
           AND approval.approval_kind = 'randomized_day_1'
           AND approval.issue_number = 642
           AND approval.approved_by LIKE 'verdify-api:%'
         WHERE experiment.experiment_id = NEW.experiment_id
           AND experiment.protocol_version = 2
           AND experiment.execution_phase = 'randomized'
           AND experiment.status IN ('armed', 'running')) THEN
        RAISE EXCEPTION 'randomized day work requires separate audited #642 approval';
    END IF;
    RETURN NEW;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_day1_approval_audit_binding()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $body$
BEGIN
    IF NEW.approval_kind = 'randomized_day_1' AND
       (NEW.issue_number <> 642 OR
        NEW.approved_by !~ '^verdify-api:[A-Za-z0-9][A-Za-z0-9._:/#@-]{0,95}$') THEN
        RAISE EXCEPTION '#642 day-1 approval requires a separate authenticated API audit reference';
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_selector_context_day1_approval
    ON public.experiment_v2_selector_contexts;
CREATE TRIGGER trg_experiment_v2_selector_context_day1_approval
    BEFORE INSERT ON public.experiment_v2_selector_contexts
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_require_day1_approval();

DROP TRIGGER IF EXISTS trg_experiment_v2_selector_choice_day1_approval
    ON public.experiment_v2_selector_choices;
CREATE TRIGGER trg_experiment_v2_selector_choice_day1_approval
    BEFORE INSERT ON public.experiment_v2_selector_choices
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_require_day1_approval();

DROP TRIGGER IF EXISTS trg_experiment_v2_randomized_work_day1_approval
    ON public.experiment_v2_work;
CREATE TRIGGER trg_experiment_v2_randomized_work_day1_approval
    BEFORE INSERT ON public.experiment_v2_work
    FOR EACH ROW
    WHEN (NEW.operation_kind = 'randomized_assignment')
    EXECUTE FUNCTION public.fn_experiment_v2_require_day1_approval();

DROP TRIGGER IF EXISTS trg_experiment_v2_day1_approval_audit_binding
    ON public.experiment_v2_approvals;
CREATE TRIGGER trg_experiment_v2_day1_approval_audit_binding
    BEFORE INSERT ON public.experiment_v2_approvals
    FOR EACH ROW EXECUTE FUNCTION
        public.fn_experiment_v2_day1_approval_audit_binding();

-- Blinded, treatment-free launch/kill/rollback observability.  The function
-- reports only the existence of finalization/approval artifacts, never the
-- schedule, mapping, secret, arm resolution, provider response, or efficacy.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_launch_gate_status()
RETURNS TABLE (
    experiment_id uuid,
    lifecycle_status text,
    execution_phase text,
    admission_state text,
    component_enabled boolean,
    design_locked boolean,
    randomization_finalized boolean,
    randomized_day_1_approved boolean,
    launch_gate text,
    open_exposure_count integer,
    kill_action text,
    rollback_action text,
    rollback_ready boolean,
    resolved_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_now timestamptz := clock_timestamp();
BEGIN
    RETURN QUERY
    SELECT e.experiment_id, e.status, e.execution_phase, e.admission_state,
           e.component_enabled,
           e.design_lock_sha256 IS NOT NULL,
           finalized.present,
           approved.present,
           CASE
               WHEN e.admission_state = 'emergency_hold' THEN 'emergency_hold'
               WHEN exposures.open_count > 0 THEN 'exposure_active'
               WHEN e.design_lock_sha256 IS NULL THEN 'awaiting_design_lock'
               WHEN NOT finalized.present THEN 'awaiting_internal_finalization'
               WHEN NOT approved.present THEN 'awaiting_separate_day1_approval'
               WHEN e.admission_state <> 'closed' THEN 'admission_not_closed'
               ELSE 'authorized_closed'
           END,
           exposures.open_count,
           CASE WHEN e.admission_state = 'emergency_hold'
                THEN 'already_held'
                ELSE 'set_admission:emergency_hold' END,
           CASE
               WHEN exposures.open_count > 0 THEN 'exposure_close_first'
               WHEN e.admission_state = 'emergency_hold' THEN 'facility_yielded'
               WHEN NOT baseline.present THEN 'facility_authorized_baseline_recovery'
               WHEN e.component_enabled THEN 'coarse_disable_after_baseline'
               ELSE 'dormant_baseline_confirmed'
           END,
           exposures.open_count = 0 AND baseline.present AND
               e.admission_state <> 'emergency_hold',
           v_now
      FROM public.control_experiments e
      LEFT JOIN LATERAL (
          SELECT EXISTS (
              SELECT 1 FROM public.experiment_v2_randomization randomized
               WHERE randomized.experiment_id = e.experiment_id
                 AND randomized.design_lock_sha256 = e.design_lock_sha256)
              AS present
      ) finalized ON true
      LEFT JOIN LATERAL (
          SELECT EXISTS (
              SELECT 1 FROM public.experiment_v2_approvals approval
               WHERE approval.experiment_id = e.experiment_id
                 AND approval.revision_bundle_sha256 = e.revision_bundle_sha256
                 AND approval.approval_kind = 'randomized_day_1'
                 AND approval.issue_number = 642
                 AND approval.approved_by LIKE 'verdify-api:%') AS present
      ) approved ON true
      LEFT JOIN LATERAL (
          SELECT count(*)::integer AS open_count
            FROM public.experiment_v2_exposures exposure
            LEFT JOIN public.experiment_v2_exposure_closures closure
              USING (exposure_id)
           WHERE exposure.experiment_id = e.experiment_id
             AND closure.exposure_id IS NULL
      ) exposures ON true
      LEFT JOIN LATERAL (
          SELECT EXISTS (
              SELECT 1
                FROM public.experiment_v2_work work
                JOIN public.experiment_v2_work_events recovered
                  USING (experiment_id, work_id)
                JOIN public.experiment_v2_state_artifacts state
                  ON state.experiment_id = work.experiment_id
                 AND state.profile = 'baseline'
                 AND state.state_content_sha256 = work.target_state_content_sha256
               WHERE work.experiment_id = e.experiment_id
                 AND work.operation_kind = 'baseline_recovery'
                 AND work.revision_bundle_sha256 = e.revision_bundle_sha256
                 AND work.lease_generation = e.lease_generation
                 AND recovered.event_kind = 'recovered'
                 AND (SELECT count(*)
                        FROM public.experiment_v2_observation_receipts receipt
                       WHERE receipt.experiment_id = work.experiment_id
                         AND receipt.work_id = work.work_id
                         AND receipt.policy_state_content_sha256 =
                             work.target_state_content_sha256) >= 2) AS present
      ) baseline ON true
     WHERE e.protocol_version = 2 AND e.kind = 'randomized';
END;
$body$;

ALTER FUNCTION public.fn_experiment_v2_direct_launch_cycle(uuid,text)
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_require_day1_approval()
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_day1_approval_audit_binding()
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_launch_gate_status()
    OWNER TO verdify_experiment_v2_owner;

REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_direct_launch_cycle(uuid,text)
    FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_launch_gate_status()
    FROM PUBLIC CASCADE;
GRANT EXECUTE ON FUNCTION
    public.fn_experiment_v2_direct_launch_cycle(uuid,text)
    TO verdify_experiment_shadow_scheduler;
GRANT EXECUTE ON FUNCTION
    public.fn_experiment_v2_launch_gate_status()
    TO verdify;

DO $audit$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.experiment_v2_approvals approval
         WHERE approval.approval_kind = 'randomized_day_1'
           AND (approval.issue_number <> 642 OR
                approval.approved_by !~
                    '^verdify-api:[A-Za-z0-9][A-Za-z0-9._:/#@-]{0,95}$')) THEN
        RAISE EXCEPTION 'existing randomized day-1 approval lacks a separate authenticated API audit reference';
    END IF;
END
$audit$;
