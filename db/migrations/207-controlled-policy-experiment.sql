-- 207-controlled-policy-experiment.sql
--
-- Issue #583 (Lane B of epic #581): controlled planner-experiment schema per
-- audit §8.7 (docs/research/planner-efficacy-current-firmware-2026-08-14.md).
-- Implements the evidence chain:
--
--   immutable assignment -> append-only proposals -> assignment-aware arbiter
--     -> atomic 49-field effective vector -> durable outbox -> staged device
--     commit -> exact generation/hash readback -> continuous exposure interval
--     -> blinded block outcome
--
-- NON-SELF-TRANSACTIONAL (no top-level BEGIN/COMMIT; only DO-block/function
-- BEGINs) — safe to rollback-validate by wrapping in an outer
-- BEGIN; ... ROLLBACK; per scripts/check_migration_rollback_safety.py (#23).
--
-- IDEMPOTENT: CREATE TABLE/INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION,
-- guarded DO blocks. Safe to re-run.
--
-- OVERLAP ENFORCEMENT (deliberate design): this database does NOT install
-- btree_gist (only timescaledb / pgcrypto / vector — db/schema.sql), so a
-- composite EXCLUDE USING gist (greenhouse_id WITH =, valid_range WITH &&)
-- constraint is not available. Assignment non-overlap is therefore enforced
-- exclusively by the SECURITY DEFINER transition functions below, which take a
-- per-greenhouse advisory transaction lock
-- (pg_advisory_xact_lock(hashtext('control_assignments-'||greenhouse_id))),
-- run the overlap query, insert, and write the ledger row atomically. Direct
-- table INSERTs bypassing fn_create_assignment are NOT overlap-safe; the role
-- split (db/roles/experiment-roles.sql) removes direct DML from application
-- roles so the function is the only path.
--
-- OWNERSHIP: the SECURITY DEFINER functions are intended to be owned by the
-- non-login verdify_migration_owner role (db/roles/experiment-roles.sql).
-- That role ships with the Lane C role-split migration; until then the
-- functions are owned by the migration-applying user. EXECUTE is revoked from
-- PUBLIC here; per-role GRANTs land with the role split.
--
-- ROLLBACK NOTES: purely additive (new control_/policy_/experiment_/
-- qualification_/planner_inference_ tables, functions, triggers). Functional
-- rollback = DROP the objects created here; no existing table is altered and
-- no rows in pre-existing tables are touched.

-- ============================================================================
-- 1. Experiments, arms, and the restricted blinded-arm resolution
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.control_experiments (
    experiment_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    greenhouse_id       text NOT NULL REFERENCES public.greenhouses(id),
    kind                text NOT NULL CHECK (kind IN ('qualification', 'aa', 'randomized')),
    status              text NOT NULL DEFAULT 'draft' CHECK (status IN (
                            'draft', 'locked', 'armed', 'running',
                            'paused', 'completed', 'aborted')),
    name                text NOT NULL,
    protocol_ref        text,
    protocol_sha256     text CHECK (protocol_sha256 IS NULL OR protocol_sha256 ~ '^[0-9a-f]{64}$'),
    timezone            text NOT NULL DEFAULT 'America/Denver',
    -- Frozen revision set every treatment action must match while armed/running.
    schema_revision     text,
    manifest_revision   text,
    compiler_revision   text,
    registry_revision   text,
    -- Randomized-only commitment material (never the mapping secret itself).
    beacon_identity     text,
    beacon_hash         text CHECK (beacon_hash IS NULL OR beacon_hash ~ '^[0-9a-f]{64}$'),
    mapping_commitment_sha256 text CHECK (mapping_commitment_sha256 IS NULL
                                          OR mapping_commitment_sha256 ~ '^[0-9a-f]{64}$'),
    schedule_sha256     text CHECK (schedule_sha256 IS NULL OR schedule_sha256 ~ '^[0-9a-f]{64}$'),
    -- Template commitments (rows in policy_templates are the resolution
    -- source once locked; these hashes are the cross-check, not the source).
    baseline_content_sha256   text CHECK (baseline_content_sha256 IS NULL
                                          OR baseline_content_sha256 ~ '^[0-9a-f]{64}$'),
    moderate_content_sha256   text CHECK (moderate_content_sha256 IS NULL
                                          OR moderate_content_sha256 ~ '^[0-9a-f]{64}$'),
    aggressive_content_sha256 text CHECK (aggressive_content_sha256 IS NULL
                                          OR aggressive_content_sha256 ~ '^[0-9a-f]{64}$'),
    transition_graph_sha256   text CHECK (transition_graph_sha256 IS NULL
                                          OR transition_graph_sha256 ~ '^[0-9a-f]{64}$'),
    -- Declared protocol allowlists (enforced by the arbiter functions).
    permitted_producers text[] NOT NULL DEFAULT ARRAY['ai', 'forecast', 'baseline', 'guardrail', 'operator'],
    mutable_fields      text[],                      -- 11-field AI allowlist for randomized
    locked_at           timestamptz,
    armed_at            timestamptz,
    started_at          timestamptz,
    ended_at            timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.control_experiments IS
    'Controlled planner experiments (#583, audit §8.7). kind=qualification|aa|randomized; '
    'lifecycle draft->locked->armed->running->paused->completed/aborted driven ONLY by '
    'fn_experiment_transition(). At most one armed/running/paused experiment per greenhouse '
    '(partial unique index).';

-- At most ONE live (armed/running/paused) experiment per greenhouse.
CREATE UNIQUE INDEX IF NOT EXISTS uq_control_experiments_one_live_per_greenhouse
    ON public.control_experiments (greenhouse_id)
    WHERE status IN ('armed', 'running', 'paused');

CREATE TABLE IF NOT EXISTS public.control_experiment_arms (
    arm_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id   uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    arm_label       text NOT NULL,
    -- Blinded arms (randomized X/Y) carry no physical resolution here; the
    -- restricted mapping lives ONLY in control_arm_resolutions.
    is_blinded      boolean NOT NULL DEFAULT false,
    description     text,
    -- Template kinds this arm may activate ('baseline' | 'moderate' | 'aggressive').
    allowed_template_kinds text[],
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, arm_label),
    CHECK (NOT is_blinded OR arm_label IN ('X', 'Y'))
);

COMMENT ON TABLE public.control_experiment_arms IS
    'Arms per experiment. Randomized studies register blinded labels X/Y only; the '
    'X/Y -> A/B physical resolution is quarantined in control_arm_resolutions.';

-- Restricted blinded->physical mapping. NO grants to ordinary roles: this
-- table stays reachable only through the assignment-service transition
-- functions until the completed-state unblind transition. Actual role GRANTs /
-- REVOKEs land with db/roles/experiment-roles.sql once the roles exist; the
-- role split explicitly denies the blinded analysis role any path to this
-- table (audit §8.7 role paragraph).
CREATE TABLE IF NOT EXISTS public.control_arm_resolutions (
    experiment_id   uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    blinded_label   text NOT NULL CHECK (blinded_label IN ('X', 'Y')),
    physical_arm    text NOT NULL CHECK (physical_arm IN ('A', 'B')),
    resolved_at     timestamptz NOT NULL DEFAULT now(),
    resolution_source text NOT NULL,               -- e.g. 'unblind-ceremony:<ref>'
    PRIMARY KEY (experiment_id, blinded_label),
    UNIQUE (experiment_id, physical_arm)
);

COMMENT ON TABLE public.control_arm_resolutions IS
    'RESTRICTED: randomized X/Y -> A/B physical-arm resolution. No application-role '
    'grants; exposed only through SECURITY DEFINER assignment-service functions until '
    'the one-way unblind at experiment completion (#583).';

REVOKE ALL ON public.control_arm_resolutions FROM PUBLIC;

-- ============================================================================
-- 2. Policy templates: canonical bytes, 49 components, six-edge graph
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.policy_templates (
    template_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id       uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    kind                text NOT NULL CHECK (kind IN ('baseline', 'moderate', 'aggressive')),
    schema_revision     text NOT NULL,
    manifest_revision   text NOT NULL,
    compiler_revision   text NOT NULL,
    registry_revision   text NOT NULL,
    -- Exact ordered canonical wire bytes (Lane A codec output) and their hash.
    canonical_bytes     bytea NOT NULL,
    content_sha256      text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    approval_ref        text,
    approved_by         text,
    approved_at         timestamptz,
    qualification_spec_sha256   text CHECK (qualification_spec_sha256 IS NULL
                                            OR qualification_spec_sha256 ~ '^[0-9a-f]{64}$'),
    qualification_result_sha256 text CHECK (qualification_result_sha256 IS NULL
                                            OR qualification_result_sha256 ~ '^[0-9a-f]{64}$'),
    locked_at           timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, kind),
    UNIQUE (experiment_id, content_sha256),
    -- The stored canonical bytes and the content hash must always agree.
    CHECK (content_sha256 = encode(public.digest(canonical_bytes, 'sha256'), 'hex'))
);

COMMENT ON TABLE public.policy_templates IS
    'Executable treatment definitions (#583). The baseline/moderate/aggressive rows — '
    'canonical bytes + 49 components — are the SOLE resolution source once locked; a bare '
    'hash is never executable. identity_rebind is an assignment-bound operation, NOT a '
    'row here and NOT an edge in policy_template_edges.';

CREATE TABLE IF NOT EXISTS public.policy_template_components (
    template_id     uuid NOT NULL REFERENCES public.policy_templates(template_id),
    field_name      text NOT NULL,
    component_index integer NOT NULL CHECK (component_index BETWEEN 0 AND 48),
    normalized_value numeric NOT NULL,
    encoded_value   bytea,
    unit            text,
    PRIMARY KEY (template_id, field_name),
    UNIQUE (template_id, component_index)
);

COMMENT ON TABLE public.policy_template_components IS
    '49 unique encoded components per template. Exactly-49 completeness cannot be a '
    'row-count CHECK; it is enforced by fn_policy_template_is_complete() at experiment '
    'lock time and re-checked by fn_admit_policy_vector().';

-- The six-edge directed content-changing graph over
-- {baseline, moderate, aggressive}. identity_rebind (byte-identical content,
-- new assignment/activation/generation) is deliberately NOT representable
-- here: it is recorded as a distinct operation on assignments, never as a
-- qualified physical edge.
CREATE TABLE IF NOT EXISTS public.policy_template_edges (
    edge_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id       uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    from_template_id    uuid NOT NULL REFERENCES public.policy_templates(template_id),
    to_template_id      uuid NOT NULL REFERENCES public.policy_templates(template_id),
    -- Regime/eligibility requirements a qualification transition must satisfy.
    requirements        jsonb NOT NULL DEFAULT '{}'::jsonb,
    qualification_result_sha256 text CHECK (qualification_result_sha256 IS NULL
                                            OR qualification_result_sha256 ~ '^[0-9a-f]{64}$'),
    qualification_passed boolean,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, from_template_id, to_template_id),
    CHECK (from_template_id <> to_template_id)
);

-- ============================================================================
-- 3. Qualification slots (24 FIFO cells x 4 ordered slots = the fixed 96)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.qualification_transition_slots (
    slot_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id       uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    cell_index          integer NOT NULL CHECK (cell_index BETWEEN 0 AND 23),
    slot_ordinal        integer NOT NULL CHECK (slot_ordinal BETWEEN 1 AND 4),
    edge_id             uuid REFERENCES public.policy_template_edges(edge_id),
    -- Regime / eligibility / version requirements the claim must prove.
    -- Deliberately NO fabricated future timestamp: slots carry requirements,
    -- never a scheduled time (audit §8.7).
    regime_requirements jsonb NOT NULL DEFAULT '{}'::jsonb,
    eligibility_requirements jsonb NOT NULL DEFAULT '{}'::jsonb,
    required_schema_revision   text,
    required_manifest_revision text,
    status              text NOT NULL DEFAULT 'open' CHECK (status IN (
                            'open', 'claimed', 'completed', 'failed', 'skipped')),
    -- Persisted at claim time by fn_claim_qualification_slot().
    eligibility_snapshot jsonb,
    claimed_at          timestamptz,
    claimed_by          text,
    assignment_id       uuid,                       -- FK added after control_assignments below
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, cell_index, slot_ordinal)
);

COMMENT ON TABLE public.qualification_transition_slots IS
    'Locked qualification owns 24 FIFO cell queues x 4 ordered slots (the fixed 96). '
    'Claims only via fn_claim_qualification_slot(): FIFO within cell, eligibility '
    'snapshot persisted, exactly one immutable analyzed assignment materialized before '
    'actuation. No fabricated future timestamps.';

-- ============================================================================
-- 4. Assignments (immutable) + append-only transition ledger
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.control_assignments (
    assignment_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id   uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    greenhouse_id   text NOT NULL REFERENCES public.greenhouses(id),
    pair_index      integer,
    block_index     integer,
    -- Randomized: blinded 'X'/'Y' ONLY. A/A: fixed baseline lane. Qualification
    -- analyzed rows: the qualification member (template kind) under test.
    arm_label       text NOT NULL,
    operation_kind  text NOT NULL CHECK (operation_kind IN (
                        'analyzed', 'positioning', 'baseline_recovery',
                        'identity_hold', 'aa_lane', 'randomized_day')),
    -- Only analyzed qualification claims carry a slot; all other kinds are
    -- audited non-analysis moves with slot_id NULL + scheduler/reason link.
    slot_id         uuid REFERENCES public.qualification_transition_slots(slot_id),
    scheduler_ref   text,
    reason          text,
    algorithm       text,
    algorithm_version text,
    -- Exact half-open UTC validity [lower, upper).
    valid_range     tstzrange NOT NULL,
    frozen_strata   jsonb,
    status          text NOT NULL DEFAULT 'active' CHECK (status IN (
                        'active', 'closed', 'failed', 'superseded')),
    locked_at       timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL DEFAULT current_user,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (NOT isempty(valid_range)
           AND NOT lower_inf(valid_range) AND NOT upper_inf(valid_range)
           AND lower_inc(valid_range) AND NOT upper_inc(valid_range)),
    CHECK ((operation_kind = 'analyzed') = (slot_id IS NOT NULL)),
    CHECK (operation_kind = 'analyzed'
           OR operation_kind IN ('aa_lane', 'randomized_day')
           OR (scheduler_ref IS NOT NULL AND reason IS NOT NULL)),
    CHECK (operation_kind <> 'randomized_day' OR arm_label IN ('X', 'Y'))
);

COMMENT ON TABLE public.control_assignments IS
    'Immutable treatment assignments (#583). Non-overlap per greenhouse is enforced by '
    'fn_create_assignment() under pg_advisory_xact_lock(hashtext(''control_assignments-''||'
    'greenhouse_id)) — NOT by an EXCLUDE constraint, because btree_gist is not installed. '
    'Rows are locked (immutable) from creation; only status/updated_at may change, via '
    'the transition functions.';

-- Overlap-query support: plain GiST on the range (built-in opclass — no
-- btree_gist needed for a single range column) + btree on greenhouse.
CREATE INDEX IF NOT EXISTS idx_control_assignments_valid_range
    ON public.control_assignments USING gist (valid_range);
CREATE INDEX IF NOT EXISTS idx_control_assignments_greenhouse
    ON public.control_assignments (greenhouse_id, lower(valid_range));

-- Late FK: slots reference the assignment they materialized.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'qualification_transition_slots_assignment_id_fkey'
          AND conrelid = 'public.qualification_transition_slots'::regclass
    ) THEN
        ALTER TABLE public.qualification_transition_slots
            ADD CONSTRAINT qualification_transition_slots_assignment_id_fkey
            FOREIGN KEY (assignment_id) REFERENCES public.control_assignments(assignment_id);
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS public.control_transition_ledger (
    ledger_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_id   uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    greenhouse_id   text NOT NULL,
    assignment_id   uuid REFERENCES public.control_assignments(assignment_id),
    slot_id         uuid REFERENCES public.qualification_transition_slots(slot_id),
    event_kind      text NOT NULL CHECK (event_kind IN (
                        'analyzed', 'positioning', 'baseline_recovery',
                        'identity_hold', 'aa_lane', 'randomized_day',
                        'failed', 'skipped')),
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb,
    recorded_by     text NOT NULL DEFAULT current_user,
    recorded_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.control_transition_ledger IS
    'APPEND-ONLY: every analyzed, positioning, baseline-recovery, identity-hold, failed, '
    'and skipped move. Only a pre-eligible non-null slot claim (event_kind=analyzed) can '
    'enter the fixed 96. UPDATE/DELETE blocked by trigger.';

-- ============================================================================
-- 5. Frozen context snapshots (virtual planner state, both arms identically)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.experiment_context_snapshots (
    snapshot_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id   uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    assignment_id   uuid REFERENCES public.control_assignments(assignment_id),
    trigger_ref     text NOT NULL,
    prompt_sha256   text CHECK (prompt_sha256 IS NULL OR prompt_sha256 ~ '^[0-9a-f]{64}$'),
    model_id        text,
    tool_manifest_sha256    text CHECK (tool_manifest_sha256 IS NULL
                                        OR tool_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    lessons_corpus_sha256   text CHECK (lessons_corpus_sha256 IS NULL
                                        OR lessons_corpus_sha256 ~ '^[0-9a-f]{64}$'),
    retrieval_corpus_sha256 text CHECK (retrieval_corpus_sha256 IS NULL
                                        OR retrieval_corpus_sha256 ~ '^[0-9a-f]{64}$'),
    crop_topology_sha256    text CHECK (crop_topology_sha256 IS NULL
                                        OR crop_topology_sha256 ~ '^[0-9a-f]{64}$'),
    response_model_sha256   text CHECK (response_model_sha256 IS NULL
                                        OR response_model_sha256 ~ '^[0-9a-f]{64}$'),
    context_payload jsonb,
    -- Per-trigger virtual planner state: recorded identically in both arms and
    -- NEVER overwritten by physical admission state (append-only trigger).
    virtual_prior_template_id    uuid REFERENCES public.policy_templates(template_id),
    virtual_selected_template_id uuid REFERENCES public.policy_templates(template_id),
    created_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.experiment_context_snapshots IS
    'APPEND-ONLY immutable prompt/model/tool + corpus/topology manifests and per-trigger '
    'virtual prior/selected template, used identically in both arms (#583, §8.7).';

-- ============================================================================
-- 6. Proposals (append-only producer stream)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.policy_proposals (
    proposal_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id   uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    assignment_id   uuid NOT NULL REFERENCES public.control_assignments(assignment_id),
    producer        text NOT NULL CHECK (producer IN (
                        'ai', 'forecast', 'baseline', 'guardrail', 'operator')),
    trigger_ref     text,
    context_snapshot_id uuid REFERENCES public.experiment_context_snapshots(snapshot_id),
    prompt_sha256   text CHECK (prompt_sha256 IS NULL OR prompt_sha256 ~ '^[0-9a-f]{64}$'),
    model_id        text,
    compiler_revision text,
    forecast_ref    text,
    -- Hash of the base (pre-proposal) content this proposal was computed from.
    base_content_sha256 text CHECK (base_content_sha256 IS NULL
                                    OR base_content_sha256 ~ '^[0-9a-f]{64}$'),
    proposed_template_id uuid REFERENCES public.policy_templates(template_id),
    validity        tstzrange,
    digest_sha256   text CHECK (digest_sha256 IS NULL OR digest_sha256 ~ '^[0-9a-f]{64}$'),
    state           text NOT NULL DEFAULT 'proposed' CHECK (state IN (
                        'proposed', 'shadow', 'rejected', 'admitted', 'expired')),
    state_reason    text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.policy_proposals IS
    'Append-only AI/forecast/baseline/guardrail/operator proposals with full lineage. '
    'Rows are never deleted; only state/state_reason/updated_at may change (trigger-'
    'enforced). Shadow proposals can NEVER create outbox rows (fn_admit_policy_vector).';

CREATE TABLE IF NOT EXISTS public.policy_proposal_components (
    proposal_id     uuid NOT NULL REFERENCES public.policy_proposals(proposal_id),
    field_name      text NOT NULL,
    component_index integer NOT NULL CHECK (component_index BETWEEN 0 AND 48),
    normalized_value numeric NOT NULL,
    encoded_value   bytea,
    producer        text,
    clamped         boolean NOT NULL DEFAULT false,
    clamp_reason    text,
    PRIMARY KEY (proposal_id, field_name),
    UNIQUE (proposal_id, component_index)
);

-- ============================================================================
-- 7. Effective policy vectors (atomic 49-field treatment truth)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.effective_policy_vectors (
    vector_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id   uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    assignment_id   uuid NOT NULL REFERENCES public.control_assignments(assignment_id),
    greenhouse_id   text NOT NULL REFERENCES public.greenhouses(id),
    source_proposal_id uuid REFERENCES public.policy_proposals(proposal_id),
    template_id     uuid REFERENCES public.policy_templates(template_id),
    -- Monotonically increasing per greenhouse; assigned ONLY by
    -- fn_admit_policy_vector() under the per-greenhouse advisory lock.
    -- Generations are never reused (unique index below + function-enforced
    -- strict monotonicity).
    device_generation bigint NOT NULL CHECK (device_generation > 0),
    -- Validity is assignment-contained (function-enforced: validity <@ assignment.valid_range).
    validity        tstzrange NOT NULL,
    canonical_bytes bytea NOT NULL,
    content_sha256  text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    -- Assignment-bound activation hash: same canonical content under a new
    -- assignment (identity_rebind) yields a NEW activation hash.
    activation_sha256 text NOT NULL CHECK (activation_sha256 ~ '^[0-9a-f]{64}$'),
    status          text NOT NULL DEFAULT 'ready' CHECK (status IN (
                        'ready', 'delivering', 'active', 'superseded', 'aborted')),
    created_by      text NOT NULL DEFAULT current_user,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (NOT isempty(validity)
           AND NOT lower_inf(validity) AND NOT upper_inf(validity)
           AND lower_inc(validity) AND NOT upper_inc(validity)),
    CHECK (content_sha256 = encode(public.digest(canonical_bytes, 'sha256'), 'hex')),
    UNIQUE (greenhouse_id, device_generation),
    UNIQUE (activation_sha256)
);

COMMENT ON TABLE public.effective_policy_vectors IS
    'Atomic 49-field effective policy vectors (#583). Exactly-49 completeness, generation '
    'monotonicity, and assignment-contained validity are enforced by fn_admit_policy_vector() '
    'under pg_advisory_xact_lock(hashtext(''effective_policy_vectors-''||greenhouse_id)). '
    'Treatment truth lives here; setpoint_plan/setpoint_changes/setpoint_snapshot remain '
    'compatibility/event projections only.';

CREATE TABLE IF NOT EXISTS public.effective_policy_vector_components (
    vector_id       uuid NOT NULL REFERENCES public.effective_policy_vectors(vector_id),
    field_name      text NOT NULL,
    component_index integer NOT NULL CHECK (component_index BETWEEN 0 AND 48),
    normalized_value numeric NOT NULL,
    encoded_value   bytea,
    -- Per-component producer/clamp lineage.
    producer        text NOT NULL,
    source_proposal_id uuid REFERENCES public.policy_proposals(proposal_id),
    clamped         boolean NOT NULL DEFAULT false,
    clamp_reason    text,
    PRIMARY KEY (vector_id, field_name),
    UNIQUE (vector_id, component_index)
);

-- ============================================================================
-- 8. Delivery outbox + attempt lifecycle
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.policy_delivery_outbox (
    outbox_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id       text NOT NULL,
    vector_id       uuid NOT NULL REFERENCES public.effective_policy_vectors(vector_id),
    state           text NOT NULL DEFAULT 'queued' CHECK (state IN (
                        'queued', 'leased', 'staging', 'staged',
                        'activating', 'activated', 'failed', 'abandoned')),
    lease_owner     text,
    lease_expires_at timestamptz,
    attempt_count   integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz,
    last_error_class text CHECK (last_error_class IS NULL OR last_error_class IN (
                        'timeout', 'connection', 'device_busy', 'hash_mismatch',
                        'schema_mismatch', 'generation_conflict',
                        'validation_reject', 'internal')),
    staged_at       timestamptz,
    activated_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    -- Idempotency key: one outbox intent per (device, vector).
    UNIQUE (device_id, vector_id)
);

COMMENT ON TABLE public.policy_delivery_outbox IS
    'Durable delivery outbox (#583). Idempotency key (device_id, vector_id); lease '
    'columns for the Lane C policy_delivery worker; bounded error-class enum. Rows are '
    'created ONLY by fn_admit_policy_vector() in the same transaction as the vector.';

CREATE INDEX IF NOT EXISTS idx_policy_delivery_outbox_pending
    ON public.policy_delivery_outbox (next_attempt_at)
    WHERE state IN ('queued', 'failed');

CREATE TABLE IF NOT EXISTS public.policy_delivery_attempts (
    attempt_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    outbox_id       uuid NOT NULL REFERENCES public.policy_delivery_outbox(outbox_id),
    attempt_no      integer NOT NULL CHECK (attempt_no >= 1),
    stage           text NOT NULL CHECK (stage IN (
                        'begin', 'chunk', 'validate', 'commit', 'activate', 'abort')),
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    ok              boolean,
    error_class     text CHECK (error_class IS NULL OR error_class IN (
                        'timeout', 'connection', 'device_busy', 'hash_mismatch',
                        'schema_mismatch', 'generation_conflict',
                        'validation_reject', 'internal')),
    detail          jsonb,
    UNIQUE (outbox_id, attempt_no, stage)
);

-- ============================================================================
-- 9. Device snapshots (device-echoed identity readback)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.policy_device_snapshots (
    snapshot_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    device_id       text NOT NULL,
    greenhouse_id   text REFERENCES public.greenhouses(id),
    reported_at     timestamptz NOT NULL DEFAULT now(),
    schema_revision text,
    device_generation bigint,
    assignment_id   uuid REFERENCES public.control_assignments(assignment_id),
    content_sha256  text CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'),
    activation_sha256 text CHECK (activation_sha256 IS NULL OR activation_sha256 ~ '^[0-9a-f]{64}$'),
    validity        tstzrange,
    apply_state     text CHECK (apply_state IS NULL OR apply_state IN (
                        'staged', 'active', 'rom_baseline', 'recovery', 'unknown')),
    firmware_revision text
);

COMMENT ON TABLE public.policy_device_snapshots IS
    'APPEND-ONLY device-echoed schema/generation/assignment/hashes/validity/apply-state/'
    'firmware readback (#583). Written only via fn_record_device_snapshot().';

CREATE INDEX IF NOT EXISTS idx_policy_device_snapshots_device_time
    ON public.policy_device_snapshots (device_id, reported_at DESC);

-- ============================================================================
-- 10. Exposures (confirmed continuous intervals)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.policy_exposures (
    exposure_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id   uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    assignment_id   uuid NOT NULL REFERENCES public.control_assignments(assignment_id),
    device_id       text NOT NULL,
    vector_id       uuid NOT NULL REFERENCES public.effective_policy_vectors(vector_id),
    started_at      timestamptz NOT NULL,
    ended_at        timestamptz,
    -- Expected identity (from the admitted vector).
    expected_generation     bigint NOT NULL,
    expected_content_sha256 text NOT NULL CHECK (expected_content_sha256 ~ '^[0-9a-f]{64}$'),
    expected_activation_sha256 text NOT NULL CHECK (expected_activation_sha256 ~ '^[0-9a-f]{64}$'),
    -- Observed identity (device-echoed at open).
    observed_generation     bigint,
    observed_content_sha256 text,
    observed_activation_sha256 text,
    open_snapshot_id  bigint REFERENCES public.policy_device_snapshots(snapshot_id),
    close_snapshot_id bigint REFERENCES public.policy_device_snapshots(snapshot_id),
    identity_confirmed boolean NOT NULL DEFAULT false,
    coverage_fraction numeric CHECK (coverage_fraction IS NULL
                                     OR (coverage_fraction >= 0 AND coverage_fraction <= 1)),
    close_reason    text CHECK (close_reason IS NULL OR close_reason IN (
                        'boundary', 'superseded', 'fallback', 'device_lost',
                        'protocol_deviation', 'manual', 'experiment_end')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (ended_at IS NULL OR ended_at >= started_at),
    CHECK ((ended_at IS NULL) = (close_reason IS NULL))
);

COMMENT ON TABLE public.policy_exposures IS
    'Confirmed continuous exposure intervals (#583). Opened by fn_open_exposure() ONLY '
    'after the device echoes the exact assignment/schema/generation/activation hash; '
    'closed with an explicit bounded reason. A failed AI delivery or baseline fallback '
    'never relabels the ITT arm — it closes the exposure with reason=fallback.';

-- One open exposure per device.
CREATE UNIQUE INDEX IF NOT EXISTS uq_policy_exposures_one_open_per_device
    ON public.policy_exposures (device_id)
    WHERE ended_at IS NULL;

-- ============================================================================
-- 11. Experiment events (protocol deviations / overrides; append-only)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.experiment_events (
    event_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_id   uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    assignment_id   uuid REFERENCES public.control_assignments(assignment_id),
    event_kind      text NOT NULL CHECK (event_kind IN (
                        'state_transition', 'protocol_deviation', 'override',
                        'emergency_action', 'note')),
    severity        text NOT NULL DEFAULT 'info' CHECK (severity IN (
                        'info', 'warning', 'critical')),
    actor           text NOT NULL DEFAULT current_user,
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb,
    recorded_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.experiment_events IS
    'APPEND-ONLY protocol deviations, overrides, emergency actions, and state '
    'transitions (#583). UPDATE/DELETE blocked by trigger.';

-- ============================================================================
-- 12. Planner inference runs (cost/energy accounting, both arms)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.planner_inference_runs (
    run_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id   uuid REFERENCES public.control_experiments(experiment_id),
    assignment_id   uuid REFERENCES public.control_assignments(assignment_id),
    proposal_id     uuid REFERENCES public.policy_proposals(proposal_id),
    -- The same scheduled work is recorded in BOTH arms (virtual runs in the
    -- frozen arm), so cost/energy accounting is arm-symmetric.
    scheduled_in_arm text,
    provider        text NOT NULL,
    model_id        text NOT NULL,
    prompt_sha256   text CHECK (prompt_sha256 IS NULL OR prompt_sha256 ~ '^[0-9a-f]{64}$'),
    tool_manifest_sha256 text CHECK (tool_manifest_sha256 IS NULL
                                     OR tool_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    started_at      timestamptz NOT NULL,
    ended_at        timestamptz,
    retries         integer NOT NULL DEFAULT 0,
    input_tokens    bigint,
    output_tokens   bigint,
    cache_read_tokens  bigint,
    cache_write_tokens bigint,
    price_snapshot  jsonb,
    billed_cost_usd numeric,
    energy_wh       numeric,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ============================================================================
-- 13. Append-only / immutability triggers
-- ============================================================================

CREATE OR REPLACE FUNCTION public.fn_experiment_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % blocked (#583)', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'raise_exception';
END;
$$;

DROP TRIGGER IF EXISTS trg_control_transition_ledger_append_only
    ON public.control_transition_ledger;
CREATE TRIGGER trg_control_transition_ledger_append_only
    BEFORE UPDATE OR DELETE ON public.control_transition_ledger
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_append_only();

DROP TRIGGER IF EXISTS trg_experiment_events_append_only
    ON public.experiment_events;
CREATE TRIGGER trg_experiment_events_append_only
    BEFORE UPDATE OR DELETE ON public.experiment_events
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_append_only();

DROP TRIGGER IF EXISTS trg_experiment_context_snapshots_append_only
    ON public.experiment_context_snapshots;
CREATE TRIGGER trg_experiment_context_snapshots_append_only
    BEFORE UPDATE OR DELETE ON public.experiment_context_snapshots
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_append_only();

DROP TRIGGER IF EXISTS trg_policy_device_snapshots_append_only
    ON public.policy_device_snapshots;
CREATE TRIGGER trg_policy_device_snapshots_append_only
    BEFORE UPDATE OR DELETE ON public.policy_device_snapshots
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_append_only();

-- Assignments: immutable from creation (locked_at defaults to now()). Only
-- status and updated_at may change; DELETE is always blocked.
CREATE OR REPLACE FUNCTION public.fn_control_assignment_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'control_assignments rows are immutable: DELETE blocked (#583)';
    END IF;
    IF NEW.assignment_id  IS DISTINCT FROM OLD.assignment_id
       OR NEW.experiment_id IS DISTINCT FROM OLD.experiment_id
       OR NEW.greenhouse_id IS DISTINCT FROM OLD.greenhouse_id
       OR NEW.pair_index    IS DISTINCT FROM OLD.pair_index
       OR NEW.block_index   IS DISTINCT FROM OLD.block_index
       OR NEW.arm_label     IS DISTINCT FROM OLD.arm_label
       OR NEW.operation_kind IS DISTINCT FROM OLD.operation_kind
       OR NEW.slot_id       IS DISTINCT FROM OLD.slot_id
       OR NEW.scheduler_ref IS DISTINCT FROM OLD.scheduler_ref
       OR NEW.reason        IS DISTINCT FROM OLD.reason
       OR NEW.algorithm     IS DISTINCT FROM OLD.algorithm
       OR NEW.algorithm_version IS DISTINCT FROM OLD.algorithm_version
       OR NEW.valid_range   IS DISTINCT FROM OLD.valid_range
       OR NEW.frozen_strata IS DISTINCT FROM OLD.frozen_strata
       OR NEW.locked_at     IS DISTINCT FROM OLD.locked_at
       OR NEW.created_by    IS DISTINCT FROM OLD.created_by
       OR NEW.created_at    IS DISTINCT FROM OLD.created_at
    THEN
        RAISE EXCEPTION
            'control_assignments % is locked: only status/updated_at may change (#583)',
            OLD.assignment_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_control_assignments_immutable
    ON public.control_assignments;
CREATE TRIGGER trg_control_assignments_immutable
    BEFORE UPDATE OR DELETE ON public.control_assignments
    FOR EACH ROW EXECUTE FUNCTION public.fn_control_assignment_immutable();

-- Templates: fully immutable once locked_at is set, except the
-- post-qualification result hash and updated_at. Components/edges of a locked
-- template are frozen entirely.
CREATE OR REPLACE FUNCTION public.fn_policy_template_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.locked_at IS NOT NULL THEN
            RAISE EXCEPTION 'policy_templates % is locked: DELETE blocked (#583)', OLD.template_id;
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.locked_at IS NOT NULL THEN
        IF NEW.template_id  IS DISTINCT FROM OLD.template_id
           OR NEW.experiment_id IS DISTINCT FROM OLD.experiment_id
           OR NEW.kind      IS DISTINCT FROM OLD.kind
           OR NEW.schema_revision IS DISTINCT FROM OLD.schema_revision
           OR NEW.manifest_revision IS DISTINCT FROM OLD.manifest_revision
           OR NEW.compiler_revision IS DISTINCT FROM OLD.compiler_revision
           OR NEW.registry_revision IS DISTINCT FROM OLD.registry_revision
           OR NEW.canonical_bytes IS DISTINCT FROM OLD.canonical_bytes
           OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
           OR NEW.approval_ref IS DISTINCT FROM OLD.approval_ref
           OR NEW.approved_by IS DISTINCT FROM OLD.approved_by
           OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
           OR NEW.qualification_spec_sha256 IS DISTINCT FROM OLD.qualification_spec_sha256
           OR NEW.locked_at IS DISTINCT FROM OLD.locked_at
           OR NEW.created_at IS DISTINCT FROM OLD.created_at
        THEN
            RAISE EXCEPTION
                'policy_templates % is locked: only qualification_result_sha256/updated_at may change (#583)',
                OLD.template_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_policy_templates_immutable ON public.policy_templates;
CREATE TRIGGER trg_policy_templates_immutable
    BEFORE UPDATE OR DELETE ON public.policy_templates
    FOR EACH ROW EXECUTE FUNCTION public.fn_policy_template_immutable();

CREATE OR REPLACE FUNCTION public.fn_policy_template_child_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_template_id uuid;
    v_locked timestamptz;
BEGIN
    v_template_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.template_id ELSE NEW.template_id END;
    SELECT locked_at INTO v_locked
      FROM public.policy_templates WHERE template_id = v_template_id;
    IF v_locked IS NOT NULL THEN
        RAISE EXCEPTION
            'policy_template_components of locked template % are immutable: % blocked (#583)',
            v_template_id, TG_OP;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_policy_template_components_immutable
    ON public.policy_template_components;
CREATE TRIGGER trg_policy_template_components_immutable
    BEFORE INSERT OR UPDATE OR DELETE ON public.policy_template_components
    FOR EACH ROW EXECUTE FUNCTION public.fn_policy_template_child_immutable();

-- Edges freeze with the experiment lock (edges belong to the experiment's
-- six-edge graph, not to a single template). Post-lock, only the
-- qualification result columns may change.
CREATE OR REPLACE FUNCTION public.fn_policy_template_edge_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_experiment_id uuid;
    v_locked timestamptz;
BEGIN
    v_experiment_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.experiment_id ELSE NEW.experiment_id END;
    SELECT locked_at INTO v_locked
      FROM public.control_experiments WHERE experiment_id = v_experiment_id;
    IF v_locked IS NULL THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP IN ('INSERT', 'DELETE') THEN
        RAISE EXCEPTION
            'policy_template_edges of locked experiment % are frozen: % blocked (#583)',
            v_experiment_id, TG_OP;
    END IF;
    IF NEW.edge_id IS DISTINCT FROM OLD.edge_id
       OR NEW.experiment_id IS DISTINCT FROM OLD.experiment_id
       OR NEW.from_template_id IS DISTINCT FROM OLD.from_template_id
       OR NEW.to_template_id IS DISTINCT FROM OLD.to_template_id
       OR NEW.requirements IS DISTINCT FROM OLD.requirements
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
    THEN
        RAISE EXCEPTION
            'policy_template_edges % of locked experiment: only qualification result columns may change (#583)',
            OLD.edge_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_policy_template_edges_guard ON public.policy_template_edges;
CREATE TRIGGER trg_policy_template_edges_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.policy_template_edges
    FOR EACH ROW EXECUTE FUNCTION public.fn_policy_template_edge_guard();

-- Proposals: append-only stream; only state/state_reason/updated_at may change.
CREATE OR REPLACE FUNCTION public.fn_policy_proposal_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'policy_proposals rows are append-only: DELETE blocked (#583)';
    END IF;
    IF NEW.proposal_id IS DISTINCT FROM OLD.proposal_id
       OR NEW.experiment_id IS DISTINCT FROM OLD.experiment_id
       OR NEW.assignment_id IS DISTINCT FROM OLD.assignment_id
       OR NEW.producer IS DISTINCT FROM OLD.producer
       OR NEW.trigger_ref IS DISTINCT FROM OLD.trigger_ref
       OR NEW.context_snapshot_id IS DISTINCT FROM OLD.context_snapshot_id
       OR NEW.prompt_sha256 IS DISTINCT FROM OLD.prompt_sha256
       OR NEW.model_id IS DISTINCT FROM OLD.model_id
       OR NEW.compiler_revision IS DISTINCT FROM OLD.compiler_revision
       OR NEW.forecast_ref IS DISTINCT FROM OLD.forecast_ref
       OR NEW.base_content_sha256 IS DISTINCT FROM OLD.base_content_sha256
       OR NEW.proposed_template_id IS DISTINCT FROM OLD.proposed_template_id
       OR NEW.validity IS DISTINCT FROM OLD.validity
       OR NEW.digest_sha256 IS DISTINCT FROM OLD.digest_sha256
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
    THEN
        RAISE EXCEPTION
            'policy_proposals % : only state/state_reason/updated_at may change (#583)',
            OLD.proposal_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_policy_proposals_guard ON public.policy_proposals;
CREATE TRIGGER trg_policy_proposals_guard
    BEFORE UPDATE OR DELETE ON public.policy_proposals
    FOR EACH ROW EXECUTE FUNCTION public.fn_policy_proposal_guard();

-- updated_at maintenance (reuses update_updated_at_column() from migration 023).
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'control_experiments', 'qualification_transition_slots',
        'control_assignments', 'policy_templates', 'policy_proposals',
        'effective_policy_vectors', 'policy_delivery_outbox', 'policy_exposures'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_updated_at ON public.%I', t, t);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_updated_at BEFORE UPDATE ON public.%I '
            'FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()', t, t);
    END LOOP;
END
$$;

-- ============================================================================
-- 14. Helper predicates
-- ============================================================================

-- Exactly 49 unique components (unique field_name via PK, unique index via
-- constraint) covering component_index 0..48 with the stored hash matching
-- the canonical bytes.
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
        SELECT count(*) = 49
               AND count(DISTINCT component_index) = 49
               AND min(component_index) = 0
               AND max(component_index) = 48
          FROM public.policy_template_components
         WHERE template_id = p_template_id
    );
$$;

COMMENT ON FUNCTION public.fn_policy_template_is_complete(uuid) IS
    'True iff the template has exactly 49 unique components covering indexes 0..48 and '
    'its content hash matches its canonical bytes (#583).';

-- ============================================================================
-- 15. SECURITY DEFINER transition functions
--
-- All functions: SECURITY DEFINER, search_path pinned to public,pg_temp.
-- Intended owner: verdify_migration_owner (non-login; db/roles/
-- experiment-roles.sql). EXECUTE revoked from PUBLIC below; per-role GRANTs
-- ship with the role-split migration once Lane C consumers exist.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.fn_experiment_transition(
    p_experiment_id uuid,
    p_target_status text,
    p_actor         text DEFAULT current_user,
    p_note          text DEFAULT NULL
) RETURNS public.control_experiments
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_ok  boolean := false;
    v_n   integer;
BEGIN
    SELECT * INTO v_exp
      FROM public.control_experiments
     WHERE experiment_id = p_experiment_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown experiment %', p_experiment_id;
    END IF;

    -- State machine: draft->locked->armed->running<->paused->completed/aborted.
    v_ok := (v_exp.status, p_target_status) IN (
        ('draft', 'locked'),
        ('locked', 'draft'),      -- unlock is allowed ONLY before arming
        ('locked', 'armed'),
        ('locked', 'aborted'),
        ('armed', 'running'),
        ('armed', 'aborted'),
        ('running', 'paused'),
        ('paused', 'running'),
        ('running', 'completed'),
        ('running', 'aborted'),
        ('paused', 'aborted')
    );
    IF NOT v_ok THEN
        RAISE EXCEPTION 'illegal experiment transition % -> % (experiment %)',
            v_exp.status, p_target_status, p_experiment_id;
    END IF;

    -- ---- Kind-specific structural gates ------------------------------------
    IF p_target_status = 'locked' THEN
        -- Every kind requires a complete, hash-consistent baseline template.
        IF NOT EXISTS (
            SELECT 1 FROM public.policy_templates t
             WHERE t.experiment_id = p_experiment_id AND t.kind = 'baseline'
               AND public.fn_policy_template_is_complete(t.template_id)
        ) THEN
            RAISE EXCEPTION 'lock gate: experiment % has no complete baseline template',
                p_experiment_id;
        END IF;

        IF v_exp.kind IN ('qualification', 'randomized') THEN
            -- Three templates (baseline/moderate/aggressive), all complete.
            SELECT count(*) INTO v_n
              FROM public.policy_templates t
             WHERE t.experiment_id = p_experiment_id
               AND public.fn_policy_template_is_complete(t.template_id);
            IF v_n <> 3 THEN
                RAISE EXCEPTION
                    'lock gate: experiment % (kind %) needs 3 complete templates, found %',
                    p_experiment_id, v_exp.kind, v_n;
            END IF;
            -- The six-edge directed graph must be fully declared.
            SELECT count(*) INTO v_n
              FROM public.policy_template_edges e
             WHERE e.experiment_id = p_experiment_id;
            IF v_n <> 6 THEN
                RAISE EXCEPTION
                    'lock gate: experiment % needs exactly 6 directed template edges, found %',
                    p_experiment_id, v_n;
            END IF;
        END IF;

        IF v_exp.kind = 'qualification' THEN
            -- The fixed 96: 24 cells x 4 ordered slots.
            SELECT count(*) INTO v_n
              FROM public.qualification_transition_slots s
             WHERE s.experiment_id = p_experiment_id;
            IF v_n <> 96 THEN
                RAISE EXCEPTION
                    'lock gate: qualification experiment % needs 96 slots (24 cells x 4), found %',
                    p_experiment_id, v_n;
            END IF;
        END IF;

        -- LANE-C: protocol-loader byte-agreement proof (stored bytes vs
        -- component rows vs schema manifest vs content sha) and the
        -- protocol/schedule hash cross-checks extend here.

        -- Lock the templates with the experiment.
        UPDATE public.policy_templates
           SET locked_at = now()
         WHERE experiment_id = p_experiment_id AND locked_at IS NULL;
    END IF;

    IF p_target_status = 'draft' THEN
        -- Unlock: only reachable from 'locked' (matrix above). Unfreeze templates.
        UPDATE public.policy_templates
           SET locked_at = NULL
         WHERE experiment_id = p_experiment_id;
    END IF;

    IF p_target_status = 'armed' THEN
        IF v_exp.kind = 'randomized' THEN
            IF v_exp.beacon_hash IS NULL
               OR v_exp.mapping_commitment_sha256 IS NULL
               OR v_exp.schedule_sha256 IS NULL THEN
                RAISE EXCEPTION
                    'arm gate: randomized experiment % requires beacon_hash, mapping_commitment_sha256 and schedule_sha256',
                    p_experiment_id;
            END IF;
            -- Blinded arms X and Y must be registered.
            SELECT count(*) INTO v_n
              FROM public.control_experiment_arms a
             WHERE a.experiment_id = p_experiment_id
               AND a.is_blinded AND a.arm_label IN ('X', 'Y');
            IF v_n <> 2 THEN
                RAISE EXCEPTION
                    'arm gate: randomized experiment % must register blinded arms X and Y',
                    p_experiment_id;
            END IF;
        END IF;
        IF v_exp.kind IN ('aa', 'randomized') THEN
            -- LANE-C: precommitted range verification (30 randomized / 7 A/A
            -- day ranges) against schedule_sha256 extends here.
            NULL;
        END IF;
        -- Frozen revision set must be declared before arming.
        IF v_exp.schema_revision IS NULL OR v_exp.manifest_revision IS NULL
           OR v_exp.compiler_revision IS NULL OR v_exp.registry_revision IS NULL THEN
            RAISE EXCEPTION
                'arm gate: experiment % must freeze schema/manifest/compiler/registry revisions',
                p_experiment_id;
        END IF;
    END IF;

    UPDATE public.control_experiments
       SET status     = p_target_status,
           locked_at  = CASE WHEN p_target_status = 'locked' THEN now()
                             WHEN p_target_status = 'draft' THEN NULL
                             ELSE locked_at END,
           armed_at   = CASE WHEN p_target_status = 'armed' THEN now() ELSE armed_at END,
           started_at = CASE WHEN p_target_status = 'running' AND started_at IS NULL
                             THEN now() ELSE started_at END,
           ended_at   = CASE WHEN p_target_status IN ('completed', 'aborted')
                             THEN now() ELSE ended_at END,
           updated_at = now()
     WHERE experiment_id = p_experiment_id
     RETURNING * INTO v_exp;

    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail)
    VALUES
        (p_experiment_id, 'state_transition', 'info', p_actor,
         jsonb_build_object('to', p_target_status, 'note', p_note));

    RETURN v_exp;
END;
$$;

COMMENT ON FUNCTION public.fn_experiment_transition(uuid, text, text, text) IS
    'Sole lifecycle driver for control_experiments (#583). Structural gates enforced '
    'here; protocol-level gates extend at the LANE-C markers. SECURITY DEFINER, '
    'search_path pinned; intended owner verdify_migration_owner.';

CREATE OR REPLACE FUNCTION public.fn_create_assignment(
    p_experiment_id  uuid,
    p_greenhouse_id  text,
    p_arm_label      text,
    p_operation_kind text,
    p_valid_range    tstzrange,
    p_slot_id        uuid    DEFAULT NULL,
    p_pair_index     integer DEFAULT NULL,
    p_block_index    integer DEFAULT NULL,
    p_algorithm      text    DEFAULT NULL,
    p_algorithm_version text DEFAULT NULL,
    p_scheduler_ref  text    DEFAULT NULL,
    p_reason         text    DEFAULT NULL,
    p_frozen_strata  jsonb   DEFAULT NULL,
    p_actor          text    DEFAULT current_user
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_assignment_id uuid;
    v_overlap uuid;
BEGIN
    SELECT * INTO v_exp
      FROM public.control_experiments
     WHERE experiment_id = p_experiment_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown experiment %', p_experiment_id;
    END IF;
    IF v_exp.status NOT IN ('armed', 'running') THEN
        RAISE EXCEPTION 'experiment % is % — assignments require armed/running',
            p_experiment_id, v_exp.status;
    END IF;
    IF v_exp.greenhouse_id <> p_greenhouse_id THEN
        RAISE EXCEPTION 'experiment % belongs to greenhouse %, not %',
            p_experiment_id, v_exp.greenhouse_id, p_greenhouse_id;
    END IF;

    -- Kind/operation consistency (table CHECKs cover shape; cross-checks here).
    IF p_operation_kind IN ('analyzed', 'positioning', 'baseline_recovery', 'identity_hold')
       AND v_exp.kind <> 'qualification' THEN
        RAISE EXCEPTION 'operation % requires a qualification experiment', p_operation_kind;
    END IF;
    IF p_operation_kind = 'aa_lane' AND v_exp.kind <> 'aa' THEN
        RAISE EXCEPTION 'operation aa_lane requires an aa experiment';
    END IF;
    IF p_operation_kind = 'randomized_day' AND v_exp.kind <> 'randomized' THEN
        RAISE EXCEPTION 'operation randomized_day requires a randomized experiment';
    END IF;
    IF p_operation_kind = 'randomized_day' AND p_arm_label NOT IN ('X', 'Y') THEN
        RAISE EXCEPTION 'randomized assignments store only blinded X/Y labels';
    END IF;

    -- Serialize all assignment writes for this greenhouse: btree_gist is not
    -- installed, so this advisory transaction lock + overlap query + insert
    -- IS the non-overlap guarantee (see file header).
    PERFORM pg_advisory_xact_lock(hashtext('control_assignments-' || p_greenhouse_id));

    SELECT assignment_id INTO v_overlap
      FROM public.control_assignments a
     WHERE a.greenhouse_id = p_greenhouse_id
       AND a.status IN ('active', 'closed')
       AND a.valid_range && p_valid_range
     LIMIT 1;
    IF v_overlap IS NOT NULL THEN
        RAISE EXCEPTION
            'assignment range % overlaps existing assignment % for greenhouse %',
            p_valid_range, v_overlap, p_greenhouse_id;
    END IF;

    INSERT INTO public.control_assignments
        (experiment_id, greenhouse_id, pair_index, block_index, arm_label,
         operation_kind, slot_id, scheduler_ref, reason, algorithm,
         algorithm_version, valid_range, frozen_strata, created_by)
    VALUES
        (p_experiment_id, p_greenhouse_id, p_pair_index, p_block_index, p_arm_label,
         p_operation_kind, p_slot_id, p_scheduler_ref, p_reason, p_algorithm,
         p_algorithm_version, p_valid_range, p_frozen_strata, p_actor)
    RETURNING assignment_id INTO v_assignment_id;

    INSERT INTO public.control_transition_ledger
        (experiment_id, greenhouse_id, assignment_id, slot_id, event_kind,
         detail, recorded_by)
    VALUES
        (p_experiment_id, p_greenhouse_id, v_assignment_id, p_slot_id,
         p_operation_kind,
         jsonb_build_object(
             'arm_label', p_arm_label,
             'valid_range', p_valid_range::text,
             'scheduler_ref', p_scheduler_ref,
             'reason', p_reason),
         p_actor);

    RETURN v_assignment_id;
END;
$$;

COMMENT ON FUNCTION public.fn_create_assignment(uuid, text, text, text, tstzrange,
    uuid, integer, integer, text, text, text, text, jsonb, text) IS
    'Sole assignment-creation path (#583): per-greenhouse advisory xact lock, overlap '
    'query, immutable insert, append-only ledger row — one transaction.';

CREATE OR REPLACE FUNCTION public.fn_claim_qualification_slot(
    p_slot_id       uuid,
    p_eligibility_snapshot jsonb,
    p_valid_range   tstzrange,
    p_arm_label     text,
    p_actor         text DEFAULT current_user
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_slot public.qualification_transition_slots%ROWTYPE;
    v_exp  public.control_experiments%ROWTYPE;
    v_blocking integer;
    v_assignment_id uuid;
BEGIN
    SELECT * INTO v_slot
      FROM public.qualification_transition_slots
     WHERE slot_id = p_slot_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown qualification slot %', p_slot_id;
    END IF;
    IF v_slot.status <> 'open' THEN
        RAISE EXCEPTION 'slot % is % — only open slots are claimable', p_slot_id, v_slot.status;
    END IF;

    SELECT * INTO v_exp
      FROM public.control_experiments
     WHERE experiment_id = v_slot.experiment_id;
    IF v_exp.kind <> 'qualification' OR v_exp.status <> 'running' THEN
        RAISE EXCEPTION
            'slot claim requires a RUNNING qualification experiment (found kind=%, status=%)',
            v_exp.kind, v_exp.status;
    END IF;

    -- FIFO within the cell: every lower-ordinal slot must already be resolved.
    SELECT count(*) INTO v_blocking
      FROM public.qualification_transition_slots s
     WHERE s.experiment_id = v_slot.experiment_id
       AND s.cell_index = v_slot.cell_index
       AND s.slot_ordinal < v_slot.slot_ordinal
       AND s.status IN ('open', 'claimed');
    IF v_blocking > 0 THEN
        RAISE EXCEPTION
            'slot % violates cell % FIFO: % earlier slot(s) still open/claimed',
            p_slot_id, v_slot.cell_index, v_blocking;
    END IF;

    -- The eligibility snapshot is the pre-step evidence and must be present
    -- at claim time. LANE-C: evaluate the slot''s regime/eligibility/version
    -- requirements against this snapshot (declared rules; structural check here).
    IF p_eligibility_snapshot IS NULL OR p_eligibility_snapshot = 'null'::jsonb THEN
        RAISE EXCEPTION 'slot claim requires a non-null eligibility snapshot';
    END IF;

    -- Materialize exactly one immutable analyzed assignment before actuation.
    v_assignment_id := public.fn_create_assignment(
        p_experiment_id  => v_slot.experiment_id,
        p_greenhouse_id  => v_exp.greenhouse_id,
        p_arm_label      => p_arm_label,
        p_operation_kind => 'analyzed',
        p_valid_range    => p_valid_range,
        p_slot_id        => p_slot_id,
        p_actor          => p_actor);

    UPDATE public.qualification_transition_slots
       SET status = 'claimed',
           eligibility_snapshot = p_eligibility_snapshot,
           claimed_at = now(),
           claimed_by = p_actor,
           assignment_id = v_assignment_id,
           updated_at = now()
     WHERE slot_id = p_slot_id;

    RETURN v_assignment_id;
END;
$$;

COMMENT ON FUNCTION public.fn_claim_qualification_slot(uuid, jsonb, tstzrange, text, text) IS
    'Advisory-locked qualification slot claim (#583): FIFO within cell, persists the '
    'eligibility snapshot, materializes exactly one immutable analyzed assignment '
    '(via fn_create_assignment) before actuation.';

CREATE OR REPLACE FUNCTION public.fn_admit_policy_vector(
    p_proposal_id uuid,
    p_device_id   text,
    p_validity    tstzrange DEFAULT NULL,
    p_actor       text DEFAULT current_user
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_prop public.policy_proposals%ROWTYPE;
    v_asg  public.control_assignments%ROWTYPE;
    v_exp  public.control_experiments%ROWTYPE;
    v_validity tstzrange;
    v_count integer;
    v_gen bigint;
    v_canon text;
    v_bytes bytea;
    v_content_sha text;
    v_activation_sha text;
    v_vector_id uuid;
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

    -- LANE-C: arm/allowlist/revision/edge checks extend here — AI may change
    -- only experiment.mutable_fields; a content-changing edge requires either
    -- a claimed analyzed slot or a pre-actuation non-analysis assignment
    -- (qualification) / an approved template with a passing qualification
    -- result on that exact edge (aa/randomized); identity_rebind requires
    -- byte-identical content. Structural checks below are always enforced.

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

    -- Canonical bytes + hashes.
    -- LANE-C / LANE-A: replace this deterministic component serialization with
    -- the Lane A canonical wire codec bytes and the §8.9 activation-hash
    -- treatment octets. The structural contract (content hash = sha256 of the
    -- stored canonical bytes; activation hash bound to assignment+generation)
    -- holds either way.
    SELECT COALESCE(string_agg(field_name || '=' || normalized_value::text, ';'
                               ORDER BY component_index), '') INTO v_canon
      FROM public.policy_proposal_components
     WHERE proposal_id = p_proposal_id;
    v_bytes := convert_to(v_canon, 'UTF8');
    v_content_sha := encode(public.digest(v_bytes, 'sha256'), 'hex');
    v_activation_sha := encode(public.digest(
        v_bytes || convert_to('|' || v_asg.assignment_id::text || '|' || v_gen::text, 'UTF8'),
        'sha256'), 'hex');

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

COMMENT ON FUNCTION public.fn_admit_policy_vector(uuid, text, tstzrange, text) IS
    'Arbiter admission (#583): proposal eligibility, producer allowlist, 49-component '
    'completeness, assignment-contained validity, per-greenhouse generation monotonicity '
    '(advisory xact lock), vector+components+outbox insert in one transaction. LANE-C '
    'extends the arm/allowlist/edge rules.';

CREATE OR REPLACE FUNCTION public.fn_record_device_snapshot(
    p_device_id         text,
    p_greenhouse_id     text,
    p_schema_revision   text,
    p_device_generation bigint,
    p_assignment_id     uuid,
    p_content_sha256    text,
    p_activation_sha256 text,
    p_apply_state       text,
    p_firmware_revision text,
    p_validity          tstzrange DEFAULT NULL,
    p_reported_at       timestamptz DEFAULT now()
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_snapshot_id bigint;
BEGIN
    INSERT INTO public.policy_device_snapshots
        (device_id, greenhouse_id, reported_at, schema_revision,
         device_generation, assignment_id, content_sha256, activation_sha256,
         validity, apply_state, firmware_revision)
    VALUES
        (p_device_id, p_greenhouse_id, p_reported_at, p_schema_revision,
         p_device_generation, p_assignment_id, p_content_sha256,
         p_activation_sha256, p_validity, p_apply_state, p_firmware_revision)
    RETURNING snapshot_id INTO v_snapshot_id;
    RETURN v_snapshot_id;
END;
$$;

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
    -- schema, generation, and activation hash.
    IF v_snap.device_id <> p_device_id
       OR v_snap.assignment_id IS DISTINCT FROM v_vec.assignment_id
       OR v_snap.device_generation IS DISTINCT FROM v_vec.device_generation
       OR v_snap.content_sha256 IS DISTINCT FROM v_vec.content_sha256
       OR v_snap.activation_sha256 IS DISTINCT FROM v_vec.activation_sha256 THEN
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

    -- LANE-C: coverage_fraction computation (confirmed continuous coverage of
    -- the assignment interval) extends here.
    UPDATE public.policy_exposures
       SET ended_at = p_ended_at,
           close_reason = p_close_reason,
           close_snapshot_id = p_close_snapshot_id,
           updated_at = now()
     WHERE exposure_id = p_exposure_id;
END;
$$;

-- EXECUTE is granted per-role by the role-split migration
-- (db/roles/experiment-roles.sql); until then, deny PUBLIC.
REVOKE ALL ON FUNCTION public.fn_experiment_transition(uuid, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_create_assignment(uuid, text, text, text, tstzrange,
    uuid, integer, integer, text, text, text, text, jsonb, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_claim_qualification_slot(uuid, jsonb, tstzrange, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_admit_policy_vector(uuid, text, tstzrange, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_record_device_snapshot(text, text, text, bigint, uuid,
    text, text, text, text, tstzrange, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_open_exposure(uuid, text, bigint, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_close_exposure(uuid, text, bigint, timestamptz, text) FROM PUBLIC;
