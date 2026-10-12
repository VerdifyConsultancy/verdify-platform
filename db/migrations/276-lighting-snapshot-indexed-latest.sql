-- DRAFT: source-owned additive276, not ready to merge/apply.
-- Canonical production-native successor receipt guards/refresh and closed
-- six-bootstrap276 profile must be bound before this file is deliverable.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
        AND seq=275 AND filename='db/migrations/275-lighting-minutes-policy-bounded-jit.sql'
        AND sha256='f4f8da110236461680f41770bad766a7e28b1a6db1982ecc632e93930a375dc4'
        AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=276)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex')
            IS DISTINCT FROM '5091627a4dfd40c679ed4e2c0729cde507f705ab9c45c1e2eb82b42d29634bba'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex')
            IS DISTINCT FROM '8690d5fc25a742cdd4abbbb940b0f000f708ef567e5ff301fbfd3d27f449a743'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex')
            IS DISTINCT FROM '7083e4d43044e7f2b0150b58fa3cc8cc45c599a68b1f3cafaf9242e0f0f79e68'
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts
            WHERE login_name='verdify_api_runtime_login'
            AND boundary_sha256=decode('5091627a4dfd40c679ed4e2c0729cde507f705ab9c45c1e2eb82b42d29634bba','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts
            WHERE login_name='verdify_ingestor_runtime_login'
            AND boundary_sha256=decode('8690d5fc25a742cdd4abbbb940b0f000f708ef567e5ff301fbfd3d27f449a743','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=decode('7083e4d43044e7f2b0150b58fa3cc8cc45c599a68b1f3cafaf9242e0f0f79e68','hex'))
    THEN RAISE EXCEPTION '276 refuses an unqualified predecessor'; END IF;
END $predecessor$;
DO $indexed_snapshot$
DECLARE
    subject oid := 'public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure;
    before_metadata jsonb;
    before_body text;
    before_defaults text;
    before_defaults_sql text;
    definition text;
    old_path text := $old_path$    FROM setpoint_snapshot ss
    JOIN tracked_params tp ON tp.parameter = ss.parameter
    WHERE COALESCE(ss.greenhouse_id, p_greenhouse_id) = p_greenhouse_id
    ORDER BY ss.parameter, ss.ts DESC$old_path$;
    new_path text := $new_path$    FROM tracked_params tp
    CROSS JOIN LATERAL (
        SELECT ss.parameter, ss.value, ss.ts
        FROM setpoint_snapshot ss
        WHERE ss.parameter = tp.parameter
          AND COALESCE(ss.greenhouse_id, p_greenhouse_id) = p_greenhouse_id
        ORDER BY ss.ts DESC
        LIMIT 1
    ) ss
    ORDER BY ss.parameter, ss.ts DESC$new_path$;
BEGIN
    SELECT to_jsonb(p)-'prosrc'-'proargdefaults',p.prosrc,pg_get_functiondef(p.oid),
        p.proargdefaults::text,pg_get_expr(p.proargdefaults,0)
    INTO before_metadata,before_body,definition,before_defaults,before_defaults_sql FROM pg_catalog.pg_proc p WHERE p.oid=subject;
    IF encode(sha256(convert_to(before_body,'UTF8')),'hex')
       <> '049c12f4e73337b2bfc97eda1faf4db9685b98da64e1184c7a90f1d36520cbdd'
       OR (SELECT proconfig FROM pg_proc WHERE oid=subject) IS DISTINCT FROM ARRAY['jit=off']::text[]
       OR (length(before_body)-length(replace(before_body,old_path,'')))<>length(old_path)
       OR (length(definition)-length(replace(definition,old_path,'')))<>length(old_path)
    THEN RAISE EXCEPTION '276 refuses an unexpected lighting policy body/configuration'; END IF;
    EXECUTE replace(definition,old_path,new_path);
    IF (SELECT to_jsonb(p)-'prosrc'-'proargdefaults' FROM pg_catalog.pg_proc p WHERE p.oid=subject)
          IS DISTINCT FROM before_metadata
       -- CREATE OR REPLACE reparses defaults. Only source-coordinate offsets
       -- may differ; every AST token/value and deparsed expression stays exact.
       OR regexp_replace((SELECT proargdefaults::text FROM pg_proc WHERE oid=subject),
            ':location -?[0-9]+',':location -1','g') IS DISTINCT FROM
          regexp_replace(before_defaults,':location -?[0-9]+',':location -1','g')
       OR (SELECT pg_get_expr(proargdefaults,0) FROM pg_proc WHERE oid=subject)
          IS DISTINCT FROM before_defaults_sql
       OR encode(sha256(convert_to((SELECT prosrc FROM pg_proc WHERE oid=subject),'UTF8')),'hex')
          IS DISTINCT FROM 'a77f196eb1076e1a32679fd8c70670411c204fee69b8cf148016b1cd533d8f69'
    THEN RAISE EXCEPTION '276 changed lighting policy beyond its indexed latest snapshot'; END IF;
END $indexed_snapshot$;
-- Intentionally refuses incomplete delivery; replace with authentic guarded
-- successors/receipt refresh only after ROOT's native capture is independently qualified.
DO $pending_native_successors$
BEGIN RAISE EXCEPTION '276 successor native custody is not yet qualified'; END $pending_native_successors$;
