-- 213-experiment-result-binding.sql — result-hash binding slots + arming gates
-- (#583/#588, epic #581; closes the gap flagged in PR #602).
--
-- Adds the experiment-level result artifact hash (an experiment's OWN frozen
-- outcome, recorded at/after completion by the analyzer pipeline) and the
-- qualification/aa binding columns the audit's §8.7 state machine requires:
-- aa arms only against a completed qualification's result hash; randomized
-- arms only against both. fn_experiment_transition is REPLACED wholesale with
-- the 207 body + the new arm gates (only 207 ever defined it; verified before
-- authoring). Bindings are writable only in draft/locked via
-- fn_bind_experiment_result and immutable once armed; result_sha256 is
-- write-once via fn_record_experiment_result.
--
-- Wrap-safe (no top-level BEGIN/COMMIT); idempotent.

ALTER TABLE public.control_experiments
    ADD COLUMN IF NOT EXISTS result_sha256               text,
    ADD COLUMN IF NOT EXISTS qualification_result_sha256 text,
    ADD COLUMN IF NOT EXISTS aa_result_sha256            text;

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'control_experiments_result_sha256_hex') THEN
        ALTER TABLE public.control_experiments
            ADD CONSTRAINT control_experiments_result_sha256_hex
            CHECK (result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'control_experiments_qualification_result_sha256_hex') THEN
        ALTER TABLE public.control_experiments
            ADD CONSTRAINT control_experiments_qualification_result_sha256_hex
            CHECK (qualification_result_sha256 IS NULL OR qualification_result_sha256 ~ '^[0-9a-f]{64}$');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'control_experiments_aa_result_sha256_hex') THEN
        ALTER TABLE public.control_experiments
            ADD CONSTRAINT control_experiments_aa_result_sha256_hex
            CHECK (aa_result_sha256 IS NULL OR aa_result_sha256 ~ '^[0-9a-f]{64}$');
    END IF;
END
$do$;

COMMENT ON COLUMN public.control_experiments.result_sha256 IS
    'This experiment''s OWN frozen result-artifact hash (settling analyzer for '
    'qualification, experiment-aa-gates.py for aa, frozen analysis export for '
    'randomized). Write-once via fn_record_experiment_result (#213).';
COMMENT ON COLUMN public.control_experiments.qualification_result_sha256 IS
    'Binding to a completed qualification experiment''s result_sha256; required '
    'to arm aa/randomized. Set via fn_bind_experiment_result in draft/locked only.';
COMMENT ON COLUMN public.control_experiments.aa_result_sha256 IS
    'Binding to a completed aa experiment''s result_sha256; required to arm '
    'randomized. Set via fn_bind_experiment_result in draft/locked only.';

CREATE OR REPLACE FUNCTION public.fn_bind_experiment_result(
    p_experiment_id uuid,
    p_source_kind   text,
    p_result_sha256 text,
    p_actor         text DEFAULT current_user
) RETURNS public.control_experiments
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
BEGIN
    IF p_source_kind NOT IN ('qualification', 'aa') THEN
        RAISE EXCEPTION 'fn_bind_experiment_result: source kind must be qualification|aa, got %', p_source_kind;
    END IF;
    IF p_result_sha256 IS NULL OR p_result_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'fn_bind_experiment_result: result hash must be 64 lowercase hex chars';
    END IF;
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'fn_bind_experiment_result: unknown experiment %', p_experiment_id;
    END IF;
    IF v_exp.status NOT IN ('draft', 'locked') THEN
        RAISE EXCEPTION
            'fn_bind_experiment_result: experiment % is % — bindings are immutable once armed',
            p_experiment_id, v_exp.status;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.control_experiments src
         WHERE src.greenhouse_id = v_exp.greenhouse_id
           AND src.kind = p_source_kind
           AND src.status = 'completed'
           AND src.result_sha256 = p_result_sha256
    ) THEN
        RAISE EXCEPTION
            'fn_bind_experiment_result: hash matches no completed % experiment for greenhouse %',
            p_source_kind, v_exp.greenhouse_id;
    END IF;
    IF p_source_kind = 'qualification' THEN
        UPDATE public.control_experiments
           SET qualification_result_sha256 = p_result_sha256, updated_at = now()
         WHERE experiment_id = p_experiment_id RETURNING * INTO v_exp;
    ELSE
        UPDATE public.control_experiments
           SET aa_result_sha256 = p_result_sha256, updated_at = now()
         WHERE experiment_id = p_experiment_id RETURNING * INTO v_exp;
    END IF;
    INSERT INTO public.experiment_events (experiment_id, event_kind, severity, actor, detail)
    VALUES (p_experiment_id, 'result_binding', 'info', p_actor,
            jsonb_build_object('source_kind', p_source_kind, 'result_sha256', p_result_sha256));
    RETURN v_exp;
END;
$$;

REVOKE ALL ON FUNCTION public.fn_bind_experiment_result(uuid, text, text, text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.fn_record_experiment_result(
    p_experiment_id uuid,
    p_result_sha256 text,
    p_actor         text DEFAULT current_user
) RETURNS public.control_experiments
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
BEGIN
    IF p_result_sha256 IS NULL OR p_result_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'fn_record_experiment_result: result hash must be 64 lowercase hex chars';
    END IF;
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'fn_record_experiment_result: unknown experiment %', p_experiment_id;
    END IF;
    IF v_exp.status NOT IN ('running', 'paused', 'completed') THEN
        RAISE EXCEPTION
            'fn_record_experiment_result: experiment % is % — a result exists only for running/paused/completed experiments',
            p_experiment_id, v_exp.status;
    END IF;
    IF v_exp.result_sha256 IS NOT NULL AND v_exp.result_sha256 <> p_result_sha256 THEN
        RAISE EXCEPTION
            'fn_record_experiment_result: experiment % already has result % (write-once)',
            p_experiment_id, v_exp.result_sha256;
    END IF;
    UPDATE public.control_experiments
       SET result_sha256 = p_result_sha256, updated_at = now()
     WHERE experiment_id = p_experiment_id RETURNING * INTO v_exp;
    INSERT INTO public.experiment_events (experiment_id, event_kind, severity, actor, detail)
    VALUES (p_experiment_id, 'result_recorded', 'info', p_actor,
            jsonb_build_object('result_sha256', p_result_sha256));
    RETURN v_exp;
END;
$$;

REVOKE ALL ON FUNCTION public.fn_record_experiment_result(uuid, text, text) FROM PUBLIC;

-- ── fn_experiment_transition: 207 body + result-binding arm gates ──────────
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
            -- Result-hash binding gates (#583/#588, migration 213): an aa
            -- experiment may arm only with a bound qualification result that
            -- matches a COMPLETED qualification experiment's own recorded
            -- result for the same greenhouse; randomized requires both.
            IF v_exp.qualification_result_sha256 IS NULL THEN
                RAISE EXCEPTION
                    'arm gate: % experiment % requires a bound qualification_result_sha256 (fn_bind_experiment_result)',
                    v_exp.kind, p_experiment_id;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.control_experiments src
                 WHERE src.greenhouse_id = v_exp.greenhouse_id
                   AND src.kind = 'qualification'
                   AND src.status = 'completed'
                   AND src.result_sha256 = v_exp.qualification_result_sha256
            ) THEN
                RAISE EXCEPTION
                    'arm gate: bound qualification_result_sha256 of experiment % matches no completed qualification experiment',
                    p_experiment_id;
            END IF;
        END IF;
        IF v_exp.kind = 'randomized' THEN
            IF v_exp.aa_result_sha256 IS NULL THEN
                RAISE EXCEPTION
                    'arm gate: randomized experiment % requires a bound aa_result_sha256 (fn_bind_experiment_result)',
                    p_experiment_id;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.control_experiments src
                 WHERE src.greenhouse_id = v_exp.greenhouse_id
                   AND src.kind = 'aa'
                   AND src.status = 'completed'
                   AND src.result_sha256 = v_exp.aa_result_sha256
            ) THEN
                RAISE EXCEPTION
                    'arm gate: bound aa_result_sha256 of experiment % matches no completed aa experiment',
                    p_experiment_id;
            END IF;
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
