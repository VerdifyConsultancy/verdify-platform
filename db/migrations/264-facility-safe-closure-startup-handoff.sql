-- 264: A current-lease immutable facility-safe closure is a lasting handoff
-- for the one device bound to the closed, disabled experiment. Migration 256
-- already stamps the post-closure lease and makes the closure row immutable;
-- the original startup attestation only recognized emergency_hold, so closing
-- the incident would otherwise reassert 98 historical recovery rows and block
-- the ordinary writer. This changes no study, work, alert, or device row.
SET LOCAL search_path = pg_catalog, public, pg_temp;
LOCK TABLE public.schema_migrations IN SHARE ROW EXCLUSIVE MODE;

DO $preflight$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migrations
         WHERE source='db/migrations' AND seq=263
           AND filename='db/migrations/263-mcp-timescale-chunk-boundary-digest.sql'
           AND sha256='9f5fa53cde76224b06865095bfd9a531aadae058f6ca13e50e74ecd95ad5770b'
           AND stamp_method='runner'
    ) OR EXISTS (
        SELECT 1 FROM public.schema_migrations
         WHERE source='db/migrations' AND seq>=264
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p
         WHERE p.oid='public.fn_experiment_v2_safe_startup_attestation(text,uuid)'::regprocedure
           AND p.proowner='verdify_experiment_v2_owner'::regrole
           AND p.prosecdef
           AND p.proconfig=ARRAY['search_path=public, pg_temp']::text[]
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute a
         WHERE a.attrelid='public.experiment_v2_facility_safe_closures'::regclass
           AND a.attname='closed_lease_generation' AND NOT a.attisdropped
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger t
         WHERE t.tgrelid='public.experiment_v2_facility_safe_closures'::regclass
           AND t.tgname='trg_experiment_v2_stamp_facility_safe_lease'
           AND t.tgenabled='O'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger t
         WHERE t.tgrelid='public.experiment_v2_facility_safe_closures'::regclass
           AND t.tgname='trg_experiment_v2_facility_safe_closures_immutable'
           AND t.tgenabled='O'
    ) OR has_function_privilege(
        'verdify_ingestor_runtime_login',
        'public.fn_experiment_v2_safe_startup_attestation(text,uuid)', 'EXECUTE')
       OR NOT has_function_privilege(
        'verdify_experiment_v2_component_executor_login',
        'public.fn_experiment_v2_safe_startup_attestation(text,uuid)', 'EXECUTE')
       OR EXISTS (
        SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts receipt
         WHERE receipt.boundary_sha256 IS DISTINCT FROM
               public.fn_runtime_ordinary_boundary_digest(receipt.login_name)
    ) THEN
        RAISE EXCEPTION '264 refuses ledger, lease-stamp, role, or ordinary-boundary drift';
    END IF;
END;
$preflight$;

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
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_bound_count integer := 0;
    v_unbound_active integer := 0;
    v_active_count integer := 0;
    v_open_count integer := 0;
    v_recovery_count integer := 0;
    v_scope_resolved boolean := false;
    v_authority_active boolean := false;
    v_facility_yielded boolean := false;
    v_closed_facility_handoff boolean := false;
    v_hold boolean := true;
    v_reason text;
    v_now timestamptz := clock_timestamp();
BEGIN
    IF p_device_id IS NULL OR length(p_device_id) = 0 THEN
        RAISE EXCEPTION 'startup attestation requires a device identity';
    END IF;
    SELECT count(*)::integer INTO v_active_count
      FROM public.control_experiments e
     WHERE e.protocol_version = 2 AND e.execution_phase <> 'shadow'
       AND e.status IN ('draft', 'armed', 'running', 'paused');
    IF p_experiment_id IS NOT NULL THEN
        SELECT e.* INTO v_exp FROM public.control_experiments e
         WHERE e.experiment_id = p_experiment_id AND e.protocol_version = 2;
        IF NOT FOUND THEN
            v_reason := 'unknown_requested_experiment';
        ELSE
            v_scope_resolved := true;
        END IF;
    ELSE
        SELECT count(*)::integer INTO v_bound_count
          FROM public.control_experiments e
         WHERE e.protocol_version = 2
           AND (e.status IN ('draft', 'armed', 'running', 'paused') OR EXISTS (
                SELECT 1 FROM public.experiment_v2_exposures x
                LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
                 WHERE x.experiment_id = e.experiment_id
                   AND x.device_id = p_device_id AND c.exposure_id IS NULL) OR EXISTS (
                SELECT 1 FROM public.experiment_v2_work w
                 WHERE w.experiment_id = e.experiment_id
                   AND w.operation_kind = 'baseline_recovery'
                   AND NOT EXISTS (
                       SELECT 1 FROM public.experiment_v2_work_events recovered
                        WHERE recovered.work_id = w.work_id
                          AND recovered.event_kind = 'recovered')))
           AND (EXISTS (
                SELECT 1 FROM public.experiment_v2_runtime_generations g
                 WHERE g.experiment_id = e.experiment_id
                   AND g.device_id = p_device_id) OR EXISTS (
                SELECT 1 FROM public.experiment_v2_exposures x
                 WHERE x.experiment_id = e.experiment_id
                   AND x.device_id = p_device_id));
        SELECT count(*)::integer INTO v_unbound_active
          FROM public.control_experiments e
         WHERE e.protocol_version = 2 AND e.execution_phase <> 'shadow'
           AND e.status IN ('draft', 'armed', 'running', 'paused')
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_runtime_generations g
                WHERE g.experiment_id = e.experiment_id)
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_exposures x
                WHERE x.experiment_id = e.experiment_id);
        IF v_unbound_active > 0 THEN
            v_reason := 'unbound_active_v2_experiment';
        ELSIF v_bound_count > 1 THEN
            v_reason := 'ambiguous_device_scope';
        ELSIF v_bound_count = 0 THEN
            v_scope_resolved := true;
            v_hold := false;
            v_reason := 'no_active_v2_authority';
        ELSE
            SELECT e.* INTO v_exp FROM public.control_experiments e
             WHERE e.protocol_version = 2
               AND (e.status IN ('draft', 'armed', 'running', 'paused') OR EXISTS (
                    SELECT 1 FROM public.experiment_v2_exposures open_x
                    LEFT JOIN public.experiment_v2_exposure_closures close_x
                      USING (exposure_id)
                     WHERE open_x.experiment_id = e.experiment_id
                       AND open_x.device_id = p_device_id
                       AND close_x.exposure_id IS NULL) OR EXISTS (
                    SELECT 1 FROM public.experiment_v2_work pending
                     WHERE pending.experiment_id = e.experiment_id
                       AND pending.operation_kind = 'baseline_recovery'
                       AND NOT EXISTS (
                           SELECT 1 FROM public.experiment_v2_work_events recovered
                            WHERE recovered.work_id = pending.work_id
                              AND recovered.event_kind = 'recovered')))
               AND (EXISTS (
                    SELECT 1 FROM public.experiment_v2_runtime_generations g
                     WHERE g.experiment_id = e.experiment_id
                       AND g.device_id = p_device_id) OR EXISTS (
                    SELECT 1 FROM public.experiment_v2_exposures x
                     WHERE x.experiment_id = e.experiment_id
                       AND x.device_id = p_device_id))
             ORDER BY e.updated_at DESC LIMIT 1;
            v_scope_resolved := true;
        END IF;
    END IF;
    IF v_scope_resolved AND v_exp.experiment_id IS NOT NULL THEN
        SELECT count(*)::integer INTO v_open_count
          FROM public.experiment_v2_exposures x
          LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
         WHERE x.experiment_id = v_exp.experiment_id
           AND x.device_id = p_device_id AND c.exposure_id IS NULL;
        SELECT count(*)::integer INTO v_recovery_count
          FROM public.experiment_v2_work w
         WHERE w.experiment_id = v_exp.experiment_id
           AND w.operation_kind = 'baseline_recovery'
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_work_events recovered
                WHERE recovered.work_id = w.work_id
                  AND recovered.event_kind = 'recovered');
        -- Migration 256 stamps the post-closure lease before the closure
        -- function increments the experiment lease. Historical NULL and stale
        -- closures cannot transfer authority. A later work/fault invalidates
        -- the handoff, as it does the end-of-study closure alternative.
        SELECT EXISTS (
            SELECT 1 FROM public.experiment_v2_facility_safe_closures closure
             WHERE closure.experiment_id = v_exp.experiment_id
               AND closure.safe_state_kind = 'facility_owned_safe_state'
               AND closure.closed_lease_generation = v_exp.lease_generation
               AND closure.closed_at <= v_now
               AND v_exp.admission_state = 'closed'
               AND NOT v_exp.component_enabled
               AND v_open_count = 0
               AND EXISTS (
                   SELECT 1 FROM public.experiment_v2_runtime_generations bound
                    WHERE bound.experiment_id = v_exp.experiment_id
                      AND bound.device_id = p_device_id)
               AND NOT EXISTS (
                   SELECT 1 FROM public.experiment_v2_runtime_generations other
                    WHERE other.experiment_id = v_exp.experiment_id
                      AND other.device_id <> p_device_id)
               AND NOT EXISTS (
                   SELECT 1 FROM public.experiment_v2_work later_work
                    WHERE later_work.experiment_id = v_exp.experiment_id
                      AND later_work.created_at > closure.closed_at)
               AND NOT EXISTS (
                   SELECT 1 FROM public.experiment_v2_runtime_faults later_fault
                    WHERE later_fault.experiment_id = v_exp.experiment_id
                      AND later_fault.recorded_at >= closure.closed_at)
        ) INTO v_closed_facility_handoff;
        v_facility_yielded := v_exp.admission_state = 'emergency_hold' OR
            v_closed_facility_handoff;
        v_authority_active := NOT v_facility_yielded AND
            v_exp.execution_phase <> 'shadow' AND v_exp.component_enabled AND
            v_exp.status IN ('draft', 'armed', 'running', 'paused');
        v_hold := v_open_count > 0 OR
            (NOT v_facility_yielded AND
             (v_authority_active OR v_recovery_count > 0));
        v_reason := CASE
            WHEN v_open_count > 0 THEN 'open_exposure'
            WHEN v_closed_facility_handoff THEN 'facility_safe_closure'
            WHEN v_facility_yielded THEN 'facility_authority_yielded'
            WHEN v_recovery_count > 0 THEN 'baseline_recovery_pending'
            WHEN v_authority_active THEN 'experiment_authority_active'
            WHEN v_exp.status IN ('completed', 'aborted') THEN 'experiment_terminal'
            ELSE 'no_experiment_authority'
        END;
    END IF;
    RETURN QUERY SELECT
        v_now, p_device_id, p_experiment_id, v_exp.experiment_id,
        v_scope_resolved, v_exp.lease_generation, v_active_count,
        v_open_count, v_recovery_count, v_authority_active,
        v_facility_yielded, v_hold, v_reason;
END;
$body$;

DO $postflight$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts receipt
         WHERE receipt.boundary_sha256 IS DISTINCT FROM
               public.fn_runtime_ordinary_boundary_digest(receipt.login_name)
    ) OR has_function_privilege(
        'verdify_ingestor_runtime_login',
        'public.fn_experiment_v2_safe_startup_attestation(text,uuid)', 'EXECUTE')
       OR NOT has_function_privilege(
        'verdify_experiment_v2_component_executor_login',
        'public.fn_experiment_v2_safe_startup_attestation(text,uuid)', 'EXECUTE')
    THEN
        RAISE EXCEPTION '264 changed an ordinary authority boundary or executor privilege';
    END IF;
END;
$postflight$;
