-- 286 (VD-33, #1149, architecture 5.2 Release and 9 M5): the lighting policy
-- functions stop reading setpoint_changes rows with source 'manual' as an
-- override. A desired-state value reaches the controller as a 'manual' push;
-- after its release the next dispatcher pass must apply the policy's own value
-- instead of reading the released value back. Only the override read changes,
-- from COALESCE(source, '') <> 'esp32' to NOT IN ('esp32', 'manual'): the
-- latest_changes CTE of fn_lighting_circuit_policy (185) and the sc.source
-- filter of fn_lighting_minutes_policy (179). Each function is replaced from its
-- own live definition, so its signature, owner, grants, ROWS and SET clauses
-- stay, 275's jit=off included; a catalog change beyond prosrc is refused.
-- Its predecessor is exact285 (VD-56). Until 285 is measured, the predecessor
-- block's sha and seals are stand-ins: 285's current file sha and the seals it
-- registers, which are 284's. Until this file is measured, its successor block
-- and registrations carry those same seals as stand-ins, and that block refuses
-- them once the two replacements have moved the api and ingestor digests;
-- tests/fixtures/286-lighting-policy-ignores-manual-native-qualification.json
-- records which they are.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
        AND seq=285 AND filename='db/migrations/285-daily-scorecard-view.sql'
        AND sha256='89db770e96f030d2836fe033dce035e8c244d7b08a52c7f6ac378f5526c9ca77'
        AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=286)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '03d2a7ce5efd2eecee965672175585bad43194b5fea85d99739efe70e01a81a8' OR
        encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM 'c571ff1bdc212ca84815718406aaa6b7242ba9f86a2727d3855168dcd4ed81ad' OR
        encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login' AND boundary_sha256=decode('03d2a7ce5efd2eecee965672175585bad43194b5fea85d99739efe70e01a81a8','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login' AND boundary_sha256=decode('c571ff1bdc212ca84815718406aaa6b7242ba9f86a2727d3855168dcd4ed81ad','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton AND boundary_sha256=decode('6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e','hex'))
    THEN RAISE EXCEPTION '286 refuses an unqualified predecessor'; END IF;
END $predecessor$;

DO $manual_override$
DECLARE
    subject regprocedure;
    expected_before text;
    expected_after text;
    expected_config text[];
    old_read text;
    new_read text;
    definition text;
    before_metadata jsonb;
BEGIN
    FOR subject, expected_before, expected_after, expected_config, old_read, new_read IN
        SELECT * FROM (VALUES
            ('public.fn_lighting_circuit_policy(timestamptz,text)'::regprocedure,
             '8d4688c4ff0386b3b939b968ae815b50bacb7eb73a9d18cbbdddf95861d17c96',
             'a32e478b42de56045d1a50cd08f73fcd79726a40bdf3bb5812633a86c7f36fdb',
             NULL::text[],
             $read$COALESCE(source, '') <> 'esp32'$read$,
             $read$COALESCE(source, '') NOT IN ('esp32', 'manual')$read$),
            ('public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure,
             '049c12f4e73337b2bfc97eda1faf4db9685b98da64e1184c7a90f1d36520cbdd',
             '1d4df376190f99ffc5683c57aea401ec24d4497035efd747c98390dbb250e8d4',
             ARRAY['jit=off'],
             $read$COALESCE(sc.source, '') <> 'esp32'$read$,
             $read$COALESCE(sc.source, '') NOT IN ('esp32', 'manual')$read$)
        ) AS subjects(subject, expected_before, expected_after, expected_config, old_read, new_read)
    LOOP
        -- Every catalog column but the source. The argument defaults compare
        -- deparsed: their stored node trees carry the parse offsets of the
        -- statement that created them.
        SELECT (to_jsonb(p) - 'prosrc' - 'proargdefaults')
                   || jsonb_build_object('arguments', pg_catalog.pg_get_function_arguments(p.oid)),
               pg_catalog.pg_get_functiondef(p.oid)
          INTO before_metadata, definition
          FROM pg_catalog.pg_proc p WHERE p.oid = subject;
        IF encode(sha256(convert_to((SELECT prosrc FROM pg_catalog.pg_proc WHERE oid = subject), 'UTF8')), 'hex')
               IS DISTINCT FROM expected_before
           OR (SELECT proconfig FROM pg_catalog.pg_proc WHERE oid = subject) IS DISTINCT FROM expected_config
           OR (length(definition) - length(replace(definition, old_read, ''))) / length(old_read) <> 1
        THEN RAISE EXCEPTION '286 refuses an unexpected lighting policy predecessor'; END IF;
        EXECUTE replace(definition, old_read, new_read);
        IF (SELECT (to_jsonb(p) - 'prosrc' - 'proargdefaults')
                       || jsonb_build_object('arguments', pg_catalog.pg_get_function_arguments(p.oid))
              FROM pg_catalog.pg_proc p WHERE p.oid = subject) IS DISTINCT FROM before_metadata
           OR encode(sha256(convert_to((SELECT prosrc FROM pg_catalog.pg_proc WHERE oid = subject), 'UTF8')), 'hex')
               IS DISTINCT FROM expected_after
        THEN RAISE EXCEPTION '286 changed a lighting policy beyond its manual-source read'; END IF;
    END LOOP;
END $manual_override$;

-- Exact production-OID successor seals, measured by the rolled-back native
-- qualification (tests/fixtures/286-lighting-policy-ignores-manual-native-qualification.json).
-- Replacing two functions both runtime logins execute moves the api and
-- ingestor seals, as 275's jit change did; MCP's stays its predecessor's.
DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '03d2a7ce5efd2eecee965672175585bad43194b5fea85d99739efe70e01a81a8' OR
        encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM 'c571ff1bdc212ca84815718406aaa6b7242ba9f86a2727d3855168dcd4ed81ad' OR
        encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
    THEN RAISE EXCEPTION '286 refuses an unqualified successor'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('03d2a7ce5efd2eecee965672175585bad43194b5fea85d99739efe70e01a81a8','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_api_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '286 missing api receipt'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('c571ff1bdc212ca84815718406aaa6b7242ba9f86a2727d3855168dcd4ed81ad','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_ingestor_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '286 missing ingestor receipt'; END IF;
END $successor$;
