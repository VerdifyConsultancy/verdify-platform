-- Disposable PostgreSQL fixture only. Never run against production.
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE verdify_experiment_v2_owner;
CREATE ROLE verdify_experiment_component_executor;
CREATE ROLE verdify_experiment_v2_component_executor_login
    IN ROLE verdify_experiment_component_executor;
CREATE ROLE verdify_ingestor_runtime_login;

CREATE TABLE public.schema_migrations
    (source text, seq integer, filename text, sha256 text, stamp_method text);
INSERT INTO public.schema_migrations VALUES
    ('db/migrations', 263,
     'db/migrations/263-mcp-timescale-chunk-boundary-digest.sql',
     '9f5fa53cde76224b06865095bfd9a531aadae058f6ca13e50e74ecd95ad5770b',
     'runner');
CREATE TABLE public.runtime_ordinary_login_attestation_receipts
    (login_name text, boundary_sha256 bytea);
INSERT INTO public.runtime_ordinary_login_attestation_receipts VALUES
    ('verdify_api_runtime_login', decode(repeat('a', 64), 'hex')),
    ('verdify_ingestor_runtime_login', decode(repeat('b', 64), 'hex'));
CREATE FUNCTION public.fn_runtime_ordinary_boundary_digest(p_login text)
RETURNS bytea LANGUAGE sql STABLE AS $digest$
    SELECT boundary_sha256
      FROM public.runtime_ordinary_login_attestation_receipts
     WHERE login_name = p_login
$digest$;

CREATE TABLE public.control_experiments (
    experiment_id uuid PRIMARY KEY, protocol_version integer, transport_kind text,
    status text, execution_phase text, admission_state text,
    component_enabled boolean, lease_generation bigint, updated_at timestamptz,
    revision_bundle_sha256 text, firmware_revision text, config_revision text,
    registry_revision text, grid_revision text
);
CREATE TABLE public.experiment_v2_runtime_generations (
    generation_event_id bigint GENERATED ALWAYS AS IDENTITY, experiment_id uuid,
    device_id text, writer_generation bigint, connection_generation bigint
);
CREATE TABLE public.experiment_v2_exposures
    (exposure_id uuid, experiment_id uuid, device_id text);
CREATE TABLE public.experiment_v2_exposure_closures (exposure_id uuid);
CREATE TABLE public.experiment_v2_work (
    experiment_id uuid, work_id uuid, operation_kind text, created_at timestamptz,
    execution_phase text, revision_bundle_sha256 text, firmware_revision text,
    config_revision text, registry_revision text, grid_revision text,
    lease_generation bigint, expires_at timestamptz, valid_range tstzrange,
    target_profile text, parent_work_id uuid
);
CREATE TABLE public.experiment_v2_work_events
    (experiment_id uuid, work_id uuid, event_kind text);
CREATE TABLE public.experiment_v2_runtime_faults
    (experiment_id uuid, recorded_at timestamptz);
CREATE TABLE public.experiment_v2_facility_safe_closures (
    experiment_id uuid PRIMARY KEY, authorization_ref text,
    safe_state_artifact_sha256 text, safe_state_kind text, closed_by text,
    closed_at timestamptz, closed_lease_generation bigint
);
GRANT SELECT ON ALL TABLES IN SCHEMA public TO verdify_experiment_v2_owner;
GRANT UPDATE ON public.control_experiments TO verdify_experiment_v2_owner;

\i db/migrations/tests/fixture-264-existing-functions.sql
ALTER FUNCTION public.fn_experiment_v2_safe_startup_attestation(text, uuid)
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL ON FUNCTION public.fn_experiment_v2_safe_startup_attestation(text, uuid)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.fn_experiment_v2_safe_startup_attestation(text, uuid)
    TO verdify_experiment_component_executor;
ALTER FUNCTION public.fn_experiment_v2_stamp_facility_safe_lease()
    OWNER TO verdify_experiment_v2_owner;
CREATE TRIGGER trg_experiment_v2_facility_safe_closures_immutable
    BEFORE UPDATE OR DELETE ON public.experiment_v2_facility_safe_closures
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_immutable();
CREATE TRIGGER trg_experiment_v2_stamp_facility_safe_lease
    BEFORE INSERT ON public.experiment_v2_facility_safe_closures
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_stamp_facility_safe_lease();

INSERT INTO public.control_experiments VALUES
    ('45039c86-c1d9-52f6-a0a9-d94a17bc4b14', 2, 'legacy_components_v1',
     'draft', 'shadow', 'emergency_hold', false, 18, clock_timestamp(),
     repeat('a', 64), 'fw', 'cfg', 'registry', 'grid');
INSERT INTO public.experiment_v2_runtime_generations
    (experiment_id, device_id, writer_generation, connection_generation)
VALUES ('45039c86-c1d9-52f6-a0a9-d94a17bc4b14', 'esp32-vallery', 17, 192);
INSERT INTO public.experiment_v2_work
    (experiment_id, work_id, operation_kind, created_at, execution_phase,
     revision_bundle_sha256, firmware_revision, config_revision,
     registry_revision, grid_revision, lease_generation, expires_at,
     valid_range, target_profile)
SELECT '45039c86-c1d9-52f6-a0a9-d94a17bc4b14', md5(i::text)::uuid,
       'baseline_recovery', clock_timestamp() - interval '30 days',
       'shadow', repeat('a', 64), 'fw', 'cfg', 'registry', 'grid', 18,
       clock_timestamp() + interval '1 day',
       tstzrange(clock_timestamp() - interval '1 day',
                 clock_timestamp() + interval '1 day', '[)'), 'baseline'
  FROM generate_series(1, 98) i;

\i db/migrations/264-facility-safe-closure-startup-handoff.sql
DO $proof$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM public.fn_experiment_v2_safe_startup_attestation(
        'esp32-vallery', NULL);
    IF NOT a.scope_resolved OR a.hold_required OR NOT a.facility_authority_yielded
       OR a.recovery_pending_count <> 98 OR a.current_lease_generation <> 18
       OR (SELECT count(*) FROM public.experiment_v2_facility_safe_closures) <> 0
       OR (SELECT count(*) FROM public.experiment_v2_work_events) <> 0 THEN
        RAISE EXCEPTION 'migration changed preclosure authority or evidence';
    END IF;
END
$proof$;

-- The production migration-256 trigger stamps 18 + 1 on insert; the owning
-- closure command then advances the experiment lease to that same value.
INSERT INTO public.experiment_v2_facility_safe_closures
    (experiment_id, authorization_ref, safe_state_artifact_sha256,
     safe_state_kind, closed_by, closed_at)
VALUES ('45039c86-c1d9-52f6-a0a9-d94a17bc4b14', 'fixture-authorization',
        repeat('c', 64), 'facility_owned_safe_state', 'fixture', clock_timestamp());
UPDATE public.control_experiments
   SET admission_state = 'closed', lease_generation = lease_generation + 1
 WHERE experiment_id = '45039c86-c1d9-52f6-a0a9-d94a17bc4b14';
DO $proof$
DECLARE a record; blocked boolean := false;
BEGIN
    SELECT * INTO a FROM public.fn_experiment_v2_safe_startup_attestation(
        'esp32-vallery', NULL);
    IF NOT a.scope_resolved OR a.hold_required OR NOT a.facility_authority_yielded
       OR a.attestation_reason <> 'facility_safe_closure'
       OR a.recovery_pending_count <> 98 OR a.current_lease_generation <> 19
       OR (SELECT closed_lease_generation
             FROM public.experiment_v2_facility_safe_closures) <> 19 THEN
        RAISE EXCEPTION 'current-lease closure did not preserve yielded authority';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.experiment_v2_work w
         WHERE public.fn_experiment_v2_work_is_eligible(
             w.experiment_id, w.work_id, 19, clock_timestamp(), 'recovery')
            OR public.fn_experiment_v2_work_is_eligible(
             w.experiment_id, w.work_id, 18, clock_timestamp(), 'recovery'))
       OR (SELECT count(*) FROM public.experiment_v2_work) <> 98
       OR (SELECT count(*) FROM public.experiment_v2_work_events) <> 0 THEN
        RAISE EXCEPTION 'closure claimed or relabeled historical recovery debt';
    END IF;
    BEGIN
        UPDATE public.experiment_v2_facility_safe_closures
           SET closed_lease_generation = 18;
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'closure was mutable'; END IF;
    IF EXISTS (
           SELECT 1 FROM pg_proc p
           CROSS JOIN LATERAL aclexplode(p.proacl) acl
            WHERE p.oid =
                  'public.fn_experiment_v2_safe_startup_attestation(text,uuid)'::regprocedure
              AND acl.grantee = 0 AND acl.privilege_type = 'EXECUTE')
       OR has_function_privilege('verdify_ingestor_runtime_login',
           'public.fn_experiment_v2_safe_startup_attestation(text,uuid)', 'EXECUTE')
       OR NOT has_function_privilege('verdify_experiment_v2_component_executor_login',
           'public.fn_experiment_v2_safe_startup_attestation(text,uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION 'startup attestation role boundary changed';
    END IF;
END
$proof$;

DO $proof$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM public.fn_experiment_v2_safe_startup_attestation(
        'other-device', '45039c86-c1d9-52f6-a0a9-d94a17bc4b14');
    IF a.facility_authority_yielded OR NOT a.hold_required
       OR a.recovery_pending_count <> 98 THEN
        RAISE EXCEPTION 'other device inherited closure';
    END IF;
END
$proof$;
INSERT INTO public.experiment_v2_runtime_generations
    (experiment_id, device_id, writer_generation, connection_generation)
VALUES ('45039c86-c1d9-52f6-a0a9-d94a17bc4b14', 'other-device', 1, 1);
DO $proof$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM public.fn_experiment_v2_safe_startup_attestation(
        'esp32-vallery', NULL);
    IF a.facility_authority_yielded OR NOT a.hold_required THEN
        RAISE EXCEPTION 'multi-device binding reused one facility closure';
    END IF;
END
$proof$;
DELETE FROM public.experiment_v2_runtime_generations
 WHERE device_id = 'other-device';
INSERT INTO public.experiment_v2_exposures VALUES
    ('11111111-1111-4111-8111-111111111111',
     '45039c86-c1d9-52f6-a0a9-d94a17bc4b14', 'esp32-vallery');
DO $proof$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM public.fn_experiment_v2_safe_startup_attestation(
        'esp32-vallery', NULL);
    IF a.facility_authority_yielded OR NOT a.hold_required
       OR a.attestation_reason <> 'open_exposure' THEN
        RAISE EXCEPTION 'open exposure did not reassert hold';
    END IF;
END
$proof$;
DELETE FROM public.experiment_v2_exposures;
UPDATE public.control_experiments SET lease_generation = 20;
DO $proof$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM public.fn_experiment_v2_safe_startup_attestation(
        'esp32-vallery', NULL);
    IF a.facility_authority_yielded OR NOT a.hold_required
       OR a.attestation_reason <> 'baseline_recovery_pending' THEN
        RAISE EXCEPTION 'stale closure lease released historical debt';
    END IF;
END
$proof$;
UPDATE public.control_experiments SET lease_generation = 19;
INSERT INTO public.experiment_v2_runtime_faults VALUES
    ('45039c86-c1d9-52f6-a0a9-d94a17bc4b14', clock_timestamp());
DO $proof$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM public.fn_experiment_v2_safe_startup_attestation(
        'esp32-vallery', NULL);
    IF a.facility_authority_yielded OR NOT a.hold_required THEN
        RAISE EXCEPTION 'later runtime fault did not invalidate closure';
    END IF;
END
$proof$;
DELETE FROM public.experiment_v2_runtime_faults;
INSERT INTO public.experiment_v2_work
    (experiment_id, work_id, operation_kind, created_at)
VALUES ('45039c86-c1d9-52f6-a0a9-d94a17bc4b14',
        '22222222-2222-4222-8222-222222222222',
        'baseline_recovery', clock_timestamp());
DO $proof$
DECLARE a record;
BEGIN
    SELECT * INTO a FROM public.fn_experiment_v2_safe_startup_attestation(
        'esp32-vallery', NULL);
    IF a.facility_authority_yielded OR NOT a.hold_required THEN
        RAISE EXCEPTION 'later work did not invalidate closure';
    END IF;
END
$proof$;
ROLLBACK;
