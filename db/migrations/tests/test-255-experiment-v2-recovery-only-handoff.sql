-- Disposable PostgreSQL 16 fixture: a closed Gate R study with 98 historical
-- failed recovery rows. No production connection or device client is used.
BEGIN;
CREATE ROLE verdify_experiment_v2_owner;
CREATE ROLE verdify_experiment_lifecycle;
CREATE ROLE verdify_experiment_component_executor;
CREATE ROLE verdify_experiment_v2_component_executor_login;
CREATE ROLE verdify_ingestor_runtime_login;
CREATE ROLE verdify_api_runtime_login;
GRANT verdify_experiment_component_executor
    TO verdify_experiment_v2_component_executor_login;

CREATE TABLE public.control_experiments (
    experiment_id uuid PRIMARY KEY, protocol_version integer, kind text,
    status text, execution_phase text, admission_state text,
    component_enabled boolean, lease_generation bigint,
    revision_bundle_sha256 text, firmware_revision text,
    config_revision text, registry_revision text, grid_revision text,
    updated_at timestamptz
);
CREATE TABLE public.experiment_v2_work (
    work_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), experiment_id uuid,
    parent_work_id uuid, execution_phase text, operation_kind text,
    target_profile text, target_state_content_sha256 text,
    revision_bundle_sha256 text, lease_generation bigint,
    valid_range tstzrange, expires_at timestamptz, created_at timestamptz
);
CREATE TABLE public.experiment_v2_work_events (
    experiment_id uuid, work_id uuid, event_kind text,
    recorded_at timestamptz
);
CREATE TABLE public.experiment_v2_state_artifacts (
    state_artifact_id uuid, experiment_id uuid, revision_bundle_sha256 text,
    profile text, wire_vector bytea, state_content_sha256 text
);
CREATE TABLE public.experiment_v2_runtime_faults (
    experiment_id uuid, fault_report_id uuid, recorded_at timestamptz
);
CREATE TABLE public.experiment_v2_exposures (
    exposure_id uuid, experiment_id uuid
);
CREATE TABLE public.experiment_v2_exposure_closures (exposure_id uuid);
CREATE TABLE public.experiment_v2_runtime_generations (
    generation_event_id bigint, experiment_id uuid, device_id text,
    runtime_instance_id uuid, writer_generation bigint,
    connection_generation bigint
);
CREATE TABLE public.experiment_v2_observation_epochs (
    source_epoch_id uuid PRIMARY KEY, experiment_id uuid, work_id uuid,
    bundle_id uuid, wire_vector bytea, observations jsonb,
    first_observed_at timestamptz, last_observed_at timestamptz,
    runtime_instance_id uuid, writer_generation bigint,
    connection_generation bigint
);
CREATE TABLE public.experiment_v2_observation_receipts (
    source_epoch_id uuid, experiment_id uuid, work_id uuid,
    bundle_id uuid, policy_state_content_sha256 text
);
CREATE TABLE public.experiment_events (
    experiment_id uuid, event_kind text, severity text, actor text,
    detail jsonb
);
CREATE TABLE public.runtime_ordinary_login_attestation_receipts (
    login_name text, boundary_sha256 bytea
);
GRANT ALL ON ALL TABLES IN SCHEMA public TO verdify_experiment_v2_owner;

INSERT INTO public.control_experiments VALUES (
    '45039c86-c1d9-52f6-a0a9-d94a17bc4b14', 2, 'randomized',
    'draft', 'shadow', 'closed', false, 17,
    '3e26a2da1863bd14d255d58fe00d8e94aae9226f9b588ddd9d4f993e6bcb7016',
    'fw', 'cfg', 'registry', 'grid', clock_timestamp());
INSERT INTO public.experiment_v2_state_artifacts VALUES (
    gen_random_uuid(), '45039c86-c1d9-52f6-a0a9-d94a17bc4b14',
    '3e26a2da1863bd14d255d58fe00d8e94aae9226f9b588ddd9d4f993e6bcb7016',
    'baseline', repeat('a',178)::bytea, repeat('b',64));
INSERT INTO public.experiment_v2_runtime_faults VALUES (
    '45039c86-c1d9-52f6-a0a9-d94a17bc4b14',
    '725306a0-9b4b-4083-a3ba-8912308e1d55',
    '2026-08-30 01:12:38.787056+00');
INSERT INTO public.experiment_v2_runtime_generations VALUES (
    1, '45039c86-c1d9-52f6-a0a9-d94a17bc4b14', 'esp32-vallery',
    '11111111-1111-4111-8111-111111111111', 1, 1);
WITH prior AS (
    INSERT INTO public.experiment_v2_work
        (experiment_id, execution_phase, operation_kind, target_profile,
         target_state_content_sha256, revision_bundle_sha256,
         lease_generation, valid_range, expires_at, created_at)
    SELECT '45039c86-c1d9-52f6-a0a9-d94a17bc4b14', 'commissioning',
           'baseline_recovery', 'baseline', repeat('b',64),
           '3e26a2da1863bd14d255d58fe00d8e94aae9226f9b588ddd9d4f993e6bcb7016',
           1, tstzrange('2026-08-28', '2026-08-29', '[)'),
           '2026-08-29', '2026-08-28'::timestamptz
      FROM generate_series(1,98)
    RETURNING experiment_id, work_id
)
INSERT INTO public.experiment_v2_work_events
SELECT experiment_id, work_id, 'failed', '2026-08-29' FROM prior;
INSERT INTO public.runtime_ordinary_login_attestation_receipts VALUES
    ('verdify_api_runtime_login',
     decode('9b5e6841cebc95cff6020f1a899e65c1f504d927844f66f7045ecfb1eef23451','hex')),
    ('verdify_ingestor_runtime_login',
     decode('52c1d03192df977396e4e61075616ee18ecb66c6b771005261c510980e97adc3','hex'));

CREATE FUNCTION public.fn_runtime_ordinary_boundary_digest(p_login text)
RETURNS bytea LANGUAGE sql AS $fn$
    SELECT boundary_sha256 FROM public.runtime_ordinary_login_attestation_receipts
    WHERE login_name = p_login
$fn$;
CREATE FUNCTION public.fn_experiment_v2_gate_p_sealed_recovery_target(p_id uuid)
RETURNS boolean LANGUAGE sql AS $fn$
    SELECT EXISTS (
        SELECT 1 FROM public.control_experiments e
        WHERE e.experiment_id = p_id AND e.execution_phase = 'shadow'
          AND e.admission_state = 'closed' AND NOT e.component_enabled
          AND e.lease_generation = 17
          AND NOT EXISTS (
              SELECT 1 FROM public.experiment_v2_work_events ev
              WHERE ev.experiment_id = p_id AND ev.event_kind = 'recovered'
                AND ev.recorded_at > '2026-08-30'::timestamptz))
$fn$;
CREATE FUNCTION public.fn_experiment_v2_request_recovery_at(
    p_id uuid, p_parent uuid, p_range tstzrange, p_expire timestamptz,
    p_reason text, p_now timestamptz, p_actor text)
RETURNS uuid LANGUAGE plpgsql AS $fn$
DECLARE v_id uuid;
BEGIN
    INSERT INTO public.experiment_v2_work
        (experiment_id, parent_work_id, execution_phase, operation_kind,
         target_profile, target_state_content_sha256, revision_bundle_sha256,
         lease_generation, valid_range, expires_at, created_at)
    SELECT p_id, p_parent, e.execution_phase, 'baseline_recovery',
           'baseline', s.state_content_sha256, e.revision_bundle_sha256,
           e.lease_generation, p_range, p_expire, p_now
      FROM public.control_experiments e
      JOIN public.experiment_v2_state_artifacts s USING (experiment_id)
     WHERE e.experiment_id = p_id AND s.profile = 'baseline'
    RETURNING work_id INTO v_id;
    RETURN v_id;
END
$fn$;
CREATE FUNCTION public.fn_experiment_v2_set_admission(
    p_id uuid, p_target text, p_actor text, p_reason text)
RETURNS public.control_experiments LANGUAGE plpgsql AS $fn$
DECLARE v_row public.control_experiments%ROWTYPE;
BEGIN
    UPDATE public.control_experiments SET admission_state = p_target
    WHERE experiment_id = p_id RETURNING * INTO v_row;
    RETURN v_row;
END
$fn$;
CREATE FUNCTION public.fn_experiment_v2_safe_startup_attestation(
    p_device_id text, p_id uuid DEFAULT NULL)
RETURNS TABLE (
    attested_at timestamptz, device_id text, requested_experiment_id uuid,
    scoped_experiment_id uuid, scope_resolved boolean,
    current_lease_generation bigint, active_experiment_count integer,
    open_exposure_count integer, recovery_pending_count integer,
    experiment_authority_active boolean, facility_authority_yielded boolean,
    hold_required boolean, attestation_reason text)
LANGUAGE sql AS $fn$
    SELECT clock_timestamp(), p_device_id, p_id, e.experiment_id, true,
           e.lease_generation, CASE WHEN e.execution_phase = 'shadow' THEN 0 ELSE 1 END,
           0, (SELECT count(*)::integer FROM public.experiment_v2_work w
               WHERE w.experiment_id = e.experiment_id AND w.operation_kind = 'baseline_recovery'
                 AND NOT EXISTS (SELECT 1 FROM public.experiment_v2_work_events ev
                     WHERE ev.work_id = w.work_id AND ev.event_kind = 'recovered')),
           e.component_enabled, false, true, 'baseline_recovery_pending'::text
      FROM public.control_experiments e WHERE e.experiment_id =
        coalesce(p_id, '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid)
$fn$;
CREATE FUNCTION public.fn_experiment_v2_ops_status()
RETURNS TABLE (experiment_id uuid, safety_state text, alert_reason text,
               open_exposure_count integer, observation_truth text)
LANGUAGE sql AS $fn$
    SELECT e.experiment_id,
           CASE WHEN f.recorded_at > coalesce(r.recorded_at, '-infinity'::timestamptz)
                THEN 'runtime_fault' ELSE 'shadow_closed' END,
           CASE WHEN f.recorded_at > coalesce(r.recorded_at, '-infinity'::timestamptz)
                THEN 'runtime_fault_requires_recovery'::text ELSE NULL::text END,
           0, 'no_current_work'::text
      FROM public.control_experiments e
      LEFT JOIN LATERAL (SELECT max(recorded_at) recorded_at
          FROM public.experiment_v2_runtime_faults WHERE experiment_id = e.experiment_id) f ON true
      LEFT JOIN LATERAL (SELECT max(recorded_at) recorded_at
          FROM public.experiment_v2_work_events WHERE experiment_id = e.experiment_id
          AND event_kind = 'recovered') r ON true
$fn$;

\i db/migrations/255-experiment-v2-recovery-only-handoff.sql

SET ROLE verdify_ingestor_runtime_login;
DO $denied$
BEGIN
    BEGIN
        PERFORM public.fn_experiment_v2_recovery_only_begin(
            '45039c86-c1d9-52f6-a0a9-d94a17bc4b14',
            '725306a0-9b4b-4083-a3ba-8912308e1d55',
            'ace2d26c-539d-4007-ad9c-d25ad812644f',
            'unauthorized', tstzrange(clock_timestamp(),
                clock_timestamp() + interval '6 minutes', '[)'), 'fixture');
        RAISE EXCEPTION 'ordinary ingestor invoked recovery-only begin';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END
$denied$;
RESET ROLE;

DO $test$
DECLARE
    v_id uuid := '45039c86-c1d9-52f6-a0a9-d94a17bc4b14';
    v_work uuid;
    v_h public.experiment_v2_recovery_only_handoffs%ROWTYPE;
    v_att record;
BEGIN
    IF has_function_privilege('verdify_api_runtime_login',
        'public.fn_experiment_v2_recovery_only_begin(uuid,uuid,uuid,text,tstzrange,text)',
        'EXECUTE') OR has_function_privilege('verdify_ingestor_runtime_login',
        'public.fn_experiment_v2_recovery_only_finish(uuid,uuid,text,text)', 'EXECUTE')
       OR has_function_privilege('verdify_ingestor_runtime_login',
        'public.fn_experiment_v2_safe_startup_after_recovery_only(text,uuid)', 'EXECUTE')
       OR NOT has_function_privilege('verdify_experiment_lifecycle',
        'public.fn_experiment_v2_recovery_only_begin(uuid,uuid,uuid,text,tstzrange,text)',
        'EXECUTE') THEN
        RAISE EXCEPTION 'recovery-only privilege boundary failed';
    END IF;
    SELECT * INTO v_att FROM public.fn_experiment_v2_safe_startup_after_recovery_only(
        'esp32-vallery', NULL);
    IF NOT v_att.hold_required OR v_att.recovery_pending_count <> 98 THEN
        RAISE EXCEPTION 'pre-recovery hold lost';
    END IF;
    BEGIN
        PERFORM public.fn_experiment_v2_recovery_only_begin(
            v_id, gen_random_uuid(), 'ace2d26c-539d-4007-ad9c-d25ad812644f',
            'test-authorization', tstzrange(clock_timestamp(),
                clock_timestamp() + interval '6 minutes', '[)'), 'fixture');
        RAISE EXCEPTION 'wrong latest fault was accepted';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM = 'wrong latest fault was accepted' THEN RAISE; END IF;
    END;
    v_work := public.fn_experiment_v2_recovery_only_begin(
        v_id, '725306a0-9b4b-4083-a3ba-8912308e1d55',
        'ace2d26c-539d-4007-ad9c-d25ad812644f',
        'test-authorization', tstzrange(clock_timestamp() - interval '1 minute',
            clock_timestamp() + interval '6 minutes', '[)'), 'fixture');
    SELECT * INTO v_h FROM public.experiment_v2_recovery_only_handoffs
    WHERE experiment_id = v_id;
    IF v_h.work_id <> v_work OR v_h.recovery_lease_generation <> 18
       OR (SELECT count(*) FROM public.experiment_v2_work
           WHERE experiment_id = v_id AND lease_generation = 18) <> 1
       OR EXISTS (SELECT 1 FROM public.experiment_v2_work
           WHERE experiment_id = v_id AND lease_generation = 18
             AND (target_profile <> 'baseline' OR operation_kind <> 'baseline_recovery'))
       OR EXISTS (SELECT 1 FROM public.experiment_v2_exposures) THEN
        RAISE EXCEPTION 'begin created work outside one baseline-only recovery';
    END IF;
    BEGIN
        PERFORM public.fn_experiment_v2_recovery_only_finish(
            v_id, v_work, 'test-authorization', 'fixture');
        RAISE EXCEPTION 'finish without real recovered event was accepted';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM = 'finish without real recovered event was accepted' THEN RAISE; END IF;
    END;
END
$test$;

-- The fixture advances only its own clock-bearing row to avoid a 30-second
-- wall-clock sleep; actual finish still checks two distinct raw epoch times.
UPDATE public.experiment_v2_recovery_only_handoffs
SET began_at = clock_timestamp() - interval '90 seconds';
INSERT INTO public.experiment_v2_observation_epochs
SELECT gen_random_uuid(), h.experiment_id, h.work_id,
       '22222222-2222-4222-8222-222222222222', repeat('a',178)::bytea,
       jsonb_agg(jsonb_build_object('key', n)) OVER (),
       clock_timestamp() - interval '75 seconds',
       clock_timestamp() - interval '70 seconds',
       '11111111-1111-4111-8111-111111111111', 1, 1
FROM public.experiment_v2_recovery_only_handoffs h
CROSS JOIN generate_series(1,48) n LIMIT 1;
INSERT INTO public.experiment_v2_observation_epochs
SELECT gen_random_uuid(), h.experiment_id, h.work_id,
       '22222222-2222-4222-8222-222222222222', repeat('a',178)::bytea,
       (SELECT jsonb_agg(jsonb_build_object('key', n)) FROM generate_series(1,48) n),
       clock_timestamp() - interval '25 seconds',
       clock_timestamp() - interval '20 seconds',
       '11111111-1111-4111-8111-111111111111', 1, 1
FROM public.experiment_v2_recovery_only_handoffs h;
INSERT INTO public.experiment_v2_observation_receipts
SELECT e.source_epoch_id, e.experiment_id, e.work_id, e.bundle_id,
       h.baseline_state_content_sha256
FROM public.experiment_v2_observation_epochs e
JOIN public.experiment_v2_recovery_only_handoffs h USING (experiment_id, work_id);
INSERT INTO public.experiment_v2_work_events
SELECT experiment_id, work_id, 'recovered', clock_timestamp()
FROM public.experiment_v2_recovery_only_handoffs;

UPDATE public.experiment_v2_observation_epochs
   SET observations = observations - 47
 WHERE last_observed_at =
       (SELECT min(last_observed_at) FROM public.experiment_v2_observation_epochs);
DO $incomplete$
DECLARE
    v_id uuid := '45039c86-c1d9-52f6-a0a9-d94a17bc4b14';
    v_work uuid;
BEGIN
    SELECT work_id INTO v_work FROM public.experiment_v2_recovery_only_handoffs;
    BEGIN
        PERFORM public.fn_experiment_v2_recovery_only_finish(
            v_id, v_work, 'test-authorization', 'fixture');
        RAISE EXCEPTION '47-field epoch was accepted';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM = '47-field epoch was accepted' THEN RAISE; END IF;
    END;
END
$incomplete$;
UPDATE public.experiment_v2_observation_epochs
   SET observations =
       (SELECT jsonb_agg(jsonb_build_object('key', n))
          FROM generate_series(1,48) n)
 WHERE jsonb_array_length(observations) = 47;

DO $test$
DECLARE
    v_id uuid := '45039c86-c1d9-52f6-a0a9-d94a17bc4b14';
    v_work uuid;
    v_att record;
BEGIN
    SELECT work_id INTO v_work FROM public.experiment_v2_recovery_only_handoffs;
    IF NOT public.fn_experiment_v2_recovery_only_finish(
        v_id, v_work, 'test-authorization', 'fixture') THEN
        RAISE EXCEPTION 'finish refused complete recovery';
    END IF;
    SELECT * INTO v_att FROM public.fn_experiment_v2_safe_startup_after_recovery_only(
        'esp32-vallery', NULL);
    IF v_att.hold_required OR v_att.recovery_pending_count <> 98
       OR v_att.attestation_reason <> 'recovery_only_baseline_confirmed'
       OR (SELECT count(*) FROM public.experiment_v2_work_events
           WHERE event_kind = 'failed') <> 98
       OR (SELECT count(*) FROM public.experiment_v2_runtime_faults) <> 1
       OR (SELECT safety_state FROM public.fn_experiment_v2_ops_status()
           WHERE experiment_id = v_id) <> 'shadow_closed'
       OR public.fn_experiment_v2_gate_p_sealed_recovery_target(v_id)
       OR NOT public.fn_experiment_v2_gate_p_recovery_handoff_ready(v_id) THEN
        RAISE EXCEPTION 'truthful post-recovery handoff failed';
    END IF;
    INSERT INTO public.experiment_v2_runtime_faults VALUES
        (v_id, gen_random_uuid(), clock_timestamp() + interval '1 second');
    SELECT * INTO v_att FROM public.fn_experiment_v2_safe_startup_after_recovery_only(
        'esp32-vallery', NULL);
    IF NOT v_att.hold_required
       OR public.fn_experiment_v2_gate_p_recovery_handoff_ready(v_id)
       OR public.fn_experiment_v2_recovery_only_finish(
           v_id, v_work, 'test-authorization', 'fixture') THEN
        RAISE EXCEPTION 'newer fault did not restore hold';
    END IF;
END
$test$;
ROLLBACK;
