-- 215-experiment-v2-ops-observability.sql
--
-- Blinded-safe operational observability for the confirmed-component v2 lane
-- (#587).  The generalized Grafana/application login receives one read-only,
-- SECURITY DEFINER function and no v2 table privilege.  Randomized identities
-- are masked before their validity window, and the surface contains no secret,
-- X/Y mapping, physical-arm resolution, comparative outcome, or efficacy data.
--
-- NON-SELF-TRANSACTIONAL / ROLLBACK SAFE: additive function plus an idempotent
-- operator-runbook seed.  Functional rollback drops the function after the
-- dashboard and alert consumer have first been rolled back; the inert runbook
-- row may remain as historical operator guidance.

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_ops_status()
RETURNS TABLE (
    experiment_id uuid,
    lifecycle_status text,
    execution_phase text,
    admission_state text,
    component_enabled boolean,
    lease_generation bigint,
    work_id uuid,
    assignment_id uuid,
    operation_kind text,
    future_randomized_identity_masked boolean,
    expected_state_content_sha256 text,
    observed_state_content_sha256 text,
    expected_observed_equal boolean,
    observation_evidence_count integer,
    observation_age_seconds bigint,
    observation_truth text,
    open_exposure_count integer,
    writer_generation bigint,
    connection_generation bigint,
    safety_state text,
    fallback_state text,
    frozen_outcome_count integer,
    expected_outcome_count integer,
    outcomes_complete boolean,
    rollback_ready boolean,
    alert_severity text,
    alert_reason text,
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
    SELECT
        e.experiment_id,
        e.status,
        e.execution_phase,
        e.admission_state,
        e.component_enabled,
        e.lease_generation,
        CASE WHEN selected.future_masked THEN NULL ELSE selected.work_id END,
        CASE WHEN selected.future_masked THEN NULL ELSE selected.assignment_id END,
        selected.operation_kind,
        coalesce(selected.future_masked, false),
        CASE WHEN selected.future_masked THEN NULL
             ELSE selected.target_state_content_sha256 END,
        CASE WHEN selected.future_masked THEN NULL
             ELSE evidence.observed_state_content_sha256 END,
        CASE
            WHEN selected.future_masked OR selected.target_state_content_sha256 IS NULL OR
                 evidence.observed_state_content_sha256 IS NULL THEN NULL
            ELSE selected.target_state_content_sha256 =
                 evidence.observed_state_content_sha256
        END,
        CASE WHEN selected.future_masked THEN 0 ELSE coalesce(receipts.receipt_count, 0) END,
        CASE
            WHEN selected.future_masked OR evidence.observed_at IS NULL THEN NULL
            ELSE greatest(0, floor(extract(epoch FROM (v_now - evidence.observed_at)))::bigint)
        END,
        CASE
            WHEN selected.future_masked THEN 'future_identity_masked'
            WHEN selected.work_id IS NULL THEN 'no_current_work'
            WHEN selected.target_state_content_sha256 IS NULL THEN 'expected_state_missing'
            WHEN evidence.observed_at IS NULL THEN 'unobserved'
            WHEN v_now - evidence.observed_at > interval '90 seconds' THEN 'stale'
            WHEN evidence.observed_state_content_sha256 IS DISTINCT FROM
                 selected.target_state_content_sha256 THEN 'mismatch'
            ELSE 'exact'
        END,
        coalesce(exposures.open_count, 0),
        generation.writer_generation,
        generation.connection_generation,
        CASE
            WHEN e.admission_state = 'emergency_hold' THEN 'facility_emergency_hold'
            WHEN e.admission_state = 'baseline_recovery' THEN 'baseline_recovery'
            WHEN active_fault.recorded_at IS NOT NULL AND
                 active_fault.recorded_at > coalesce(recovered.recorded_at, '-infinity'::timestamptz)
                THEN 'runtime_fault'
            WHEN coalesce(exposures.open_count, 0) > 0 THEN 'exposure_open'
            WHEN e.execution_phase = 'shadow' THEN 'shadow_closed'
            ELSE 'nominal'
        END,
        CASE
            WHEN selected.future_masked THEN 'future_identity_masked'
            WHEN choice.choice_status = 'fallback' THEN 'fallback_used'
            WHEN choice.choice_status = 'selected' THEN 'selected'
            ELSE 'not_applicable'
        END,
        coalesce(outcomes.frozen_count, 0),
        coalesce(outcomes.expected_count, 0),
        coalesce(outcomes.expected_count, 0) > 0 AND
            outcomes.frozen_count = outcomes.expected_count,
        baseline.present AND baseline_confirmation.present AND
            generation.generation_event_id IS NOT NULL AND
            e.admission_state <> 'emergency_hold',
        CASE
            WHEN coalesce(exposures.open_count, 0) > 1 OR
                 (e.status IN ('completed', 'aborted') AND e.component_enabled) OR
                 expired_work.present OR
                 (coalesce(exposures.open_count, 0) > 0 AND
                  selected.work_expired) OR
                 (e.component_enabled AND
                  NOT (baseline.present AND baseline_confirmation.present AND
                       generation.generation_event_id IS NOT NULL AND
                       e.admission_state <> 'emergency_hold')) OR
                 e.admission_state = 'emergency_hold' OR
                 (pre_mismatch.recorded_at IS NOT NULL AND
                  pre_mismatch.recorded_at >
                      coalesce(recovered.recorded_at, '-infinity'::timestamptz)) OR
                 (coalesce(exposures.open_count, 0) > 0 AND
                  (evidence.observed_at IS NULL OR
                   v_now - evidence.observed_at > interval '90 seconds' OR
                   evidence.observed_state_content_sha256 IS DISTINCT FROM
                       selected.target_state_content_sha256)) OR
                 (e.admission_state = 'open' AND selected.work_id IS NULL AND
                  coalesce(exposures.open_count, 0) = 0) OR
                 (e.status = 'completed' AND
                  NOT (coalesce(outcomes.expected_count, 0) > 0 AND
                       outcomes.frozen_count = outcomes.expected_count))
                THEN 'critical'
            WHEN e.admission_state = 'baseline_recovery' OR
                 (active_fault.recorded_at IS NOT NULL AND
                  active_fault.recorded_at >
                      coalesce(recovered.recorded_at, '-infinity'::timestamptz))
                THEN 'warning'
            ELSE NULL
        END,
        CASE
            WHEN coalesce(exposures.open_count, 0) > 1
                THEN 'multiple_open_exposures'
            WHEN e.status IN ('completed', 'aborted') AND e.component_enabled
                THEN 'terminal_experiment_capability_enabled'
            WHEN coalesce(exposures.open_count, 0) > 0 AND selected.work_expired
                THEN 'open_exposure_work_expired'
            WHEN expired_work.present
                THEN 'expired_work_not_terminal'
            WHEN e.component_enabled AND NOT baseline.present
                THEN 'baseline_artifact_missing'
            WHEN e.component_enabled AND NOT baseline_confirmation.present
                THEN 'confirmed_baseline_recovery_missing'
            WHEN e.component_enabled AND generation.generation_event_id IS NULL
                THEN 'runtime_generation_missing'
            WHEN e.admission_state = 'emergency_hold'
                THEN 'facility_emergency_hold'
            WHEN pre_mismatch.recorded_at IS NOT NULL AND
                 pre_mismatch.recorded_at >
                     coalesce(recovered.recorded_at, '-infinity'::timestamptz)
                THEN 'preexposure_state_mismatch'
            WHEN coalesce(exposures.open_count, 0) > 0 AND evidence.observed_at IS NULL
                THEN 'open_exposure_observation_missing'
            WHEN coalesce(exposures.open_count, 0) > 0 AND
                 v_now - evidence.observed_at > interval '90 seconds'
                THEN 'open_exposure_observation_stale'
            WHEN coalesce(exposures.open_count, 0) > 0 AND
                 evidence.observed_state_content_sha256 IS DISTINCT FROM
                     selected.target_state_content_sha256
                THEN 'open_exposure_state_mismatch'
            WHEN e.admission_state = 'open' AND selected.work_id IS NULL AND
                 coalesce(exposures.open_count, 0) = 0
                THEN 'open_admission_without_current_work'
            WHEN e.status = 'completed' AND
                 NOT (coalesce(outcomes.expected_count, 0) > 0 AND
                      outcomes.frozen_count = outcomes.expected_count)
                THEN 'completed_with_incomplete_outcomes'
            WHEN e.admission_state = 'baseline_recovery'
                THEN 'baseline_recovery_in_progress'
            WHEN active_fault.recorded_at IS NOT NULL AND
                 active_fault.recorded_at >
                     coalesce(recovered.recorded_at, '-infinity'::timestamptz)
                THEN 'runtime_fault_requires_recovery'
            ELSE NULL
        END,
        v_now
      FROM public.control_experiments e
      LEFT JOIN LATERAL (
          SELECT w.work_id, w.assignment_id, w.operation_kind,
                 w.target_state_content_sha256,
                 (v_now >= w.expires_at OR v_now >= upper(w.valid_range)) AS work_expired,
                 (w.operation_kind = 'randomized_assignment' AND
                  lower(w.valid_range) > v_now) AS future_masked
            FROM public.experiment_v2_work w
           WHERE w.experiment_id = e.experiment_id
             AND (
                 EXISTS (
                     SELECT 1
                       FROM public.experiment_v2_exposures x
                       LEFT JOIN public.experiment_v2_exposure_closures c
                         USING (exposure_id)
                      WHERE x.work_id = w.work_id AND c.exposure_id IS NULL)
                 OR (
                     v_now < w.expires_at AND
                     (v_now <@ w.valid_range OR lower(w.valid_range) > v_now) AND
                     NOT EXISTS (
                         SELECT 1
                           FROM public.experiment_v2_work_events terminal
                          WHERE terminal.work_id = w.work_id
                            AND terminal.event_kind IN
                                ('completed', 'failed', 'recovered',
                                 'cancelled', 'superseded'))))
           ORDER BY
             CASE WHEN EXISTS (
                 SELECT 1
                   FROM public.experiment_v2_exposures x
                   LEFT JOIN public.experiment_v2_exposure_closures c
                     USING (exposure_id)
                  WHERE x.work_id = w.work_id AND c.exposure_id IS NULL)
                 THEN 0 ELSE 1 END,
             CASE WHEN v_now <@ w.valid_range THEN 0 ELSE 1 END,
             CASE w.operation_kind WHEN 'baseline_recovery' THEN 0 ELSE 1 END,
             lower(w.valid_range), w.created_at
           LIMIT 1
      ) selected ON true
      LEFT JOIN LATERAL (
          SELECT count(*)::integer AS open_count
            FROM public.experiment_v2_exposures x
            LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
           WHERE x.experiment_id = e.experiment_id AND c.exposure_id IS NULL
      ) exposures ON true
      LEFT JOIN LATERAL (
          SELECT g.generation_event_id, g.writer_generation,
                 g.connection_generation
            FROM public.experiment_v2_runtime_generations g
           WHERE g.experiment_id = e.experiment_id
           ORDER BY g.generation_event_id DESC
           LIMIT 1
      ) generation ON true
      LEFT JOIN LATERAL (
          SELECT q.observed_state_content_sha256, q.observed_at
            FROM (
                SELECT s.observed_state_content_sha256,
                       s.recorded_at AS observed_at,
                       0 AS source_order
                  FROM public.experiment_v2_runtime_snapshots s
                 WHERE s.work_id = selected.work_id
                UNION ALL
                SELECT r.policy_state_content_sha256,
                       r.persisted_at,
                       1
                  FROM public.experiment_v2_observation_receipts r
                 WHERE r.work_id = selected.work_id
            ) q
           ORDER BY q.observed_at DESC, q.source_order
           LIMIT 1
      ) evidence ON true
      LEFT JOIN LATERAL (
          SELECT count(*)::integer AS receipt_count
            FROM public.experiment_v2_observation_receipts r
           WHERE r.work_id = selected.work_id
      ) receipts ON true
      LEFT JOIN public.experiment_v2_selector_choices choice
        ON choice.assignment_id = selected.assignment_id
      LEFT JOIN LATERAL (
          SELECT count(*)::integer AS expected_count,
                 count(f.assignment_id)::integer AS frozen_count
            FROM public.control_assignments a
            LEFT JOIN public.experiment_v2_outcome_freezes f USING (assignment_id)
           WHERE a.experiment_id = e.experiment_id
      ) outcomes ON true
      LEFT JOIN LATERAL (
          SELECT EXISTS (
              SELECT 1 FROM public.experiment_v2_state_artifacts s
               WHERE s.experiment_id = e.experiment_id AND s.profile = 'baseline')
              AS present
      ) baseline ON true
      LEFT JOIN LATERAL (
          SELECT EXISTS (
              SELECT 1
                FROM public.experiment_v2_work w
                JOIN public.experiment_v2_state_artifacts s
                  ON s.experiment_id = w.experiment_id
                 AND s.profile = 'baseline'
                 AND s.state_content_sha256 = w.target_state_content_sha256
               WHERE w.experiment_id = e.experiment_id
                 AND w.target_profile = 'baseline'
                 AND w.lease_generation = e.lease_generation
                 AND w.revision_bundle_sha256 = e.revision_bundle_sha256
                 AND w.firmware_revision = e.firmware_revision
                 AND w.config_revision = e.config_revision
                 AND w.registry_revision = e.registry_revision
                 AND w.grid_revision = e.grid_revision
                 AND EXISTS (
                     SELECT 1
                       FROM public.experiment_v2_work_events terminal
                      WHERE terminal.experiment_id = w.experiment_id
                        AND terminal.work_id = w.work_id
                        AND terminal.event_kind IN ('completed', 'recovered'))
                 AND (SELECT count(*)
                        FROM public.experiment_v2_observation_receipts receipt
                       WHERE receipt.experiment_id = w.experiment_id
                         AND receipt.work_id = w.work_id
                         AND receipt.policy_state_content_sha256 =
                             w.target_state_content_sha256) >= 2)
              AS present
      ) baseline_confirmation ON true
      LEFT JOIN LATERAL (
          SELECT EXISTS (
              SELECT 1
                FROM public.experiment_v2_work w
               WHERE w.experiment_id = e.experiment_id
                 AND (v_now >= w.expires_at OR v_now >= upper(w.valid_range))
                 AND NOT EXISTS (
                     SELECT 1
                       FROM public.experiment_v2_work_events terminal
                      WHERE terminal.experiment_id = w.experiment_id
                        AND terminal.work_id = w.work_id
                        AND terminal.event_kind IN
                            ('completed', 'failed', 'recovered',
                             'cancelled', 'superseded')))
              AS present
      ) expired_work ON true
      LEFT JOIN LATERAL (
          SELECT f.recorded_at
            FROM public.experiment_v2_runtime_faults f
           WHERE f.experiment_id = e.experiment_id
           ORDER BY f.recorded_at DESC, f.fault_report_id DESC
           LIMIT 1
      ) active_fault ON true
      LEFT JOIN LATERAL (
          SELECT mismatch.recorded_at
            FROM public.experiment_v2_preexposure_mismatch_epochs mismatch
           WHERE mismatch.experiment_id = e.experiment_id
           ORDER BY mismatch.recorded_at DESC, mismatch.source_epoch_id DESC
           LIMIT 1
      ) pre_mismatch ON true
      LEFT JOIN LATERAL (
          SELECT ev.recorded_at
            FROM public.experiment_v2_work_events ev
           WHERE ev.experiment_id = e.experiment_id
             AND ev.event_kind = 'recovered'
           ORDER BY ev.recorded_at DESC, ev.work_event_id DESC
           LIMIT 1
      ) recovered ON true
     WHERE e.protocol_version = 2 AND e.kind = 'randomized'
     ORDER BY e.created_at, e.experiment_id;
END;
$body$;

REVOKE ALL ON FUNCTION public.fn_experiment_v2_ops_status() FROM PUBLIC;
ALTER FUNCTION public.fn_experiment_v2_ops_status()
    OWNER TO verdify_experiment_v2_owner;

-- The existing Grafana and ordinary ingestor datasource use the `verdify`
-- login.  Grant only this safe function if that deployment role is present;
-- no base relation or v2 duty role is inherited.
DO $grant_observer$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verdify') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.fn_experiment_v2_ops_status() TO verdify';
    END IF;
END;
$grant_observer$;

DO $runbook$
BEGIN
    -- Reduced migration fixtures may intentionally omit the pre-existing
    -- Slack surface.  The production schema has it; keep the v2 projection
    -- independently replayable when it does not.
    IF to_regclass('public.slack_alert_runbooks') IS NOT NULL THEN
        INSERT INTO public.slack_alert_runbooks
            (greenhouse_id, alert_type, severity, title, summary, runbook_url,
             steps, is_active)
        VALUES
            ('vallery', 'component_experiment_integrity', NULL,
             'Confirmed-component experiment integrity',
             'A database-derived safety, authority, readback, exposure, outcome, or rollback gate requires action before the experiment may advance.',
             'https://graphs.verdify.ai/d/confirmed-component-experiment-v2',
             ARRAY[
               'Keep or move admission closed before investigating; never use a ConfigMap phase change as the response.',
               'If an exposure or runtime fault is involved, retain the all-component writer hold and complete confirmed baseline recovery before ordinary-writer restoration.',
               'If facility emergency authority is active, yield to the facility owner and do not issue automatic experiment writes.',
               'Use the blinded-safe board and immutable experiment ledger to record the exact recovery evidence before resolving the alert.'
             ]::text[],
             true)
        ON CONFLICT (greenhouse_id, lower(alert_type), COALESCE(severity, ''))
            WHERE is_active
        DO UPDATE SET
            title = EXCLUDED.title,
            summary = EXCLUDED.summary,
            runbook_url = EXCLUDED.runbook_url,
            steps = EXCLUDED.steps,
            updated_at = clock_timestamp();
    END IF;
END;
$runbook$;
