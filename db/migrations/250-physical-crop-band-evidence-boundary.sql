-- #371: a separate revision boundary for qualified fixed-panel crop outcomes.
-- No historical rows are backfilled. The Sep 4 counterfactual and the
-- house-average/graded daily columns cannot populate this table.
DO $preflight$
DECLARE v_login_name text;
BEGIN
    IF (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2 THEN
        RAISE EXCEPTION 'ordinary-runtime attestation receipt set is incomplete';
    END IF;
    FOREACH v_login_name IN ARRAY ARRAY['verdify_api_runtime_login', 'verdify_ingestor_runtime_login'] LOOP
        IF (SELECT receipt.boundary_sha256 FROM public.runtime_ordinary_login_attestation_receipts receipt
             WHERE receipt.login_name = v_login_name)
           IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(v_login_name) THEN
            RAISE EXCEPTION 'pre-existing ordinary-runtime boundary drift for %', v_login_name;
        END IF;
    END LOOP;
END;
$preflight$;

CREATE TABLE public.crop_band_physical_revisions (
    revision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    greenhouse_id text NOT NULL CHECK (greenhouse_id = 'vallery'),
    day date NOT NULL,
    diagnostic jsonb NOT NULL CHECK ((
        jsonb_typeof(diagnostic) = 'object'
        AND diagnostic->>'definition' = 'fixed-panel-crop-band-v1'
        AND diagnostic->>'day' = day::text
        AND diagnostic->>'greenhouse_id' = greenhouse_id
        AND diagnostic->>'target_basis' = 'immutable_crop_targets_as_of_bin'
        AND diagnostic->>'sample_basis' = 'fixed_panel_fresh_probe_observations'
        AND diagnostic->'fixed_sensor_panel' = 'true'::jsonb
        AND diagnostic->'historical_crop_target_verified' = 'true'::jsonb
        AND diagnostic->'per_probe_freshness_verified' = 'true'::jsonb
        AND diagnostic->'physical_proof_eligible' = 'true'::jsonb
        AND diagnostic->'experiment_endpoint_eligible' = 'false'::jsonb
    ) IS TRUE)
);
CREATE INDEX crop_band_physical_revisions_lookup
    ON public.crop_band_physical_revisions (greenhouse_id, day, revision_id DESC);
REVOKE ALL ON public.crop_band_physical_revisions FROM PUBLIC,
    verdify_api_runtime, verdify_ingestor_runtime,
    verdify_api_runtime_login, verdify_ingestor_runtime_login;

CREATE FUNCTION public.fn_reject_crop_band_physical_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    RAISE EXCEPTION 'crop-band physical revisions are append-only';
END;
$function$;
REVOKE ALL ON FUNCTION public.fn_reject_crop_band_physical_revision_mutation() FROM PUBLIC,
    verdify_api_runtime, verdify_ingestor_runtime,
    verdify_api_runtime_login, verdify_ingestor_runtime_login;
CREATE TRIGGER crop_band_physical_revisions_append_only
BEFORE UPDATE OR DELETE ON public.crop_band_physical_revisions
FOR EACH ROW EXECUTE FUNCTION public.fn_reject_crop_band_physical_revision_mutation();

CREATE FUNCTION public.fn_crop_band_physical_evidence(p_day date, p_greenhouse text DEFAULT 'vallery')
RETURNS TABLE (
    day date, greenhouse_id text, served_at timestamptz,
    revision_id bigint, recorded_at timestamptz,
    unavailable_reason text, diagnostic jsonb
) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $function$
BEGIN
    IF p_day IS NULL OR p_greenhouse IS DISTINCT FROM 'vallery' THEN
        RAISE EXCEPTION 'one explicit day and supported greenhouse required' USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
    SELECT p_day, p_greenhouse, statement_timestamp(), r.revision_id,
           r.recorded_at,
           CASE WHEN r.revision_id IS NULL THEN 'not_computed'::text ELSE NULL::text END,
           r.diagnostic
      FROM (SELECT 1) singleton
      LEFT JOIN LATERAL (
          SELECT x.revision_id, x.recorded_at, x.diagnostic
            FROM public.crop_band_physical_revisions x
           WHERE x.greenhouse_id = p_greenhouse AND x.day = p_day
           ORDER BY x.revision_id DESC LIMIT 1
      ) r ON true;
END;
$function$;

DO $owner$
DECLARE owner_name text;
BEGIN
    SELECT pg_get_userbyid(datdba) INTO owner_name
      FROM pg_database WHERE datname = current_database();
    IF owner_name IN ('verdify_api_runtime', 'verdify_ingestor_runtime',
                      'verdify_api_runtime_login', 'verdify_ingestor_runtime_login') THEN
        RAISE EXCEPTION 'runtime role must not own physical evidence';
    END IF;
    EXECUTE format('ALTER TABLE public.crop_band_physical_revisions OWNER TO %I', owner_name);
    EXECUTE format('ALTER FUNCTION public.fn_reject_crop_band_physical_revision_mutation() OWNER TO %I', owner_name);
    EXECUTE format('ALTER FUNCTION public.fn_crop_band_physical_evidence(date,text) OWNER TO %I', owner_name);
END;
$owner$;
REVOKE ALL ON FUNCTION public.fn_crop_band_physical_evidence(date,text) FROM PUBLIC,
    verdify_api_runtime, verdify_ingestor_runtime,
    verdify_api_runtime_login, verdify_ingestor_runtime_login;
GRANT EXECUTE ON FUNCTION public.fn_crop_band_physical_evidence(date,text) TO verdify_api_runtime;
COMMENT ON TABLE public.crop_band_physical_revisions IS
'Append-only qualified fixed-panel crop outcome claims. No runtime writer grant, '
'no automatic promotion from daily_summary, observed-minute evidence, or the Sep 4 counterfactual. '
'Manifest hashes and validity flags are claims to be qualified against immutable raw inputs.';
COMMENT ON FUNCTION public.fn_crop_band_physical_evidence(date,text) IS
'Latest one-day physical crop-band claim or explicit not_computed; no older-valid fallback. '
'Consumers must validate counts, versions, window, and provenance. Separate from controller credit.';

UPDATE public.runtime_ordinary_login_attestation_receipts receipt
   SET boundary_sha256 = public.fn_runtime_ordinary_boundary_digest(receipt.login_name),
       captured_at = clock_timestamp()
 WHERE receipt.login_name IN ('verdify_api_runtime_login', 'verdify_ingestor_runtime_login')
   AND receipt.boundary_sha256 IS DISTINCT FROM
       public.fn_runtime_ordinary_boundary_digest(receipt.login_name);

DO $postflight$
DECLARE v_login_name text;
BEGIN
    IF (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2 THEN
        RAISE EXCEPTION 'ordinary-runtime attestation receipt set changed';
    END IF;
    FOREACH v_login_name IN ARRAY ARRAY['verdify_api_runtime_login', 'verdify_ingestor_runtime_login'] LOOP
        IF (SELECT receipt.boundary_sha256 FROM public.runtime_ordinary_login_attestation_receipts receipt
             WHERE receipt.login_name = v_login_name)
           IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(v_login_name) THEN
            RAISE EXCEPTION 'post-change ordinary-runtime attestation failed for %', v_login_name;
        END IF;
    END LOOP;
END;
$postflight$;
