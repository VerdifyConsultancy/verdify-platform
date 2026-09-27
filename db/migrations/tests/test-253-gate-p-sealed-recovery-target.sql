-- Disposable PostgreSQL fixture only. No production or device connection.
BEGIN;
CREATE ROLE verdify_experiment_v2_owner;
CREATE ROLE verdify_ingestor_runtime;
CREATE TABLE public.control_experiments (
    experiment_id uuid, protocol_version integer, status text,
    execution_phase text, admission_state text, component_enabled boolean,
    lease_generation bigint, revision_bundle_sha256 text
);
CREATE TABLE public.experiment_v2_exposures (exposure_id uuid, experiment_id uuid);
CREATE TABLE public.experiment_v2_exposure_closures (exposure_id uuid);
CREATE TABLE public.experiment_v2_direct_proof_receipts (experiment_id uuid);
CREATE TABLE public.experiment_v2_direct_proof_emergency_recovery_receipts (
    resolution_id uuid, authorization_id uuid, experiment_id uuid,
    recovery_work_id uuid, recovery_evidence_sha256 text
);
CREATE TABLE public.experiment_v2_direct_proof_emergency_resolutions (
    resolution_id uuid, resolution_kind text
);
CREATE TABLE public.experiment_v2_runtime_faults (
    experiment_id uuid, fault_report_id uuid, reported_fault_kind text,
    close_reason text, recovery_work_id uuid, admission_state_after text,
    facility_authority_yielded boolean, recorded_at timestamptz
);
CREATE TABLE public.experiment_v2_work_events (
    experiment_id uuid, event_kind text, recorded_at timestamptz
);
GRANT ALL ON ALL TABLES IN SCHEMA public TO verdify_experiment_v2_owner;
CREATE FUNCTION public.fn_experiment_v2_ops_status()
RETURNS TABLE(experiment_id uuid, safety_state text, alert_reason text,
              open_exposure_count integer, observation_truth text)
LANGUAGE sql AS $ops$
    SELECT e.experiment_id, 'runtime_fault'::text,
           'runtime_fault_requires_recovery'::text, 0::integer,
           'no_current_work'::text FROM public.control_experiments e
$ops$;

INSERT INTO public.control_experiments VALUES (
    '45039c86-c1d9-52f6-a0a9-d94a17bc4b14', 2, 'draft', 'shadow',
    'closed', false, 17,
    '3e26a2da1863bd14d255d58fe00d8e94aae9226f9b588ddd9d4f993e6bcb7016');
INSERT INTO public.experiment_v2_direct_proof_emergency_resolutions VALUES (
    'ace2d26c-539d-4007-ad9c-d25ad812644f', 'bounded_baseline_recovery');
INSERT INTO public.experiment_v2_direct_proof_emergency_recovery_receipts VALUES (
    'ace2d26c-539d-4007-ad9c-d25ad812644f',
    'd00304d1-74f9-4872-857e-6944de53ac46',
    '45039c86-c1d9-52f6-a0a9-d94a17bc4b14',
    '7093f8c3-a36e-49f2-8b4b-443d32a9a51b',
    '0fa6d172de87cf2008d5908ff4a3517eeca1d1cd4811e86461fc349c25f41b91');
INSERT INTO public.experiment_v2_runtime_faults VALUES (
    '45039c86-c1d9-52f6-a0a9-d94a17bc4b14',
    '725306a0-9b4b-4083-a3ba-8912308e1d55',
    'db_outage', 'writer_collision', NULL, 'emergency_hold', true,
    '2026-08-30 01:12:38.787056+00');

\i db/migrations/253-gate-p-sealed-recovery-target.sql

DO $test$
BEGIN
    IF NOT public.fn_experiment_v2_gate_p_sealed_recovery_target(
        '45039c86-c1d9-52f6-a0a9-d94a17bc4b14') THEN
        RAISE EXCEPTION 'exact sealed target was rejected';
    END IF;
    UPDATE public.control_experiments SET component_enabled = true;
    IF public.fn_experiment_v2_gate_p_sealed_recovery_target(
        '45039c86-c1d9-52f6-a0a9-d94a17bc4b14') THEN
        RAISE EXCEPTION 'enabled component passed';
    END IF;
    UPDATE public.control_experiments SET component_enabled = false;
    INSERT INTO public.experiment_v2_work_events VALUES (
        '45039c86-c1d9-52f6-a0a9-d94a17bc4b14', 'recovered',
        '2026-08-30 01:13:00+00');
    IF public.fn_experiment_v2_gate_p_sealed_recovery_target(
        '45039c86-c1d9-52f6-a0a9-d94a17bc4b14') THEN
        RAISE EXCEPTION 'already recovered fault passed';
    END IF;
    DELETE FROM public.experiment_v2_work_events;
    INSERT INTO public.experiment_v2_runtime_faults VALUES (
        '45039c86-c1d9-52f6-a0a9-d94a17bc4b14',
        '00000000-0000-4000-8000-000000000001',
        'sensor_gap', 'sensor_gap', NULL, 'emergency_hold', true,
        '2026-08-30 01:14:00+00');
    IF public.fn_experiment_v2_gate_p_sealed_recovery_target(
        '45039c86-c1d9-52f6-a0a9-d94a17bc4b14') THEN
        RAISE EXCEPTION 'newer runtime fault passed';
    END IF;
    DELETE FROM public.experiment_v2_runtime_faults
     WHERE fault_report_id = '00000000-0000-4000-8000-000000000001';
    UPDATE public.experiment_v2_direct_proof_emergency_recovery_receipts
       SET recovery_evidence_sha256 = repeat('a',64);
    IF public.fn_experiment_v2_gate_p_sealed_recovery_target(
        '45039c86-c1d9-52f6-a0a9-d94a17bc4b14') THEN
        RAISE EXCEPTION 'mismatched Gate R receipt passed';
    END IF;
END
$test$;
ROLLBACK;
