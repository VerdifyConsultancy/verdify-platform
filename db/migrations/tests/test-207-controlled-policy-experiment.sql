\set ON_ERROR_STOP on

BEGIN;

-- Apply twice inside one disposable transaction: migration must be idempotent.
\ir ../207-controlled-policy-experiment.sql
\ir ../207-controlled-policy-experiment.sql

-- A schema-only disposable restore has no seed rows.  Keep the fixture valid
-- there without mutating a populated environment; the outer rollback removes
-- this row when it was absent at entry.
INSERT INTO public.greenhouses (id, name)
VALUES ('vallery', 'Migration 207 disposable fixture')
ON CONFLICT (id) DO NOTHING;

DO $$
DECLARE
    v_exp_id uuid;
    v_tpl_id uuid;
    v_asg_id uuid;
    v_asg2_id uuid;
    v_prop_id uuid;
    v_prop2_id uuid;
    v_vec_id uuid;
    v_vec2_id uuid;
    v_gen1 bigint;
    v_gen2 bigint;
    v_canon bytea := convert_to('test-207-baseline-template', 'UTF8');
    overlap_blocked boolean := false;
    ledger_update_blocked boolean := false;
    assignment_mutation_blocked boolean := false;
    incomplete_lock_blocked boolean := false;
    shadow_admit_blocked boolean := false;
    n integer;
BEGIN
    -- ── experiment fixture (kind=aa: needs only a complete baseline template)
    INSERT INTO public.control_experiments
        (greenhouse_id, kind, name, schema_revision, manifest_revision,
         compiler_revision, registry_revision)
    VALUES
        ('vallery', 'aa', 'test-207 aa fixture', 'schema-r1', 'manifest-r1',
         'compiler-r1', 'registry-r1')
    RETURNING experiment_id INTO v_exp_id;

    INSERT INTO public.policy_templates
        (experiment_id, kind, schema_revision, manifest_revision,
         compiler_revision, registry_revision, canonical_bytes, content_sha256)
    VALUES
        (v_exp_id, 'baseline', 'schema-r1', 'manifest-r1', 'compiler-r1',
         'registry-r1', v_canon, encode(public.digest(v_canon, 'sha256'), 'hex'))
    RETURNING template_id INTO v_tpl_id;

    -- Lock must FAIL while the template has fewer than 49 components.
    BEGIN
        PERFORM public.fn_experiment_transition(v_exp_id, 'locked', 'test-207');
    EXCEPTION WHEN OTHERS THEN
        incomplete_lock_blocked := true;
    END;
    IF NOT incomplete_lock_blocked THEN
        RAISE EXCEPTION 'lock gate accepted an incomplete (0/49 component) template';
    END IF;

    INSERT INTO public.policy_template_components
        (template_id, field_name, component_index, normalized_value)
    SELECT v_tpl_id, 'field_' || lpad(i::text, 2, '0'), i, i * 1.5
      FROM generate_series(0, 48) AS i;

    IF NOT public.fn_policy_template_is_complete(v_tpl_id) THEN
        RAISE EXCEPTION 'fn_policy_template_is_complete false for a complete template';
    END IF;

    -- ── lifecycle: draft -> locked -> armed -> running
    PERFORM public.fn_experiment_transition(v_exp_id, 'locked', 'test-207');
    PERFORM public.fn_experiment_transition(v_exp_id, 'armed', 'test-207');
    PERFORM public.fn_experiment_transition(v_exp_id, 'running', 'test-207');

    -- ── assignments: creation, ledger row, overlap rejection, immutability
    v_asg_id := public.fn_create_assignment(
        p_experiment_id  => v_exp_id,
        p_greenhouse_id  => 'vallery',
        p_arm_label      => 'baseline',
        p_operation_kind => 'aa_lane',
        p_valid_range    => tstzrange('2030-01-01T00:00:00Z', '2030-01-02T00:00:00Z', '[)'));

    SELECT count(*) INTO n FROM public.control_transition_ledger
     WHERE assignment_id = v_asg_id AND event_kind = 'aa_lane';
    IF n <> 1 THEN
        RAISE EXCEPTION 'expected exactly one ledger row for assignment, found %', n;
    END IF;

    BEGIN
        PERFORM public.fn_create_assignment(
            p_experiment_id  => v_exp_id,
            p_greenhouse_id  => 'vallery',
            p_arm_label      => 'baseline',
            p_operation_kind => 'aa_lane',
            p_valid_range    => tstzrange('2030-01-01T12:00:00Z', '2030-01-03T00:00:00Z', '[)'));
    EXCEPTION WHEN OTHERS THEN
        overlap_blocked := true;
    END;
    IF NOT overlap_blocked THEN
        RAISE EXCEPTION 'overlapping assignment range was NOT rejected';
    END IF;

    v_asg2_id := public.fn_create_assignment(
        p_experiment_id  => v_exp_id,
        p_greenhouse_id  => 'vallery',
        p_arm_label      => 'baseline',
        p_operation_kind => 'aa_lane',
        p_valid_range    => tstzrange('2030-01-02T00:00:00Z', '2030-01-03T00:00:00Z', '[)'));

    BEGIN
        UPDATE public.control_assignments
           SET arm_label = 'tampered'
         WHERE assignment_id = v_asg_id;
    EXCEPTION WHEN OTHERS THEN
        assignment_mutation_blocked := true;
    END;
    IF NOT assignment_mutation_blocked THEN
        RAISE EXCEPTION 'locked assignment accepted an arm_label mutation';
    END IF;

    BEGIN
        UPDATE public.control_transition_ledger SET detail = '{}'::jsonb;
    EXCEPTION WHEN OTHERS THEN
        ledger_update_blocked := true;
    END;
    IF NOT ledger_update_blocked THEN
        RAISE EXCEPTION 'append-only transition ledger accepted an UPDATE';
    END IF;

    -- ── proposals -> vectors: 49-component gate, monotonic generations, outbox
    INSERT INTO public.policy_proposals
        (experiment_id, assignment_id, producer, trigger_ref,
         validity, proposed_template_id)
    VALUES
        (v_exp_id, v_asg_id, 'baseline', 'test-207-trigger-1',
         tstzrange('2030-01-01T00:00:00Z', '2030-01-01T12:00:00Z', '[)'), v_tpl_id)
    RETURNING proposal_id INTO v_prop_id;

    -- Incomplete proposal (no components) must be rejected.
    BEGIN
        PERFORM public.fn_admit_policy_vector(v_prop_id, 'esp32-test');
    EXCEPTION WHEN OTHERS THEN
        shadow_admit_blocked := true;   -- reused flag: incomplete-components gate
    END;
    IF NOT shadow_admit_blocked THEN
        RAISE EXCEPTION 'vector admitted without 49 components';
    END IF;
    shadow_admit_blocked := false;

    INSERT INTO public.policy_proposal_components
        (proposal_id, field_name, component_index, normalized_value, producer)
    SELECT v_prop_id, 'field_' || lpad(i::text, 2, '0'), i, i * 2.0, 'baseline'
      FROM generate_series(0, 48) AS i;

    v_vec_id := public.fn_admit_policy_vector(v_prop_id, 'esp32-test');
    SELECT device_generation INTO v_gen1
      FROM public.effective_policy_vectors WHERE vector_id = v_vec_id;

    SELECT count(*) INTO n FROM public.effective_policy_vector_components
     WHERE vector_id = v_vec_id;
    IF n <> 49 THEN
        RAISE EXCEPTION 'expected 49 vector components, found %', n;
    END IF;
    SELECT count(*) INTO n FROM public.policy_delivery_outbox
     WHERE vector_id = v_vec_id AND device_id = 'esp32-test' AND state = 'queued';
    IF n <> 1 THEN
        RAISE EXCEPTION 'expected exactly one queued outbox row, found %', n;
    END IF;

    -- Shadow proposals can NEVER create outbox rows.
    INSERT INTO public.policy_proposals
        (experiment_id, assignment_id, producer, trigger_ref, validity, state)
    VALUES
        (v_exp_id, v_asg2_id, 'forecast', 'test-207-shadow',
         tstzrange('2030-01-02T00:00:00Z', '2030-01-02T06:00:00Z', '[)'), 'shadow')
    RETURNING proposal_id INTO v_prop2_id;
    BEGIN
        PERFORM public.fn_admit_policy_vector(v_prop2_id, 'esp32-test');
    EXCEPTION WHEN OTHERS THEN
        shadow_admit_blocked := true;
    END;
    IF NOT shadow_admit_blocked THEN
        RAISE EXCEPTION 'shadow proposal was admitted to the outbox';
    END IF;

    -- Second admitted vector: strictly increasing generation.
    INSERT INTO public.policy_proposals
        (experiment_id, assignment_id, producer, trigger_ref, validity)
    VALUES
        (v_exp_id, v_asg2_id, 'baseline', 'test-207-trigger-2',
         tstzrange('2030-01-02T00:00:00Z', '2030-01-02T12:00:00Z', '[)'))
    RETURNING proposal_id INTO v_prop2_id;
    INSERT INTO public.policy_proposal_components
        (proposal_id, field_name, component_index, normalized_value, producer)
    SELECT v_prop2_id, 'field_' || lpad(i::text, 2, '0'), i, i * 3.0, 'baseline'
      FROM generate_series(0, 48) AS i;
    v_vec2_id := public.fn_admit_policy_vector(v_prop2_id, 'esp32-test');
    SELECT device_generation INTO v_gen2
      FROM public.effective_policy_vectors WHERE vector_id = v_vec2_id;
    IF v_gen2 <= v_gen1 THEN
        RAISE EXCEPTION 'device generation not monotonic: % then %', v_gen1, v_gen2;
    END IF;

    -- ── exposure identity gate: snapshot must echo the exact vector identity
    DECLARE
        v_snap_id bigint;
        v_expo_id uuid;
        wrong_echo_blocked boolean := false;
    BEGIN
        v_snap_id := public.fn_record_device_snapshot(
            'esp32-test', 'vallery', 'schema-r1',
            v_gen1 + 999, v_asg_id, 'deadbeef' || repeat('0', 56),
            'deadbeef' || repeat('0', 56), 'active', 'fw-test');
        BEGIN
            PERFORM public.fn_open_exposure(v_vec_id, 'esp32-test', v_snap_id);
        EXCEPTION WHEN OTHERS THEN
            wrong_echo_blocked := true;
        END;
        IF NOT wrong_echo_blocked THEN
            RAISE EXCEPTION 'exposure opened on a mismatched device echo';
        END IF;

        SELECT public.fn_record_device_snapshot(
            'esp32-test', 'vallery', 'schema-r1',
            v.device_generation, v.assignment_id, v.content_sha256,
            v.activation_sha256, 'active', 'fw-test')
          INTO v_snap_id
          FROM public.effective_policy_vectors v WHERE v.vector_id = v_vec_id;
        v_expo_id := public.fn_open_exposure(v_vec_id, 'esp32-test', v_snap_id);
        PERFORM public.fn_close_exposure(v_expo_id, 'boundary');
    END;

    RAISE NOTICE 'test-207 assertions complete (exp=%)', v_exp_id;
END;
$$;

ROLLBACK;

SELECT 'test-207-controlled-policy-experiment: PASS' AS result;
