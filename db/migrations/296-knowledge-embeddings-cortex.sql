-- 296 (#1351 row 2): the knowledge corpus moves off OpenAI text-embedding-3-large onto local Cortex
-- embed.main (Qwen3-Embedding-4B). Jason, 2026-11-05: everything Verdify runs is local; "drive the
-- re-embedding plan". embed.main returns 2560 dimensions and GPUStack refuses an explicit `dimensions`
-- request, so the vectors keep their natural size. pgvector (0.8.1 in production) indexes `vector` with
-- HNSW or IVFFlat only up to 2000 dimensions but `halfvec` up to 4000, so the column is halfvec(2560)
-- with an HNSW cosine index. Half precision costs nothing measurable for cosine ranking at this size.
--
-- public.knowledge_embeddings sits beside public.verdify_embeddings (3072-dim OpenAI, frozen since
-- 2026-05-24); nothing here touches the old table, its function or its data. scripts/embed-corpora.py,
-- run as the verdify-knowledge-embed Job, fills the new table from each row's source text; MCP
-- lessons_search and knowledge_search read it through fn_search_knowledge_embeddings. The old table and
-- fn_search_embeddings are dropped by a later migration once the new search is verified live.
--
-- Grants: the MCP duty gets SELECT on the table and EXECUTE on the search function, nothing else, and
-- PUBLIC gets neither. The re-embed Job writes as the owner, so no runtime role can write the corpus.
-- Only the MCP seal moves (263 takes in a relation or function an MCP role holds an entry on); neither
-- object carries an api, ingestor or PUBLIC entry, so 217's two ordinary digests and receipts stay 294's.
-- Its predecessor is exact294 (same PR; 294 chains from 293 and moves no seal). Until the rolled-back
-- native qualification measures it, the successor block and registrations carry 294's stand-in seals,
-- MCP included, which this file's grants move, so that block refuses them;
-- tests/fixtures/296-knowledge-embeddings-native-qualification.json records which they are.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF current_user <> 'verdify'
        OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
            AND seq=294 AND filename='db/migrations/294-cortex-planner-shadow-storage.sql'
            AND sha256='c0400e8b273ebe086f729a205c20be76430bd62b34ac528d4be152a01de2554a'
            AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>294)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'b4b8499b426ef1c7abb2c6c996dee098d9aec968ddc3292d210cca70c2e77b76'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '2dcc7c99cd7cb2e80745ee1f2f05af9f5fa3dcdf0f6bbdd34e9cf1b870692806'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login' AND boundary_sha256=decode('b4b8499b426ef1c7abb2c6c996dee098d9aec968ddc3292d210cca70c2e77b76','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login' AND boundary_sha256=decode('2dcc7c99cd7cb2e80745ee1f2f05af9f5fa3dcdf0f6bbdd34e9cf1b870692806','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton AND boundary_sha256=decode('6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e','hex'))
        OR NOT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector'
            AND string_to_array(extversion,'.')::int[] >= ARRAY[0,7])
        OR to_regclass('public.knowledge_embeddings') IS NOT NULL
    THEN RAISE EXCEPTION '296 refuses an unqualified predecessor'; END IF;
END $predecessor$;

CREATE TABLE public.knowledge_embeddings (
    source_type     text NOT NULL CHECK (source_type IN ('lesson', 'plan', 'site_doc', 'playbook', 'observation')),
    source_id       text NOT NULL,
    chunk_idx       integer NOT NULL DEFAULT 0 CHECK (chunk_idx >= 0),
    content         text NOT NULL,
    content_hash    text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    embedding_model text NOT NULL CHECK (embedding_model = 'embed.main'),
    embedding       halfvec(2560) NOT NULL,
    metadata        jsonb,
    embedded_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_type, source_id, chunk_idx)
);
COMMENT ON TABLE public.knowledge_embeddings IS
    'Knowledge corpus for MCP lessons_search and knowledge_search (296, #1351): Cortex embed.main '
    '(Qwen3-Embedding-4B), 2560 dimensions as halfvec, HNSW cosine. Filled from each source row''s text by '
    'scripts/embed-corpora.py (the verdify-knowledge-embed Job); a row is current when its content_hash '
    'and embedding_model match.';
CREATE INDEX knowledge_embeddings_hnsw ON public.knowledge_embeddings
    USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE FUNCTION public.fn_search_knowledge_embeddings(query_embedding halfvec, p_top_k integer, p_source_types text[])
    RETURNS TABLE(source_type text, source_id text, chunk_idx integer, content text, metadata jsonb, distance double precision)
    LANGUAGE sql STABLE
    SET search_path = pg_catalog, public, pg_temp
    SET hnsw.iterative_scan = strict_order
AS $function$
    SELECT e.source_type, e.source_id, e.chunk_idx, e.content, e.metadata,
           (e.embedding <=> query_embedding)::double precision AS distance
      FROM public.knowledge_embeddings e
      LEFT JOIN public.planner_lessons pl
        ON e.source_type = 'lesson' AND pl.id::text = e.source_id
     WHERE e.source_type = ANY (p_source_types)
       AND (e.source_type <> 'lesson'
            OR (pl.is_active IS TRUE AND pl.superseded_by IS NULL
                AND NOT public.fn_dli_proxy_lesson_invalid(pl.condition, pl.lesson)))
     ORDER BY e.embedding <=> query_embedding
     LIMIT p_top_k
$function$;
COMMENT ON FUNCTION public.fn_search_knowledge_embeddings(halfvec, integer, text[]) IS
    'Top-K cosine retrieval over knowledge_embeddings (296). Active lesson rows also pass the DLI proxy '
    'invalidation guard. hnsw.iterative_scan keeps a source-type filter from starving the index scan.';

REVOKE ALL ON public.knowledge_embeddings FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_search_knowledge_embeddings(halfvec, integer, text[]) FROM PUBLIC;
GRANT SELECT ON public.knowledge_embeddings TO verdify_mcp_runtime;
GRANT EXECUTE ON FUNCTION public.fn_search_knowledge_embeddings(halfvec, integer, text[]) TO verdify_mcp_runtime;

-- Successor seals: the two MCP entries move the MCP digest and re-seal its receipt; the api and ingestor
-- digests and receipts must still equal 294's.
DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'b4b8499b426ef1c7abb2c6c996dee098d9aec968ddc3292d210cca70c2e77b76'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '2dcc7c99cd7cb2e80745ee1f2f05af9f5fa3dcdf0f6bbdd34e9cf1b870692806'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
    THEN RAISE EXCEPTION '296 refuses an unqualified successor'; END IF;
    UPDATE public.mcp_runtime_boundary_receipt SET boundary_sha256=decode('6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e','hex') WHERE singleton;
    IF NOT FOUND THEN RAISE EXCEPTION '296 missing mcp receipt'; END IF;
END $successor$;
