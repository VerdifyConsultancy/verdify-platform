-- #782: prospective crop assignment lineage for frozen fixed-panel targets.
-- The seed records migration-time state only; it does not reconstruct past
-- plantings or assert that a future crop will remain physically in its zone.
SET LOCAL search_path = pg_catalog, public, pg_temp;
LOCK TABLE public.crops IN SHARE ROW EXCLUSIVE MODE;

CREATE TABLE public.crop_assignment_revisions (
    revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    operation text NOT NULL CHECK (operation IN ('baseline', 'insert', 'update', 'delete')),
    crop_id integer NOT NULL,
    greenhouse_id text,
    crop jsonb NOT NULL CHECK (jsonb_typeof(crop) = 'object'),
    crop_sha256 text NOT NULL CHECK (
        crop_sha256 = encode(pg_catalog.sha256(convert_to(crop::text, 'UTF8')), 'hex')),
    transaction_id bigint NOT NULL DEFAULT txid_current()
);
CREATE INDEX crop_assignment_revisions_asof
    ON public.crop_assignment_revisions (crop_id, revision_id DESC);
COMMENT ON TABLE public.crop_assignment_revisions IS
'Append-only crop row revisions from migration 255 forward. Baseline means '
'migration-time database state, not original planting, physical placement, '
'or a reconstructed historical assignment. recorded_at is capture time.';

CREATE FUNCTION public.fn_capture_crop_assignment_revision()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $function$
DECLARE
    captured jsonb;
    source_row public.crops;
BEGIN
    IF TG_OP = 'DELETE' THEN source_row := OLD; ELSE source_row := NEW; END IF;
    captured := to_jsonb(source_row);
    IF TG_OP = 'UPDATE' AND to_jsonb(OLD) = captured THEN RETURN NULL; END IF;
    IF TG_OP = 'UPDATE' AND OLD.id IS DISTINCT FROM NEW.id THEN
        INSERT INTO public.crop_assignment_revisions
            (operation, crop_id, greenhouse_id, crop, crop_sha256)
        VALUES ('delete', OLD.id, OLD.greenhouse_id, to_jsonb(OLD),
                encode(pg_catalog.sha256(convert_to(to_jsonb(OLD)::text, 'UTF8')), 'hex'));
    END IF;
    INSERT INTO public.crop_assignment_revisions
        (operation, crop_id, greenhouse_id, crop, crop_sha256)
    VALUES (lower(TG_OP), source_row.id, source_row.greenhouse_id, captured,
            encode(pg_catalog.sha256(convert_to(captured::text, 'UTF8')), 'hex'));
    RETURN NULL; -- AFTER trigger
END;
$function$;

CREATE FUNCTION public.fn_reject_crop_assignment_history_mutation()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $function$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF pg_trigger_depth() < 2 THEN
            RAISE EXCEPTION 'crop assignment revisions require the source trigger';
        END IF;
        NEW.recorded_at := clock_timestamp();
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = 'crops' THEN
        RAISE EXCEPTION 'crops TRUNCATE bypasses assignment revision capture';
    END IF;
    RAISE EXCEPTION 'crop assignment revisions are append-only';
END;
$function$;

DO $owner$
DECLARE owner_name text;
BEGIN
    SELECT pg_get_userbyid(datdba) INTO owner_name
      FROM pg_database WHERE datname = current_database();
    IF owner_name IN ('verdify_api_runtime', 'verdify_ingestor_runtime',
                      'verdify_api_runtime_login', 'verdify_ingestor_runtime_login') THEN
        RAISE EXCEPTION 'runtime role must not own crop assignment history';
    END IF;
    EXECUTE format('ALTER TABLE public.crop_assignment_revisions OWNER TO %I', owner_name);
    EXECUTE format('ALTER FUNCTION public.fn_capture_crop_assignment_revision() OWNER TO %I', owner_name);
    EXECUTE format('ALTER FUNCTION public.fn_reject_crop_assignment_history_mutation() OWNER TO %I', owner_name);
END;
$owner$;
REVOKE ALL ON public.crop_assignment_revisions
    FROM PUBLIC, verdify_api_runtime, verdify_ingestor_runtime,
         verdify_api_runtime_login, verdify_ingestor_runtime_login;
REVOKE ALL ON SEQUENCE public.crop_assignment_revisions_revision_id_seq
    FROM PUBLIC, verdify_api_runtime, verdify_ingestor_runtime,
         verdify_api_runtime_login, verdify_ingestor_runtime_login;
REVOKE ALL ON FUNCTION public.fn_capture_crop_assignment_revision(),
                   public.fn_reject_crop_assignment_history_mutation()
    FROM PUBLIC, verdify_api_runtime, verdify_ingestor_runtime,
         verdify_api_runtime_login, verdify_ingestor_runtime_login;

INSERT INTO public.crop_assignment_revisions
    (operation, crop_id, greenhouse_id, crop, crop_sha256)
SELECT 'baseline', c.id, c.greenhouse_id, to_jsonb(c),
       encode(pg_catalog.sha256(convert_to(to_jsonb(c)::text, 'UTF8')), 'hex')
  FROM public.crops c;

CREATE TRIGGER crop_assignment_revisions_no_mutation
    BEFORE UPDATE OR DELETE ON public.crop_assignment_revisions
    FOR EACH ROW EXECUTE FUNCTION public.fn_reject_crop_assignment_history_mutation();
CREATE TRIGGER crop_assignment_revisions_no_direct_insert
    BEFORE INSERT ON public.crop_assignment_revisions
    FOR EACH ROW EXECUTE FUNCTION public.fn_reject_crop_assignment_history_mutation();
CREATE TRIGGER crop_assignment_revisions_no_truncate
    BEFORE TRUNCATE ON public.crop_assignment_revisions
    FOR EACH STATEMENT EXECUTE FUNCTION public.fn_reject_crop_assignment_history_mutation();
CREATE TRIGGER crops_capture_assignment_revision
    AFTER INSERT OR UPDATE OR DELETE ON public.crops
    FOR EACH ROW EXECUTE FUNCTION public.fn_capture_crop_assignment_revision();
CREATE TRIGGER crops_no_unaudited_truncate
    BEFORE TRUNCATE ON public.crops
    FOR EACH STATEMENT EXECUTE FUNCTION public.fn_reject_crop_assignment_history_mutation();

-- No target declaration exists at installation. Require both complete live
-- snapshots and their exact row-revision vectors on every new declaration.
ALTER TABLE public.fixed_panel_target_revisions
    ADD COLUMN source_assignment_state_sha256 text NOT NULL
        CHECK (source_assignment_state_sha256 ~ '^[0-9a-f]{64}$'),
    ADD COLUMN profile_revision_ids bigint[] NOT NULL
        CHECK (cardinality(profile_revision_ids) BETWEEN 1 AND 10000),
    ADD COLUMN assignment_revision_ids bigint[] NOT NULL
        CHECK (cardinality(assignment_revision_ids) BETWEEN 1 AND 10000),
    ADD COLUMN target_rule text NOT NULL
        CHECK (target_rule = 'fixed_panel_equal_zone_ideal_mean_v1');
COMMENT ON COLUMN public.fixed_panel_target_revisions.source_assignment_state_sha256 IS
'SHA-256 of PostgreSQL jsonb text for all current greenhouse crops ordered by ID, including inactive rows.';
COMMENT ON COLUMN public.fixed_panel_target_revisions.target_rule IS
'Prospective panel-mean grading reference: per-zone ideal intersections/defaults, then equal mean of north/east/west low and high edges. Not a per-zone crop-compliance or device-consumed band claim.';

CREATE OR REPLACE FUNCTION public.fn_guard_fixed_panel_source_history()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $function$
DECLARE
    live_profiles_sha256 text;
    live_assignments_sha256 text;
    live_profile_revision_ids bigint[];
    live_assignment_revision_ids bigint[];
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'fixed panel source history cannot be truncated';
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        RAISE EXCEPTION 'fixed panel source history is append-only';
    END IF;
    NEW.recorded_at := clock_timestamp();
    IF TG_TABLE_NAME = 'fixed_panel_contributor_revisions' THEN
        IF NEW.valid_from < NEW.recorded_at THEN
            RAISE EXCEPTION 'fixed panel source binding cannot claim prior validity';
        END IF;
    ELSIF TG_TABLE_NAME = 'fixed_panel_target_revisions' THEN
        IF NEW.effective_from < NEW.recorded_at THEN
            RAISE EXCEPTION 'fixed panel target cannot claim prior effectiveness';
        END IF;
        -- The rare declaration holds this short lock through its transaction,
        -- preventing a source edit from racing the snapshot/check/commit.
        LOCK TABLE public.crops, public.crop_target_profiles IN SHARE MODE;
        SELECT encode(pg_catalog.sha256(convert_to(
                   COALESCE(jsonb_agg(to_jsonb(p) ORDER BY p.id), '[]'::jsonb)::text,
                   'UTF8')), 'hex')
          INTO live_profiles_sha256
          FROM public.crop_target_profiles p
         WHERE p.greenhouse_id = NEW.greenhouse_id;
        SELECT encode(pg_catalog.sha256(convert_to(
                   COALESCE(jsonb_agg(to_jsonb(c) ORDER BY c.id), '[]'::jsonb)::text,
                   'UTF8')), 'hex')
          INTO live_assignments_sha256
          FROM public.crops c
         WHERE c.greenhouse_id = NEW.greenhouse_id;
        IF NEW.source_profile_state_sha256 <> live_profiles_sha256
           OR NEW.source_assignment_state_sha256 <> live_assignments_sha256 THEN
            RAISE EXCEPTION 'fixed panel target source snapshot differs from live source';
        END IF;
        SELECT array_agg(r.revision_id ORDER BY r.revision_id)
          INTO live_profile_revision_ids
          FROM public.crop_target_profiles p
          JOIN LATERAL (
              SELECT revision_id, operation, profile
                FROM public.crop_target_profile_revisions
               WHERE profile_id = p.id ORDER BY revision_id DESC LIMIT 1
          ) r ON r.operation <> 'delete' AND r.profile = to_jsonb(p)
         WHERE p.greenhouse_id = NEW.greenhouse_id;
        SELECT array_agg(r.revision_id ORDER BY r.revision_id)
          INTO live_assignment_revision_ids
          FROM public.crops c
          JOIN LATERAL (
              SELECT revision_id, operation, crop
                FROM public.crop_assignment_revisions
               WHERE crop_id = c.id ORDER BY revision_id DESC LIMIT 1
          ) r ON r.operation <> 'delete' AND r.crop = to_jsonb(c)
         WHERE c.greenhouse_id = NEW.greenhouse_id;
        IF NEW.profile_revision_ids IS DISTINCT FROM live_profile_revision_ids
           OR NEW.assignment_revision_ids IS DISTINCT FROM live_assignment_revision_ids
           OR cardinality(live_profile_revision_ids) <> (
               SELECT count(*) FROM public.crop_target_profiles WHERE greenhouse_id = NEW.greenhouse_id)
           OR cardinality(live_assignment_revision_ids) <> (
               SELECT count(*) FROM public.crops WHERE greenhouse_id = NEW.greenhouse_id) THEN
            RAISE EXCEPTION 'fixed panel target row-revision vector differs from live source';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;
