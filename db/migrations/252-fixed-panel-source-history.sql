-- #782: capture future crop definition changes and fixed-panel source bindings.
-- This is source history, not an experiment admission, target-bin export, or
-- authentication of physical serials. Existing rows are a migration-time
-- baseline only; no effective/recorded history is imputed before installation.
-- Forward-only, outer-transaction safe. Serialize seed and trigger installation.
SET LOCAL search_path = pg_catalog, public, pg_temp;
LOCK TABLE public.crop_target_profiles IN SHARE ROW EXCLUSIVE MODE;

CREATE TABLE public.crop_target_profile_revisions (
    revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    operation text NOT NULL CHECK (operation IN ('baseline', 'insert', 'update', 'delete')),
    profile_id integer NOT NULL,
    greenhouse_id text,
    profile jsonb NOT NULL CHECK (jsonb_typeof(profile) = 'object'),
    profile_sha256 text NOT NULL CHECK (
        profile_sha256 = encode(pg_catalog.sha256(convert_to(profile::text, 'UTF8')), 'hex')),
    transaction_id bigint NOT NULL DEFAULT txid_current()
);
CREATE INDEX crop_target_profile_revisions_asof
    ON public.crop_target_profile_revisions (profile_id, revision_id DESC);

COMMENT ON TABLE public.crop_target_profile_revisions IS
'Append-only row revisions of crop_target_profiles from installation forward. '
'The baseline records migration-time contents, not their original effective or '
'recorded dates. recorded_at is database capture time, not a verified commit or '
'physical effectiveness timestamp. A future fixed-target export must use a '
'pre-window frozen revision and explicitly resolve crop, zone, season and bins.';

CREATE FUNCTION public.fn_capture_crop_target_profile_revision()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $function$
DECLARE
    captured jsonb;
    source_row public.crop_target_profiles;
BEGIN
    IF TG_OP = 'DELETE' THEN
        source_row := OLD;
    ELSE
        source_row := NEW;
    END IF;
    captured := to_jsonb(source_row);
    IF TG_OP = 'UPDATE' AND to_jsonb(OLD) = captured THEN
        RETURN NULL;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.id IS DISTINCT FROM NEW.id THEN
        INSERT INTO public.crop_target_profile_revisions
            (operation, profile_id, greenhouse_id, profile, profile_sha256)
        VALUES ('delete', OLD.id, OLD.greenhouse_id, to_jsonb(OLD),
                encode(pg_catalog.sha256(convert_to(to_jsonb(OLD)::text, 'UTF8')), 'hex'));
    END IF;
    INSERT INTO public.crop_target_profile_revisions
        (operation, profile_id, greenhouse_id, profile, profile_sha256)
    VALUES (lower(TG_OP), source_row.id, source_row.greenhouse_id, captured,
            encode(pg_catalog.sha256(convert_to(captured::text, 'UTF8')), 'hex'));
    RETURN NULL; -- AFTER trigger; never changes the source row
END;
$function$;

-- An explicit, future-effective frozen target definition is separate from
-- the live crop profile table. It never changes the production resolver.
CREATE TABLE public.fixed_panel_target_revisions (
    revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    greenhouse_id text NOT NULL CHECK (greenhouse_id = 'vallery'),
    target_version text NOT NULL UNIQUE CHECK (length(target_version) BETWEEN 1 AND 128),
    effective_from timestamptz NOT NULL,
    effective_to timestamptz NOT NULL,
    source_profile_state_sha256 text NOT NULL
        CHECK (source_profile_state_sha256 ~ '^[0-9a-f]{64}$'),
    target_bins jsonb NOT NULL CHECK (
        jsonb_typeof(target_bins) = 'array'
        AND jsonb_array_length(target_bins) BETWEEN 1 AND 9000),
    db_target_bins_sha256 text NOT NULL CHECK (
        db_target_bins_sha256 = encode(
            pg_catalog.sha256(convert_to(target_bins::text, 'UTF8')), 'hex')),
    transaction_id bigint NOT NULL DEFAULT txid_current(),
    CHECK (effective_from < effective_to)
);
CREATE INDEX fixed_panel_target_revisions_effective
    ON public.fixed_panel_target_revisions (greenhouse_id, effective_from, effective_to);

COMMENT ON TABLE public.fixed_panel_target_revisions IS
'Append-only future-effective fixed-panel target declarations. target_bins '
'must be independently verified and exported through pretrial_provenance.py; '
'the database hash is over PostgreSQL jsonb text, not a proof of crop truth. '
'This table does not change any live crop, band, device or experiment setting.';

CREATE TABLE public.fixed_panel_contributor_revisions (
    revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    greenhouse_id text NOT NULL CHECK (greenhouse_id = 'vallery'),
    zone text NOT NULL CHECK (zone IN ('north', 'east', 'west')),
    route_id text NOT NULL CHECK (length(route_id) BETWEEN 1 AND 128),
    modbus_address integer NOT NULL CHECK (modbus_address BETWEEN 1 AND 247),
    temp_field text NOT NULL,
    vpd_field text NOT NULL,
    physical_serial text,
    physical_evidence_sha256 text,
    source_revision_sha256 text NOT NULL CHECK (source_revision_sha256 ~ '^[0-9a-f]{64}$'),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz NOT NULL,
    capture_scope text NOT NULL CHECK (capture_scope IN ('route_only', 'hardware_attested')),
    CHECK (temp_field = 'temp_' || zone AND vpd_field = 'vpd_' || zone),
    CHECK (valid_from < valid_to),
    CHECK (
        (capture_scope = 'route_only' AND physical_serial IS NULL AND physical_evidence_sha256 IS NULL)
        OR (capture_scope = 'hardware_attested' AND length(physical_serial) BETWEEN 1 AND 128
            AND physical_evidence_sha256 ~ '^[0-9a-f]{64}$')
    )
);
CREATE INDEX fixed_panel_contributor_revisions_asof
    ON public.fixed_panel_contributor_revisions (greenhouse_id, zone, valid_from, valid_to);

COMMENT ON TABLE public.fixed_panel_contributor_revisions IS
'Append-only, future-only source binding declarations. A route-only row does '
'not identify a physical sensor. A hardware_attested row records an external '
'serial/evidence claim but does not authenticate that claim; inspect the source '
'inventory independently. No historical installation interval is inferred.';

CREATE FUNCTION public.fn_guard_fixed_panel_source_history()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $function$
DECLARE
    live_profiles_sha256 text;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'fixed panel source history cannot be truncated';
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        RAISE EXCEPTION 'fixed panel source history is append-only';
    END IF;
    -- Callers cannot backdate a binding by supplying their own recorded_at.
    NEW.recorded_at := clock_timestamp();
    IF TG_TABLE_NAME = 'fixed_panel_contributor_revisions' THEN
        IF NEW.valid_from < NEW.recorded_at THEN
            RAISE EXCEPTION 'fixed panel source binding cannot claim prior validity';
        END IF;
    ELSIF TG_TABLE_NAME = 'fixed_panel_target_revisions' THEN
        IF NEW.effective_from < NEW.recorded_at THEN
            RAISE EXCEPTION 'fixed panel target cannot claim prior effectiveness';
        END IF;
        SELECT encode(pg_catalog.sha256(convert_to(
                   COALESCE(jsonb_agg(to_jsonb(p) ORDER BY p.id), '[]'::jsonb)::text,
                   'UTF8')), 'hex')
          INTO live_profiles_sha256
          FROM public.crop_target_profiles p
         WHERE p.greenhouse_id = NEW.greenhouse_id;
        IF NEW.source_profile_state_sha256 <> live_profiles_sha256 THEN
            RAISE EXCEPTION 'fixed panel target profile snapshot hash differs from live source';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE FUNCTION public.fn_reject_crop_target_history_mutation()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $function$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF pg_trigger_depth() < 2 THEN
            RAISE EXCEPTION 'crop target profile revisions require the source trigger';
        END IF;
        NEW.recorded_at := clock_timestamp();
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = 'crop_target_profiles' THEN
        RAISE EXCEPTION 'crop_target_profiles TRUNCATE bypasses revision capture';
    END IF;
    RAISE EXCEPTION 'crop target profile revisions are append-only';
END;
$function$;

-- The owner is the database owner. Ordinary runtime roles cannot insert,
-- rewrite or inspect the private physical-source history.
DO $owner$
DECLARE owner_name text;
BEGIN
    SELECT pg_get_userbyid(datdba) INTO owner_name
      FROM pg_database WHERE datname = current_database();
    IF owner_name IN ('verdify_api_runtime', 'verdify_ingestor_runtime',
                      'verdify_api_runtime_login', 'verdify_ingestor_runtime_login') THEN
        RAISE EXCEPTION 'runtime role must not own fixed panel source history';
    END IF;
    EXECUTE format('ALTER TABLE public.crop_target_profile_revisions OWNER TO %I', owner_name);
    EXECUTE format('ALTER TABLE public.fixed_panel_target_revisions OWNER TO %I', owner_name);
    EXECUTE format('ALTER TABLE public.fixed_panel_contributor_revisions OWNER TO %I', owner_name);
    EXECUTE format('ALTER FUNCTION public.fn_capture_crop_target_profile_revision() OWNER TO %I', owner_name);
    EXECUTE format('ALTER FUNCTION public.fn_guard_fixed_panel_source_history() OWNER TO %I', owner_name);
    EXECUTE format('ALTER FUNCTION public.fn_reject_crop_target_history_mutation() OWNER TO %I', owner_name);
END;
$owner$;

REVOKE ALL ON public.crop_target_profile_revisions, public.fixed_panel_target_revisions,
              public.fixed_panel_contributor_revisions
    FROM PUBLIC, verdify_api_runtime, verdify_ingestor_runtime,
         verdify_api_runtime_login, verdify_ingestor_runtime_login;
REVOKE ALL ON SEQUENCE public.crop_target_profile_revisions_revision_id_seq,
                       public.fixed_panel_target_revisions_revision_id_seq,
                       public.fixed_panel_contributor_revisions_revision_id_seq
    FROM PUBLIC, verdify_api_runtime, verdify_ingestor_runtime,
         verdify_api_runtime_login, verdify_ingestor_runtime_login;
REVOKE ALL ON FUNCTION public.fn_capture_crop_target_profile_revision(),
                       public.fn_guard_fixed_panel_source_history(),
                       public.fn_reject_crop_target_history_mutation()
    FROM PUBLIC, verdify_api_runtime, verdify_ingestor_runtime,
         verdify_api_runtime_login, verdify_ingestor_runtime_login;

-- This is a captured baseline at installation time only. It deliberately
-- precedes the direct-insert guard, which protects all subsequent revisions.
INSERT INTO public.crop_target_profile_revisions
    (operation, profile_id, greenhouse_id, profile, profile_sha256)
SELECT 'baseline', p.id, p.greenhouse_id, to_jsonb(p),
       encode(pg_catalog.sha256(convert_to(to_jsonb(p)::text, 'UTF8')), 'hex')
FROM public.crop_target_profiles p;

CREATE TRIGGER crop_target_profile_revisions_no_mutation
    BEFORE UPDATE OR DELETE ON public.crop_target_profile_revisions
    FOR EACH ROW EXECUTE FUNCTION public.fn_reject_crop_target_history_mutation();
CREATE TRIGGER crop_target_profile_revisions_no_direct_insert
    BEFORE INSERT ON public.crop_target_profile_revisions
    FOR EACH ROW EXECUTE FUNCTION public.fn_reject_crop_target_history_mutation();
CREATE TRIGGER crop_target_profile_revisions_no_truncate
    BEFORE TRUNCATE ON public.crop_target_profile_revisions
    FOR EACH STATEMENT EXECUTE FUNCTION public.fn_reject_crop_target_history_mutation();
CREATE TRIGGER fixed_panel_contributor_revisions_no_mutation
    BEFORE UPDATE OR DELETE ON public.fixed_panel_contributor_revisions
    FOR EACH ROW EXECUTE FUNCTION public.fn_guard_fixed_panel_source_history();
CREATE TRIGGER fixed_panel_contributor_revisions_no_truncate
    BEFORE TRUNCATE ON public.fixed_panel_contributor_revisions
    FOR EACH STATEMENT EXECUTE FUNCTION public.fn_guard_fixed_panel_source_history();
CREATE TRIGGER fixed_panel_contributor_revisions_no_backdate
    BEFORE INSERT ON public.fixed_panel_contributor_revisions
    FOR EACH ROW EXECUTE FUNCTION public.fn_guard_fixed_panel_source_history();
CREATE TRIGGER fixed_panel_target_revisions_no_mutation
    BEFORE UPDATE OR DELETE ON public.fixed_panel_target_revisions
    FOR EACH ROW EXECUTE FUNCTION public.fn_guard_fixed_panel_source_history();
CREATE TRIGGER fixed_panel_target_revisions_no_truncate
    BEFORE TRUNCATE ON public.fixed_panel_target_revisions
    FOR EACH STATEMENT EXECUTE FUNCTION public.fn_guard_fixed_panel_source_history();
CREATE TRIGGER fixed_panel_target_revisions_no_backdate
    BEFORE INSERT ON public.fixed_panel_target_revisions
    FOR EACH ROW EXECUTE FUNCTION public.fn_guard_fixed_panel_source_history();

CREATE TRIGGER crop_target_profiles_capture_revision
    AFTER INSERT OR UPDATE OR DELETE ON public.crop_target_profiles
    FOR EACH ROW EXECUTE FUNCTION public.fn_capture_crop_target_profile_revision();
CREATE TRIGGER crop_target_profiles_no_unaudited_truncate
    BEFORE TRUNCATE ON public.crop_target_profiles
    FOR EACH STATEMENT EXECUTE FUNCTION public.fn_reject_crop_target_history_mutation();
