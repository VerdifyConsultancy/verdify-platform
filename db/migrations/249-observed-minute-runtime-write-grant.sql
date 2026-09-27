-- The daily ingestor owns this diagnostic, but migration 245 added its column
-- after the column-scoped runtime grants in migration 217. This GRANT changes
-- both ordinary-login boundary digests. The owning runner wraps this entire
-- file and its ledger stamp in one transaction; update the two receipts only
-- to independently reviewed literals from two isolated C0 clones.
DO $preflight$
BEGIN
    IF (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2
       OR (SELECT encode(boundary_sha256, 'hex')
             FROM public.runtime_ordinary_login_attestation_receipts
             WHERE login_name = 'verdify_api_runtime_login')
          IS DISTINCT FROM '04f9cad09a10fd0a2564d48be25ab60f438c8a1f7576115c18bab10a2b242b97'
       OR (SELECT encode(boundary_sha256, 'hex')
             FROM public.runtime_ordinary_login_attestation_receipts
             WHERE login_name = 'verdify_ingestor_runtime_login')
          IS DISTINCT FROM '77855152c6ec746ac4a4036e8058b28025e07e93ed476ab54ea9762980487eea'
       OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'), 'hex')
          IS DISTINCT FROM '04f9cad09a10fd0a2564d48be25ab60f438c8a1f7576115c18bab10a2b242b97'
       OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'), 'hex')
          IS DISTINCT FROM '77855152c6ec746ac4a4036e8058b28025e07e93ed476ab54ea9762980487eea'
       OR has_table_privilege('verdify_ingestor_runtime', 'public.daily_summary', 'UPDATE')
       OR has_column_privilege('verdify_ingestor_runtime', 'public.daily_summary',
                               'climate_observed_minute_metrics', 'UPDATE') THEN
        RAISE EXCEPTION 'migration 249 refuses changed predecessor boundary';
    END IF;
END;
$preflight$;

GRANT UPDATE (climate_observed_minute_metrics)
    ON public.daily_summary TO verdify_ingestor_runtime;

DO $postflight$
DECLARE v_rows integer;
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'), 'hex')
          IS DISTINCT FROM '8b895d1a4dcf403098fcfa8dc9645ed330e7b43ae8b08128456692cd0a89105a'
       OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'), 'hex')
          IS DISTINCT FROM '2556baed8f07bb9d9537b963e7749610004b32814e71666a8d12ae2177908cd2'
       OR NOT has_column_privilege('verdify_ingestor_runtime', 'public.daily_summary',
                                    'climate_observed_minute_metrics', 'UPDATE')
       OR NOT has_column_privilege('verdify_ingestor_runtime_login', 'public.daily_summary',
                                    'climate_observed_minute_metrics', 'UPDATE')
       OR has_table_privilege('verdify_ingestor_runtime', 'public.daily_summary', 'UPDATE') THEN
        RAISE EXCEPTION 'migration 249 refuses unreviewed successor boundary';
    END IF;

    UPDATE public.runtime_ordinary_login_attestation_receipts
       SET boundary_sha256 = CASE login_name
           WHEN 'verdify_api_runtime_login' THEN
               decode('8b895d1a4dcf403098fcfa8dc9645ed330e7b43ae8b08128456692cd0a89105a', 'hex')
           WHEN 'verdify_ingestor_runtime_login' THEN
               decode('2556baed8f07bb9d9537b963e7749610004b32814e71666a8d12ae2177908cd2', 'hex') END,
           captured_at = clock_timestamp()
     WHERE login_name IN ('verdify_api_runtime_login', 'verdify_ingestor_runtime_login');
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    IF v_rows <> 2
       OR (SELECT encode(boundary_sha256, 'hex')
             FROM public.runtime_ordinary_login_attestation_receipts
             WHERE login_name = 'verdify_api_runtime_login')
          IS DISTINCT FROM '8b895d1a4dcf403098fcfa8dc9645ed330e7b43ae8b08128456692cd0a89105a'
       OR (SELECT encode(boundary_sha256, 'hex')
             FROM public.runtime_ordinary_login_attestation_receipts
             WHERE login_name = 'verdify_ingestor_runtime_login')
          IS DISTINCT FROM '2556baed8f07bb9d9537b963e7749610004b32814e71666a8d12ae2177908cd2' THEN
        RAISE EXCEPTION 'migration 249 successor receipts failed attestation';
    END IF;
END;
$postflight$;
