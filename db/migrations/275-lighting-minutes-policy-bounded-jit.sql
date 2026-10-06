-- The full-history lighting policy expanded to 2,695 JIT functions and exceeded
-- the ordinary setpoint client's 30-second deadline on the complete restore.
-- Keep its query and results unchanged; avoid compilation within this function.
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
