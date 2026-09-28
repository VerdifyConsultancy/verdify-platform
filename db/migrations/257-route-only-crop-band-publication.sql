-- #371: append-only publication of prospective route-only observations.
-- Source artifact contains summary counts only: no raw climate, crop notes,
-- physical serials or privileged lineage rows. No historical backfill.
-- The guarded writer is the database owner; runtime roles receive only the
-- one-day, read-only projection below. This draft is deliberately unsealed:
-- qualification requires exact live predecessor/successor ordinary-login
-- digests. A logical restore changes catalog OIDs and ACLs, so its hashes
-- cannot be substituted for production hashes.
DO $unsealed$
BEGIN
    RAISE EXCEPTION 'migration 257 requires exact live boundary qualification';
END;
$unsealed$;
SET LOCAL search_path = pg_catalog, public, pg_temp;
LOCK TABLE public.schema_migrations,
           public.runtime_ordinary_login_attestation_receipts
    IN SHARE ROW EXCLUSIVE MODE;

CREATE TABLE public.route_only_crop_band_publications (
    publication_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    day date NOT NULL,
    greenhouse_id text NOT NULL CHECK (greenhouse_id = 'vallery'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    diagnostic_raw text NOT NULL CHECK (octet_length(diagnostic_raw) BETWEEN 1 AND 16384),
    artifact_sha256 text NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    target_revision_id bigint NOT NULL,
    contributor_revision_ids bigint[] NOT NULL CHECK (array_length(contributor_revision_ids, 1) = 3)
);
CREATE INDEX route_only_crop_band_publications_latest
    ON public.route_only_crop_band_publications (greenhouse_id, day, publication_id DESC);
COMMENT ON TABLE public.route_only_crop_band_publications IS
'Append-only prospective route-only fixed-panel observations. Database flush '
'snapshots are not fresh-probe, physical identity, crop placement, continuous '
'exposure, experiment or causal proof. Publication requires a reviewed frozen '
'target and three valid route-only declarations. No runtime role can write.';

CREATE FUNCTION public.fn_guard_route_only_crop_band_publication()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $guard$
DECLARE
    v_diagnostic jsonb;
    v_start timestamptz;
    v_end timestamptz;
    v_target public.fixed_panel_target_revisions;
    v_route jsonb;
    v_route_count integer;
    v_route_ids bigint[];
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'route-only publication is append-only';
    END IF;
    NEW.recorded_at := clock_timestamp();
    IF NEW.greenhouse_id <> 'vallery' OR NEW.day IS NULL THEN
        RAISE EXCEPTION 'route-only publication requires one supported day';
    END IF;
    v_start := ((NEW.day::timestamp + interval '6 hours') AT TIME ZONE 'America/Denver');
    v_end := (((NEW.day + 1)::timestamp) AT TIME ZONE 'America/Denver');
    IF v_end - v_start IS DISTINCT FROM interval '18 hours' OR NEW.recorded_at < v_end
       OR NEW.artifact_sha256 IS DISTINCT FROM encode(pg_catalog.sha256(convert_to(NEW.diagnostic_raw, 'UTF8')), 'hex') THEN
        RAISE EXCEPTION 'route-only publication window or exact artifact bytes invalid';
    END IF;
    v_diagnostic := NEW.diagnostic_raw::jsonb;
    IF jsonb_typeof(v_diagnostic) IS DISTINCT FROM 'object'
       OR v_diagnostic->>'definition' IS DISTINCT FROM 'fixed-panel-route-observation-v1'
       OR v_diagnostic->>'greenhouse_id' IS DISTINCT FROM NEW.greenhouse_id
       OR v_diagnostic->>'day' IS DISTINCT FROM NEW.day::text
       OR (v_diagnostic->>'window_start')::timestamptz IS DISTINCT FROM v_start
       OR (v_diagnostic->>'window_end')::timestamptz IS DISTINCT FROM v_end
       OR v_diagnostic->>'target_basis' IS DISTINCT FROM 'prospective_frozen_panel_mean_crop_reference'
       OR v_diagnostic->>'sample_basis' IS DISTINCT FROM 'database_flush_snapshot_not_per_probe_observation_time'
       OR v_diagnostic->>'contributor_scope' IS DISTINCT FROM 'source_route_only_no_physical_identity'
       OR v_diagnostic->'physical_proof_eligible' IS DISTINCT FROM 'false'::jsonb
       OR v_diagnostic->'physical_hardware_identity_verified' IS DISTINCT FROM 'false'::jsonb
       OR v_diagnostic->'per_probe_freshness_verified' IS DISTINCT FROM 'false'::jsonb
       OR v_diagnostic->'crop_placement_verified' IS DISTINCT FROM 'false'::jsonb
       OR v_diagnostic->'experiment_endpoint_eligible' IS DISTINCT FROM 'false'::jsonb
       OR v_diagnostic->'causal_effect_estimate' IS DISTINCT FROM 'false'::jsonb
       OR v_diagnostic->>'target_revision_id' IS DISTINCT FROM NEW.target_revision_id::text
       OR v_diagnostic->>'expected_bins' IS DISTINCT FROM '72'
       OR coalesce(v_diagnostic->>'panel_source_sha256' ~ '^[0-9a-f]{64}$', false) IS NOT TRUE
       OR jsonb_typeof(v_diagnostic->'panel_routes') IS DISTINCT FROM 'array'
       OR jsonb_array_length(v_diagnostic->'panel_routes') IS DISTINCT FROM 3
       OR v_diagnostic->'panel_members' IS DISTINCT FROM '["north", "east", "west"]'::jsonb THEN
        RAISE EXCEPTION 'route-only diagnostic scope or eligibility invalid';
    END IF;
    SELECT * INTO v_target FROM public.fixed_panel_target_revisions
     WHERE revision_id = NEW.target_revision_id AND greenhouse_id = NEW.greenhouse_id;
    IF NOT FOUND OR v_target.target_version IS DISTINCT FROM v_diagnostic->>'target_version'
       OR v_target.recorded_at >= v_start
       OR v_target.effective_from > v_start OR v_target.effective_to < v_end THEN
        RAISE EXCEPTION 'route-only publication lacks a pre-window frozen target';
    END IF;
    -- All overlapping contributor declarations must be exactly this fixed
    -- three-route panel. A replacement or overlapping ambiguity fails closed.
    SELECT count(*), array_agg(revision_id ORDER BY revision_id)
      INTO v_route_count, v_route_ids
      FROM public.fixed_panel_contributor_revisions
     WHERE greenhouse_id = NEW.greenhouse_id
       AND valid_from < v_end AND valid_to > v_start;
    IF v_route_count <> 3 OR NEW.contributor_revision_ids IS DISTINCT FROM v_route_ids THEN
        RAISE EXCEPTION 'route-only contributor revision set differs from day lineage';
    END IF;
    FOR v_route IN SELECT value FROM jsonb_array_elements(v_diagnostic->'panel_routes') LOOP
        IF NOT EXISTS (
            SELECT 1 FROM public.fixed_panel_contributor_revisions r
             WHERE r.revision_id = ANY(v_route_ids)
               AND r.greenhouse_id = NEW.greenhouse_id
               AND r.zone = v_route->>'zone'
               AND r.route_id = v_route->>'route_id'
               AND r.modbus_address::text = v_route->>'modbus_address'
               AND r.source_revision_sha256 = v_diagnostic->>'panel_source_sha256'
               AND r.valid_from <= v_start AND r.valid_to >= v_end
               AND r.recorded_at < v_start
               AND r.capture_scope = 'route_only'
               AND r.physical_serial IS NULL
               AND r.physical_evidence_sha256 IS NULL
        ) THEN
            RAISE EXCEPTION 'route-only panel route has no prospective declaration';
        END IF;
    END LOOP;
    IF (SELECT count(DISTINCT value->>'zone')
          FROM jsonb_array_elements(v_diagnostic->'panel_routes')) <> 3 THEN
        RAISE EXCEPTION 'route-only panel contains duplicate zones';
    END IF;
    RETURN NEW;
END;
$guard$;

CREATE TRIGGER route_only_crop_band_publications_no_mutation
    BEFORE UPDATE OR DELETE ON public.route_only_crop_band_publications
    FOR EACH ROW EXECUTE FUNCTION public.fn_guard_route_only_crop_band_publication();
CREATE TRIGGER route_only_crop_band_publications_no_truncate
    BEFORE TRUNCATE ON public.route_only_crop_band_publications
    FOR EACH STATEMENT EXECUTE FUNCTION public.fn_guard_route_only_crop_band_publication();
CREATE TRIGGER route_only_crop_band_publications_validate
    BEFORE INSERT ON public.route_only_crop_band_publications
    FOR EACH ROW EXECUTE FUNCTION public.fn_guard_route_only_crop_band_publication();

CREATE FUNCTION public.fn_route_only_crop_band_diagnostic(p_day date, p_greenhouse text DEFAULT 'vallery')
RETURNS TABLE (
    day date, greenhouse_id text, served_at timestamptz,
    revision_id bigint, recorded_at timestamptz,
    unavailable_reason text, diagnostic jsonb
) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $reader$
BEGIN
    IF p_day IS NULL OR p_greenhouse IS DISTINCT FROM 'vallery' THEN
        RAISE EXCEPTION 'one explicit day and supported greenhouse required' USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
    WITH latest AS (
        SELECT p.* FROM public.route_only_crop_band_publications p
         WHERE p.day = p_day AND p.greenhouse_id = p_greenhouse
         ORDER BY p.publication_id DESC LIMIT 1
    )
    SELECT p_day, p_greenhouse, statement_timestamp(), p.publication_id, p.recorded_at,
           CASE WHEN p.publication_id IS NULL THEN 'not_published'
                WHEN encode(pg_catalog.sha256(convert_to(p.diagnostic_raw, 'UTF8')), 'hex')
                     IS DISTINCT FROM p.artifact_sha256 THEN 'revision_mismatch'
                ELSE NULL END,
           CASE WHEN encode(pg_catalog.sha256(convert_to(p.diagnostic_raw, 'UTF8')), 'hex') = p.artifact_sha256
                THEN p.diagnostic_raw::jsonb ELSE NULL END
      FROM (SELECT 1) singleton LEFT JOIN latest p ON true;
END;
$reader$;

DO $owner$
DECLARE owner_name text;
BEGIN
    SELECT pg_get_userbyid(datdba) INTO owner_name
      FROM pg_database WHERE datname = current_database();
    IF owner_name IN ('verdify_api_runtime', 'verdify_ingestor_runtime',
                      'verdify_api_runtime_login', 'verdify_ingestor_runtime_login') THEN
        RAISE EXCEPTION 'runtime role must not own route-only publication';
    END IF;
    EXECUTE format('ALTER TABLE public.route_only_crop_band_publications OWNER TO %I', owner_name);
    EXECUTE format('ALTER FUNCTION public.fn_guard_route_only_crop_band_publication() OWNER TO %I', owner_name);
    EXECUTE format('ALTER FUNCTION public.fn_route_only_crop_band_diagnostic(date,text) OWNER TO %I', owner_name);
END;
$owner$;
REVOKE ALL ON public.route_only_crop_band_publications FROM PUBLIC,
    verdify_api_runtime, verdify_ingestor_runtime,
    verdify_api_runtime_login, verdify_ingestor_runtime_login;
REVOKE ALL ON SEQUENCE public.route_only_crop_band_publications_publication_id_seq FROM PUBLIC,
    verdify_api_runtime, verdify_ingestor_runtime,
    verdify_api_runtime_login, verdify_ingestor_runtime_login;
REVOKE ALL ON FUNCTION public.fn_guard_route_only_crop_band_publication() FROM PUBLIC,
    verdify_api_runtime, verdify_ingestor_runtime,
    verdify_api_runtime_login, verdify_ingestor_runtime_login;
REVOKE ALL ON FUNCTION public.fn_route_only_crop_band_diagnostic(date,text) FROM PUBLIC,
    verdify_api_runtime, verdify_ingestor_runtime,
    verdify_api_runtime_login, verdify_ingestor_runtime_login;
GRANT EXECUTE ON FUNCTION public.fn_route_only_crop_band_diagnostic(date,text) TO verdify_api_runtime;
COMMENT ON FUNCTION public.fn_route_only_crop_band_diagnostic(date,text) IS
'One-day route-only observational summary. No raw climate or source-history access, '
'physical efficacy, experiment or causal claim. The newest publication alone is considered.';
