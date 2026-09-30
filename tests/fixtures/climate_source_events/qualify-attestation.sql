\set ON_ERROR_STOP on
BEGIN;
DO $guard$ BEGIN
 IF current_database() NOT IN ('verdify_rehearsal','climate_265_clone_b') OR inet_server_addr() IS NOT NULL OR current_setting('listen_addresses')<>'' THEN RAISE EXCEPTION 'disposable socket-only clone required'; END IF;
END $guard$;
-- Clone-specific receipts only, after independent mapped successor equivalence.
UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=public.fn_runtime_ordinary_boundary_digest(login_name);
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $positive$ BEGIN IF NOT public.fn_runtime_attest_ordinary_login() THEN RAISE EXCEPTION 'API successor attestation failed'; END IF; END $positive$;
RESET SESSION AUTHORIZATION;
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
DO $positive$ BEGIN IF NOT public.fn_runtime_attest_ordinary_login() THEN RAISE EXCEPTION 'ingestor successor attestation failed'; END IF; END $positive$;
RESET SESSION AUTHORIZATION;
GRANT SELECT ON public.climate_source_events TO verdify_api_runtime;
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $negative$ BEGIN IF public.fn_runtime_attest_ordinary_login() THEN RAISE EXCEPTION 'widened API grant attested'; END IF; END $negative$;
RESET SESSION AUTHORIZATION;
SELECT 'PASS: both clone-specific successor attestations; widened API grant fails closed';
ROLLBACK;
