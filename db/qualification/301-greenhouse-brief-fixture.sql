-- Run only inside an explicit rollback-only transaction after exact 301 (#1346), as the superuser
-- owner: scripts/qualify-migration-successor.py --native-fixture runs it at the end of its final
-- session. The dashboard login attests and does what CronJob verdify-greenhouse-brief does: inserts
-- an ok row and an error row, confirms one (checked_at), marks one outdated (outdated_at), reads them
-- back and deletes rows past retention. It cannot change any other column. The MCP duty may only
-- read the table, and neither ordinary login reaches it. The rollback discards all of it. The last
-- line reports the result to the qualifier.
-- SET SESSION AUTHORIZATION needs the superuser owner; the rollback undoes it.
SET SESSION AUTHORIZATION verdify_dashboard_runtime_login;
DO $dashboard$
DECLARE
    ok_id bigint;
    failed_id bigint;
BEGIN
    IF verdify_dashboard_runtime.fn_attest_login() IS NOT TRUE THEN
        RAISE EXCEPTION '301 dashboard login does not attest';
    END IF;
    INSERT INTO public.greenhouse_brief (valid_until, model, route, prompt_version, facts, facts_sha256, text,
                                         latency_ms, prompt_tokens, completion_tokens, status)
    VALUES (now() + interval '15 minutes', 'llm.primary.longctx', 'https://cortex.vallery.net/v1', 'qualify',
            '{"as_of": "qualify"}', repeat('a', 64), 'Qualification row.', 1, 1, 1, 'ok')
    RETURNING id INTO ok_id;
    INSERT INTO public.greenhouse_brief (valid_until, model, route, prompt_version, facts, facts_sha256, status, error)
    VALUES (now() + interval '15 minutes', 'llm.primary.longctx', 'https://cortex.vallery.net/v1', 'qualify',
            '{"as_of": "qualify"}', repeat('b', 64), 'error', 'qualification error row')
    RETURNING id INTO failed_id;
    UPDATE public.greenhouse_brief SET checked_at = now() WHERE id = ok_id;
    UPDATE public.greenhouse_brief SET outdated_at = now() WHERE id = failed_id;
    IF (SELECT count(*) FROM public.greenhouse_brief WHERE id IN (ok_id, failed_id)) <> 2 THEN
        RAISE EXCEPTION '301 dashboard login cannot read its rows';
    END IF;
    BEGIN
        UPDATE public.greenhouse_brief SET text = 'changed' WHERE id = ok_id;
        RAISE EXCEPTION '301 dashboard login changed text';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    DELETE FROM public.greenhouse_brief WHERE generated_at < now() - interval '30 days';
    DELETE FROM public.greenhouse_brief WHERE id IN (ok_id, failed_id);
END $dashboard$;
RESET SESSION AUTHORIZATION;

DO $fixture$
BEGIN
    IF NOT has_table_privilege('verdify_mcp_runtime', 'public.greenhouse_brief', 'SELECT')
        OR has_table_privilege('verdify_mcp_runtime', 'public.greenhouse_brief', 'INSERT,UPDATE,DELETE,TRUNCATE')
        OR has_table_privilege('verdify_api_runtime_login', 'public.greenhouse_brief',
                               'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        OR has_table_privilege('verdify_ingestor_runtime_login', 'public.greenhouse_brief',
                               'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        OR has_table_privilege('public', 'public.greenhouse_brief', 'SELECT')
    THEN RAISE EXCEPTION '301 greenhouse_brief access is not exact'; END IF;
END $fixture$;

SELECT 'QUALIFY native_fixture ' || json_build_object(
    'checks', json_build_array('dashboard_login_attests', 'dashboard_inserts_ok_and_error', 'dashboard_confirms_and_outdates',
                               'dashboard_cannot_change_text', 'dashboard_deletes_past_retention', 'mcp_reads_only',
                               'ordinary_logins_and_public_denied'),
    'all_passed', true,
    'attested', json_build_object('dashboard', true))::text;
