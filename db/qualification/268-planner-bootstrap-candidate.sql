-- #643 source-owned planner bootstrap candidate for reserved268.
-- NOT an applied/ledgered migration. Qualify exact267 predecessor and role design first.
-- Existing rows remain in place; these are the exact current initialize statements.

-- planner_graph/store.py:325
CREATE TABLE IF NOT EXISTS planner_graph_runs (
                        trigger_id UUID PRIMARY KEY,
                        thread_id UUID NOT NULL,
                        status TEXT NOT NULL,
                        run_mode TEXT NOT NULL,
                        current_step TEXT NULL,
                        terminal_status TEXT NULL,
                        execution_owner TEXT NULL,
                        last_error TEXT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        queued BOOLEAN NOT NULL DEFAULT TRUE,
                        submission_count INTEGER NOT NULL DEFAULT 1,
                        state JSONB NOT NULL DEFAULT '{}'::jsonb,
                        lease_owner TEXT NULL,
                        lease_expires_at TIMESTAMPTZ NULL,
                        started_at TIMESTAMPTZ NULL,
                        completed_at TIMESTAMPTZ NULL
                    );

-- planner_graph/store.py:347
CREATE INDEX IF NOT EXISTS planner_graph_runs_status_idx ON planner_graph_runs(status, queued, updated_at);

-- planner_graph/memory.py:237
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

-- planner_graph/memory.py:275
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

-- planner_graph/memory.py:327
CREATE UNIQUE INDEX IF NOT EXISTS planner_memory_items_unique_content_idx
                    ON planner_memory_items(greenhouse_id, memory_type, content_hash);

-- planner_graph/memory.py:333
CREATE UNIQUE INDEX IF NOT EXISTS planner_memory_items_unique_source_idx
                    ON planner_memory_items(greenhouse_id, source_type, source_id)
                    WHERE source_id IS NOT NULL;

-- planner_graph/memory.py:340
CREATE INDEX IF NOT EXISTS planner_memory_items_lookup_idx
                    ON planner_memory_items(greenhouse_id, memory_type, event_type, created_at DESC);

-- planner_graph/memory.py:346
CREATE INDEX IF NOT EXISTS planner_memory_items_search_idx
                    ON planner_memory_items
                    USING GIN (
                        to_tsvector(
                            'english',
                            coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(body, '')
                        )
                    );

-- planner_graph/memory.py:358
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

-- planner_graph/memory.py:374
CREATE INDEX IF NOT EXISTS planner_memory_retrievals_trigger_idx
                    ON planner_memory_retrievals(trigger_id, created_at DESC);

-- planner_graph/memory.py:380
CREATE INDEX IF NOT EXISTS planner_memory_retrievals_greenhouse_idx
                    ON planner_memory_retrievals(greenhouse_id, created_at DESC);
