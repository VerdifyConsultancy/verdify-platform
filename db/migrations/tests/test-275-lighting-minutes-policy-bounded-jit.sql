-- Run in an isolated full-data clone. All function/config changes roll back.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='30s';
SET LOCAL lock_timeout='5s';
SET LOCAL jit=off;
CREATE TEMP TABLE lighting_275_before_metadata ON COMMIT DROP AS
SELECT to_jsonb(p)-'proconfig' AS metadata, p.proconfig AS config
FROM pg_catalog.pg_proc p
WHERE p.oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure;
CREATE TEMP TABLE lighting_275_results (at timestamptz, rows bigint, result_sha256 text) ON COMMIT DROP;
INSERT INTO lighting_275_results SELECT at, count(*) AS rows,
       encode(sha256(convert_to(string_agg(to_jsonb(result)::text,'' ORDER BY result.light_key),'UTF8')),'hex') AS result_sha256
FROM (VALUES ('2026-10-05T20:05:00Z'::timestamptz)) cases(at)
CROSS JOIN LATERAL public.fn_lighting_minutes_policy(at,'vallery') result
GROUP BY at;
INSERT INTO lighting_275_results SELECT at, count(*) AS rows,
       encode(sha256(convert_to(string_agg(to_jsonb(result)::text,'' ORDER BY result.light_key),'UTF8')),'hex') AS result_sha256
FROM (VALUES ('2000-01-01T00:00:00Z'::timestamptz)) cases(at)
CROSS JOIN LATERAL public.fn_lighting_minutes_policy(at,'vallery') result
GROUP BY at;
\ir ../275-lighting-minutes-policy-bounded-jit.sql
-- The caller keeps its ordinary JIT default; only the function avoids it.
SET LOCAL jit=on;
CREATE TEMP TABLE lighting_275_after (LIKE lighting_275_results) ON COMMIT DROP;
INSERT INTO lighting_275_after SELECT at, count(*) AS rows,
       encode(sha256(convert_to(string_agg(to_jsonb(result)::text,'' ORDER BY result.light_key),'UTF8')),'hex') AS result_sha256
FROM (VALUES ('2026-10-05T20:05:00Z'::timestamptz)) cases(at)
CROSS JOIN LATERAL public.fn_lighting_minutes_policy(at,'vallery') result
GROUP BY at;
INSERT INTO lighting_275_after SELECT at, count(*) AS rows,
       encode(sha256(convert_to(string_agg(to_jsonb(result)::text,'' ORDER BY result.light_key),'UTF8')),'hex') AS result_sha256
FROM (VALUES ('2000-01-01T00:00:00Z'::timestamptz)) cases(at)
CROSS JOIN LATERAL public.fn_lighting_minutes_policy(at,'vallery') result
GROUP BY at;
DO $equivalence$
BEGIN
    IF (SELECT to_jsonb(p)-'proconfig' FROM pg_catalog.pg_proc p
        WHERE p.oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure)
        IS DISTINCT FROM (SELECT metadata FROM lighting_275_before_metadata)
    THEN RAISE EXCEPTION '275 changed body, owner, ACL, signature or other function metadata'; END IF;
    IF (SELECT proconfig FROM pg_catalog.pg_proc
        WHERE oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure)
        IS DISTINCT FROM (SELECT array_append(coalesce(config,ARRAY[]::text[]),'jit=off')
                          FROM lighting_275_before_metadata)
    THEN RAISE EXCEPTION '275 failed to preserve other function settings'; END IF;
    IF EXISTS(SELECT 1 FROM lighting_275_results WHERE rows<>2)
       OR EXISTS((TABLE lighting_275_results EXCEPT ALL TABLE lighting_275_after)
                  UNION ALL (TABLE lighting_275_after EXCEPT ALL TABLE lighting_275_results))
    THEN RAISE EXCEPTION '275 changed ordinary or historical-fallback lighting results'; END IF;
END $equivalence$;
SELECT json_build_object('test','275_lighting_result_and_metadata_equivalence',
                         'passed',true,'deadline_seconds',30,'rollback',true);
ROLLBACK;
