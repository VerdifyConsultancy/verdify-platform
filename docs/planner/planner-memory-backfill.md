# Planner Memory Backfill And Seed Contract

Status: implementation brief for Verdify agent work, 2026-05-22.

This document defines the next agent-facing work for populating planner-owned
memory with historical Verdify context. It covers two related deliverables:

1. The exact staged backfill plan for past context categories.
2. The support-doc seed file format and backfill script contract.

The target system is the private `planner_graph` Cloud Run service, which now
accepts `POST /planner-memory/ingest` and persists canonical memory rows in the
planner-owned Postgres store.

## Goal

Reduce repeated historical context in `/planner-runs` payloads by moving durable
planning context into planner-owned memory.

Verdify remains responsible for:

- current-run context
- bounded outcome facts
- curated reference material
- selected compact prior-plan summaries

Planner remains responsible for:

- persistence
- dedupe
- retrieval
- trust weighting
- long-term memory ownership

## Current Live Ingestion Path

Already implemented in Verdify:

- `ingestor/planner_memory_ingest.py`
  - auth resolution
  - memory item shaping helpers
  - support-doc file loader
  - ingest state file read/write
  - `POST /planner-memory/ingest` client
- `ingestor/tasks.py`
  - `planner_memory_ingest_sync(pool)`
- `ingestor/ingestor.py`
  - periodic task registration

Current rollout defaults on `iris`:

- `PLANNER_MEMORY_INGEST_ENABLED=true`
- `PLANNER_MEMORY_INGEST_OUTCOMES_ENABLED=true`
- `PLANNER_MEMORY_INGEST_PRIOR_PLANS_ENABLED=false`
- `PLANNER_MEMORY_INGEST_SUPPORT_DOCS_ENABLED=false`
- `PLANNER_MEMORY_INGEST_MAX_BATCH_ITEMS=10`

Current live behavior:

- validated `observed_outcome` rows are ingested
- prior-plan summaries are not yet enabled
- support-doc seeding is not yet enabled

## Backfill Strategy

Do not bulk-dump all legacy context into planner memory.

Use a staged backfill in this order:

1. `observed_outcome`
2. `prior_plan`
3. curated `support_doc`
4. optional later imports from older reference material

Do not backfill:

- raw prompt transcripts
- raw `retrieval_refs` blobs
- full site snapshots
- unbounded telemetry
- anything marked `planner_inferred`
- secrets or credentials

## Category Plan

### 1. Observed Outcome Backfill

Source of truth:

- `plan_journal`
- `plan_delivery_log`

Ingest only rows that satisfy all of:

- `validated_at IS NOT NULL`
- not an `iris-reactive%` plan id
- outcome text is present enough to form a useful summary

Map to planner memory as:

- `memory_type = "observed_outcome"`
- `source_type = "verdify_outcome"`
- `source_id = "plan-eval:{plan_id}"`
- `trust_level = "observed_outcome"`

Payload should include compact machine-readable facts:

- `plan_id`
- `trigger_id`
- `planner_instance`
- `outcome_score`
- `anchor_score`
- `validated_at`

Backfill order:

- oldest validated rows first
- bounded batches
- idempotent retries by `source_id`

Recommendation:

- first historical backfill target: last 30 to 90 days
- skip rows with obviously low-value or empty summaries

### 2. Prior Plan Backfill

Source of truth:

- `plan_journal`
- optionally `plan_delivery_log`

This should be compact summary memory, not full plan serialization.

Map to planner memory as:

- `memory_type = "prior_plan"`
- `source_type = "verdify_plan_summary"`
- `source_id = "plan-summary:{plan_id}"`
- `trust_level = "verdify_context"`

Body should remain short and structured around:

- hypothesis
- expected outcome
- event type
- plan id
- compact execution/result notes if available

Only backfill after observed outcomes are already flowing.

Recommendation:

- start with the same validated cohort as observed outcomes
- keep this behind a separate flag until retrieval quality is reviewed

### 3. Support Doc Seed Backfill

Source of truth:

- curated JSON seed file committed in repo or stored in a known local path

Support docs should represent stable operating guidance, not noisy run history.

Good candidates:

- venting heuristics
- sunrise humidity ramp guidance
- irrigation guardrails
- seasonal playbooks
- operator reference notes that stay true across many runs

Bad candidates:

- transient alarms
- single-run anecdotes without validation
- copied planner prompts
- giant handbook dumps

Recommendation:

- start with 5 to 20 documents
- one JSON seed file
- idempotent reseeding by `source_id`

## Suggested Historical Backfill Order

Use this exact order for the agent implementation:

1. Confirm current live outcomes-only ingestion is healthy.
2. Build a one-off historical backfill script for `observed_outcome`.
3. Run a bounded dry-run mode showing candidate rows and generated payloads.
4. Backfill recent validated outcomes.
5. Review planner retrieval quality on those rows.
6. Add `prior_plan` backfill behind a separate flag.
7. Create and review the first `support_docs.json` seed file.
8. Seed support docs once.
9. Only then consider broader historical imports.

## Backfill Script Contract

Verdify provides a dedicated sidecar script for historical planner memory
loads:

- `scripts/backfill-planner-memory.py`

The script is not part of the hot control loop. Operators run it explicitly for
bounded dry-runs and one-off writes.

It should support:

- `--mode outcomes|prior-plans|support-docs`
- `--since 2026-04-01`
- `--until 2026-05-22`
- `--limit 100`
- `--dry-run`
- `--batch-size 10`
- `--support-doc-file /path/to/support_docs.json`
- `--greenhouse-id vallery`

Examples:

```bash
/srv/greenhouse/.venv/bin/python scripts/backfill-planner-memory.py \
  --mode outcomes --since 2026-03-01 --limit 25 --dry-run

/srv/greenhouse/.venv/bin/python scripts/backfill-planner-memory.py \
  --mode support-docs \
  --support-doc-file docs/planner/support_docs.example.json \
  --dry-run
```

Remove `--dry-run` only after reviewing candidate `source_id` values and the
sample payload. Writes are idempotent by source id on the planner side and are
reported as accepted, duplicate, or rejected counts per batch.

The script should:

- reuse `ingestor/planner_memory_ingest.py`
- reuse the existing auth path
- reuse existing item builders where practical
- print batch summaries
- stop on hard HTTP errors
- remain safe to retry

### Dry Run Requirements

`--dry-run` must:

- avoid writing to planner
- print candidate count
- print a sample payload
- show which `source_id` values would be sent

### Logging Requirements

For each batch, log:

- mode
- batch id
- item count
- accepted count
- duplicate count
- rejected count
- first and last source id in the batch

### Idempotency Requirements

The script must preserve stable `source_id` values.

This is required so:

- repeated runs are safe
- partial failures are recoverable
- planner counts duplicates instead of duplicating rows

## Support Doc Seed File Contract

Suggested repo path:

- `docs/planner/support_docs.example.json`

Suggested live file path for the first real seed:

- `/srv/verdify/state/planner-support-docs.json`

The file must contain a top-level JSON array.

Each item must include:

- `source_id`
- `title`
- `summary`
- `body`

Optional fields:

- `source_type`
- `event_type`
- `tags`
- `importance`
- `confidence`
- `payload`
- `valid_from`
- `expires_at`

Planner-side shaping rules already enforced by Verdify:

- `memory_type` becomes `support_doc`
- default `source_type` is `verdify_doc`
- `trust_level` becomes `verdify_context`
- title, summary, and body are truncated to planner-safe limits

## Sample Support Doc File

See adjacent file:

- `docs/planner/support_docs.example.json`

## Acceptance Criteria For The Agent

The implementation is complete when:

1. A one-off historical backfill script exists.
2. It can backfill `observed_outcome` rows using stable `source_id` values.
3. It supports `--dry-run`.
4. It supports bounded batched writes.
5. It can seed support docs from a JSON file.
6. The first support-doc file format is documented and exemplified.
7. The script does not block the live ingestor loop.
8. Retries are idempotent.
9. The implementation docs explain exactly which historical categories are allowed.

## Recommended First Execution Plan

For the first real rollout, the agent should:

1. Build `scripts/backfill-planner-memory.py`.
2. Test `--dry-run --mode outcomes --limit 5`.
3. Backfill a small recent `observed_outcome` slice.
4. Add the first 5 to 10 support docs in a seed file.
5. Seed support docs once.
6. Leave `prior_plan` historical backfill off until retrieval quality is reviewed.

## Explicit Non-Goal

Do not switch to vector-only storage in this phase.

Short-term recommendation:

- keep Postgres as the canonical memory store
- backfill canonical rows first
- add embeddings later
- use hybrid retrieval later

That keeps memory auditable and stable while leaving the door open for
`pgvector` or other embedding-backed retrieval once the memory corpus is worth
embedding.
