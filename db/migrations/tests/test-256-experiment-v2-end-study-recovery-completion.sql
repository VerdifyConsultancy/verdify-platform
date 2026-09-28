-- Disposable PostgreSQL fixture only. Never run against production.
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE verdify_experiment_v2_owner;
CREATE TABLE public.schema_migrations (
    source text, filename text, seq integer, sha256 text, stamp_method text
);
INSERT INTO public.schema_migrations VALUES (
    'db/migrations', 'db/migrations/255-fixed-panel-crop-assignment-lineage.sql',
    255, 'c9276c02fa7641f37bad82b1d7fb829aba81c143d16d514606c5b6f6b8facd7b', 'runner');
CREATE TABLE public.runtime_ordinary_login_attestation_receipts (
    login_name text PRIMARY KEY, boundary_sha256 bytea NOT NULL,
    captured_at timestamptz
);
INSERT INTO public.runtime_ordinary_login_attestation_receipts VALUES
    ('verdify_api_runtime_login', decode('a52e94f2b6fdecf792cfa819a33f6cd4a950d1076fec1895e9870a63b362cf62','hex')),
    ('verdify_ingestor_runtime_login', decode('5bdcd842aa593e15f7d33f4f335dfac0278adc62a0ea929b2590880adbf24c8d','hex'));
CREATE FUNCTION public.fn_runtime_ordinary_boundary_digest(login_name text)
RETURNS bytea LANGUAGE sql AS $fixture$
    SELECT CASE
        WHEN to_regprocedure(
            'public.fn_experiment_v2_require_end_recovery_completion()') IS NOT NULL
        THEN decode(CASE $1
            WHEN 'verdify_api_runtime_login' THEN
                '7c8b0d3f8dcfa8552068ca8373e0f394aafb3c27aab1fda72c7eebd3083c904a'
            WHEN 'verdify_ingestor_runtime_login' THEN
                '7783f5d743751224ae157fe063941c76e00167a0208cd233150a9b68b06633fa'
            END, 'hex')
        ELSE (SELECT boundary_sha256
                FROM public.runtime_ordinary_login_attestation_receipts
               WHERE runtime_ordinary_login_attestation_receipts.login_name = $1)
    END
$fixture$;

CREATE TABLE public.control_experiments (
    experiment_id uuid PRIMARY KEY, protocol_version integer, status text,
    execution_phase text, admission_state text, lease_generation bigint,
    revision_bundle_sha256 text, ended_at timestamptz,
    component_enabled boolean
);
CREATE TABLE public.control_assignments (
    experiment_id uuid, operation_kind text, valid_range tstzrange
);
CREATE TABLE public.experiment_v2_state_artifacts (
    experiment_id uuid, revision_bundle_sha256 text, profile text,
    state_content_sha256 text
);
CREATE TABLE public.experiment_v2_facility_safe_closures (
    experiment_id uuid, safe_state_kind text, closed_at timestamptz
);
CREATE TABLE public.experiment_v2_work (
    experiment_id uuid, work_id uuid PRIMARY KEY, operation_kind text,
    execution_phase text, target_profile text, target_state_content_sha256 text,
    revision_bundle_sha256 text, lease_generation bigint, created_at timestamptz
);
CREATE TABLE public.experiment_v2_work_events (
    experiment_id uuid, work_id uuid, event_kind text, recorded_at timestamptz
);
CREATE TABLE public.experiment_v2_exposures (
    exposure_id uuid PRIMARY KEY, experiment_id uuid, started_at timestamptz
);
CREATE TABLE public.experiment_v2_exposure_closures (
    exposure_id uuid PRIMARY KEY, ended_at timestamptz
);
CREATE TABLE public.experiment_v2_runtime_faults (
    experiment_id uuid, recorded_at timestamptz
);
CREATE TABLE public.experiment_v2_observation_epochs (
    source_epoch_id uuid PRIMARY KEY, last_observed_at timestamptz
);
CREATE TABLE public.experiment_v2_observation_receipts (
    experiment_id uuid, work_id uuid, bundle_id uuid, source_epoch_id uuid,
    policy_state_content_sha256 text, persisted_at timestamptz
);
GRANT SELECT ON ALL TABLES IN SCHEMA public TO verdify_experiment_v2_owner;
GRANT UPDATE ON public.control_experiments TO verdify_experiment_v2_owner;

INSERT INTO public.control_experiments VALUES (
    '25625625-6256-4256-8256-256256256256', 2, 'running', 'randomized',
    'closed', 9, repeat('b',64), NULL, false);
INSERT INTO public.control_assignments VALUES (
    '25625625-6256-4256-8256-256256256256', 'randomized_day',
    tstzrange('2026-09-01 00:00+00','2026-09-02 00:00+00','[)'));
INSERT INTO public.experiment_v2_state_artifacts VALUES (
    '25625625-6256-4256-8256-256256256256', repeat('b',64), 'baseline', repeat('a',64));
INSERT INTO public.experiment_v2_facility_safe_closures VALUES (
    '25625625-6256-4256-8256-256256256256', 'facility_owned_safe_state',
    '2026-08-31 00:00+00');

\i db/migrations/256-experiment-v2-end-study-recovery-completion.sql

DO $proof$
BEGIN
    IF (SELECT closed_lease_generation
          FROM public.experiment_v2_facility_safe_closures
         WHERE experiment_id = '25625625-6256-4256-8256-256256256256') IS NOT NULL THEN
        RAISE EXCEPTION 'historical facility closure gained an invented lease';
    END IF;
END
$proof$;

DO $proof$
DECLARE blocked boolean;
BEGIN
    blocked := false;
    BEGIN
        UPDATE public.control_experiments
           SET status='completed', ended_at='2026-09-02 00:05+00';
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'old generic facility closure bypassed final recovery'; END IF;
END
$proof$;

INSERT INTO public.experiment_v2_work VALUES (
    '25625625-6256-4256-8256-256256256256',
    '11111111-1111-4111-8111-111111111111', 'baseline_recovery',
    'randomized', 'baseline', repeat('a',64), repeat('b',64), 9,
    '2026-09-01 23:59+00');
INSERT INTO public.experiment_v2_work_events VALUES (
    '25625625-6256-4256-8256-256256256256',
    '11111111-1111-4111-8111-111111111111', 'recovered',
    '2026-09-02 00:04+00');
DO $proof$
DECLARE blocked boolean;
BEGIN
    blocked := false;
    BEGIN
        UPDATE public.control_experiments
           SET status='completed', ended_at='2026-09-02 00:05+00';
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'pre-end recovery work passed'; END IF;
END
$proof$;

UPDATE public.experiment_v2_work SET created_at='2026-09-02 00:01+00';
DO $proof$
DECLARE blocked boolean;
BEGIN
    blocked := false;
    BEGIN
        UPDATE public.control_experiments
           SET status='completed', ended_at='2026-09-02 00:05+00';
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'recovered label without epoch pair passed'; END IF;
END
$proof$;

INSERT INTO public.experiment_v2_observation_epochs VALUES
    ('22222222-2222-4222-8222-222222222222','2026-09-02 00:02+00'),
    ('33333333-3333-4333-8333-333333333333','2026-09-02 00:03+00');
INSERT INTO public.experiment_v2_observation_receipts VALUES
    ('25625625-6256-4256-8256-256256256256',
     '11111111-1111-4111-8111-111111111111',
     '44444444-4444-4444-8444-444444444444',
     '22222222-2222-4222-8222-222222222222', repeat('a',64),
     '2026-09-02 00:02+00');
DO $proof$
DECLARE blocked boolean;
BEGIN
    blocked := false;
    BEGIN
        UPDATE public.control_experiments
           SET status='completed', ended_at='2026-09-02 00:05+00';
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'single receipt passed'; END IF;
END
$proof$;

INSERT INTO public.experiment_v2_observation_receipts VALUES
    ('25625625-6256-4256-8256-256256256256',
     '11111111-1111-4111-8111-111111111111',
     '44444444-4444-4444-8444-444444444444',
     '33333333-3333-4333-8333-333333333333', repeat('a',64),
     '2026-09-02 00:03+00');
INSERT INTO public.experiment_v2_exposures VALUES (
    '55555555-5555-4555-8555-555555555555',
    '25625625-6256-4256-8256-256256256256', '2026-09-02 00:04:30+00');
DO $proof$
DECLARE blocked boolean;
BEGIN
    blocked := false;
    BEGIN
        UPDATE public.control_experiments
           SET status='completed', ended_at='2026-09-02 00:05+00';
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'later exposure invalidation did not block'; END IF;
END
$proof$;
DELETE FROM public.experiment_v2_exposures;
INSERT INTO public.experiment_v2_runtime_faults VALUES (
    '25625625-6256-4256-8256-256256256256', '2026-09-02 00:04:30+00');
DO $proof$
DECLARE blocked boolean;
BEGIN
    blocked := false;
    BEGIN
        UPDATE public.control_experiments
           SET status='completed', ended_at='2026-09-02 00:05+00';
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'later runtime fault invalidation did not block'; END IF;
END
$proof$;
DELETE FROM public.experiment_v2_runtime_faults;
INSERT INTO public.experiment_v2_work VALUES (
    '25625625-6256-4256-8256-256256256256',
    '77777777-7777-4777-8777-777777777777', 'baseline_recovery',
    'randomized', 'baseline', repeat('a',64), repeat('b',64), 9,
    '2026-09-02 00:04:30+00');
DO $proof$
DECLARE blocked boolean := false;
BEGIN
    BEGIN
        UPDATE public.control_experiments
           SET status='completed', ended_at='2026-09-02 00:05+00';
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'later work invalidation did not block'; END IF;
END
$proof$;
DELETE FROM public.experiment_v2_work
 WHERE work_id = '77777777-7777-4777-8777-777777777777';
UPDATE public.control_experiments
   SET status='completed', ended_at='2026-09-02 00:05+00'
 WHERE experiment_id = '25625625-6256-4256-8256-256256256256';
DO $proof$
BEGIN
    IF (SELECT status FROM public.control_experiments
         WHERE experiment_id = '25625625-6256-4256-8256-256256256256') <> 'completed' THEN
        RAISE EXCEPTION 'post-study receipt-bound baseline recovery was rejected';
    END IF;
END
$proof$;

-- Facility authority can close at the actual end of study without a work row.
-- Its lease is stamped from the held control row, never supplied by a caller.
INSERT INTO public.control_experiments VALUES (
    '66666666-6666-4666-8666-666666666666', 2, 'running', 'randomized',
    'emergency_hold', 20, repeat('b',64), NULL, false);
INSERT INTO public.control_assignments VALUES (
    '66666666-6666-4666-8666-666666666666', 'randomized_day',
    tstzrange('2026-09-01 00:00+00','2026-09-02 00:00+00','[)'));
DO $proof$
DECLARE blocked boolean := false;
BEGIN
    BEGIN
        INSERT INTO public.experiment_v2_facility_safe_closures
            (experiment_id, safe_state_kind, closed_at, closed_lease_generation)
        VALUES ('66666666-6666-4666-8666-666666666666',
                'facility_owned_safe_state', clock_timestamp(), 21);
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'caller-supplied facility lease was accepted'; END IF;
END
$proof$;
INSERT INTO public.experiment_v2_facility_safe_closures
    (experiment_id, safe_state_kind, closed_at)
VALUES ('66666666-6666-4666-8666-666666666666',
        'facility_owned_safe_state', clock_timestamp());
DO $proof$
BEGIN
    IF (SELECT closed_lease_generation
          FROM public.experiment_v2_facility_safe_closures
         WHERE experiment_id = '66666666-6666-4666-8666-666666666666') <> 21 THEN
        RAISE EXCEPTION 'facility lease was not stamped from held generation';
    END IF;
END
$proof$;
UPDATE public.control_experiments
   SET admission_state='closed', lease_generation=21
 WHERE experiment_id = '66666666-6666-4666-8666-666666666666';
DO $proof$
DECLARE blocked boolean := false;
BEGIN
    BEGIN
        UPDATE public.control_experiments
           SET status='completed', lease_generation=22,
               ended_at=clock_timestamp() + interval '1 second'
         WHERE experiment_id = '66666666-6666-4666-8666-666666666666';
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'stale facility lease completed study'; END IF;
END
$proof$;
INSERT INTO public.experiment_v2_runtime_faults VALUES (
    '66666666-6666-4666-8666-666666666666', clock_timestamp());
DO $proof$
DECLARE blocked boolean := false;
BEGIN
    BEGIN
        UPDATE public.control_experiments
           SET status='completed', ended_at=clock_timestamp() + interval '1 second'
         WHERE experiment_id = '66666666-6666-4666-8666-666666666666';
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'post-closure fault completed study'; END IF;
END
$proof$;
DELETE FROM public.experiment_v2_runtime_faults
 WHERE experiment_id = '66666666-6666-4666-8666-666666666666';
INSERT INTO public.experiment_v2_work VALUES (
    '66666666-6666-4666-8666-666666666666',
    '88888888-8888-4888-8888-888888888888', 'baseline_recovery',
    'randomized', 'baseline', repeat('a',64), repeat('b',64), 21,
    clock_timestamp());
DO $proof$
DECLARE blocked boolean := false;
BEGIN
    BEGIN
        UPDATE public.control_experiments
           SET status='completed', ended_at=clock_timestamp() + interval '1 second'
         WHERE experiment_id = '66666666-6666-4666-8666-666666666666';
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'post-closure work completed study'; END IF;
END
$proof$;
DELETE FROM public.experiment_v2_work
 WHERE work_id = '88888888-8888-4888-8888-888888888888';
UPDATE public.control_experiments
   SET status='completed', ended_at=clock_timestamp() + interval '1 second'
 WHERE experiment_id = '66666666-6666-4666-8666-666666666666';
DO $proof$
BEGIN
    IF (SELECT count(*) FROM public.control_experiments
         WHERE status = 'completed') <> 2 THEN
        RAISE EXCEPTION 'authentic end-of-study facility closure was rejected';
    END IF;
END
$proof$;

-- A new, correctly stamped closure before the scheduled final day cannot
-- become end-of-study proof merely by waiting until that day has elapsed.
INSERT INTO public.control_experiments VALUES (
    '99999999-9999-4999-8999-999999999999', 2, 'running', 'randomized',
    'emergency_hold', 30, repeat('b',64), NULL, false);
INSERT INTO public.control_assignments VALUES (
    '99999999-9999-4999-8999-999999999999', 'randomized_day',
    tstzrange(clock_timestamp() + interval '1 day',
              clock_timestamp() + interval '2 days','[)'));
INSERT INTO public.experiment_v2_facility_safe_closures
    (experiment_id, safe_state_kind, closed_at)
VALUES ('99999999-9999-4999-8999-999999999999',
        'facility_owned_safe_state', clock_timestamp());
UPDATE public.control_experiments
   SET admission_state='closed', lease_generation=31
 WHERE experiment_id = '99999999-9999-4999-8999-999999999999';
DO $proof$
DECLARE blocked boolean := false;
BEGIN
    BEGIN
        UPDATE public.control_experiments
           SET status='completed', ended_at=clock_timestamp() + interval '3 days'
         WHERE experiment_id = '99999999-9999-4999-8999-999999999999';
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'pre-end current-lease closure completed study'; END IF;
END
$proof$;
ROLLBACK;
