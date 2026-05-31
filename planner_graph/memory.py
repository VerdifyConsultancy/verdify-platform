"""Planner-owned memory stores and retrieval contracts.

This module defines the interfaces and concrete backends for bounded planner
memory. It keeps durable historical context separate from run lifecycle
storage, allowing planner retrieval to evolve without coupling to run queueing.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast
from uuid import UUID, uuid4

from planner_graph.state import PlannerState

MemoryType = Literal[
    "lesson", "support_doc", "prior_plan", "observed_outcome", "planner_summary"
]
TrustLevel = Literal["observed_outcome", "verdify_context", "planner_inferred"]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class MemoryItem:
    memory_id: UUID
    greenhouse_id: str
    memory_type: MemoryType
    title: str
    summary: str
    snippet: str
    source_type: str
    source_id: str | None = None
    trigger_id: str | None = None
    event_type: str | None = None
    tags: tuple[str, ...] = ()
    importance: int = 3
    confidence: float | None = None
    trust_level: TrustLevel = "planner_inferred"
    payload: dict[str, object] = field(default_factory=dict)
    valid_from: str | None = None
    expires_at: str | None = None
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class MemoryQuery:
    greenhouse_id: str
    event_type: str | None
    query_text: str
    top_k: int = 3
    trigger_id: str | None = None


@dataclass(frozen=True)
class MemoryResult:
    lessons: list[MemoryItem] = field(default_factory=list)
    docs: list[MemoryItem] = field(default_factory=list)
    plan_refs: list[MemoryItem] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryIngestRejection:
    index: int
    reason: str


@dataclass(frozen=True)
class MemoryIngestResult:
    accepted_count: int = 0
    rejected_count: int = 0
    duplicate_count: int = 0
    accepted_ids: list[UUID] = field(default_factory=list)
    rejections: list[MemoryIngestRejection] = field(default_factory=list)


class PlannerMemoryStore(Protocol):
    def initialize(self) -> None: ...

    def retrieve(self, query: MemoryQuery) -> MemoryResult: ...

    def persist_run_summary(self, state: PlannerState) -> MemoryItem | None: ...

    def ingest_items(self, items: list[MemoryItem]) -> MemoryIngestResult: ...


class DisabledMemoryStore:
    def initialize(self) -> None:
        return None

    def retrieve(self, query: MemoryQuery) -> MemoryResult:
        del query
        return MemoryResult(metadata={"backend": "disabled"})

    def persist_run_summary(self, state: PlannerState) -> MemoryItem | None:
        del state
        return None

    def ingest_items(self, items: list[MemoryItem]) -> MemoryIngestResult:
        return MemoryIngestResult(
            rejected_count=len(items),
            rejections=[
                MemoryIngestRejection(
                    index=index, reason="planner memory backend is disabled"
                )
                for index, _ in enumerate(items)
            ],
        )


class InMemoryMemoryStore:
    def __init__(self, seed_items: list[MemoryItem] | None = None) -> None:
        self._items: list[MemoryItem] = list(seed_items or [])
        self._lock = threading.Lock()

    def initialize(self) -> None:
        return None

    def retrieve(self, query: MemoryQuery) -> MemoryResult:
        with self._lock:
            candidates = [
                item
                for item in self._items
                if item.greenhouse_id == query.greenhouse_id
            ]
        ranked = sorted(
            candidates,
            key=lambda item: (
                0 if item.event_type == query.event_type else 1,
                -item.importance,
                -_timestamp_sort_value(item.created_at),
            ),
            reverse=False,
        )
        lessons: list[MemoryItem] = []
        docs: list[MemoryItem] = []
        plan_refs: list[MemoryItem] = []
        for item in ranked:
            bucket: list[MemoryItem]
            if item.memory_type in {"lesson", "observed_outcome"}:
                bucket = lessons
            elif item.memory_type == "support_doc":
                bucket = docs
            elif item.memory_type == "prior_plan":
                bucket = plan_refs
            else:
                continue
            if len(bucket) < query.top_k:
                bucket.append(item)
        return MemoryResult(
            lessons=lessons,
            docs=docs,
            plan_refs=plan_refs,
            metadata={"backend": "memory", "query_text": query.query_text},
        )

    def persist_run_summary(self, state: PlannerState) -> MemoryItem | None:
        greenhouse_id = cast(str | None, state.get("greenhouse_id"))
        trigger_id = cast(str | None, state.get("trigger_id"))
        selected_action = cast(str | None, state.get("selected_action"))
        if greenhouse_id is None or trigger_id is None or selected_action is None:
            return None
        item = build_run_summary_item(state)
        with self._lock:
            for existing in self._items:
                if (
                    existing.memory_type == "prior_plan"
                    and existing.source_id == item.source_id
                ):
                    return existing
            self._items.append(item)
        return item

    def ingest_items(self, items: list[MemoryItem]) -> MemoryIngestResult:
        accepted_ids: list[UUID] = []
        accepted_count = 0
        duplicate_count = 0
        with self._lock:
            for item in items:
                content_hash = hash_memory_item(item)
                duplicate = next(
                    (
                        existing
                        for existing in self._items
                        if existing.greenhouse_id == item.greenhouse_id
                        and existing.memory_type == item.memory_type
                        and hash_memory_item(existing) == content_hash
                    ),
                    None,
                )
                if duplicate is not None:
                    duplicate_count += 1
                    accepted_ids.append(duplicate.memory_id)
                    continue
                source_match = next(
                    (
                        existing
                        for existing in self._items
                        if item.source_id is not None
                        and existing.greenhouse_id == item.greenhouse_id
                        and existing.source_type == item.source_type
                        and existing.source_id == item.source_id
                    ),
                    None,
                )
                if source_match is not None:
                    self._items.remove(source_match)
                    replacement = replace(item, memory_id=source_match.memory_id)
                    self._items.append(replacement)
                    accepted_ids.append(replacement.memory_id)
                    accepted_count += 1
                    continue
                self._items.append(item)
                accepted_ids.append(item.memory_id)
                accepted_count += 1
        return MemoryIngestResult(
            accepted_count=accepted_count,
            duplicate_count=duplicate_count,
            accepted_ids=accepted_ids,
        )


class PostgresMemoryStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def initialize(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
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
                    )
                    """
                )
                cur.execute(
                    """
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
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS planner_memory_items_unique_content_idx
                    ON planner_memory_items(greenhouse_id, memory_type, content_hash)
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS planner_memory_items_unique_source_idx
                    ON planner_memory_items(greenhouse_id, source_type, source_id)
                    WHERE source_id IS NOT NULL
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS planner_memory_items_lookup_idx
                    ON planner_memory_items(greenhouse_id, memory_type, event_type, created_at DESC)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS planner_memory_items_search_idx
                    ON planner_memory_items
                    USING GIN (
                        to_tsvector(
                            'english',
                            coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(body, '')
                        )
                    )
                    """
                )
                cur.execute(
                    """
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
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS planner_memory_retrievals_trigger_idx
                    ON planner_memory_retrievals(trigger_id, created_at DESC)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS planner_memory_retrievals_greenhouse_idx
                    ON planner_memory_retrievals(greenhouse_id, created_at DESC)
                    """
                )
                conn.commit()

    def retrieve(self, query: MemoryQuery) -> MemoryResult:
        started = datetime.now(UTC)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT memory_id, greenhouse_id, memory_type, title, summary, source_type, source_id,
                           trigger_id, event_type, body, tags, importance, confidence, trust_level, payload,
                           valid_from, expires_at, created_at
                      FROM planner_memory_items
                     WHERE greenhouse_id = %s
                       AND is_active = TRUE
                       AND memory_type IN ('lesson', 'support_doc', 'prior_plan', 'observed_outcome')
                       AND (valid_from IS NULL OR valid_from <= now())
                       AND (expires_at IS NULL OR expires_at > now())
                     ORDER BY
                       CASE WHEN event_type = %s THEN 0 ELSE 1 END,
                       ts_rank_cd(
                         to_tsvector(
                           'english',
                           coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(body, '')
                         ),
                         plainto_tsquery('english', %s)
                       ) DESC,
                       importance DESC,
                       created_at DESC
                     LIMIT %s
                    """,
                    (
                        query.greenhouse_id,
                        query.event_type,
                        query.query_text,
                        max(query.top_k * 6, 6),
                    ),
                )
                rows = cast(
                    list[dict[str, object]],
                    [dict(cast(Any, row)) for row in cur.fetchall()],
                )
                lessons: list[MemoryItem] = []
                docs: list[MemoryItem] = []
                plan_refs: list[MemoryItem] = []
                scores: dict[str, float] = {}
                for row in rows:
                    item = self._row_to_item(row)
                    scores[str(item.memory_id)] = float(item.importance)
                    if (
                        item.memory_type in {"lesson", "observed_outcome"}
                        and len(lessons) < query.top_k
                    ):
                        lessons.append(item)
                    elif item.memory_type == "support_doc" and len(docs) < query.top_k:
                        docs.append(item)
                    elif (
                        item.memory_type == "prior_plan"
                        and len(plan_refs) < query.top_k
                    ):
                        plan_refs.append(item)
                result_ids = [item.memory_id for item in [*lessons, *docs, *plan_refs]]
                latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
                cur.execute(
                    """
                    INSERT INTO planner_memory_retrievals (
                        retrieval_id, trigger_id, greenhouse_id, strategy, query_text, filters, result_ids, scores, latency_ms
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s)
                    """,
                    (
                        uuid4(),
                        _uuid_or_none(query.trigger_id),
                        query.greenhouse_id,
                        "postgres-recency",
                        query.query_text,
                        json.dumps(
                            {"event_type": query.event_type, "top_k": query.top_k}
                        ),
                        result_ids,
                        json.dumps(scores),
                        latency_ms,
                    ),
                )
                if result_ids:
                    cur.execute(
                        """
                        UPDATE planner_memory_items
                           SET last_used_at = now()
                         WHERE memory_id = ANY(%s)
                        """,
                        (result_ids,),
                    )
                conn.commit()
        return MemoryResult(
            lessons=lessons,
            docs=docs,
            plan_refs=plan_refs,
            metadata={"backend": "postgres", "query_text": query.query_text},
        )

    def persist_run_summary(self, state: PlannerState) -> MemoryItem | None:
        item = build_run_summary_item(state)
        content_hash = hash_memory_item(item)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO planner_memory_items (
                        memory_id, greenhouse_id, memory_type, source_type, source_id, trigger_id, event_type,
                        title, summary, body, tags, importance, confidence, trust_level, content_hash, payload
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (greenhouse_id, source_type, source_id)
                    WHERE source_id IS NOT NULL
                    DO UPDATE SET
                        memory_type = EXCLUDED.memory_type,
                        trigger_id = EXCLUDED.trigger_id,
                        event_type = EXCLUDED.event_type,
                        title = EXCLUDED.title,
                        summary = EXCLUDED.summary,
                        body = EXCLUDED.body,
                        tags = EXCLUDED.tags,
                        importance = EXCLUDED.importance,
                        payload = EXCLUDED.payload,
                        confidence = EXCLUDED.confidence,
                        trust_level = EXCLUDED.trust_level,
                        content_hash = EXCLUDED.content_hash,
                        updated_at = now(),
                        last_used_at = now()
                    RETURNING memory_id, greenhouse_id, memory_type, title, summary, source_type, source_id,
                              trigger_id, event_type, body, tags, importance, confidence, trust_level, payload,
                              valid_from, expires_at, created_at
                    """,
                    (
                        item.memory_id,
                        item.greenhouse_id,
                        item.memory_type,
                        item.source_type,
                        item.source_id,
                        UUID(item.trigger_id) if item.trigger_id is not None else None,
                        item.event_type,
                        item.title,
                        item.summary,
                        item.snippet,
                        list(item.tags),
                        item.importance,
                        item.confidence,
                        item.trust_level,
                        content_hash,
                        json.dumps(item.payload),
                    ),
                )
                raw_row = cur.fetchone()
                conn.commit()
        if raw_row is None:
            return item
        return self._row_to_item(dict(cast(Any, raw_row)))

    def ingest_items(self, items: list[MemoryItem]) -> MemoryIngestResult:
        accepted_ids: list[UUID] = []
        accepted_count = 0
        duplicate_count = 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                for item in items:
                    content_hash = hash_memory_item(item)
                    cur.execute(
                        """
                        SELECT memory_id
                          FROM planner_memory_items
                         WHERE greenhouse_id = %s
                           AND memory_type = %s
                           AND content_hash = %s
                         LIMIT 1
                        """,
                        (item.greenhouse_id, item.memory_type, content_hash),
                    )
                    duplicate_row = cur.fetchone()
                    if duplicate_row is not None:
                        duplicate_count += 1
                        accepted_ids.append(
                            cast(UUID, dict(cast(Any, duplicate_row))["memory_id"])
                        )
                        continue

                    source_memory_id: UUID | None = None
                    if item.source_id is not None:
                        cur.execute(
                            """
                            SELECT memory_id
                              FROM planner_memory_items
                             WHERE greenhouse_id = %s
                               AND source_type = %s
                               AND source_id = %s
                             LIMIT 1
                            """,
                            (item.greenhouse_id, item.source_type, item.source_id),
                        )
                        source_row = cur.fetchone()
                        if source_row is not None:
                            source_memory_id = cast(
                                UUID, dict(cast(Any, source_row))["memory_id"]
                            )

                    if source_memory_id is not None:
                        cur.execute(
                            """
                            UPDATE planner_memory_items
                               SET memory_type = %s,
                                   trigger_id = %s,
                                   event_type = %s,
                                   title = %s,
                                   summary = %s,
                                   body = %s,
                                   tags = %s,
                                   importance = %s,
                                   confidence = %s,
                                   trust_level = %s,
                                   content_hash = %s,
                                   payload = %s::jsonb,
                                   valid_from = %s,
                                   expires_at = %s,
                                   updated_at = now()
                             WHERE memory_id = %s
                            RETURNING memory_id
                            """,
                            (
                                item.memory_type,
                                _uuid_or_none(item.trigger_id),
                                item.event_type,
                                item.title,
                                item.summary,
                                item.snippet,
                                list(item.tags),
                                item.importance,
                                item.confidence,
                                item.trust_level,
                                content_hash,
                                json.dumps(item.payload),
                                item.valid_from,
                                item.expires_at,
                                source_memory_id,
                            ),
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO planner_memory_items (
                                memory_id, greenhouse_id, memory_type, source_type, source_id, trigger_id,
                                event_type, title, summary, body, tags, importance, confidence, trust_level,
                                content_hash, payload, valid_from, expires_at
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                            RETURNING memory_id
                            """,
                            (
                                item.memory_id,
                                item.greenhouse_id,
                                item.memory_type,
                                item.source_type,
                                item.source_id,
                                _uuid_or_none(item.trigger_id),
                                item.event_type,
                                item.title,
                                item.summary,
                                item.snippet,
                                list(item.tags),
                                item.importance,
                                item.confidence,
                                item.trust_level,
                                content_hash,
                                json.dumps(item.payload),
                                item.valid_from,
                                item.expires_at,
                            ),
                        )
                    stored_row = cur.fetchone()
                    if stored_row is not None:
                        accepted_ids.append(
                            cast(UUID, dict(cast(Any, stored_row))["memory_id"])
                        )
                        accepted_count += 1
                conn.commit()
        return MemoryIngestResult(
            accepted_count=accepted_count,
            duplicate_count=duplicate_count,
            accepted_ids=accepted_ids,
        )

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return cast(Any, psycopg.connect(self.dsn, row_factory=dict_row))  # pyright: ignore[reportArgumentType]

    def _row_to_item(self, row: dict[str, object]) -> MemoryItem:
        return MemoryItem(
            memory_id=cast(UUID, row["memory_id"]),
            greenhouse_id=cast(str, row["greenhouse_id"]),
            memory_type=cast(MemoryType, row["memory_type"]),
            title=cast(str, row["title"]),
            summary=cast(str, row["summary"]),
            snippet=cast(str, row["body"]),
            source_type=cast(str, row["source_type"]),
            source_id=cast(str | None, row["source_id"]),
            trigger_id=str(row["trigger_id"])
            if row["trigger_id"] is not None
            else None,
            event_type=cast(str | None, row["event_type"]),
            tags=tuple(cast(list[str], row["tags"] or [])),
            importance=cast(int, row["importance"]),
            confidence=cast(float | None, row["confidence"]),
            trust_level=cast(TrustLevel, row["trust_level"]),
            payload=cast(dict[str, object], row["payload"] or {}),
            valid_from=cast(datetime, row["valid_from"]).isoformat()
            if row.get("valid_from") is not None
            else None,
            expires_at=cast(datetime, row["expires_at"]).isoformat()
            if row.get("expires_at") is not None
            else None,
            created_at=cast(datetime, row["created_at"]).isoformat(),
        )


def _timestamp_sort_value(raw_timestamp: str) -> float:
    try:
        return datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _uuid_or_none(raw_value: str | None) -> UUID | None:
    if raw_value is None:
        return None
    try:
        return UUID(raw_value)
    except ValueError:
        return None


def build_run_summary_item(state: PlannerState) -> MemoryItem:
    greenhouse_id = cast(str | None, state.get("greenhouse_id"))
    trigger_id = cast(str | None, state.get("trigger_id"))
    event_type = cast(str | None, state.get("event_type"))
    selected_action = cast(str | None, state.get("selected_action"))
    if greenhouse_id is None or trigger_id is None or selected_action is None:
        raise ValueError(
            "build_run_summary_item requires greenhouse_id, trigger_id, and selected_action"
        )
    plan_id = cast(str | None, state.get("plan_id"))
    title = f"{selected_action} for {event_type or 'unknown event'}"
    summary = (
        f"Planner selected {selected_action}"
        + (f" plan_id={plan_id}" if plan_id else "")
        + f" for trigger {trigger_id}."
    )
    return MemoryItem(
        memory_id=uuid4(),
        greenhouse_id=greenhouse_id,
        memory_type="prior_plan",
        title=title,
        summary=summary,
        snippet=summary,
        source_type="planner_run",
        source_id=trigger_id,
        trigger_id=trigger_id,
        event_type=event_type,
        tags=(selected_action, event_type or "unknown_event"),
        importance=3,
        confidence=cast(float | None, state.get("proposed_confidence")),
        trust_level="planner_inferred",
        payload={
            "selected_action": selected_action,
            "proposed_payload": cast(
                dict[str, object], state.get("proposed_payload", {})
            ),
            "proposed_rationale": state.get("proposed_rationale", ""),
            "expected_effect": state.get("expected_effect", ""),
            "validation_status": state.get("validation_status", ""),
            "guardrail_outcome": state.get("guardrail_outcome", ""),
            "diagnosis": cast(dict[str, object], state.get("diagnosis", {})),
        },
    )


def hash_memory_item(item: MemoryItem) -> str:
    payload = {
        "greenhouse_id": item.greenhouse_id,
        "memory_type": item.memory_type,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "event_type": item.event_type,
        "title": item.title,
        "summary": item.summary,
        "snippet": item.snippet,
        "tags": list(item.tags),
        "payload": item.payload,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
