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
\i db/migrations/255-fixed-panel-crop-assignment-lineage.sql

DO $proof$
DECLARE
    profile_hash text;
    assignment_hash text;
    profile_ids bigint[];
    assignment_ids bigint[];
    rejected boolean;
BEGIN
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
