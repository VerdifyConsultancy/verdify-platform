-- Planner-owned memory tables.
-- Apply before enabling PLANNER_MEMORY_BACKEND=postgres in production.

CREATE TABLE IF NOT EXISTS planner_memory_items (
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

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'planner_memory_items'::regclass
           AND conname = 'planner_memory_items_importance_check'
    ) THEN
        ALTER TABLE planner_memory_items
        ADD CONSTRAINT planner_memory_items_importance_check
        CHECK (importance BETWEEN 1 AND 5);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'planner_memory_items'::regclass
           AND conname = 'planner_memory_items_confidence_check'
    ) THEN
        ALTER TABLE planner_memory_items
        ADD CONSTRAINT planner_memory_items_confidence_check
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));
    END IF;

    ALTER TABLE planner_memory_items
    DROP CONSTRAINT IF EXISTS planner_memory_items_type_check;
    ALTER TABLE planner_memory_items
    ADD CONSTRAINT planner_memory_items_type_check
    CHECK (memory_type IN ('lesson', 'support_doc', 'prior_plan', 'observed_outcome', 'planner_summary'));

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'planner_memory_items'::regclass
           AND conname = 'planner_memory_items_trust_level_check'
    ) THEN
        ALTER TABLE planner_memory_items
        ADD CONSTRAINT planner_memory_items_trust_level_check
        CHECK (trust_level IN ('observed_outcome', 'verdify_context', 'planner_inferred'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'planner_memory_items'::regclass
           AND conname = 'planner_memory_items_valid_window_check'
    ) THEN
        ALTER TABLE planner_memory_items
        ADD CONSTRAINT planner_memory_items_valid_window_check
        CHECK (expires_at IS NULL OR valid_from IS NULL OR expires_at > valid_from);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS planner_memory_items_unique_content_idx
ON planner_memory_items(greenhouse_id, memory_type, content_hash);

CREATE UNIQUE INDEX IF NOT EXISTS planner_memory_items_unique_source_idx
ON planner_memory_items(greenhouse_id, source_type, source_id)
WHERE source_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS planner_memory_items_lookup_idx
ON planner_memory_items(greenhouse_id, memory_type, event_type, created_at DESC);

CREATE INDEX IF NOT EXISTS planner_memory_items_search_idx
ON planner_memory_items
USING GIN (
    to_tsvector(
        'english',
        coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(body, '')
    )
);

CREATE TABLE IF NOT EXISTS planner_memory_retrievals (
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

CREATE INDEX IF NOT EXISTS planner_memory_retrievals_trigger_idx
ON planner_memory_retrievals(trigger_id, created_at DESC);

CREATE INDEX IF NOT EXISTS planner_memory_retrievals_greenhouse_idx
ON planner_memory_retrievals(greenhouse_id, created_at DESC);
