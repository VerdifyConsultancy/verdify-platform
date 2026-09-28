-- Disposable empty PostgreSQL database only. Never run against production.
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
CREATE TABLE public.crops (
    id integer PRIMARY KEY, greenhouse_id text NOT NULL,
    zone text NOT NULL, crop_catalog_id integer, is_active boolean NOT NULL
);
INSERT INTO public.crop_target_profiles VALUES (1, 'vallery', 'pepper', 68.0);
INSERT INTO public.crops VALUES (1, 'vallery', 'east', 6, true);
\i db/migrations/252-fixed-panel-source-history.sql
-- The minimal fixture has no migration-217 catalog. Model only the exact
-- predecessor/successor receipt contract here; the real digest is qualified
-- by the bounded live rollback probe, not by this synthetic function.
CREATE TABLE public.schema_migrations (
    source text NOT NULL, filename text NOT NULL, seq integer,
    sha256 text, stamp_method text NOT NULL
);
INSERT INTO public.schema_migrations VALUES
    ('db/migrations', 'db/migrations/254-post-253-ordinary-login-attestation.sql',
     254, 'd4f5a0d5366caf4ed74835aa5a768ed8261cd5683afe09a0c1c14b4f07ef1cd8', 'runner');
CREATE TABLE public.runtime_ordinary_login_attestation_receipts (
    login_name text PRIMARY KEY, boundary_sha256 bytea NOT NULL,
    captured_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO public.runtime_ordinary_login_attestation_receipts (login_name, boundary_sha256)
VALUES
    ('verdify_api_runtime_login', decode('9b5e6841cebc95cff6020f1a899e65c1f504d927844f66f7045ecfb1eef23451', 'hex')),
    ('verdify_ingestor_runtime_login', decode('52c1d03192df977396e4e61075616ee18ecb66c6b771005261c510980e97adc3', 'hex'));
CREATE FUNCTION public.fn_runtime_ordinary_boundary_digest(login_name text)
RETURNS bytea LANGUAGE sql AS $fixture$
    SELECT decode(CASE
        WHEN to_regclass('public.crop_assignment_revisions') IS NULL
             AND login_name = 'verdify_api_runtime_login' THEN
            '9b5e6841cebc95cff6020f1a899e65c1f504d927844f66f7045ecfb1eef23451'
        WHEN to_regclass('public.crop_assignment_revisions') IS NULL
             AND login_name = 'verdify_ingestor_runtime_login' THEN
            '52c1d03192df977396e4e61075616ee18ecb66c6b771005261c510980e97adc3'
        WHEN login_name = 'verdify_api_runtime_login' THEN
            'a52e94f2b6fdecf792cfa819a33f6cd4a950d1076fec1895e9870a63b362cf62'
        ELSE
            '5bdcd842aa593e15f7d33f4f335dfac0278adc62a0ea929b2590880adbf24c8d'
    END, 'hex');
$fixture$;
\i db/migrations/255-fixed-panel-crop-assignment-lineage.sql

DO $proof$
DECLARE
    profile_hash text;
    assignment_hash text;
    profile_ids bigint[];
    assignment_ids bigint[];
    rejected boolean;
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts receipt
         WHERE receipt.boundary_sha256 IS DISTINCT FROM
               public.fn_runtime_ordinary_boundary_digest(receipt.login_name)
    ) THEN
        RAISE EXCEPTION 'migration 255 did not advance exact successor receipts';
    END IF;
    IF (SELECT count(*) FROM public.crop_assignment_revisions) <> 1
       OR (SELECT operation FROM public.crop_assignment_revisions) <> 'baseline' THEN
        RAISE EXCEPTION 'migration baseline invented prior crop assignment history';
    END IF;
    UPDATE public.crops SET zone = 'east' WHERE id = 1;
    IF (SELECT count(*) FROM public.crop_assignment_revisions) <> 1 THEN
        RAISE EXCEPTION 'no-op update generated a revision';
    END IF;
    UPDATE public.crops SET zone = 'west' WHERE id = 1;
    IF (SELECT count(*) FROM public.crop_assignment_revisions) <> 2
       OR (SELECT crop->>'zone' FROM public.crop_assignment_revisions
             ORDER BY revision_id DESC LIMIT 1) <> 'west' THEN
        RAISE EXCEPTION 'source assignment update was not captured';
    END IF;
    INSERT INTO public.crops VALUES (2, 'vallery', 'north', NULL, true);
    DELETE FROM public.crops WHERE id = 2;
    IF NOT EXISTS (SELECT 1 FROM public.crop_assignment_revisions
                    WHERE operation = 'delete' AND crop_id = 2) THEN
        RAISE EXCEPTION 'source assignment delete was not captured';
    END IF;
    SELECT encode(pg_catalog.sha256(convert_to(
               jsonb_agg(to_jsonb(p) ORDER BY p.id)::text, 'UTF8')), 'hex')
      INTO profile_hash FROM public.crop_target_profiles p WHERE greenhouse_id = 'vallery';
    SELECT encode(pg_catalog.sha256(convert_to(
               jsonb_agg(to_jsonb(c) ORDER BY c.id)::text, 'UTF8')), 'hex')
      INTO assignment_hash FROM public.crops c WHERE greenhouse_id = 'vallery';
    SELECT array_agg(revision_id ORDER BY revision_id) INTO profile_ids
      FROM public.crop_target_profile_revisions WHERE operation <> 'delete';
    assignment_ids := ARRAY[(SELECT max(revision_id) FROM public.crop_assignment_revisions
                              WHERE crop_id = 1)]::bigint[];

    rejected := false;
    BEGIN
        INSERT INTO public.crop_assignment_revisions
            (operation, crop_id, greenhouse_id, crop, crop_sha256)
        VALUES ('insert', 999, 'vallery', '{}'::jsonb,
                encode(pg_catalog.sha256(convert_to('{}', 'UTF8')), 'hex'));
    EXCEPTION WHEN OTHERS THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'direct revision insert accepted'; END IF;

    rejected := false;
    BEGIN
        INSERT INTO public.fixed_panel_target_revisions
            (greenhouse_id, target_version, effective_from, effective_to,
             source_profile_state_sha256, source_assignment_state_sha256,
             profile_revision_ids, assignment_revision_ids, target_rule,
             target_bins, db_target_bins_sha256)
        VALUES ('vallery', 'bad-assignment-v1', now() + interval '1 hour',
                now() + interval '1 day', profile_hash, repeat('a', 64),
                profile_ids, assignment_ids, 'fixed_panel_equal_zone_ideal_mean_v1',
                '[{"bucket_start":"future"}]'::jsonb,
                encode(pg_catalog.sha256(convert_to(
                    '[{"bucket_start":"future"}]'::jsonb::text, 'UTF8')), 'hex'));
    EXCEPTION WHEN OTHERS THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'stale assignment hash accepted'; END IF;

    INSERT INTO public.fixed_panel_target_revisions
        (greenhouse_id, target_version, effective_from, effective_to,
         source_profile_state_sha256, source_assignment_state_sha256,
         profile_revision_ids, assignment_revision_ids, target_rule,
         target_bins, db_target_bins_sha256)
    VALUES ('vallery', 'future-panel-v1', now() + interval '1 hour',
            now() + interval '1 day', profile_hash, assignment_hash,
            profile_ids, assignment_ids, 'fixed_panel_equal_zone_ideal_mean_v1',
            '[{"bucket_start":"future"}]'::jsonb,
            encode(pg_catalog.sha256(convert_to(
                '[{"bucket_start":"future"}]'::jsonb::text, 'UTF8')), 'hex'));

    rejected := false;
    BEGIN
        UPDATE public.crop_assignment_revisions SET operation = 'baseline';
    EXCEPTION WHEN OTHERS THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'assignment history was mutable'; END IF;
    rejected := false;
    BEGIN
        TRUNCATE public.crops;
    EXCEPTION WHEN OTHERS THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'assignment source truncate accepted'; END IF;
END;
$proof$;
ROLLBACK;
