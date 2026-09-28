-- Disposable empty PostgreSQL database only. Never run against production.
-- Pre-255 fixture: creates the minimal source table, installs migration 252
-- alone, and rolls back. Its target insert intentionally has the 252 column
-- shape; the current-schema successor is test-255-fixed-panel-crop-assignment-lineage.sql.
-- Exercises source DML capture, future-only declarations and mutation guards.
\set ON_ERROR_STOP on
BEGIN;

DO $roles$
DECLARE role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'verdify_api_runtime', 'verdify_ingestor_runtime',
        'verdify_api_runtime_login', 'verdify_ingestor_runtime_login'
    ] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('CREATE ROLE %I', role_name);
        END IF;
    END LOOP;
END;
$roles$;

CREATE TABLE public.crop_target_profiles (
    id integer PRIMARY KEY, greenhouse_id text NOT NULL,
    crop_type text NOT NULL, temp_ideal_min double precision NOT NULL
);
INSERT INTO public.crop_target_profiles VALUES (1, 'vallery', 'pepper', 68.0);
\i db/migrations/252-fixed-panel-source-history.sql

DO $proof$
DECLARE
    source_hash text;
    rejected boolean;
BEGIN
    IF (SELECT count(*) FROM public.crop_target_profile_revisions) <> 1
       OR (SELECT operation FROM public.crop_target_profile_revisions) <> 'baseline'
       OR EXISTS (SELECT 1 FROM public.fixed_panel_contributor_revisions)
       OR EXISTS (SELECT 1 FROM public.fixed_panel_target_revisions) THEN
        RAISE EXCEPTION 'migration baseline invented prior target or serial history';
    END IF;

    UPDATE public.crop_target_profiles SET temp_ideal_min = 69.0 WHERE id = 1;
    IF (SELECT count(*) FROM public.crop_target_profile_revisions) <> 2
       OR (SELECT profile->>'temp_ideal_min'
             FROM public.crop_target_profile_revisions ORDER BY revision_id DESC LIMIT 1) <> '69' THEN
        RAISE EXCEPTION 'source update was not captured';
    END IF;
    UPDATE public.crop_target_profiles SET id = 2 WHERE id = 1;
    IF (SELECT count(*) FROM public.crop_target_profile_revisions) <> 4
       OR NOT EXISTS (SELECT 1 FROM public.crop_target_profile_revisions
                      WHERE operation = 'delete' AND profile_id = 1) THEN
        RAISE EXCEPTION 'source key change left an untombstoned prior row';
    END IF;

    rejected := false;
    BEGIN
        INSERT INTO public.crop_target_profile_revisions
            (operation, profile_id, profile, profile_sha256)
        VALUES ('insert', 999, '{}'::jsonb,
                encode(pg_catalog.sha256(convert_to('{}', 'UTF8')), 'hex'));
    EXCEPTION WHEN OTHERS THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'direct audit insertion was accepted'; END IF;

    rejected := false;
    BEGIN
        INSERT INTO public.fixed_panel_contributor_revisions
            (greenhouse_id, zone, route_id, modbus_address, temp_field, vpd_field,
             source_revision_sha256, valid_from, valid_to, capture_scope)
        VALUES ('vallery', 'north', 'north_wall_probe', 2, 'temp_north', 'vpd_north',
                repeat('a', 64), now() - interval '1 day', now() + interval '1 day',
                'route_only');
    EXCEPTION WHEN OTHERS THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'backdated route was accepted'; END IF;

    INSERT INTO public.fixed_panel_contributor_revisions
        (greenhouse_id, zone, route_id, modbus_address, temp_field, vpd_field,
         source_revision_sha256, valid_from, valid_to, capture_scope)
    VALUES ('vallery', 'north', 'north_wall_probe', 2, 'temp_north', 'vpd_north',
            repeat('a', 64), now() + interval '1 hour', now() + interval '1 day',
            'route_only');
    IF EXISTS (SELECT 1 FROM public.fixed_panel_contributor_revisions
               WHERE physical_serial IS NOT NULL) THEN
        RAISE EXCEPTION 'route-only declaration invented a serial';
    END IF;

    SELECT encode(pg_catalog.sha256(convert_to(
               jsonb_agg(to_jsonb(p) ORDER BY p.id)::text, 'UTF8')), 'hex')
      INTO source_hash FROM public.crop_target_profiles p WHERE p.greenhouse_id = 'vallery';
    INSERT INTO public.fixed_panel_target_revisions
        (greenhouse_id, target_version, effective_from, effective_to,
         source_profile_state_sha256, target_bins, db_target_bins_sha256)
    VALUES ('vallery', 'future-test-v1', now() + interval '1 hour',
            now() + interval '1 day', source_hash,
            '[{"bucket_start":"future"}]'::jsonb,
            encode(pg_catalog.sha256(convert_to(
                '[{"bucket_start":"future"}]'::jsonb::text, 'UTF8')), 'hex'));

    rejected := false;
    BEGIN
        UPDATE public.fixed_panel_target_revisions SET target_version = 'edited';
    EXCEPTION WHEN OTHERS THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'target revision was mutable'; END IF;

    rejected := false;
    BEGIN
        TRUNCATE public.crop_target_profiles;
    EXCEPTION WHEN OTHERS THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'source truncate bypassed audit'; END IF;
END;
$proof$;

ROLLBACK;
