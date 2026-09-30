\set ON_ERROR_STOP on
-- SYNTHETIC DEVICE-DENIED FIXTURE ONLY. Never apply this to production.
DO $guard$
BEGIN
    IF current_database() <> 'verdify_rehearsal'
       OR inet_server_addr() IS NOT NULL
       OR current_setting('listen_addresses') <> ''
       OR session_user <> 'verdify' THEN
        RAISE EXCEPTION 'owner-operated socket-only disposable fixture required';
    END IF;
END;
$guard$;
CREATE TABLE public.fixture_v2_clock (
    singleton boolean PRIMARY KEY CHECK (singleton),
    instant timestamptz NOT NULL
);
INSERT INTO public.fixture_v2_clock VALUES (true, clock_timestamp());
REVOKE ALL ON public.fixture_v2_clock FROM PUBLIC;
GRANT SELECT ON public.fixture_v2_clock TO verdify_experiment_v2_owner;
CREATE FUNCTION public.fixture_v2_now() RETURNS timestamptz
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public
AS 'SELECT instant FROM public.fixture_v2_clock WHERE singleton';
REVOKE ALL ON FUNCTION public.fixture_v2_now() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.fixture_v2_now() TO verdify_experiment_v2_owner;
CREATE TABLE public.fixture_v2_clock_function_sources (
    signature text PRIMARY KEY, source_sha256 text NOT NULL,
    replacements integer NOT NULL CHECK (replacements > 0)
);
REVOKE ALL ON public.fixture_v2_clock_function_sources FROM PUBLIC;
DO $install$
DECLARE r record; original text; injected text; n integer;
BEGIN
    FOR r IN
        SELECT p.oid, p.oid::regprocedure::text AS signature
          FROM pg_proc p JOIN pg_namespace ns ON ns.oid=p.pronamespace
          JOIN pg_roles owner ON owner.oid=p.proowner
         WHERE ns.nspname='public' AND owner.rolname='verdify_experiment_v2_owner'
           AND p.prokind='f'
    LOOP
        original := pg_get_functiondef(r.oid);
        injected := regexp_replace(original,
            '\m(clock_timestamp|now)\(\)', 'public.fixture_v2_now()', 'g');
        IF injected <> original THEN
            SELECT count(*) INTO n FROM regexp_matches(original,
                '\m(clock_timestamp|now)\(\)', 'g');
            INSERT INTO public.fixture_v2_clock_function_sources VALUES
                (r.signature, encode(sha256(convert_to(original,'UTF8')),'hex'),n);
            EXECUTE injected;
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM public.fixture_v2_clock_function_sources) THEN
        RAISE EXCEPTION 'no source-owned experiment functions received fixture clock';
    END IF;
END;
$install$;
SELECT count(*) AS source_functions_clock_injected,
       sum(replacements) AS source_clock_sites
FROM public.fixture_v2_clock_function_sources;
