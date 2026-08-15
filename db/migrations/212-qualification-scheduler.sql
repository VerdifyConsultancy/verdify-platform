-- 212-qualification-scheduler.sql
--
-- Issues #584/#588 (epic #581): qualification-phase scheduler guards on the
-- migration-207/208 experiment schema (audit §8.3/§8.7,
-- docs/research/planner-efficacy-current-firmware-2026-08-14.md). The new
-- ingestor/tasks/experiment_qualification.py worker drives the §8.3 step-test
-- protocol (FIFO cell claims, positioning/identity-hold chaining,
-- failed-never-replaced resolution); 207/208 lack four guards it needs:
--
--   1. policy_proposals.producer CHECK does not admit the deterministic
--      qualification scheduler as a producer — extended (constraint renamed
--      _v2) with 'qualification_scheduler'. fn_submit_policy_proposal already
--      defers to control_experiments.permitted_producers, so a qualification
--      experiment must ALSO list the producer there (the qualification spec
--      template does).
--   2. fn_claim_qualification_slot (207) materializes the analyzed assignment
--      WITHOUT frozen_strata, but the Lane C arbiter derives the §8.9
--      treatment octets from frozen_strata (source/target template ids +
--      regime code) — every vector under a strata-less analyzed assignment
--      would be rejected at admission. The 207 five-argument signature is
--      DROPPED (the 208 precedent for fn_admit_policy_vector) and replaced by
--      one that requires frozen_strata, cross-checks it against the slot's
--      edge and the locked cell layout (regime code = cell_index % 4), and
--      passes it through to fn_create_assignment.
--   3. fn_resolve_qualification_slot — the ONLY path from 'claimed' to
--      'completed'/'failed'. §8.3: a started analyzed step that fails is a
--      failed cell result and is NEVER replaced; terminal states are
--      one-way (no path back to 'open', no fifth slot anywhere).
--   4. fn_record_qualification_event — audited append path for 'failed' /
--      'skipped' scheduler moves into control_transition_ledger, so the
--      worker never needs direct table DML (§8.7 role-split direction).
--
-- NON-SELF-TRANSACTIONAL (no top-level BEGIN/COMMIT; only DO-block/function
-- BEGINs) — safe to rollback-validate per
-- scripts/check_migration_rollback_safety.py (#23).
--
-- IDEMPOTENT: DROP CONSTRAINT/FUNCTION IF EXISTS pinned to superseded
-- names/signatures, conditional ADD CONSTRAINT, CREATE OR REPLACE FUNCTION.
-- Safe to re-run.
--
-- ROLLBACK NOTES: constraint rename + function replacements; no rows in
-- pre-existing tables are touched. Functional rollback = restore the 207
-- producer CHECK and fn_claim_qualification_slot body, and DROP
-- fn_resolve_qualification_slot / fn_record_qualification_event.

-- ============================================================================
-- 1. policy_proposals.producer: admit the deterministic qualification
--    scheduler. The per-experiment permitted_producers allowlist (checked by
--    fn_submit_policy_proposal) remains the protocol-level gate.
-- ============================================================================

ALTER TABLE public.policy_proposals
    DROP CONSTRAINT IF EXISTS policy_proposals_producer_check;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'policy_proposals_producer_check_v2'
          AND conrelid = 'public.policy_proposals'::regclass
    ) THEN
        ALTER TABLE public.policy_proposals
            ADD CONSTRAINT policy_proposals_producer_check_v2 CHECK (producer IN (
                'ai', 'forecast', 'baseline', 'guardrail', 'operator',
                'qualification_scheduler'));
    END IF;
END
$$;

-- ============================================================================
-- 2. Slot claim: require + validate frozen_strata (the §8.9 treatment
--    identity), then materialize the analyzed assignment with it. The 207
--    five-argument signature is dropped so exactly one claim path exists.
-- ============================================================================

DROP FUNCTION IF EXISTS public.fn_claim_qualification_slot(uuid, jsonb, tstzrange, text, text);

CREATE OR REPLACE FUNCTION public.fn_claim_qualification_slot(
    p_slot_id       uuid,
    p_eligibility_snapshot jsonb,
    p_valid_range   tstzrange,
    p_arm_label     text,
    p_frozen_strata jsonb,
    p_actor         text DEFAULT current_user
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_slot public.qualification_transition_slots%ROWTYPE;
    v_exp  public.control_experiments%ROWTYPE;
    v_edge public.policy_template_edges%ROWTYPE;
    v_blocking integer;
    v_assignment_id uuid;
    v_source uuid;
    v_target uuid;
    v_regime integer;
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
    -- at claim time (the worker persists predicates, conditions, regime and
    -- pretrace stats here).
    IF p_eligibility_snapshot IS NULL OR p_eligibility_snapshot = 'null'::jsonb THEN
        RAISE EXCEPTION 'slot claim requires a non-null eligibility snapshot';
    END IF;

    -- frozen_strata is the §8.9 treatment identity: the arbiter builds the
    -- activation-hash treatment octets from it, so it is claim-mandatory and
    -- must agree with the locked slot.
    IF p_frozen_strata IS NULL OR p_frozen_strata = 'null'::jsonb THEN
        RAISE EXCEPTION 'slot claim requires frozen_strata (source/target template ids + regime)';
    END IF;
    BEGIN
        v_source := (p_frozen_strata->>'source_template_id')::uuid;
        v_target := (p_frozen_strata->>'target_template_id')::uuid;
        v_regime := (p_frozen_strata->>'regime')::integer;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'frozen_strata must carry source_template_id/target_template_id/regime (%)',
            p_frozen_strata;
    END;
    IF v_source IS NULL OR v_target IS NULL OR v_regime IS NULL THEN
        RAISE EXCEPTION 'frozen_strata must carry source_template_id/target_template_id/regime (%)',
            p_frozen_strata;
    END IF;
    -- Locked cell layout (qualification spec v1): regime code = cell_index % 4.
    IF v_regime <> v_slot.cell_index % 4 THEN
        RAISE EXCEPTION 'frozen_strata regime % does not match cell % (expected %)',
            v_regime, v_slot.cell_index, v_slot.cell_index % 4;
    END IF;
    IF v_slot.edge_id IS NOT NULL THEN
        SELECT * INTO v_edge
          FROM public.policy_template_edges
         WHERE edge_id = v_slot.edge_id;
        IF v_edge.from_template_id <> v_source OR v_edge.to_template_id <> v_target THEN
            RAISE EXCEPTION
                'frozen_strata templates (%->%) do not match slot edge (%->%)',
                v_source, v_target, v_edge.from_template_id, v_edge.to_template_id;
        END IF;
    END IF;

    -- Materialize exactly one immutable analyzed assignment before actuation.
    v_assignment_id := public.fn_create_assignment(
        p_experiment_id  => v_slot.experiment_id,
        p_greenhouse_id  => v_exp.greenhouse_id,
        p_arm_label      => p_arm_label,
        p_operation_kind => 'analyzed',
        p_valid_range    => p_valid_range,
        p_slot_id        => p_slot_id,
        p_frozen_strata  => p_frozen_strata,
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

COMMENT ON FUNCTION public.fn_claim_qualification_slot(uuid, jsonb, tstzrange, text, jsonb, text) IS
    'Advisory-locked qualification slot claim (#584/#588, replaces the 207 signature): '
    'FIFO within cell, persists the eligibility snapshot, validates frozen_strata '
    '(source/target/regime) against the locked slot, materializes exactly one immutable '
    'analyzed assignment (via fn_create_assignment) before actuation.';

-- ============================================================================
-- 3. Slot resolution: the ONLY path out of 'claimed'. One-way; a failed
--    analyzed step is a failed cell result and is never replaced (§8.3).
-- ============================================================================

CREATE OR REPLACE FUNCTION public.fn_resolve_qualification_slot(
    p_slot_id  uuid,
    p_outcome  text,
    p_detail   jsonb DEFAULT '{}'::jsonb,
    p_actor    text DEFAULT current_user
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_slot public.qualification_transition_slots%ROWTYPE;
    v_exp  public.control_experiments%ROWTYPE;
BEGIN
    IF p_outcome NOT IN ('completed', 'failed') THEN
        RAISE EXCEPTION 'slot resolution outcome must be completed|failed (got %)', p_outcome;
    END IF;

    SELECT * INTO v_slot
      FROM public.qualification_transition_slots
     WHERE slot_id = p_slot_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown qualification slot %', p_slot_id;
    END IF;
    -- One-way: only a claimed slot resolves; completed/failed are terminal
    -- and can never reopen (failed-never-replaced).
    IF v_slot.status <> 'claimed' THEN
        RAISE EXCEPTION 'slot % is % — only claimed slots resolve (terminal states never reopen)',
            p_slot_id, v_slot.status;
    END IF;

    SELECT * INTO v_exp
      FROM public.control_experiments
     WHERE experiment_id = v_slot.experiment_id;

    UPDATE public.qualification_transition_slots
       SET status = p_outcome,
           updated_at = now()
     WHERE slot_id = p_slot_id;

    -- Assignment bookkeeping: a failed analyzed step marks its assignment
    -- 'failed'; a completed one is 'closed' (if the boundary close-out has
    -- not already done so). The 207 immutability trigger permits exactly
    -- status/updated_at.
    IF v_slot.assignment_id IS NOT NULL THEN
        IF p_outcome = 'failed' THEN
            UPDATE public.control_assignments
               SET status = 'failed', updated_at = now()
             WHERE assignment_id = v_slot.assignment_id;
        ELSE
            UPDATE public.control_assignments
               SET status = 'closed', updated_at = now()
             WHERE assignment_id = v_slot.assignment_id
               AND status = 'active';
        END IF;
    END IF;

    INSERT INTO public.control_transition_ledger
        (experiment_id, greenhouse_id, assignment_id, slot_id, event_kind,
         detail, recorded_by)
    VALUES
        (v_slot.experiment_id, v_exp.greenhouse_id, v_slot.assignment_id,
         p_slot_id,
         CASE WHEN p_outcome = 'failed' THEN 'failed' ELSE 'analyzed' END,
         jsonb_build_object('phase', 'resolution', 'outcome', p_outcome)
             || COALESCE(p_detail, '{}'::jsonb),
         p_actor);
END;
$$;

COMMENT ON FUNCTION public.fn_resolve_qualification_slot(uuid, text, jsonb, text) IS
    'Sole claimed->completed/failed slot transition (#584/#588, audit §8.3): one-way, '
    'ledgered, marks the analyzed assignment failed/closed. A failed cell result is '
    'never replaced; terminal slot states never reopen.';

-- ============================================================================
-- 4. Audited ledger append for non-assignment scheduler moves (skipped
--    boundary decisions, failure evidence) — no direct table DML needed by
--    the worker role.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.fn_record_qualification_event(
    p_experiment_id uuid,
    p_event_kind    text,
    p_detail        jsonb DEFAULT '{}'::jsonb,
    p_slot_id       uuid DEFAULT NULL,
    p_assignment_id uuid DEFAULT NULL,
    p_actor         text DEFAULT current_user
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_ledger_id bigint;
BEGIN
    IF p_event_kind NOT IN ('failed', 'skipped') THEN
        RAISE EXCEPTION 'fn_record_qualification_event records failed|skipped moves (got %)',
            p_event_kind;
    END IF;
    SELECT * INTO v_exp
      FROM public.control_experiments
     WHERE experiment_id = p_experiment_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown experiment %', p_experiment_id;
    END IF;
    IF v_exp.kind <> 'qualification' THEN
        RAISE EXCEPTION 'experiment % is kind % — qualification events only', p_experiment_id, v_exp.kind;
    END IF;

    INSERT INTO public.control_transition_ledger
        (experiment_id, greenhouse_id, assignment_id, slot_id, event_kind,
         detail, recorded_by)
    VALUES
        (p_experiment_id, v_exp.greenhouse_id, p_assignment_id, p_slot_id,
         p_event_kind, COALESCE(p_detail, '{}'::jsonb), p_actor)
    RETURNING ledger_id INTO v_ledger_id;

    RETURN v_ledger_id;
END;
$$;

COMMENT ON FUNCTION public.fn_record_qualification_event(uuid, text, jsonb, uuid, uuid, text) IS
    'Audited append of failed/skipped qualification scheduler moves into the '
    'append-only control_transition_ledger (#584/#588): §8.3 requires those rows to '
    'never be invisible operational traffic.';

-- EXECUTE is granted per-role by the role-split migration
-- (db/roles/experiment-roles.sql); until then, deny PUBLIC.
REVOKE ALL ON FUNCTION public.fn_claim_qualification_slot(uuid, jsonb, tstzrange, text, jsonb, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_resolve_qualification_slot(uuid, text, jsonb, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_record_qualification_event(uuid, text, jsonb, uuid, uuid, text) FROM PUBLIC;
