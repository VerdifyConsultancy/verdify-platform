-- 210-iris-experiment-context.sql
--
-- Issue #585 (Lane D tranche 2 of epic #581): the DB half of the treatment
-- firewall on the LIVE planner path (audit §8.8,
-- docs/research/planner-efficacy-current-firmware-2026-08-14.md). In
-- experiment gather mode, iris_planner/gather-plan-context.sh must read ONLY
-- the §8.8-allowed context:
--
--   current sensors + as-of forecast + fixed crop/topology context
--     + frozen knowledge snapshot + the planner's own virtual prior template
--
-- and must NOT see physical active plan/setpoints, admission/fallback state,
-- recent arm outcomes/scorecards, the evaluation backlog, or mutable lessons.
-- This migration provides that surface as one security-barrier view plus the
-- immutable per-trigger snapshot freeze/read pair keyed by experiment +
-- context revision:
--
--   1. v_iris_experiment_context — one row per armed/running experiment with
--      JSON columns (current_sensors, asof_forecast, crop_topology,
--      candidate_templates, virtual_prior). The virtual prior comes from the
--      planner's OWN append-only selection stream
--      (experiment_context_snapshots.virtual_selected_template_id, falling
--      back to producer='ai' policy_proposals rows — the Lane C
--      template_selection stream), NEVER from admission/exposure state, so an
--      A day cannot announce itself through "no previous AI vector".
--   2. experiment_context_snapshots.context_revision — additive column + a
--      per-experiment unique index so a frozen snapshot is addressable as
--      (experiment_id, context_revision).
--   3. fn_freeze_experiment_context — persists one immutable
--      experiment_context_snapshots row per planning trigger (prompt/model/
--      tool-manifest hashes, lesson/retrieval corpus hashes, crop/topology
--      hash, full context payload, virtual prior) and assigns the next
--      context_revision under a per-experiment advisory lock.
--   4. fn_get_experiment_context_snapshot — reads one frozen snapshot back by
--      experiment + revision (the distinct read-only retrieval surface §8.8
--      requires instead of the mutable lesson/knowledge tools).
--
-- NON-SELF-TRANSACTIONAL (no top-level BEGIN/COMMIT; only DO-block/function
-- BEGINs) — safe to rollback-validate per
-- scripts/check_migration_rollback_safety.py (#23).
--
-- IDEMPOTENT: ALTER TABLE ... ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT
-- EXISTS, CREATE OR REPLACE VIEW/FUNCTION. Safe to re-run.
--
-- ROLLBACK NOTES: purely additive (one nullable column on the append-only
-- experiment_context_snapshots table, one partial unique index, one view, two
-- functions). Functional rollback = DROP VIEW v_iris_experiment_context, DROP
-- the two functions, DROP INDEX ux_experiment_context_snapshots_revision, and
-- ALTER TABLE ... DROP COLUMN context_revision; no rows in pre-existing
-- tables are touched.

-- ============================================================================
-- 1. Frozen-snapshot addressing: (experiment_id, context_revision)
-- ============================================================================

ALTER TABLE public.experiment_context_snapshots
    ADD COLUMN IF NOT EXISTS context_revision bigint
        CHECK (context_revision IS NULL OR context_revision > 0);

COMMENT ON COLUMN public.experiment_context_snapshots.context_revision IS
    'Per-experiment monotonic revision assigned by fn_freeze_experiment_context '
    '(#585, audit §8.8). NULL for Lane C proposal-attached snapshots that were '
    'not frozen through the gather path; frozen rows are unique per '
    '(experiment_id, context_revision) and readable via '
    'fn_get_experiment_context_snapshot.';

CREATE UNIQUE INDEX IF NOT EXISTS ux_experiment_context_snapshots_revision
    ON public.experiment_context_snapshots (experiment_id, context_revision)
 WHERE context_revision IS NOT NULL;

-- ============================================================================
-- 2. The experiment-mode context surface (security barrier)
--
-- ALLOWED (§8.8): latest climate sensor row, as-of outdoor forecast, fixed
-- crop/topology context, the two locked AI candidate templates (opaque ids —
-- no content hashes, no canonical bytes), and the planner's own virtual prior
-- selection.
--
-- EXCLUDED BY CONSTRUCTION — this view must never reference: setpoint_changes
-- / setpoint_plan / plan_journal (active plan + setpoints + evaluation
-- backlog), effective_policy_vectors / policy_exposures / policy_delivery
-- tables (admission/fallback/exposure state), control_assignments /
-- control_arm_resolutions (arm identity), v_daily_kpi / daily_summary /
-- climate_action_log scorecards (arm outcomes), and planner_lessons (mutable
-- lessons). A source-contract test pins this exclusion list.
-- ============================================================================

CREATE OR REPLACE VIEW public.v_iris_experiment_context
    WITH (security_barrier = true) AS
SELECT e.experiment_id,
       e.greenhouse_id,
       e.kind AS experiment_kind,
       e.status AS experiment_status,
       -- Latest climate sensor row: indoor zones + outdoor + solar + dew.
       -- Deliberately omits water/flow accumulators — template selection needs
       -- climate state, not actuation-consumption telemetry.
       (
           SELECT json_build_object(
                      'ts', c.ts,
                      'temp_avg', round(c.temp_avg::numeric, 1),
                      'rh_avg', round(c.rh_avg::numeric, 1),
                      'vpd_avg', round(c.vpd_avg::numeric, 2),
                      'temp_south', round(c.temp_south::numeric, 1),
                      'temp_north', round(c.temp_north::numeric, 1),
                      'temp_east', round(c.temp_east::numeric, 1),
                      'temp_west', round(c.temp_west::numeric, 1),
                      'vpd_south', round(c.vpd_south::numeric, 2),
                      'vpd_north', round(c.vpd_north::numeric, 2),
                      'vpd_east', round(c.vpd_east::numeric, 2),
                      'vpd_west', round(c.vpd_west::numeric, 2),
                      'rh_south', round(c.rh_south::numeric, 1),
                      'rh_north', round(c.rh_north::numeric, 1),
                      'rh_east', round(c.rh_east::numeric, 1),
                      'rh_west', round(c.rh_west::numeric, 1),
                      'outdoor_temp_f', round(c.outdoor_temp_f::numeric, 1),
                      'outdoor_rh_pct', round(c.outdoor_rh_pct::numeric, 1),
                      'outdoor_lux', c.outdoor_lux,
                      'solar_irradiance_w_m2', round(c.solar_irradiance_w_m2::numeric, 0),
                      'dew_point', round(c.dew_point::numeric, 1),
                      'abs_humidity', round(c.abs_humidity::numeric, 1),
                      'enthalpy_delta', round(c.enthalpy_delta::numeric, 2),
                      'co2_ppm', round(c.co2_ppm::numeric, 0)
                  )
             FROM public.climate c
            ORDER BY c.ts DESC
            LIMIT 1
       ) AS current_sensors,
       -- As-of forecast: for each future hour in the next 24h, the LATEST
       -- fetch — the same as-of semantics both arms receive.
       (
           SELECT json_agg(json_build_object(
                      'ts', fc.ts,
                      'temp_f', round(fc.temp_f::numeric, 1),
                      'rh_pct', round(fc.rh_pct::numeric, 0),
                      'vpd_kpa', round(fc.vpd_kpa::numeric, 2),
                      'cloud_cover_pct', round(fc.cloud_cover_pct::numeric, 0),
                      'wind_speed_mph', round(fc.wind_speed_mph::numeric, 0),
                      'solar_w_m2', round(GREATEST(COALESCE(fc.direct_radiation_w_m2, 0), 0)::numeric, 0),
                      'precip_prob_pct', round(COALESCE(fc.precip_prob_pct, 0)::numeric, 0),
                      'fetched_at', fc.fetched_at
                  ) ORDER BY fc.ts)
             FROM (
                 SELECT DISTINCT ON (wf.ts) wf.*
                   FROM public.weather_forecast wf
                  WHERE wf.ts > now()
                    AND wf.ts <= now() + interval '24 hours'
                  ORDER BY wf.ts, wf.fetched_at DESC
             ) fc
       ) AS asof_forecast,
       -- Fixed crop/topology context (what is planted where — no outcomes).
       (
           SELECT json_agg(json_build_object(
                      'name', cr.name,
                      'variety', cr.variety,
                      'position', cr.position,
                      'zone', cr.zone,
                      'stage', cr.stage,
                      'planted_date', cr.planted_date
                  ) ORDER BY cr.zone, cr.position)
             FROM public.crops cr
            WHERE cr.is_active
       ) AS crop_topology,
       -- The two locked AI templates the planner may select between: opaque
       -- ids + kind labels only. Content hashes/bytes stay server-side — the
       -- planner context must never carry effective-vector content identity.
       (
           SELECT json_agg(json_build_object(
                      'template_id', t.template_id,
                      'kind', t.kind,
                      'locked', t.locked_at IS NOT NULL
                  ) ORDER BY t.kind)
             FROM public.policy_templates t
            WHERE t.experiment_id = e.experiment_id
              AND t.kind IN ('moderate', 'aggressive')
              AND t.locked_at IS NOT NULL
       ) AS candidate_templates,
       -- The planner's OWN prior virtual selection (both arms identically):
       -- newest append-only selection from the snapshot stream, falling back
       -- to the newest producer='ai' proposal (kind: template_selection).
       -- Never joins admission/exposure state.
       COALESCE(
           (
               SELECT json_build_object(
                          'template_id', s.virtual_selected_template_id,
                          'selected_at', s.created_at,
                          'trigger_ref', s.trigger_ref,
                          'context_revision', s.context_revision,
                          'source', 'context_snapshot'
                      )
                 FROM public.experiment_context_snapshots s
                WHERE s.experiment_id = e.experiment_id
                  AND s.virtual_selected_template_id IS NOT NULL
                ORDER BY s.created_at DESC
                LIMIT 1
           ),
           (
               SELECT json_build_object(
                          'template_id', p.proposed_template_id,
                          'selected_at', p.created_at,
                          'trigger_ref', p.trigger_ref,
                          'source', 'policy_proposal'
                      )
                 FROM public.policy_proposals p
                WHERE p.experiment_id = e.experiment_id
                  AND p.producer = 'ai'
                  AND p.proposed_template_id IS NOT NULL
                ORDER BY p.created_at DESC
                LIMIT 1
           )
       ) AS virtual_prior
  FROM public.control_experiments e
 WHERE e.status IN ('armed', 'running');

COMMENT ON VIEW public.v_iris_experiment_context IS
    'Experiment-mode planner context (#585, audit §8.8): current sensors, as-of '
    'forecast, fixed crop/topology, the locked AI candidate template ids, and '
    'the planner''s own virtual prior selection. security_barrier view; '
    'deliberately excludes active plan/setpoints, admission/fallback/exposure '
    'state, arm outcomes/scorecards, evaluation backlog, and mutable lessons.';

-- ============================================================================
-- 3. Freeze one immutable per-trigger snapshot (experiment + revision keyed)
-- ============================================================================

CREATE OR REPLACE FUNCTION public.fn_freeze_experiment_context(
    p_experiment_id  uuid,
    p_trigger_ref    text,
    p_assignment_id  uuid  DEFAULT NULL,
    p_prompt_sha256  text  DEFAULT NULL,
    p_model_id       text  DEFAULT NULL,
    p_tool_manifest_sha256    text DEFAULT NULL,
    p_lessons_corpus_sha256   text DEFAULT NULL,
    p_retrieval_corpus_sha256 text DEFAULT NULL,
    p_crop_topology_sha256    text DEFAULT NULL,
    p_context_payload         jsonb DEFAULT NULL,
    p_virtual_prior_template_id uuid DEFAULT NULL
) RETURNS TABLE (snapshot_id uuid, context_revision bigint)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_exp      public.control_experiments%ROWTYPE;
    v_revision bigint;
    v_snapshot uuid;
BEGIN
    IF p_experiment_id IS NULL OR p_trigger_ref IS NULL OR p_trigger_ref = '' THEN
        RAISE EXCEPTION 'fn_freeze_experiment_context requires experiment_id and trigger_ref';
    END IF;
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'experiment % not found', p_experiment_id;
    END IF;
    IF v_exp.status NOT IN ('armed', 'running') THEN
        RAISE EXCEPTION 'experiment % is % — context freezing requires armed/running',
            p_experiment_id, v_exp.status;
    END IF;
    IF p_assignment_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.control_assignments a
         WHERE a.assignment_id = p_assignment_id
           AND a.experiment_id = p_experiment_id
    ) THEN
        RAISE EXCEPTION 'assignment % does not belong to experiment %',
            p_assignment_id, p_experiment_id;
    END IF;

    -- Per-experiment advisory lock serializes revision assignment (this
    -- database deliberately has no btree_gist — see migration 207 header).
    PERFORM pg_advisory_xact_lock(
        hashtext('experiment_context_revision-' || p_experiment_id::text));
    SELECT COALESCE(MAX(s.context_revision), 0) + 1
      INTO v_revision
      FROM public.experiment_context_snapshots s
     WHERE s.experiment_id = p_experiment_id;

    INSERT INTO public.experiment_context_snapshots
        (experiment_id, assignment_id, trigger_ref, prompt_sha256, model_id,
         tool_manifest_sha256, lessons_corpus_sha256, retrieval_corpus_sha256,
         crop_topology_sha256, context_payload, virtual_prior_template_id,
         context_revision)
    VALUES
        (p_experiment_id, p_assignment_id, p_trigger_ref, p_prompt_sha256,
         p_model_id, p_tool_manifest_sha256, p_lessons_corpus_sha256,
         p_retrieval_corpus_sha256, p_crop_topology_sha256, p_context_payload,
         p_virtual_prior_template_id, v_revision)
    RETURNING experiment_context_snapshots.snapshot_id INTO v_snapshot;

    RETURN QUERY SELECT v_snapshot, v_revision;
END;
$$;

COMMENT ON FUNCTION public.fn_freeze_experiment_context(uuid, text, uuid, text,
    text, text, text, text, text, jsonb, uuid) IS
    'Persist one immutable experiment_context_snapshots row per planning '
    'trigger (#585, audit §8.8): prompt/model/tool-manifest hashes, lesson/'
    'retrieval corpus hashes, crop/topology hash, the full context payload, '
    'and the virtual prior. Assigns the next per-experiment context_revision '
    'under an advisory lock; rows are append-only (migration 207 trigger).';

-- ============================================================================
-- 4. Read one frozen snapshot back by experiment + revision
-- ============================================================================

CREATE OR REPLACE FUNCTION public.fn_get_experiment_context_snapshot(
    p_experiment_id    uuid,
    p_context_revision bigint
) RETURNS TABLE (
    snapshot_id             uuid,
    experiment_id           uuid,
    assignment_id           uuid,
    trigger_ref             text,
    context_revision        bigint,
    prompt_sha256           text,
    model_id                text,
    tool_manifest_sha256    text,
    lessons_corpus_sha256   text,
    retrieval_corpus_sha256 text,
    crop_topology_sha256    text,
    context_payload         jsonb,
    virtual_prior_template_id    uuid,
    virtual_selected_template_id uuid,
    created_at              timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT s.snapshot_id,
           s.experiment_id,
           s.assignment_id,
           s.trigger_ref,
           s.context_revision,
           s.prompt_sha256,
           s.model_id,
           s.tool_manifest_sha256,
           s.lessons_corpus_sha256,
           s.retrieval_corpus_sha256,
           s.crop_topology_sha256,
           s.context_payload,
           s.virtual_prior_template_id,
           s.virtual_selected_template_id,
           s.created_at
      FROM public.experiment_context_snapshots s
     WHERE s.experiment_id = p_experiment_id
       AND s.context_revision = p_context_revision;
$$;

COMMENT ON FUNCTION public.fn_get_experiment_context_snapshot(uuid, bigint) IS
    'Read-only retrieval over the frozen experiment context (#585, audit '
    '§8.8): one immutable snapshot keyed by experiment + context revision. '
    'This is the distinct frozen-snapshot search surface the experiment '
    'audience uses instead of the mutable lesson/knowledge tools.';

-- ============================================================================
-- 5. Grants: EXECUTE revoked from PUBLIC (per-role GRANTs live with the
--    experiment role split — db/roles/experiment-roles.sql).
-- ============================================================================

REVOKE ALL ON FUNCTION public.fn_freeze_experiment_context(uuid, text, uuid,
    text, text, text, text, text, text, jsonb, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_get_experiment_context_snapshot(uuid, bigint) FROM PUBLIC;
