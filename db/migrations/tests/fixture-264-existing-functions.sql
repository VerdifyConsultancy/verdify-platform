-- Exact predecessor signatures/bodies extracted from migrations 214/256 for disposable fixture.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_safe_startup_attestation(
    p_device_id text,
    p_experiment_id uuid DEFAULT NULL
) RETURNS TABLE (
    attested_at timestamptz,
    device_id text,
    requested_experiment_id uuid,
    scoped_experiment_id uuid,
    scope_resolved boolean,
    current_lease_generation bigint,
    active_experiment_count integer,
    open_exposure_count integer,
    recovery_pending_count integer,
    experiment_authority_active boolean,
    facility_authority_yielded boolean,
    hold_required boolean,
    attestation_reason text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
BEGIN
    RETURN;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    RAISE EXCEPTION '% is immutable: % blocked (experiment v2)', TG_TABLE_NAME, TG_OP;
END;
$body$;

CREATE FUNCTION public.fn_experiment_v2_stamp_facility_safe_lease()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $stamp$
DECLARE
    v_experiment public.control_experiments%ROWTYPE;
BEGIN
    SELECT * INTO v_experiment FROM public.control_experiments
     WHERE experiment_id = NEW.experiment_id FOR UPDATE;
    IF v_experiment.protocol_version <> 2
       OR v_experiment.admission_state <> 'emergency_hold'
       OR v_experiment.component_enabled
       OR NEW.closed_lease_generation IS NOT NULL THEN
        RAISE EXCEPTION 'facility-safe closure lease must be stamped from yielded authority';
    END IF;
    NEW.closed_lease_generation := v_experiment.lease_generation + 1;
    RETURN NEW;
END;
$stamp$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_work_is_eligible(
    p_experiment_id uuid,
    p_work_id uuid,
    p_expected_lease_generation bigint,
    p_now timestamptz,
    p_mode text
) RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
    SELECT EXISTS (
        SELECT 1
          FROM public.control_experiments e
          JOIN public.experiment_v2_work w ON w.experiment_id = e.experiment_id
         WHERE e.experiment_id = p_experiment_id
           AND w.work_id = p_work_id
           AND e.protocol_version = 2
           AND e.transport_kind = 'legacy_components_v1'
           AND e.execution_phase = w.execution_phase
           AND e.revision_bundle_sha256 = w.revision_bundle_sha256
           AND e.firmware_revision = w.firmware_revision
           AND e.config_revision = w.config_revision
           AND e.registry_revision = w.registry_revision
           AND e.grid_revision = w.grid_revision
           AND e.lease_generation = p_expected_lease_generation
           AND w.lease_generation = p_expected_lease_generation
           AND p_now < w.expires_at
           AND p_now <@ w.valid_range
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_work_events terminal
                WHERE terminal.work_id = w.work_id
                  AND terminal.event_kind IN
                      ('completed', 'failed', 'recovered', 'cancelled', 'superseded'))
           AND CASE p_mode
               WHEN 'readiness' THEN
                   w.operation_kind IN ('shadow_preview', 'commissioning_probe',
                                        'commissioning_canary', 'aa_baseline_rehearsal')
                   AND ((w.operation_kind = 'shadow_preview' AND e.status = 'draft'
                         AND e.admission_state = 'closed') OR
                        (w.operation_kind <> 'shadow_preview' AND e.status = 'draft'
                         AND e.component_enabled AND e.admission_state = 'open'))
               WHEN 'randomized' THEN
                   w.operation_kind = 'randomized_assignment'
                   AND e.execution_phase = 'randomized' AND e.status = 'running'
                   AND e.component_enabled AND e.admission_state = 'open'
               WHEN 'recovery' THEN
                   w.operation_kind = 'baseline_recovery'
                   AND e.status IN ('draft', 'armed', 'running', 'paused')
                   AND e.component_enabled AND e.admission_state = 'baseline_recovery'
               ELSE false
           END
           AND (w.operation_kind = 'shadow_preview' OR w.target_profile = 'baseline' OR EXISTS (
               SELECT 1
                 FROM public.experiment_v2_work recovery
                 JOIN public.experiment_v2_work_events recovered
                   ON recovered.work_id = recovery.work_id
                  AND recovered.event_kind = 'recovered'
                WHERE recovery.parent_work_id = w.work_id
                  AND recovery.operation_kind = 'baseline_recovery'
           ))
    )
$body$;
