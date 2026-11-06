-- 301 (#1346): the greenhouse snapshot on Now. CronJob verdify-greenhouse-brief (components/greenhouse-brief)
-- builds a fact sheet from the operator API's own reads every 5 minutes, sends it to the local Cortex model
-- (llm.primary.longctx, no tools, temperature 0, thinking off, at most 300 output tokens) and stores one row per
-- attempt here: the facts, their sha256, the text, the model and route, latency, token counts and the outcome
-- (ok, rejected by the guardrails, or error). The table is named greenhouse_brief because the vision job's
-- "snapshot" is a camera image (image_observations).
-- Writer and reader: the job runs as the dashboard login (282, verdify_dashboard_runtime), the same reads API-02
-- serves, so the dashboard duty gets SELECT, INSERT and DELETE (the job's 30-day retention), and UPDATE on
-- checked_at (an unchanged fact sheet confirms the newest row without a model call) and outdated_at (a band
-- verdict or alert change after the row was written). MCP's get_greenhouse_snapshot reads it, so the MCP duty
-- gets SELECT and nothing else. The text is read-only: no planner, controller or Iris path reads this table.
-- Seals: neither ordinary login can reach the table, so the api and ingestor digests keep 289's values. The MCP
-- SELECT adds the table's relation and relation-acl entries to fn_mcp_runtime_boundary_digest (263), so the MCP
-- seal moves and its receipt is re-sealed. The successor literal is a read-only projection on production at
-- exact289 (tests/fixtures/301-greenhouse-brief-native-qualification.json); 301 is chained after 290, the newest
-- merged migration when it was written, and is re-chained and natively qualified after 291-300 land.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF current_user <> 'verdify'
        OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
            AND seq=290 AND filename='db/migrations/290-band-compliance.sql'
            AND sha256='557d8d03a61a284e05e1b9bc26db7e3748898531ea7b80f0a6b4e458c922e6be'
            AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>290)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'a752a89a07b5b90ecbe6ccaf44358492aa8da6c562d608c8880d24d77088af7e'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM 'b17c9da67fe6456c1fd470ac5dc82dfdd20320b3d808c482162b11952bd57011'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
        OR to_regclass('public.greenhouse_brief') IS NOT NULL
        OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='verdify_dashboard_runtime' AND NOT rolcanlogin)
        OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='verdify_mcp_runtime' AND NOT rolcanlogin)
    THEN RAISE EXCEPTION '301 refuses an unqualified predecessor'; END IF;
END $predecessor$;

CREATE TABLE public.greenhouse_brief (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    generated_at      timestamptz NOT NULL DEFAULT now(),
    valid_until       timestamptz NOT NULL,
    checked_at        timestamptz,
    outdated_at       timestamptz,
    model             text NOT NULL CHECK (length(model) BETWEEN 1 AND 200),
    route             text NOT NULL CHECK (length(route) BETWEEN 1 AND 200),
    prompt_version    text NOT NULL CHECK (length(prompt_version) BETWEEN 1 AND 50),
    facts             jsonb NOT NULL CHECK (jsonb_typeof(facts) = 'object'),
    facts_sha256      text NOT NULL CHECK (facts_sha256 ~ '^[0-9a-f]{64}$'),
    text              text CHECK (text IS NULL OR length(text) BETWEEN 1 AND 4000),
    latency_ms        integer CHECK (latency_ms IS NULL OR latency_ms >= 0),
    prompt_tokens     integer CHECK (prompt_tokens IS NULL OR prompt_tokens >= 0),
    completion_tokens integer CHECK (completion_tokens IS NULL OR completion_tokens >= 0),
    status            text NOT NULL CHECK (status IN ('ok', 'rejected', 'error')),
    error             text CHECK (error IS NULL OR length(error) BETWEEN 1 AND 2000),
    CHECK (valid_until > generated_at),
    CHECK (CASE WHEN status = 'ok' THEN text IS NOT NULL AND error IS NULL ELSE error IS NOT NULL END)
);
CREATE INDEX greenhouse_brief_generated_idx ON public.greenhouse_brief (generated_at DESC);
CREATE INDEX greenhouse_brief_ok_idx ON public.greenhouse_brief (generated_at DESC) WHERE status = 'ok';
COMMENT ON TABLE public.greenhouse_brief IS
    'The greenhouse snapshot on Now (301, #1346): one row per Cortex attempt by CronJob verdify-greenhouse-brief, '
    'with the fact sheet it was given, its sha256, the text and the outcome. Read-only text: never an input to the '
    'planner or the controller. Rows older than 30 days are deleted by the job.';
COMMENT ON COLUMN public.greenhouse_brief.checked_at IS
    'The newest run that found the same fact sheet (facts_sha256) and made no model call.';
COMMENT ON COLUMN public.greenhouse_brief.outdated_at IS
    'When a band verdict or the open alerts changed after the row was written; an outdated row is never shown.';

GRANT SELECT, INSERT, DELETE ON public.greenhouse_brief TO verdify_dashboard_runtime;
GRANT UPDATE (checked_at, outdated_at) ON public.greenhouse_brief TO verdify_dashboard_runtime;
GRANT SELECT ON public.greenhouse_brief TO verdify_mcp_runtime;

-- Successor seals: the ordinary digests keep 289's values; the MCP digest takes in the table and the MCP duty's
-- SELECT, and its receipt is re-sealed.
DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'a752a89a07b5b90ecbe6ccaf44358492aa8da6c562d608c8880d24d77088af7e'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM 'b17c9da67fe6456c1fd470ac5dc82dfdd20320b3d808c482162b11952bd57011'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '717d3d0bea4032fe65fe977f78f511b6ed3bd97142d6665ebc3bfa83836414af'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
    THEN RAISE EXCEPTION '301 refuses an unqualified successor'; END IF;
    UPDATE public.mcp_runtime_boundary_receipt SET boundary_sha256=decode('717d3d0bea4032fe65fe977f78f511b6ed3bd97142d6665ebc3bfa83836414af','hex') WHERE singleton;
    IF NOT FOUND THEN RAISE EXCEPTION '301 missing mcp receipt'; END IF;
END $successor$;
