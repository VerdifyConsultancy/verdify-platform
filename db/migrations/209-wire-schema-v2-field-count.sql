-- 209-wire-schema-v2-field-count.sql
--
-- Contract v2 (#582/#586/#588, epic #581): wire schema v2 retires
-- direct_wet_stress_latest_hour (wire_id 6 — permanently reserved in
-- verdify_schemas.tunable_registry.RETIRED_WIRE_IDS), shrinking the policy
-- vector from 49 to 48 components. Migrations 207/208 store components
-- generically (no field-count DDL dependency — component_index CHECK 0..48 is
-- an upper bound that still admits 0..47), but four functions hard-coded the
-- count 49. This migration parameterizes them against ONE authoritative
-- place:
--
--   1. policy_wire_schema — schema_version -> field_count registry, seeded
--      idempotently with v1=49 and v2=48. A Python drift test
--      (tests/test_experiment_schema_migration.py) pins the seeded rows to
--      verdify_schemas.tunable_registry WIRE_SCHEMA_VERSION /
--      POLICY_WIRE_FIELD_COUNT.
--   2. fn_policy_wire_field_count() — the current (max schema_version) count.
--   3. fn_policy_template_is_complete / fn_submit_policy_proposal /
--      fn_admit_policy_vector re-created to read the count instead of 49.
--   4. fn_open_exposure re-created for the contract-v2 device echo (#586):
--      the aggregated policy_identity sensor echoes schema/generation/
--      assignment/activation with the FULL activation hash and NO separate
--      content hash — content identity is bound inside activation_sha256
--      (audit §8.9) — so the content comparison applies only when a snapshot
--      actually echoes one (v1 snapshots keep their stricter check).
--
-- NON-SELF-TRANSACTIONAL (no top-level BEGIN/COMMIT) — safe to
-- rollback-validate per scripts/check_migration_rollback_safety.py (#23).
--
-- IDEMPOTENT: CREATE TABLE IF NOT EXISTS, INSERT ... ON CONFLICT DO NOTHING,
-- CREATE OR REPLACE FUNCTION. Safe to re-run.
--
-- ROLLBACK NOTES: function replacements + one additive registry table.
-- Functional rollback = re-apply the 207/208 function bodies and drop
-- policy_wire_schema; no rows in pre-existing tables are touched.

-- ============================================================================
-- 1. Wire-schema registry — the single authoritative field count
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.policy_wire_schema (
    schema_version smallint PRIMARY KEY CHECK (schema_version > 0),
    field_count    integer NOT NULL CHECK (field_count > 0)
);

COMMENT ON TABLE public.policy_wire_schema IS
    'Policy wire schema_version -> component field_count (#588 contract v2). '
    'Mirror of verdify_schemas.tunable_registry WIRE_SCHEMA_VERSION / '
    'POLICY_WIRE_FIELD_COUNT; a source-contract test pins the rows. Retired '
    'wire ids are never reused, so a version''s count is immutable once seeded.';

INSERT INTO public.policy_wire_schema (schema_version, field_count)
VALUES (1, 49), (2, 48)
ON CONFLICT (schema_version) DO NOTHING;

CREATE OR REPLACE FUNCTION public.fn_policy_wire_field_count()
RETURNS integer
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT field_count
      FROM public.policy_wire_schema
     ORDER BY schema_version DESC
     LIMIT 1;
$$;

COMMENT ON FUNCTION public.fn_policy_wire_field_count() IS
    'Component count of the CURRENT (highest seeded) policy wire schema '
    'version (#588 contract v2: 48). The single authoritative in-database '
    'field count — completeness/admission checks must read this, never a '
    'literal.';

-- ============================================================================
-- 2. Template completeness — parameterized count (replaces 207's function)
-- ============================================================================

CREATE OR REPLACE FUNCTION public.fn_policy_template_is_complete(p_template_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT EXISTS (
        SELECT 1
          FROM public.policy_templates t
         WHERE t.template_id = p_template_id
           AND t.content_sha256 = encode(public.digest(t.canonical_bytes, 'sha256'), 'hex')
    )
    AND (
        SELECT count(*) = public.fn_policy_wire_field_count()
               AND count(DISTINCT component_index) = public.fn_policy_wire_field_count()
               AND min(component_index) = 0
               AND max(component_index) = public.fn_policy_wire_field_count() - 1
          FROM public.policy_template_components
         WHERE template_id = p_template_id
    );
$$;

COMMENT ON FUNCTION public.fn_policy_template_is_complete(uuid) IS
    'True iff the template has exactly fn_policy_wire_field_count() unique '
    'components covering indexes 0..count-1 and its content hash matches its '
    'canonical bytes (#583; count parameterized by #588 contract v2).';

-- ============================================================================
-- 3. Proposal submission — component_index bound parameterized
--    (replaces 208's function; body otherwise identical)
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
    v_field_count integer := public.fn_policy_wire_field_count();
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
            IF v_field IS NULL OR v_index IS NULL OR v_index < 0 OR v_index >= v_field_count THEN
                RAISE EXCEPTION 'proposal component % is malformed (field_name + component_index 0..% required)',
                    v_component, v_field_count - 1;
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
    'Sole proposal-producer insert path (#584 Lane C; component-index bound '
    'parameterized by #588 contract v2): resolves the live experiment/'
    'assignment for experiment-id-opaque callers, enforces the producer allowlist, '
    'persists optional partial components + an append-only context snapshot. '
    'state=shadow rows can never be admitted (fn_admit_policy_vector requires proposed).';

-- ============================================================================
-- 4. Arbiter admission — parameterized completeness (replaces 208's function;
--    body otherwise identical)
-- ============================================================================

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
    v_field_count integer := public.fn_policy_wire_field_count();
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
            RAISE EXCEPTION 'template % is incomplete — admission requires % hash-consistent components',
                v_tpl.template_id, v_field_count;
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

    -- Component completeness against the authoritative wire-schema count.
    SELECT count(*) INTO v_count
      FROM public.policy_proposal_components
     WHERE proposal_id = p_proposal_id;
    IF v_count <> v_field_count THEN
        RAISE EXCEPTION 'proposal % has % components — a vector cannot become ready without % unique registered components',
            p_proposal_id, v_count, v_field_count;
    END IF;
    SELECT count(DISTINCT component_index) INTO v_count
      FROM public.policy_proposal_components
     WHERE proposal_id = p_proposal_id;
    IF v_count <> v_field_count THEN
        RAISE EXCEPTION 'proposal % component indexes are not % unique values', p_proposal_id, v_field_count;
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
    'Arbiter admission (#584 Lane C, extends #583; completeness parameterized by '
    '#588 contract v2): proposal eligibility, producer allowlist, arm/template-kind '
    'allowlist, randomized ai template-selection rule, mutable-fields allowlist vs '
    'baseline, identity_hold byte-identity, fn_policy_wire_field_count()-component '
    'completeness, assignment-contained validity, per-greenhouse generation '
    'monotonicity (advisory xact lock), Lane A canonical bytes + §8.9 activation '
    'hash, vector+components+outbox insert in one transaction.';

-- ============================================================================
-- 5. Exposure open — contract-v2 device echo (#586): the aggregated
--    policy_identity sensor carries NO separate content hash (content identity
--    is bound inside activation_sha256, audit §8.9). Same signature — safe
--    CREATE OR REPLACE of 207's function.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.fn_open_exposure(
    p_vector_id   uuid,
    p_device_id   text,
    p_snapshot_id bigint,
    p_actor       text DEFAULT current_user
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_vec  public.effective_policy_vectors%ROWTYPE;
    v_snap public.policy_device_snapshots%ROWTYPE;
    v_exposure_id uuid;
BEGIN
    SELECT * INTO v_vec
      FROM public.effective_policy_vectors
     WHERE vector_id = p_vector_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown vector %', p_vector_id;
    END IF;

    SELECT * INTO v_snap
      FROM public.policy_device_snapshots
     WHERE snapshot_id = p_snapshot_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown device snapshot %', p_snapshot_id;
    END IF;

    -- Exposure opens ONLY after the device echoes the exact assignment,
    -- generation, and activation hash. Contract v2 (#586): the device does
    -- not echo a separate content hash — content identity is bound inside
    -- activation_sha256 (§8.9) — so the content comparison applies only when
    -- a snapshot actually echoes one (v1 snapshots keep the stricter check).
    IF v_snap.device_id <> p_device_id
       OR v_snap.assignment_id IS DISTINCT FROM v_vec.assignment_id
       OR v_snap.device_generation IS DISTINCT FROM v_vec.device_generation
       OR v_snap.activation_sha256 IS DISTINCT FROM v_vec.activation_sha256
       OR (v_snap.content_sha256 IS NOT NULL
           AND v_snap.content_sha256 IS DISTINCT FROM v_vec.content_sha256) THEN
        RAISE EXCEPTION
            'device snapshot % does not echo vector % identity exactly — exposure not opened',
            p_snapshot_id, p_vector_id;
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.policy_exposures
         WHERE device_id = p_device_id AND ended_at IS NULL
    ) THEN
        RAISE EXCEPTION 'device % already has an open exposure — close it first', p_device_id;
    END IF;

    INSERT INTO public.policy_exposures
        (experiment_id, assignment_id, device_id, vector_id, started_at,
         expected_generation, expected_content_sha256, expected_activation_sha256,
         observed_generation, observed_content_sha256, observed_activation_sha256,
         open_snapshot_id, identity_confirmed)
    VALUES
        (v_vec.experiment_id, v_vec.assignment_id, p_device_id, p_vector_id,
         v_snap.reported_at,
         v_vec.device_generation, v_vec.content_sha256, v_vec.activation_sha256,
         v_snap.device_generation, v_snap.content_sha256, v_snap.activation_sha256,
         p_snapshot_id, true)
    RETURNING exposure_id INTO v_exposure_id;

    UPDATE public.effective_policy_vectors
       SET status = 'active', updated_at = now()
     WHERE vector_id = p_vector_id AND status IN ('ready', 'delivering');

    RETURN v_exposure_id;
END;
$$;

COMMENT ON FUNCTION public.fn_open_exposure(uuid, text, bigint, text) IS
    'Opens one confirmed exposure interval (#583) after an EXACT device echo of '
    'assignment/generation/activation (contract v2, #586: content identity is '
    'bound inside the activation hash; a device-echoed content hash, when '
    'present, is still compared).';

-- EXECUTE stays denied to PUBLIC. CREATE OR REPLACE preserves ACLs on
-- already-created functions; first-apply on a fresh database still needs the
-- explicit denies for the functions this migration (re)creates.
REVOKE ALL ON FUNCTION public.fn_submit_policy_proposal(text, text, uuid, jsonb, text,
    jsonb, text, text, uuid, uuid, tstzrange, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_admit_policy_vector(uuid, text, tstzrange, text,
    bytea, text, text, bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_open_exposure(uuid, text, bigint, text) FROM PUBLIC;
