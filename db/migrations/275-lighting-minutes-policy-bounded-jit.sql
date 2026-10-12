-- The full-history lighting policy expanded to 2,695 JIT functions and exceeded
-- the ordinary setpoint client's 30-second deadline on the complete restore.
-- Keep its query and results unchanged; avoid compilation within this function.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
        AND seq=274 AND filename='db/migrations/274-qualified-physical-crop-band-publication.sql'
        AND sha256='0b939df1a79e3ce71835e59aedb5d1898805cd43cae72b79fa905f77067a8e4f'
        AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=275)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex')
            IS DISTINCT FROM '67bb961f69e72f8d59f8d38dce96201682b1fb8fb40d423071c987b075cf994e'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex')
            IS DISTINCT FROM '126f95dc75242a126579331fadf711f0c1e8e408d20ec7cd94e3d1e27a0dcf75'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex')
            IS DISTINCT FROM '7083e4d43044e7f2b0150b58fa3cc8cc45c599a68b1f3cafaf9242e0f0f79e68'
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts
            WHERE login_name='verdify_api_runtime_login'
            AND boundary_sha256=decode('67bb961f69e72f8d59f8d38dce96201682b1fb8fb40d423071c987b075cf994e','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts
            WHERE login_name='verdify_ingestor_runtime_login'
            AND boundary_sha256=decode('126f95dc75242a126579331fadf711f0c1e8e408d20ec7cd94e3d1e27a0dcf75','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=decode('7083e4d43044e7f2b0150b58fa3cc8cc45c599a68b1f3cafaf9242e0f0f79e68','hex'))
    THEN RAISE EXCEPTION '275 refuses an unqualified predecessor'; END IF;
END $predecessor$;
DO $bounded_jit$
DECLARE
    subject oid := 'public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure;
    before_metadata jsonb;
    before_config text[];
BEGIN
    SELECT to_jsonb(p) - 'proconfig', p.proconfig
    INTO before_metadata, before_config FROM pg_catalog.pg_proc p WHERE p.oid=subject;
    IF encode(sha256(convert_to((SELECT prosrc FROM pg_catalog.pg_proc WHERE oid=subject),'UTF8')),'hex')
       <> '049c12f4e73337b2bfc97eda1faf4db9685b98da64e1184c7a90f1d36520cbdd'
       OR EXISTS (SELECT 1 FROM unnest(before_config) c WHERE c LIKE 'jit=%')
    THEN RAISE EXCEPTION '275 refuses an unexpected lighting policy predecessor'; END IF;
    EXECUTE 'ALTER FUNCTION public.fn_lighting_minutes_policy(timestamptz,text) SET jit=off';
    IF (SELECT to_jsonb(p)-'proconfig' FROM pg_catalog.pg_proc p WHERE p.oid=subject)
          IS DISTINCT FROM before_metadata
       OR (SELECT proconfig FROM pg_catalog.pg_proc WHERE oid=subject)
          IS DISTINCT FROM array_append(coalesce(before_config, ARRAY[]::text[]),'jit=off')
    THEN RAISE EXCEPTION '275 changed lighting policy metadata beyond its JIT setting'; END IF;
END $bounded_jit$;
-- Authentic production-OID successors were measured inside a bounded transaction
-- and independently checked after ROLLBACK. Do not bless arbitrary live digests.
DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex')
            IS DISTINCT FROM '5091627a4dfd40c679ed4e2c0729cde507f705ab9c45c1e2eb82b42d29634bba'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex')
            IS DISTINCT FROM '8690d5fc25a742cdd4abbbb940b0f000f708ef567e5ff301fbfd3d27f449a743'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex')
            IS DISTINCT FROM '7083e4d43044e7f2b0150b58fa3cc8cc45c599a68b1f3cafaf9242e0f0f79e68'
    THEN RAISE EXCEPTION '275 refuses an unqualified successor'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts
        SET boundary_sha256=decode('5091627a4dfd40c679ed4e2c0729cde507f705ab9c45c1e2eb82b42d29634bba','hex'),
            captured_at=clock_timestamp() WHERE login_name='verdify_api_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '275 missing API receipt'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts
        SET boundary_sha256=decode('8690d5fc25a742cdd4abbbb940b0f000f708ef567e5ff301fbfd3d27f449a743','hex'),
            captured_at=clock_timestamp() WHERE login_name='verdify_ingestor_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '275 missing ingestor receipt'; END IF;
END $successor$;
