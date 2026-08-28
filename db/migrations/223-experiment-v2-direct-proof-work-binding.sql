-- 223-experiment-v2-direct-proof-work-binding.sql
--
-- Migration 222 creates the exact attended direct-proof authorization before
-- inserting its aggressive commissioning work.  The older generic work
-- trigger still required an ordinary combined_physical approval, so that
-- insert was rejected and the complete begin transaction rolled back.  Keep
-- the ordinary gate intact and admit only the one immutable, currently valid,
-- exact-study proof tuple.

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_work_insert_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_state public.experiment_v2_state_artifacts%ROWTYPE;
    v_assignment public.control_assignments%ROWTYPE;
    v_choice public.experiment_v2_selector_choices%ROWTYPE;
    v_randomization public.experiment_v2_randomization%ROWTYPE;
    v_expected_profile text;
    v_direct_proof boolean := false;
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = NEW.experiment_id;
    SELECT * INTO v_state FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = NEW.experiment_id
       AND revision_bundle_sha256 = NEW.revision_bundle_sha256
       AND profile = NEW.target_profile;
    IF v_exp.protocol_version <> 2 OR v_state.state_artifact_id IS NULL OR
       NEW.execution_phase <> v_exp.execution_phase OR
       NEW.target_state_content_sha256 <> v_state.state_content_sha256 OR
       NEW.revision_bundle_sha256 <> v_exp.revision_bundle_sha256 OR
       NEW.firmware_revision <> v_exp.firmware_revision OR
       NEW.config_revision <> v_exp.config_revision OR
       NEW.registry_revision <> v_exp.registry_revision OR
       NEW.grid_revision <> v_exp.grid_revision OR
       NEW.lease_generation <> v_exp.lease_generation THEN
        RAISE EXCEPTION 'work identity/revision/phase must bind the current frozen v2 state';
    END IF;
    IF NEW.operation_kind IN ('shadow_preview', 'commissioning_probe',
                              'commissioning_canary', 'aa_baseline_rehearsal') AND
       v_exp.status <> 'draft' THEN
        RAISE EXCEPTION 'readiness work is draft-only and cannot reopen after design lock';
    END IF;
    IF NEW.operation_kind = 'shadow_preview' AND
       (v_exp.execution_phase <> 'shadow' OR v_exp.admission_state <> 'closed' OR
        v_exp.component_enabled OR NEW.target_profile = 'commissioning_probe') THEN
        RAISE EXCEPTION 'shadow preview is device-dark and cannot use the diagnostic probe state';
    END IF;
    IF NEW.operation_kind = 'commissioning_probe' AND (
       NEW.target_profile <> 'commissioning_probe' OR NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_approvals a
            WHERE a.experiment_id = NEW.experiment_id
              AND a.revision_bundle_sha256 = NEW.revision_bundle_sha256
              AND a.approval_kind = 'scoped_probe'
              AND NEW.valid_range <@ a.valid_range
              AND NEW.expires_at <= a.expires_at
              AND clock_timestamp() < a.expires_at)) THEN
        RAISE EXCEPTION 'diagnostic probe work must bind the single live scoped #641 approval';
    END IF;
    v_direct_proof :=
        NEW.operation_kind = 'commissioning_canary' AND
        NEW.target_profile = 'aggressive' AND
        NEW.assignment_id IS NULL AND NEW.parent_work_id IS NULL AND
        NEW.experiment_id = '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid AND
        v_exp.status = 'draft' AND v_exp.execution_phase = 'commissioning' AND
        v_exp.admission_state = 'baseline_recovery' AND v_exp.component_enabled AND
        EXISTS (
            SELECT 1
              FROM public.experiment_v2_direct_proof_authorizations authz
             WHERE authz.experiment_id = NEW.experiment_id
               AND authz.revision_bundle_sha256 = NEW.revision_bundle_sha256
               AND authz.issue_number = 641
               AND authz.proof_valid_range = NEW.valid_range
               AND NEW.expires_at = upper(authz.proof_valid_range)
               AND clock_timestamp() <@ authz.proof_valid_range
               AND authz.supervisor_role = 'Jason Vallery'
               AND authz.rescue_owner_role = 'Jason Vallery'
               AND authz.authorized_by = NEW.created_by);
    IF NEW.operation_kind = 'commissioning_canary' AND (
       NEW.target_profile NOT IN ('moderate', 'aggressive') OR
       (NOT v_direct_proof AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_approvals a
            WHERE a.experiment_id = NEW.experiment_id
              AND a.revision_bundle_sha256 = NEW.revision_bundle_sha256
              AND a.approval_kind = 'combined_physical'))) THEN
        RAISE EXCEPTION 'commissioning canary requires combined #641 approval or the exact active direct proof';
    END IF;
    IF NEW.operation_kind = 'aa_baseline_rehearsal' AND
       (NEW.target_profile <> 'baseline' OR NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_approvals a
            WHERE a.experiment_id = NEW.experiment_id
              AND a.revision_bundle_sha256 = NEW.revision_bundle_sha256
              AND a.approval_kind = 'combined_physical')) THEN
        RAISE EXCEPTION 'A/A readiness is baseline-only after combined #641 approval';
    END IF;
    IF NEW.operation_kind = 'randomized_assignment' THEN
        SELECT * INTO v_assignment FROM public.control_assignments
         WHERE experiment_id = NEW.experiment_id AND assignment_id = NEW.assignment_id
           AND operation_kind = 'randomized_day';
        SELECT * INTO v_choice FROM public.experiment_v2_selector_choices
         WHERE experiment_id = NEW.experiment_id AND assignment_id = NEW.assignment_id;
        SELECT * INTO v_randomization FROM public.experiment_v2_randomization
         WHERE experiment_id = NEW.experiment_id;
        IF v_exp.status NOT IN ('armed', 'running') OR
           v_assignment.assignment_id IS NULL OR v_choice.assignment_id IS NULL OR
           v_randomization.experiment_id IS NULL OR NEW.work_id <> NEW.assignment_id OR
           NEW.valid_range <> v_assignment.valid_range OR
           NEW.expires_at <> upper(v_assignment.valid_range) THEN
            RAISE EXCEPTION 'randomized work must be source-locked to one selector choice and assignment';
        END IF;
        v_expected_profile := CASE
            WHEN (CASE WHEN v_assignment.arm_label = 'X'
                       THEN v_randomization.x_physical_arm
                       ELSE v_randomization.y_physical_arm END) = 'A'
            THEN 'baseline' ELSE v_choice.selected_profile END;
        IF NEW.target_profile <> v_expected_profile THEN
            RAISE EXCEPTION 'randomized work target does not match hidden A/B plus daily choice';
        END IF;
    END IF;
    IF NEW.operation_kind = 'baseline_recovery' AND
       v_exp.status NOT IN ('draft', 'armed', 'running', 'paused') THEN
        RAISE EXCEPTION 'baseline recovery requires an active lifecycle';
    END IF;
    RETURN NEW;
END;
$body$;

ALTER FUNCTION public.fn_experiment_v2_work_insert_binding()
    OWNER TO verdify_experiment_v2_owner;
