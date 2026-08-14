-- 208-policy-arbiter-lane-c.sql
--
-- Issue #584 (Lane C of epic #581): arbiter/delivery extensions on the
-- migration-207 experiment schema. Follows the ledger convention: 207 is
-- frozen as applied; every Lane-C extension point it marked (`-- LANE-C:`)
-- is fulfilled here in a NEW numbered migration instead of editing 207.
--
-- Contents:
--   1. climate_action_log device-confirmed vector identity columns
--      (nullable, no backfill, hypertable-safe plain ADD COLUMN).
--   2. fn_submit_policy_proposal() — the ONE insert path for proposal
--      producers (MCP set_plan/set_tunable demotion, policy_template_propose,
--      forecast engine, arbiter pre-staging).
--   3. fn_admit_policy_vector() extended (207's LANE-C marker): Lane A
--      canonical wire bytes + §8.9 activation hash supplied by the Python
--      arbiter; arm/template-kind allowlist; randomized-AI template-selection
--      rule; mutable-fields allowlist; identity_hold byte-identity.
--      The 207 four-argument signature is DROPPED so exactly one admission
--      path exists.
--   4. fn_close_exposure() coverage_fraction computation (207's LANE-C
--      marker), same signature (safe CREATE OR REPLACE).
--
-- NON-SELF-TRANSACTIONAL (no top-level BEGIN/COMMIT) — safe to
-- rollback-validate per scripts/check_migration_rollback_safety.py (#23).
--
-- IDEMPOTENT: ADD COLUMN IF NOT EXISTS, DROP FUNCTION IF EXISTS pinned to the
-- superseded signature, CREATE OR REPLACE FUNCTION. Safe to re-run.
--
-- ROLLBACK NOTES: additive columns + function replacements. Functional
-- rollback = re-apply the 207 function bodies and drop the three
-- climate_action_log columns; no rows in pre-existing tables are touched.

-- ============================================================================
-- 1. climate_action_log: device-confirmed vector identity (nullable, no
--    backfill). climate_action_log is a hypertable: plain nullable ADD COLUMN
--    with no DEFAULT is metadata-only and chunk-safe. The legacy heuristic
--    plan_id/trigger_id/planner_instance columns continue to be populated for
--    continuity; these three columns record the device-confirmed treatment
--    identity when VERDIFY_POLICY_VECTOR_MODE != off.
-- ============================================================================

ALTER TABLE public.climate_action_log
    ADD COLUMN IF NOT EXISTS policy_vector_id uuid;
ALTER TABLE public.climate_action_log
    ADD COLUMN IF NOT EXISTS policy_generation bigint;
ALTER TABLE public.climate_action_log
    ADD COLUMN IF NOT EXISTS policy_activation_sha256 text;

COMMENT ON COLUMN public.climate_action_log.policy_vector_id IS
    'Device-confirmed effective_policy_vectors.vector_id in force at this tick '
    '(#584 Lane C). NULL outside experiment mode and for all pre-208 rows (no backfill).';
COMMENT ON COLUMN public.climate_action_log.policy_generation IS
    'Device-confirmed policy generation at this tick (#584 Lane C). Nullable, no backfill.';
COMMENT ON COLUMN public.climate_action_log.policy_activation_sha256 IS
    'Device-confirmed activation hash at this tick (#584 Lane C). Nullable, no backfill.';

-- ============================================================================
-- 2. Proposal producer entry point
-- ============================================================================

CREATE OR REPLACE FUNCTION public.fn_submit_policy_proposal(
    p_producer      text,
    p_trigger_ref   text    DEFAULT NULL,
    p_proposed_template_id uuid DEFAULT NULL,
    p_components    jsonb   DEFAULT NULL,
    p_digest_sha256 text    DEFAULT NULL,
    p_context       jsonb   DEFAULT NULL,
    p_state         text    DEFAULT 'proposed',
    p_actor         text    DEFAULT current_user,
    p_experiment_id uuid    DEFAULT NULL,
    p_assignment_id uuid    DEFAULT NULL,
    p_validity      tstzrange DEFAULT NULL,
    p_prompt_sha256 text    DEFAULT NULL,
    p_model_id      text    DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_asg public.control_assignments%ROWTYPE;
    v_snapshot_id uuid;
    v_proposal_id uuid;
    v_component jsonb;
    v_field text;
    v_index integer;
BEGIN
    IF p_state NOT IN ('proposed', 'shadow') THEN
        RAISE EXCEPTION 'proposals may only be submitted as proposed or shadow (got %)', p_state;
    END IF;

    -- Resolve the live experiment when the caller is experiment-id-opaque
    -- (the blinded template-selection tool never learns the id it acts under).
    IF p_experiment_id IS NOT NULL THEN
        SELECT * INTO v_exp FROM public.control_experiments
         WHERE experiment_id = p_experiment_id;
    ELSE
        SELECT * INTO v_exp FROM public.control_experiments
         WHERE status IN ('armed', 'running')
         ORDER BY armed_at DESC NULLS LAST
         LIMIT 1;
    END IF;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no armed/running experiment to attach proposal to';
    END IF;
    IF v_exp.status NOT IN ('armed', 'running') THEN
        RAISE EXCEPTION 'experiment % is % — proposals require armed/running',
            v_exp.experiment_id, v_exp.status;
    END IF;

    IF p_assignment_id IS NOT NULL THEN
        SELECT * INTO v_asg FROM public.control_assignments
         WHERE assignment_id = p_assignment_id;
    ELSE
        SELECT * INTO v_asg FROM public.control_assignments
         WHERE experiment_id = v_exp.experiment_id
           AND status = 'active'
           AND now() <@ valid_range
         ORDER BY lower(valid_range) DESC
         LIMIT 1;
    END IF;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no active assignment covers now() for experiment %', v_exp.experiment_id;
    END IF;
    IF v_asg.experiment_id <> v_exp.experiment_id THEN
        RAISE EXCEPTION 'assignment % does not belong to experiment %',
            v_asg.assignment_id, v_exp.experiment_id;
    END IF;
    IF v_asg.status <> 'active' THEN
        RAISE EXCEPTION 'assignment % is % — proposals require an active assignment',
            v_asg.assignment_id, v_asg.status;
    END IF;

    IF v_exp.permitted_producers IS NOT NULL
       AND NOT (p_producer = ANY (v_exp.permitted_producers)) THEN
        RAISE EXCEPTION 'producer % is not permitted by experiment %', p_producer, v_exp.experiment_id;
    END IF;

    IF p_proposed_template_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.policy_templates t
         WHERE t.template_id = p_proposed_template_id
           AND t.experiment_id = v_exp.experiment_id
    ) THEN
        RAISE EXCEPTION 'template % is not part of experiment %',
            p_proposed_template_id, v_exp.experiment_id;
    END IF;

    IF p_context IS NOT NULL AND p_context <> 'null'::jsonb THEN
        INSERT INTO public.experiment_context_snapshots
            (experiment_id, assignment_id, trigger_ref, prompt_sha256, model_id,
             context_payload, virtual_selected_template_id)
        VALUES
            (v_exp.experiment_id, v_asg.assignment_id,
             COALESCE(p_trigger_ref, 'lane-c-proposal'), p_prompt_sha256,
             p_model_id, p_context, p_proposed_template_id)
        RETURNING snapshot_id INTO v_snapshot_id;
    END IF;

    INSERT INTO public.policy_proposals
        (experiment_id, assignment_id, producer, trigger_ref,
         context_snapshot_id, prompt_sha256, model_id, proposed_template_id,
         validity, digest_sha256, state, state_reason)
    VALUES
        (v_exp.experiment_id, v_asg.assignment_id, p_producer, p_trigger_ref,
         v_snapshot_id, p_prompt_sha256, p_model_id, p_proposed_template_id,
         p_validity, p_digest_sha256, p_state,
         CASE WHEN p_state = 'shadow' THEN 'submitted in shadow mode by ' || p_actor
              ELSE 'submitted by ' || p_actor END)
    RETURNING proposal_id INTO v_proposal_id;

    IF p_components IS NOT NULL AND jsonb_typeof(p_components) = 'array' THEN
        FOR v_component IN SELECT * FROM jsonb_array_elements(p_components) LOOP
            v_field := v_component->>'field_name';
            v_index := (v_component->>'component_index')::integer;
            IF v_field IS NULL OR v_index IS NULL OR v_index < 0 OR v_index > 48 THEN
                RAISE EXCEPTION 'proposal component % is malformed (field_name + component_index 0..48 required)',
                    v_component;
            END IF;
            INSERT INTO public.policy_proposal_components
                (proposal_id, field_name, component_index, normalized_value,
                 encoded_value, producer, clamped, clamp_reason)
            VALUES
                (v_proposal_id, v_field, v_index,
                 (v_component->>'normalized_value')::numeric,
                 CASE WHEN v_component ? 'encoded_value_hex'
                      THEN decode(v_component->>'encoded_value_hex', 'hex') END,
                 COALESCE(v_component->>'producer', p_producer),
                 COALESCE((v_component->>'clamped')::boolean, false),
                 v_component->>'clamp_reason');
        END LOOP;
    END IF;

    RETURN v_proposal_id;
END;
$$;

COMMENT ON FUNCTION public.fn_submit_policy_proposal(text, text, uuid, jsonb, text,
    jsonb, text, text, uuid, uuid, tstzrange, text, text) IS
    'Sole proposal-producer insert path (#584 Lane C): resolves the live experiment/'
    'assignment for experiment-id-opaque callers, enforces the producer allowlist, '
    'persists optional partial components + an append-only context snapshot. '
    'state=shadow rows can never be admitted (fn_admit_policy_vector requires proposed).';

-- ============================================================================
-- 3. Arbiter admission — Lane C extension of the 207 function. The 207
--    four-argument signature is dropped so admission has exactly one path.
-- ============================================================================

DROP FUNCTION IF EXISTS public.fn_admit_policy_vector(uuid, text, tstzrange, text);

CREATE OR REPLACE FUNCTION public.fn_admit_policy_vector(
    p_proposal_id uuid,
    p_device_id   text,
    p_validity    tstzrange DEFAULT NULL,
    p_actor       text DEFAULT current_user,
    p_canonical_bytes   bytea DEFAULT NULL,
    p_content_sha256    text  DEFAULT NULL,
    p_activation_sha256 text  DEFAULT NULL,
    p_expected_generation bigint DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_prop public.policy_proposals%ROWTYPE;
    v_asg  public.control_assignments%ROWTYPE;
    v_exp  public.control_experiments%ROWTYPE;
    v_tpl  public.policy_templates%ROWTYPE;
    v_arm  public.control_experiment_arms%ROWTYPE;
    v_validity tstzrange;
    v_count integer;
    v_gen bigint;
    v_canon text;
    v_bytes bytea;
    v_content_sha text;
    v_activation_sha text;
    v_prev_content_sha text;
    v_vector_id uuid;
    v_disallowed text[];
BEGIN
    SELECT * INTO v_prop
      FROM public.policy_proposals
     WHERE proposal_id = p_proposal_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown proposal %', p_proposal_id;
    END IF;
    -- Shadow proposals can NEVER create outbox rows; rejected/expired/admitted
    -- proposals are not re-admittable.
    IF v_prop.state <> 'proposed' THEN
        RAISE EXCEPTION 'proposal % is % — only state=proposed is admittable',
            p_proposal_id, v_prop.state;
    END IF;

    SELECT * INTO v_asg
      FROM public.control_assignments
     WHERE assignment_id = v_prop.assignment_id;
    SELECT * INTO v_exp
      FROM public.control_experiments
     WHERE experiment_id = v_prop.experiment_id;

    IF v_exp.status NOT IN ('armed', 'running') THEN
        RAISE EXCEPTION 'experiment % is % — admission requires armed/running',
            v_exp.experiment_id, v_exp.status;
    END IF;
    IF v_asg.status <> 'active' THEN
        RAISE EXCEPTION 'assignment % is % — admission requires an active assignment',
            v_asg.assignment_id, v_asg.status;
    END IF;

    -- Producer allowlist (declared protocol rule).
    IF v_exp.permitted_producers IS NOT NULL
       AND NOT (v_prop.producer = ANY (v_exp.permitted_producers)) THEN
        RAISE EXCEPTION 'producer % is not permitted by experiment %',
            v_prop.producer, v_exp.experiment_id;
    END IF;

    -- ---- Lane C arm/allowlist/template rules (207's LANE-C marker) ---------

    -- A proposed template must belong to this experiment, be complete, and be
    -- a kind the assignment's arm may activate.
    IF v_prop.proposed_template_id IS NOT NULL THEN
        SELECT * INTO v_tpl
          FROM public.policy_templates
         WHERE template_id = v_prop.proposed_template_id;
        IF NOT FOUND OR v_tpl.experiment_id <> v_exp.experiment_id THEN
            RAISE EXCEPTION 'template % is not part of experiment %',
                v_prop.proposed_template_id, v_exp.experiment_id;
        END IF;
        IF NOT public.fn_policy_template_is_complete(v_tpl.template_id) THEN
            RAISE EXCEPTION 'template % is incomplete — admission requires 49 hash-consistent components',
                v_tpl.template_id;
        END IF;
        SELECT * INTO v_arm
          FROM public.control_experiment_arms
         WHERE experiment_id = v_exp.experiment_id
           AND arm_label = v_asg.arm_label;
        IF FOUND AND v_arm.allowed_template_kinds IS NOT NULL
           AND NOT (v_tpl.kind = ANY (v_arm.allowed_template_kinds)) THEN
            RAISE EXCEPTION 'arm % may not activate template kind % (allowlist %)',
                v_asg.arm_label, v_tpl.kind, v_arm.allowed_template_kinds;
        END IF;
    END IF;

    -- Randomized studies: the AI producer's only actuation-eligible output is
    -- an opaque template selection (audit §8.8) — never free-form components.
    IF v_exp.kind = 'randomized' AND v_prop.producer = 'ai'
       AND v_prop.proposed_template_id IS NULL THEN
        RAISE EXCEPTION 'randomized experiment %: ai proposals must select a pre-qualified template',
            v_exp.experiment_id;
    END IF;

    -- AI may change only experiment.mutable_fields relative to the frozen
    -- baseline template.
    IF v_prop.producer = 'ai' AND v_exp.mutable_fields IS NOT NULL THEN
        SELECT array_agg(pc.field_name ORDER BY pc.field_name) INTO v_disallowed
          FROM public.policy_proposal_components pc
          JOIN public.policy_templates bt
            ON bt.experiment_id = v_exp.experiment_id AND bt.kind = 'baseline'
          LEFT JOIN public.policy_template_components bc
            ON bc.template_id = bt.template_id AND bc.field_name = pc.field_name
         WHERE pc.proposal_id = p_proposal_id
           AND pc.normalized_value IS DISTINCT FROM bc.normalized_value
           AND NOT (pc.field_name = ANY (v_exp.mutable_fields));
        IF v_disallowed IS NOT NULL THEN
            RAISE EXCEPTION 'ai proposal % mutates non-allowlisted field(s) % (mutable_fields %)',
                p_proposal_id, v_disallowed, v_exp.mutable_fields;
        END IF;
    END IF;

    -- Validity: assignment-contained, half-open, bounded.
    v_validity := COALESCE(p_validity, v_prop.validity);
    IF v_validity IS NULL THEN
        RAISE EXCEPTION 'proposal % has no validity range and none was supplied', p_proposal_id;
    END IF;
    IF isempty(v_validity) OR lower_inf(v_validity) OR upper_inf(v_validity)
       OR NOT lower_inc(v_validity) OR upper_inc(v_validity) THEN
        RAISE EXCEPTION 'vector validity % must be a bounded half-open [) range', v_validity;
    END IF;
    IF NOT v_validity <@ v_asg.valid_range THEN
        RAISE EXCEPTION 'vector validity % is not contained in assignment range % (validity cannot cross an assignment)',
            v_validity, v_asg.valid_range;
    END IF;

    -- 49-component completeness.
    SELECT count(*) INTO v_count
      FROM public.policy_proposal_components
     WHERE proposal_id = p_proposal_id;
    IF v_count <> 49 THEN
        RAISE EXCEPTION 'proposal % has % components — a vector cannot become ready without 49 unique registered components',
            p_proposal_id, v_count;
    END IF;
    SELECT count(DISTINCT component_index) INTO v_count
      FROM public.policy_proposal_components
     WHERE proposal_id = p_proposal_id;
    IF v_count <> 49 THEN
        RAISE EXCEPTION 'proposal % component indexes are not 49 unique values', p_proposal_id;
    END IF;

    -- Generation monotonicity: serialize per greenhouse, then strictly
    -- increment past the maximum ever issued. Generations are never reused
    -- (also backed by UNIQUE (greenhouse_id, device_generation)).
    PERFORM pg_advisory_xact_lock(hashtext('effective_policy_vectors-' || v_asg.greenhouse_id));
    SELECT COALESCE(max(device_generation), 0) + 1 INTO v_gen
      FROM public.effective_policy_vectors
     WHERE greenhouse_id = v_asg.greenhouse_id;

    -- The Python arbiter binds the §8.9 activation hash to the generation it
    -- computed against; a concurrent admission changing the next generation
    -- must fail loudly (generation_conflict), never silently mismatch.
    IF p_expected_generation IS NOT NULL AND p_expected_generation <> v_gen THEN
        RAISE EXCEPTION 'generation conflict: expected % but next generation is % for greenhouse %',
            p_expected_generation, v_gen, v_asg.greenhouse_id;
    END IF;

    -- Canonical bytes + hashes. Lane C path: the Python arbiter compiles the
    -- Lane A canonical wire bytes (verdify_schemas.policy_vector: quantize ->
    -- encode -> content_sha256 -> activation_sha256 with the assignment's
    -- §8.9 treatment octets) and passes all three. The legacy deterministic
    -- component serialization remains only as the fallback so 207-era callers
    -- keep the structural contract (content hash == sha256 of stored bytes;
    -- activation hash bound to assignment + generation).
    IF p_canonical_bytes IS NOT NULL THEN
        IF p_content_sha256 IS NULL OR p_activation_sha256 IS NULL THEN
            RAISE EXCEPTION 'canonical bytes require both content and activation hashes';
        END IF;
        IF p_content_sha256 <> encode(public.digest(p_canonical_bytes, 'sha256'), 'hex') THEN
            RAISE EXCEPTION 'content_sha256 does not match the supplied canonical bytes';
        END IF;
        v_bytes := p_canonical_bytes;
        v_content_sha := p_content_sha256;
        v_activation_sha := p_activation_sha256;
    ELSE
        SELECT COALESCE(string_agg(field_name || '=' || normalized_value::text, ';'
                                   ORDER BY component_index), '') INTO v_canon
          FROM public.policy_proposal_components
         WHERE proposal_id = p_proposal_id;
        v_bytes := convert_to(v_canon, 'UTF8');
        v_content_sha := encode(public.digest(v_bytes, 'sha256'), 'hex');
        v_activation_sha := encode(public.digest(
            v_bytes || convert_to('|' || v_asg.assignment_id::text || '|' || v_gen::text, 'UTF8'),
            'sha256'), 'hex');
    END IF;

    -- identity_rebind semantics: an identity_hold assignment may only re-admit
    -- byte-identical content under its new assignment/activation identity.
    IF v_asg.operation_kind = 'identity_hold' THEN
        SELECT content_sha256 INTO v_prev_content_sha
          FROM public.effective_policy_vectors
         WHERE greenhouse_id = v_asg.greenhouse_id
         ORDER BY device_generation DESC
         LIMIT 1;
        IF v_prev_content_sha IS NOT NULL AND v_prev_content_sha <> v_content_sha THEN
            RAISE EXCEPTION 'identity_hold assignment % requires byte-identical content (prior %, proposed %)',
                v_asg.assignment_id, v_prev_content_sha, v_content_sha;
        END IF;
    END IF;

    INSERT INTO public.effective_policy_vectors
        (experiment_id, assignment_id, greenhouse_id, source_proposal_id,
         template_id, device_generation, validity, canonical_bytes,
         content_sha256, activation_sha256, status, created_by)
    VALUES
        (v_exp.experiment_id, v_asg.assignment_id, v_asg.greenhouse_id,
         p_proposal_id, v_prop.proposed_template_id, v_gen, v_validity,
         v_bytes, v_content_sha, v_activation_sha, 'ready', p_actor)
    RETURNING vector_id INTO v_vector_id;

    INSERT INTO public.effective_policy_vector_components
        (vector_id, field_name, component_index, normalized_value,
         encoded_value, producer, source_proposal_id, clamped, clamp_reason)
    SELECT v_vector_id, field_name, component_index, normalized_value,
           encoded_value, COALESCE(producer, v_prop.producer), p_proposal_id,
           clamped, clamp_reason
      FROM public.policy_proposal_components
     WHERE proposal_id = p_proposal_id;

    -- Outbox intent in the SAME transaction (idempotency key device+vector).
    INSERT INTO public.policy_delivery_outbox (device_id, vector_id, next_attempt_at)
    VALUES (p_device_id, v_vector_id, now());

    UPDATE public.policy_proposals
       SET state = 'admitted', state_reason = 'admitted as vector ' || v_vector_id,
           updated_at = now()
     WHERE proposal_id = p_proposal_id;

    RETURN v_vector_id;
END;
$$;

COMMENT ON FUNCTION public.fn_admit_policy_vector(uuid, text, tstzrange, text,
    bytea, text, text, bigint) IS
    'Arbiter admission (#584 Lane C, extends #583): proposal eligibility, producer '
    'allowlist, arm/template-kind allowlist, randomized ai template-selection rule, '
    'mutable-fields allowlist vs baseline, identity_hold byte-identity, 49-component '
    'completeness, assignment-contained validity, per-greenhouse generation '
    'monotonicity (advisory xact lock), Lane A canonical bytes + §8.9 activation '
    'hash, vector+components+outbox insert in one transaction.';

-- ============================================================================
-- 4. Exposure close: confirmed-coverage fraction (207's LANE-C marker).
--    Same signature — safe CREATE OR REPLACE.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.fn_close_exposure(
    p_exposure_id uuid,
    p_close_reason text,
    p_close_snapshot_id bigint DEFAULT NULL,
    p_ended_at timestamptz DEFAULT now(),
    p_actor text DEFAULT current_user
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_exposure public.policy_exposures%ROWTYPE;
    v_range tstzrange;
    v_assignment_s numeric;
    v_covered_s numeric;
    v_coverage numeric;
BEGIN
    SELECT * INTO v_exposure
      FROM public.policy_exposures
     WHERE exposure_id = p_exposure_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown exposure %', p_exposure_id;
    END IF;
    IF v_exposure.ended_at IS NOT NULL THEN
        RAISE EXCEPTION 'exposure % is already closed', p_exposure_id;
    END IF;
    IF p_ended_at < v_exposure.started_at THEN
        RAISE EXCEPTION 'exposure close time % precedes open time %',
            p_ended_at, v_exposure.started_at;
    END IF;

    -- Lane C: confirmed continuous coverage of the assignment interval —
    -- the confirmed [started_at, ended_at) overlap with the assignment's
    -- valid_range as a fraction of that range, clamped to [0, 1].
    SELECT valid_range INTO v_range
      FROM public.control_assignments
     WHERE assignment_id = v_exposure.assignment_id;
    IF v_range IS NOT NULL THEN
        v_assignment_s := extract(epoch FROM (upper(v_range) - lower(v_range)));
        v_covered_s := extract(epoch FROM (
            least(p_ended_at, upper(v_range))
            - greatest(v_exposure.started_at, lower(v_range))));
        IF v_assignment_s > 0 THEN
            v_coverage := greatest(0, least(1, v_covered_s / v_assignment_s));
        END IF;
    END IF;

    UPDATE public.policy_exposures
       SET ended_at = p_ended_at,
           close_reason = p_close_reason,
           close_snapshot_id = p_close_snapshot_id,
           coverage_fraction = v_coverage,
           updated_at = now()
     WHERE exposure_id = p_exposure_id;
END;
$$;

-- EXECUTE is granted per-role by the role-split migration
-- (db/roles/experiment-roles.sql); until then, deny PUBLIC.
REVOKE ALL ON FUNCTION public.fn_submit_policy_proposal(text, text, uuid, jsonb, text,
    jsonb, text, text, uuid, uuid, tstzrange, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_admit_policy_vector(uuid, text, tstzrange, text,
    bytea, text, text, bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_close_exposure(uuid, text, bigint, timestamptz, text) FROM PUBLIC;
