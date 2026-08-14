-- db/roles/experiment-roles.sql — experiment-era DB role split SCAFFOLD (#583).
--
-- DESIGN ARTIFACT / DDL TEMPLATE — deliberately NOT a numbered db/migrations
-- file yet. It ships as a numbered migration (through the ledgered runner)
-- once the Lane C consumers (experiment_assignments / policy_arbiter /
-- policy_delivery workers, MCP authorization, planner_graph contract regen)
-- exist to take the split credentials. Committing the scaffold now fixes the
-- role names and grant surfaces so Lane C/F can code against them.
--
-- WHY (audit §8.7): direct-table DML revocation is impossible with today's
-- single shared DB_USER=verdify, which is also the schema owner. Before
-- shadow mode, each workload gets its own NOLOGIN group role; per-workload
-- LOGIN users + passwords are provisioned OUT-OF-BAND at rollout via the
-- sealed-secret path (per-workload Secret references + usernames in the
-- GitOps pod specs — mirroring the agent_ro pattern of migration 184: no
-- password ever lands in git). Within the ingestor pod, experiment-mode
-- context gathering uses a dedicated IRIS_CONTEXT_DATABASE_URL Secret and
-- rejects the generic owner/ingestor DSN; MCP and planner_graph each receive
-- their own role rather than a shared "planner" credential.
--
-- All statements are idempotent (guarded DO blocks / re-runnable GRANT and
-- REVOKE). GRANTs on experiment objects assume migration 207 has run.
-- Non-self-transactional (DO-block BEGINs only) — safe to wrap.

-- ────────────────────────────────────────────────────────────────────────────
-- Role creation (NOLOGIN group roles)
-- ────────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY[
        'verdify_migration_owner',   -- owns schema + SECURITY DEFINER fns; PreSync job only
        'verdify_api',               -- API: EXECUTE on audited experiment state transitions
        'verdify_iris_context',      -- read-only Iris context/frozen-snapshot views
        'verdify_mcp_runtime',       -- MCP: audited non-study tools + proposal EXECUTE
        'verdify_planner_graph',     -- planner_graph: claim/lease/run/memory DML + proposal EXECUTE
        'verdify_ingestor_arbiter',  -- ingestor: telemetry writes + policy transition/outbox EXECUTE
        'verdify_analysis_blinded',  -- blinded outcome analysis: X/Y SELECT only
        'verdify_twin_ro'            -- twin (§8.9): read-only as-of policy inputs
    ] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', r);
        END IF;
    END LOOP;
END
$$;

COMMENT ON ROLE verdify_migration_owner IS
    'Non-login owner of experiment schema objects and the SECURITY DEFINER transition '
    'functions (#583). Available ONLY to the PreSync migration Job login user.';
COMMENT ON ROLE verdify_analysis_blinded IS
    'Blinded outcome-analysis role (#583): SELECT only on blinded X/Y outcomes, opaque '
    'assignment-scoped receipts, equality and coverage fields. Explicitly DENIED: '
    'proposal/effective-vector components, reusable hashes, setpoint plan/change/'
    'snapshot tables, the map secret, and control_arm_resolutions until the '
    'completed-state unblind transition.';

-- ────────────────────────────────────────────────────────────────────────────
-- GRANT skeletons (per §8.7's role paragraph)
--
-- These reference migration-207 objects; run this file only on a DB where 207
-- is applied. Views referenced as "LANE-C:" do not exist yet — those grants
-- are listed as comments so the eventual migration is a copy-forward.
-- ────────────────────────────────────────────────────────────────────────────

-- verdify_api — EXECUTE on audited experiment state transitions; no direct DML.
GRANT EXECUTE ON FUNCTION public.fn_experiment_transition(uuid, text, text, text) TO verdify_api;
GRANT SELECT ON public.control_experiments, public.control_experiment_arms,
                public.experiment_events TO verdify_api;

-- verdify_iris_context — read-only experiment context / frozen snapshots only.
GRANT SELECT ON public.experiment_context_snapshots TO verdify_iris_context;
-- LANE-C: GRANT SELECT ON the frozen-context views (v_experiment_context_*)
-- once they exist; this role gets NO generic table read.

-- verdify_mcp_runtime — proposal-function EXECUTE, no direct table DML.
-- LANE-C: GRANT EXECUTE on the individually audited non-study tool functions
-- and the proposal-submission function (fn_submit_policy_proposal) when they
-- land. Structurally available today:
GRANT SELECT ON public.control_experiments, public.policy_proposals TO verdify_mcp_runtime;

-- verdify_planner_graph — its own claim/lease/run/memory DML + proposal EXECUTE.
GRANT SELECT, INSERT ON public.planner_inference_runs TO verdify_planner_graph;
-- LANE-C: planner_graph memory/claim/lease tables (separate stream) + proposal
-- submission EXECUTE.

-- verdify_ingestor_arbiter — telemetry writes + policy transition/outbox EXECUTE.
GRANT EXECUTE ON FUNCTION public.fn_create_assignment(uuid, text, text, text, tstzrange,
    uuid, integer, integer, text, text, text, text, jsonb, text) TO verdify_ingestor_arbiter;
GRANT EXECUTE ON FUNCTION public.fn_claim_qualification_slot(uuid, jsonb, tstzrange, text, text)
    TO verdify_ingestor_arbiter;
GRANT EXECUTE ON FUNCTION public.fn_admit_policy_vector(uuid, text, tstzrange, text)
    TO verdify_ingestor_arbiter;
GRANT EXECUTE ON FUNCTION public.fn_record_device_snapshot(text, text, text, bigint, uuid,
    text, text, text, text, tstzrange, timestamptz) TO verdify_ingestor_arbiter;
GRANT EXECUTE ON FUNCTION public.fn_open_exposure(uuid, text, bigint, text) TO verdify_ingestor_arbiter;
GRANT EXECUTE ON FUNCTION public.fn_close_exposure(uuid, text, bigint, timestamptz, text)
    TO verdify_ingestor_arbiter;
GRANT SELECT ON public.control_experiments, public.control_assignments,
                public.policy_templates, public.policy_template_edges,
                public.effective_policy_vectors, public.effective_policy_vector_components,
                public.policy_delivery_outbox, public.policy_delivery_attempts,
                public.policy_device_snapshots, public.policy_exposures TO verdify_ingestor_arbiter;
-- Delivery-worker lease/attempt lifecycle is direct DML on exactly two tables:
GRANT UPDATE (state, lease_owner, lease_expires_at, attempt_count, next_attempt_at,
              last_error_class, staged_at, activated_at, updated_at)
    ON public.policy_delivery_outbox TO verdify_ingestor_arbiter;
GRANT INSERT, UPDATE ON public.policy_delivery_attempts TO verdify_ingestor_arbiter;
-- LANE-C: telemetry-write grants (climate, equipment_state, ...) copy the
-- existing ingestor surface; enumerated at migration time, not scaffolded here.

-- verdify_twin_ro — §8.9 twin role: read-only policy/exposure state.
GRANT SELECT ON public.effective_policy_vectors, public.effective_policy_vector_components,
                public.policy_device_snapshots, public.policy_exposures,
                public.control_assignments TO verdify_twin_ro;
-- LANE-F: GRANT SELECT ON v_policy_twin_asof_input once it exists.

-- verdify_analysis_blinded — blinded X/Y outcomes ONLY.
-- LANE-C: the blinded outcome views (v_blinded_block_outcomes, opaque
-- assignment receipts, identity-equality/coverage projections) are the ONLY
-- SELECT surface for this role. No base-table grant is made here on purpose.

-- ────────────────────────────────────────────────────────────────────────────
-- Explicit deny list for the blinded analysis role
--
-- REVOKE is a no-op where no grant exists, but writing the denies explicitly
-- (a) documents the §8.7 firewall and (b) repairs any accidental future GRANT
-- when this file is re-run.
-- ────────────────────────────────────────────────────────────────────────────
REVOKE ALL ON public.control_arm_resolutions           FROM verdify_analysis_blinded;
REVOKE ALL ON public.policy_proposals                  FROM verdify_analysis_blinded;
REVOKE ALL ON public.policy_proposal_components        FROM verdify_analysis_blinded;
REVOKE ALL ON public.effective_policy_vectors          FROM verdify_analysis_blinded;
REVOKE ALL ON public.effective_policy_vector_components FROM verdify_analysis_blinded;
REVOKE ALL ON public.policy_templates                  FROM verdify_analysis_blinded;
REVOKE ALL ON public.policy_template_components        FROM verdify_analysis_blinded;
REVOKE ALL ON public.setpoint_plan                     FROM verdify_analysis_blinded;
REVOKE ALL ON public.setpoint_changes                  FROM verdify_analysis_blinded;
REVOKE ALL ON public.setpoint_snapshot                 FROM verdify_analysis_blinded;
-- The mapping SECRET never exists in the database at all (only its HMAC
-- commitment hash on control_experiments); the physical-arm resolution table
-- above is the closest object and is denied until the one-way unblind, which
-- is executed as a completed-state transition by verdify_migration_owner.

-- ────────────────────────────────────────────────────────────────────────────
-- Ownership transfer (runs with the migration that promotes this scaffold)
--
-- The SECURITY DEFINER transition functions from migration 207 move to the
-- non-login owner so their definer privileges are not the schema-owner
-- superuser context:
--
--   ALTER FUNCTION public.fn_experiment_transition(uuid, text, text, text)
--       OWNER TO verdify_migration_owner;
--   ... (repeat for fn_create_assignment, fn_claim_qualification_slot,
--        fn_admit_policy_vector, fn_record_device_snapshot, fn_open_exposure,
--        fn_close_exposure)
--
-- plus ALTER TABLE ... OWNER TO verdify_migration_owner for the experiment
-- tables, executed by the PreSync job login (a member of the owner role).
-- Acceptance tests then connect as each live runtime role, prove forbidden
-- direct INSERT/UPDATE/DELETE, and prove only the granted EXECUTE paths work
-- (§8.10 suite, Lane F).
