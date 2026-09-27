-- Run only in a disposable PostgreSQL database. This builds the narrow tables
-- the one-use repair reads; no production connection or device client is used.
BEGIN;
CREATE ROLE verdify_experiment_v2_owner;
CREATE ROLE verdify_experiment_lifecycle;
CREATE TABLE public.control_experiments (
    experiment_id uuid PRIMARY KEY, protocol_version integer, kind text,
    status text, execution_phase text, admission_state text,
    component_enabled boolean, lease_generation bigint,
    revision_bundle_sha256 text
);
CREATE TABLE public.experiment_v2_exposures (
    exposure_id uuid, experiment_id uuid, work_id uuid
);
CREATE TABLE public.experiment_v2_exposure_closures (exposure_id uuid);
CREATE TABLE public.experiment_v2_direct_proof_receipts (experiment_id uuid);
CREATE TABLE public.experiment_v2_direct_proof_emergency_resolutions (
    resolution_id uuid, resolution_kind text
);
CREATE TABLE public.experiment_v2_direct_proof_emergency_recovery_receipts (
    resolution_id uuid, experiment_id uuid, authorization_id uuid,
    recovery_work_id uuid, recovery_evidence_sha256 text
);
CREATE TABLE public.experiment_v2_work (
    work_id uuid PRIMARY KEY, experiment_id uuid, lease_generation bigint,
    created_at timestamptz, valid_range tstzrange, expires_at timestamptz,
    parent_work_id uuid, operation_kind text, target_profile text,
    created_by text, revision_bundle_sha256 text,
    target_state_content_sha256 text
);
CREATE TABLE public.experiment_v2_delivery_bundles (work_id uuid, bundle_id uuid);
CREATE TABLE public.experiment_v2_observation_receipts (work_id uuid, receipt_id uuid);
CREATE TABLE public.experiment_v2_runtime_faults (recovery_work_id uuid);
CREATE TABLE public.experiment_v2_work_events (
    work_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_id uuid, work_id uuid, event_kind text, worker_ref text,
    claim_expires_at timestamptz, detail jsonb, recorded_at timestamptz
);
GRANT ALL ON ALL TABLES IN SCHEMA public TO verdify_experiment_v2_owner;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO verdify_experiment_v2_owner;
CREATE FUNCTION public.fn_experiment_v2_ops_status()
RETURNS TABLE(experiment_id uuid, safety_state text, alert_reason text,
              open_exposure_count integer, observation_truth text)
LANGUAGE sql STABLE AS $ops$
    SELECT e.experiment_id, 'runtime_fault'::text,
           CASE WHEN EXISTS (
               SELECT 1 FROM public.experiment_v2_work w
               WHERE w.experiment_id = e.experiment_id
                 AND now() >= w.expires_at
                 AND NOT EXISTS (
                     SELECT 1 FROM public.experiment_v2_work_events terminal
                     WHERE terminal.work_id = w.work_id
                       AND terminal.event_kind IN
                           ('completed','failed','recovered','cancelled','superseded')))
               THEN 'expired_work_not_terminal'::text
               ELSE 'runtime_fault_requires_recovery'::text END,
           0::integer, 'no_current_work'::text
    FROM public.control_experiments e
$ops$;

INSERT INTO public.control_experiments VALUES (
    '45039c86-c1d9-52f6-a0a9-d94a17bc4b14', 2, 'randomized',
    'draft', 'shadow', 'closed', false, 17,
    '3e26a2da1863bd14d255d58fe00d8e94aae9226f9b588ddd9d4f993e6bcb7016');
INSERT INTO public.experiment_v2_direct_proof_emergency_resolutions VALUES (
    'ace2d26c-539d-4007-ad9c-d25ad812644f', 'bounded_baseline_recovery');
INSERT INTO public.experiment_v2_direct_proof_emergency_recovery_receipts VALUES (
    'ace2d26c-539d-4007-ad9c-d25ad812644f',
    '45039c86-c1d9-52f6-a0a9-d94a17bc4b14',
    'd00304d1-74f9-4872-857e-6944de53ac46',
    '7093f8c3-a36e-49f2-8b4b-443d32a9a51b',
    '0fa6d172de87cf2008d5908ff4a3517eeca1d1cd4811e86461fc349c25f41b91');
WITH expected(work_id, lease_generation, created_at, expires_at) AS (
    VALUES
    ('baadbd8b-49da-486b-9cfc-a54f534239ba'::uuid, 1::bigint,
     '2026-08-28 04:01:56.513732+00'::timestamptz,
     '2026-08-28 04:06:56.513732+00'::timestamptz),
    ('019c9b7c-c8fd-4d28-b7dc-c6bf739be506'::uuid, 2::bigint,
     '2026-08-28 06:31:05.062609+00'::timestamptz,
     '2026-08-28 06:36:05.062609+00'::timestamptz),
    ('55c353fc-f894-4179-8341-47af364eacad'::uuid, 3::bigint,
     '2026-08-28 07:33:54.741947+00'::timestamptz,
     '2026-08-28 07:38:54.741947+00'::timestamptz),
    ('48a66c19-7a2e-43c8-91d1-ba545535ff0a'::uuid, 4::bigint,
     '2026-08-28 08:19:47.095558+00'::timestamptz,
     '2026-08-28 08:24:47.095558+00'::timestamptz),
    ('b803563f-1813-4ccf-b21b-6de8ee096a4d'::uuid, 6::bigint,
     '2026-08-28 11:31:11.080946+00'::timestamptz,
     '2026-08-28 11:36:11.080946+00'::timestamptz)
)
INSERT INTO public.experiment_v2_work
    (work_id, experiment_id, lease_generation, created_at, valid_range,
     expires_at, parent_work_id, operation_kind, target_profile, created_by,
     revision_bundle_sha256, target_state_content_sha256)
SELECT work_id, '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid,
       lease_generation, created_at, tstzrange(created_at, expires_at, '[)'),
       expires_at, NULL, 'baseline_recovery', 'baseline',
       'verdify-component-executor-v2',
       '3e26a2da1863bd14d255d58fe00d8e94aae9226f9b588ddd9d4f993e6bcb7016',
       '4fbbab8b067c978500f1b45878d7fd11ae2a36c25feeff26dd2e7334b2c2b574'
FROM expected;

\i db/migrations/251-experiment-v2-legacy-expired-recovery-terminalization.sql

DO $test$
DECLARE v_inserted integer;
BEGIN
    IF has_function_privilege(
           'public',
           'public.fn_experiment_v2_terminalize_legacy_expired_recovery(uuid,uuid,bigint,text)',
           'EXECUTE') OR
       NOT has_function_privilege(
           'verdify_experiment_lifecycle',
           'public.fn_experiment_v2_terminalize_legacy_expired_recovery(uuid,uuid,bigint,text)',
           'EXECUTE') THEN
        RAISE EXCEPTION 'legacy repair function ACL is too broad or missing';
    END IF;

    BEGIN
        PERFORM public.fn_experiment_v2_terminalize_legacy_expired_recovery(
            '45039c86-c1d9-52f6-a0a9-d94a17bc4b14',
            '00000000-0000-4000-8000-000000000001', 17, 'fixture');
        RAISE EXCEPTION 'wrong resolution unexpectedly passed';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM = 'wrong resolution unexpectedly passed' THEN RAISE; END IF;
    END;
    IF EXISTS (SELECT 1 FROM public.experiment_v2_work_events) THEN
        RAISE EXCEPTION 'wrong resolution appended work history';
    END IF;

    INSERT INTO public.experiment_v2_delivery_bundles VALUES (
        'baadbd8b-49da-486b-9cfc-a54f534239ba',
        '00000000-0000-4000-8000-000000000002');
    BEGIN
        PERFORM public.fn_experiment_v2_terminalize_legacy_expired_recovery(
            '45039c86-c1d9-52f6-a0a9-d94a17bc4b14',
            'ace2d26c-539d-4007-ad9c-d25ad812644f', 17, 'fixture');
        RAISE EXCEPTION 'delivery bundle unexpectedly passed';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM = 'delivery bundle unexpectedly passed' THEN RAISE; END IF;
    END;
    DELETE FROM public.experiment_v2_delivery_bundles;
    IF EXISTS (SELECT 1 FROM public.experiment_v2_work_events) THEN
        RAISE EXCEPTION 'delivery mismatch appended work history';
    END IF;

    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (
        '45039c86-c1d9-52f6-a0a9-d94a17bc4b14',
        'baadbd8b-49da-486b-9cfc-a54f534239ba',
        'failed', 'fixture',
        jsonb_build_object(
            'reason', 'historical_expired_unclaimed_baseline_recovery',
            'gate_r_resolution_id',
            'ace2d26c-539d-4007-ad9c-d25ad812644f'::uuid,
            'source_migration', 251),
        clock_timestamp());
    BEGIN
        PERFORM public.fn_experiment_v2_terminalize_legacy_expired_recovery(
            '45039c86-c1d9-52f6-a0a9-d94a17bc4b14',
            'ace2d26c-539d-4007-ad9c-d25ad812644f', 17, 'fixture');
        RAISE EXCEPTION 'partial event history unexpectedly passed';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM = 'partial event history unexpectedly passed' THEN RAISE; END IF;
    END;
    IF (SELECT count(*) FROM public.experiment_v2_work_events) <> 1 THEN
        RAISE EXCEPTION 'partial history caused a second event';
    END IF;
    DELETE FROM public.experiment_v2_work_events;

    UPDATE public.control_experiments SET component_enabled = NULL;
    BEGIN
        PERFORM public.fn_experiment_v2_terminalize_legacy_expired_recovery(
            '45039c86-c1d9-52f6-a0a9-d94a17bc4b14',
            'ace2d26c-539d-4007-ad9c-d25ad812644f', 17, 'fixture');
        RAISE EXCEPTION 'null component state unexpectedly passed';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM = 'null component state unexpectedly passed' THEN RAISE; END IF;
    END;
    UPDATE public.control_experiments SET component_enabled = false;
    IF EXISTS (SELECT 1 FROM public.experiment_v2_work_events) THEN
        RAISE EXCEPTION 'null state appended work history';
    END IF;

    SELECT public.fn_experiment_v2_terminalize_legacy_expired_recovery(
        '45039c86-c1d9-52f6-a0a9-d94a17bc4b14',
        'ace2d26c-539d-4007-ad9c-d25ad812644f', 17, 'fixture')
      INTO v_inserted;
    IF v_inserted <> 5 OR
       (SELECT count(DISTINCT work_id) FROM public.experiment_v2_work_events
        WHERE event_kind = 'failed') <> 5 OR
       EXISTS (SELECT 1 FROM public.experiment_v2_work_events
               WHERE event_kind <> 'failed') OR
       (SELECT alert_reason FROM public.fn_experiment_v2_ops_status()
        WHERE experiment_id =
              '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid) <>
           'runtime_fault_requires_recovery' THEN
        RAISE EXCEPTION 'five exact failures were not appended or fault was hidden';
    END IF;
    SELECT public.fn_experiment_v2_terminalize_legacy_expired_recovery(
        '45039c86-c1d9-52f6-a0a9-d94a17bc4b14',
        'ace2d26c-539d-4007-ad9c-d25ad812644f', 17, 'fixture')
      INTO v_inserted;
    IF v_inserted <> 0 OR
       (SELECT count(*) FROM public.experiment_v2_work_events) <> 5 THEN
        RAISE EXCEPTION 'exact replay did not remain idempotent';
    END IF;
END
$test$;
ROLLBACK;
