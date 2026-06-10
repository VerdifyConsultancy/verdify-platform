# Planner Memory

**Status:** implementation design for planner-owned memory and ingestion, 2026-05-22.

## 1. Purpose

`planner_graph` should own its durable historical memory instead of requiring
Verdify to resend large historical context on every request.

Planner memory is for:

- lessons learned
- observed outcomes
- prior plan summaries
- support/reference snippets
- retrieval audit metadata
- optional embeddings in a later phase

Planner memory is **not** for:

- raw unbounded telemetry
- secrets
- full prompt transcripts
- direct greenhouse write authority

## 2. Ownership Boundary

Verdify continues to own and send **current run context**:

- trigger envelope and correlation ids
- current climate snapshot
- current scorecard summary
- current forecast summary
- current active plan summary
- current alerts / clamp summary / freshness indicators
- operator notes and current observations

Planner owns and persists:

- planner summaries of prior runs
- prior proposal references
- curated lessons
- imported support docs / snippets
- retrieval audit records
- optional embeddings later

During migration, Verdify-sent `retrieval_refs` and `site_refs` remain valid
fallback inputs. Planner retrieval should merge with them rather than replace
them when memory is unavailable or empty.

## 3. Recommended Storage Architecture

### Phase 1

Use planner-owned Postgres schema design, but implement and test against:

- `DisabledMemoryStore`
- `InMemoryMemoryStore`

This lets retrieval/persistence seams ship before paid infrastructure exists.

### Phase 2

Use the same planner Postgres database for both:

- `planner_graph_runs`
- planner memory tables

Recommendation:

- Cloud SQL for PostgreSQL in `buoyant-valve-496719-m0`
- same DB family as planner run storage
- no separate vector DB yet

### Phase 3

Enable `pgvector` inside the same Postgres database for hybrid lexical +
semantic retrieval once Cloud SQL is provisioned and extension support is
confirmed.

## 4. Why Postgres First

Postgres-first is the best fit because it gives:

- one operational dependency
- JSONB for bounded payload metadata
- standard indexing and full-text search
- simple joins with run lifecycle data if needed later
- a clear upgrade path to `pgvector`

Separate vector DBs add avoidable complexity right now:

- more infra
- more auth and networking
- more consistency work
- more cost before memory value is proven

## 5. Memory Types

Current planned durable memory types:

- `lesson`
- `support_doc`
- `prior_plan`
- `observed_outcome`
- `planner_summary`

Trust levels:

- `observed_outcome`
- `verdify_context`
- `planner_inferred`

Important rule:

Planner-generated lessons are not the same as observed outcomes. Retrieval and
later ranking should prefer observed or Verdify-sourced facts over
planner-inferred summaries when both exist.

## 6. Initial Retrieval Strategy

Phase 1 retrieval should stay deterministic and bounded:

- filter by `greenhouse_id`
- boost same `event_type`
- bucket output separately into:
  - `retrieved_lessons`
  - `retrieved_docs`
  - `retrieved_plan_refs`
- cap count with `PLANNER_MEMORY_TOP_K`
- cap snippet size with `PLANNER_MEMORY_MAX_SNIPPET_CHARS`
- never fail the planner run if memory lookup fails

If retrieval fails:

- append a warning
- keep Verdify-sent refs intact
- continue the run

## 7. Persistence Strategy

Initial persistence should be conservative:

- only persist bounded run summaries
- behind `PLANNER_MEMORY_PERSIST_RUN_SUMMARIES`
- never block the planner run if persistence fails

The first persisted durable object should be a compact `prior_plan` style item
containing:

- trigger id
- event type
- selected action
- proposal rationale
- expected effect
- validation / guardrail summary
- bounded proposal payload metadata

## 8. Postgres Schema

Core memory table:

```sql
CREATE TABLE planner_memory_items (
    memory_id UUID PRIMARY KEY,
    greenhouse_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NULL,
    trigger_id UUID NULL,
    event_type TEXT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    body TEXT NOT NULL,
    tags TEXT[] NOT NULL DEFAULT '{}',
    importance SMALLINT NOT NULL DEFAULT 3,
    confidence REAL NULL,
    trust_level TEXT NOT NULL DEFAULT 'planner_inferred',
    content_hash TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from TIMESTAMPTZ NULL,
    expires_at TIMESTAMPTZ NULL,
    last_used_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT planner_memory_items_importance_check
        CHECK (importance BETWEEN 1 AND 5),
    CONSTRAINT planner_memory_items_confidence_check
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    CONSTRAINT planner_memory_items_type_check
        CHECK (memory_type IN ('lesson', 'support_doc', 'prior_plan', 'observed_outcome', 'planner_summary')),
    CONSTRAINT planner_memory_items_trust_level_check
        CHECK (trust_level IN ('observed_outcome', 'verdify_context', 'planner_inferred')),
    CONSTRAINT planner_memory_items_valid_window_check
        CHECK (expires_at IS NULL OR valid_from IS NULL OR expires_at > valid_from)
);
```

Recommended indexes:

```sql
CREATE UNIQUE INDEX planner_memory_items_unique_content_idx
ON planner_memory_items(greenhouse_id, memory_type, content_hash);

CREATE UNIQUE INDEX planner_memory_items_unique_source_idx
ON planner_memory_items(greenhouse_id, source_type, source_id)
WHERE source_id IS NOT NULL;

CREATE INDEX planner_memory_items_lookup_idx
ON planner_memory_items(greenhouse_id, memory_type, event_type, created_at DESC);

CREATE INDEX planner_memory_items_search_idx
ON planner_memory_items
USING GIN (
  to_tsvector(
    'english',
    coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(body, '')
  )
);
```

The production DDL is checked in at
`planner_graph/migrations/001_planner_memory.sql`. `PostgresMemoryStore.initialize()`
still creates the same objects for local integration tests and first-boot
rehearsal, but production rollout should apply the migration explicitly before
turning on `PLANNER_MEMORY_BACKEND=postgres`.

Retrieval audit table:

```sql
CREATE TABLE planner_memory_retrievals (
    retrieval_id UUID PRIMARY KEY,
    trigger_id UUID NULL,
    greenhouse_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    query_text TEXT NOT NULL,
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_ids UUID[] NOT NULL DEFAULT '{}',
    scores JSONB NOT NULL DEFAULT '{}'::jsonb,
    latency_ms INT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Future vector table:

```sql
CREATE TABLE planner_memory_embeddings (
    memory_id UUID PRIMARY KEY REFERENCES planner_memory_items(memory_id),
    embedding_model TEXT NOT NULL,
    embedding_dimensions INT NOT NULL,
    embedding_text_hash TEXT NOT NULL,
    embedding VECTOR(1536) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## 9. Config Flags

Current implementation flags:

- `PLANNER_MEMORY_BACKEND=disabled|memory|postgres`
- `PLANNER_MEMORY_DB_DSN`
- `PLANNER_MEMORY_TOP_K`
- `PLANNER_MEMORY_MAX_SNIPPET_CHARS`
- `PLANNER_MEMORY_PERSIST_RUN_SUMMARIES=true|false`
- `PLANNER_MEMORY_INGEST_ENABLED=true|false`
- `PLANNER_MEMORY_INGEST_MAX_ITEMS=100`
- `PLANNER_MEMORY_INGEST_MAX_BODY_CHARS=4000`
- `PLANNER_MEMORY_INGEST_ALLOW_VERDIFY_PRIOR_PLANS=true|false`
- `PLANNER_MEMORY_INGEST_ALLOW_SUPPORT_DOCS=true|false`
- `PLANNER_MEMORY_EMBEDDINGS_ENABLED=true|false`
- `PLANNER_MEMORY_EMBEDDING_MODEL=text-embedding-3-small`
- `PLANNER_MEMORY_EMBEDDING_DIMENSIONS=1536`

## 10. Ingestion API

Verdify can populate durable planner-owned memory outside the planner run loop
with:

```text
POST /planner-memory/ingest
```

The endpoint is private behind the same Cloud Run service boundary as the rest
of planner. The application does not add an anonymous public path.

Endpoint behavior:

- if `PLANNER_MEMORY_INGEST_ENABLED=false`, return `404`
- if ingestion is enabled but `PLANNER_MEMORY_BACKEND=disabled`, return `503`
- valid batches return `200` with accepted, rejected, and duplicate counts
- validation rejects individual malformed or oversized items without blocking
  other valid items in the batch

Request shape:

```json
{
  "greenhouse_id": "vallery",
  "source_system": "verdify",
  "batch_id": "batch-20260522-001",
  "items": [
    {
      "memory_type": "lesson",
      "source_type": "verdify_lesson",
      "source_id": "lesson-1",
      "trigger_id": null,
      "event_type": "SUNRISE",
      "title": "Dry sunrise lesson",
      "summary": "Dry sunrise ramps can need conservative midday planning.",
      "body": "Bounded memory text.",
      "tags": ["sunrise", "vpd"],
      "importance": 4,
      "confidence": 0.8,
      "trust_level": "verdify_context",
      "payload": {},
      "valid_from": null,
      "expires_at": null
    }
  ]
}
```

Response shape:

```json
{
  "accepted_count": 1,
  "rejected_count": 0,
  "duplicate_count": 0,
  "accepted_ids": ["00000000-0000-0000-0000-000000000000"],
  "rejections": [],
  "batch_id": "batch-20260522-001"
}
```

Ingestion rules:

- Verdify may submit `observed_outcome` and `verdify_context` trust levels
- Verdify may not submit `planner_inferred`; planner creates those itself
- body, title, summary, tags, source fields, and payload are bounded
- duplicate content for the same `greenhouse_id` and `memory_type` is
  idempotent and counted as a duplicate
- source updates with the same `greenhouse_id`, `source_type`, and `source_id`
  update the existing memory item

## 11. Rollout Plan

### Phase 1: repo-only

- add memory interface + disabled/in-memory backends
- replace hardcoded retrieval stub
- merge memory output with Verdify refs
- add optional persist-memory node
- add memory ingestion endpoint and store-level ingestion
- add tests

### Phase 2: Cloud SQL Postgres

- provision planner-owned Postgres
- initialize memory tables
- enable `PLANNER_MEMORY_BACKEND=postgres`
- keep lexical/recency retrieval only

### Phase 3: pgvector

- enable `CREATE EXTENSION vector`
- add embeddings table and backfill
- use hybrid lexical + vector retrieval
- compare retrieval quality against Verdify-sent refs in evals

## 12. Risks

- stale or low-trust memory can distort planning
- over-persisting every run can create noisy memory
- embeddings add cost and model migration complexity
- separate vector infrastructure is premature for current scale

## 13. Immediate Next Steps

Implemented now:

- planner memory seam and backends
- retrieval merge behavior
- optional summary persistence node
- memory ingestion endpoint
- prompt support for prior plan refs
- docs and tests

Still pending before production durability:

- provision planner Postgres
- enable Cloud SQL Admin API
- wire `PLANNER_MEMORY_BACKEND=postgres`
- validate retrieval quality with real planner history
