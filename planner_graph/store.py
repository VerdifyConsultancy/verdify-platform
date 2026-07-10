"""Run storage for planner execution state.

This module persists planner runs, exposes queue/claim/update behavior, and
projects stored state back into API responses. It connects the planner graph to
durability, idempotency, and the asynchronous run lifecycle.
"""

from __future__ import annotations

import copy
import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast
from uuid import UUID

from planner_graph.contracts import (
    DiagnosisResponse,
    GuardrailPreviewResponse,
    PlannerMetadataResponse,
    PrimaryActionResponse,
    RunStatusResponse,
    ValidationSummaryResponse,
)
from planner_graph.state import PlannerState, PlannerStatus, RunMode, utc_now


class LeaseLostError(RuntimeError):
    """Raised when a worker no longer owns a live lease for a run."""


def empty_planner_state() -> PlannerState:
    return {}


def summarize_state(state: PlannerState) -> dict[str, object]:
    return {
        "graph_version": state.get("graph_version"),
        "event_type": state.get("event_type"),
        "event_label": state.get("event_label"),
        "context_digest": state.get("context_digest"),
        "context_sections": state.get("context_sections", [])[:8],
        "context_completeness": state.get("context_completeness"),
        "context_weaknesses": state.get("context_weaknesses", [])[:8],
        "retrieval_queries": state.get("retrieval_queries", [])[:4],
        "selected_action": state.get("selected_action"),
        "proposed_payload": state.get("proposed_payload"),
        "proposed_rationale": state.get("proposed_rationale"),
        "proposed_confidence": state.get("proposed_confidence"),
        "expected_effect": state.get("expected_effect"),
        "proposal_decision_summary": state.get("proposal_decision_summary"),
        "validation_status": state.get("validation_status"),
        "validation_errors": state.get("validation_errors", [])[:8],
        "contract_shape_rejection_reasons": state.get(
            "contract_shape_rejection_reasons", []
        )[:8],
        "guardrail_preview": state.get("guardrail_preview"),
        "guardrail_reasons": state.get("guardrail_reasons", [])[:8],
        "guardrail_outcome": state.get("guardrail_outcome"),
        "revision_reason": state.get("revision_reason"),
        "fail_closed_reason": state.get("fail_closed_reason"),
        "mcp_result": state.get("mcp_result"),
        "delivery_status": state.get("delivery_status"),
        "readback_status": state.get("readback_status"),
        "warnings": state.get("warnings", [])[:8],
        "errors": state.get("errors", [])[:8],
    }


@dataclass
class RunRecord:
    trigger_id: UUID
    thread_id: UUID
    status: PlannerStatus = "queued"
    run_mode: RunMode = "production"
    current_step: str | None = None
    terminal_status: str | None = None
    execution_owner: str | None = None
    last_error: str | None = None
    updated_at: str = field(default_factory=utc_now)
    state: PlannerState = field(default_factory=empty_planner_state)
    queued: bool = True
    submission_count: int = 1
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None

    def response(self) -> RunStatusResponse:
        diagnosis = None
        diagnosis_value = self.state.get("diagnosis")
        if diagnosis_value is not None:
            diagnosis = DiagnosisResponse.model_validate(diagnosis_value)
        primary_action = None
        if self.state.get("selected_action") is not None:
            primary_action = PrimaryActionResponse(
                action_type=cast(str, self.state.get("selected_action")),
                payload=cast(dict[str, object], self.state.get("proposed_payload", {})),
                rationale=cast(str, self.state.get("proposed_rationale", "")),
                confidence=float(self.state.get("proposed_confidence", 0.0)),
                expected_effect=cast(str | None, self.state.get("expected_effect")),
            )
        return RunStatusResponse(
            trigger_id=self.trigger_id,
            thread_id=self.thread_id,
            status=self.status,
            run_mode=self.run_mode,
            current_step=self.current_step,
            terminal_status=self.terminal_status,
            execution_owner=self.execution_owner,
            last_error=self.last_error,
            updated_at=self.updated_at,
            diagnosis=diagnosis,
            primary_action=primary_action,
            validation_summary=ValidationSummaryResponse(
                validation_status=cast(str | None, self.state.get("validation_status")),
                validation_errors=cast(
                    list[str], self.state.get("validation_errors", [])
                ),
                registry_violations=cast(
                    list[str], self.state.get("registry_violations", [])
                ),
                band_ownership_violations=cast(
                    list[str], self.state.get("band_ownership_violations", [])
                ),
                tier1_coverage_status=cast(
                    str | None, self.state.get("tier1_coverage_status")
                ),
            ),
            guardrail_preview=GuardrailPreviewResponse(
                would_clamp=cast(
                    bool | None,
                    cast(
                        dict[str, object], self.state.get("guardrail_preview", {})
                    ).get("would_clamp"),
                )
                if self.state.get("guardrail_preview") is not None
                else None,
                summary=cast(
                    str | None,
                    cast(
                        dict[str, object], self.state.get("guardrail_preview", {})
                    ).get("summary"),
                )
                if self.state.get("guardrail_preview") is not None
                else None,
                expected_clamps=cast(list[str], self.state.get("expected_clamps", [])),
                hold_risk=cast(str | None, self.state.get("hold_risk")),
                transition_audit_refs=cast(
                    list[str], self.state.get("transition_audit_refs", [])
                ),
            ),
            planner_metadata=PlannerMetadataResponse(
                contract_version=cast(str | None, self.state.get("contract_version")),
                context_version=cast(str | None, self.state.get("context_version")),
                planner_graph_version=cast(str | None, self.state.get("graph_version")),
                run_mode=self.run_mode,
            ),
            state=summarize_state(self.state),
        )


class RunStore(Protocol):
    def initialize(self) -> None: ...

    def create_or_resume(
        self, trigger_id: UUID, initial_state: PlannerState
    ) -> tuple[RunRecord, bool]: ...

    def claim_next(self, owner: str, lease_seconds: int) -> RunRecord | None: ...

    def renew_lease(self, trigger_id: UUID, owner: str, lease_seconds: int) -> bool: ...

    def mark_completed(
        self, trigger_id: UUID, state: PlannerState, owner: str
    ) -> RunRecord: ...

    def mark_failed(
        self, trigger_id: UUID, error: Exception, owner: str
    ) -> RunRecord: ...

    def get(self, trigger_id: UUID) -> RunRecord | None: ...


class InMemoryRunStore:
    def __init__(self) -> None:
        self._records: dict[UUID, RunRecord] = {}
        self._queue: list[UUID] = []
        self._lock = threading.Lock()

    def initialize(self) -> None:
        return None

    def create_or_resume(
        self, trigger_id: UUID, initial_state: PlannerState
    ) -> tuple[RunRecord, bool]:
        with self._lock:
            record = self._records.get(trigger_id)
            if record is None:
                record = RunRecord(trigger_id=trigger_id, thread_id=trigger_id)
                record.state = copy.deepcopy(initial_state)
                self._records[trigger_id] = record
                self._queue.append(trigger_id)
                return copy.deepcopy(record), True

            record.submission_count += 1
            should_enqueue = record.status not in {"queued", "running", "completed"}
            if should_enqueue:
                record.status = "queued"
                record.queued = True
                record.updated_at = utc_now()
                record.state = copy.deepcopy(initial_state)
                self._queue.append(trigger_id)
            return copy.deepcopy(record), should_enqueue

    def claim_next(self, owner: str, lease_seconds: int) -> RunRecord | None:
        with self._lock:
            now = datetime.now(UTC)
            record: RunRecord | None = None
            while self._queue and record is None:
                trigger_id = self._queue.pop(0)
                candidate = self._records[trigger_id]
                if candidate.status == "queued" and candidate.queued:
                    record = candidate
            if record is None:
                expired = sorted(
                    (
                        candidate
                        for candidate in self._records.values()
                        if candidate.status == "running"
                        and candidate.lease_expires_at is not None
                        and candidate.lease_expires_at < now
                    ),
                    key=lambda candidate: candidate.updated_at,
                )
                if not expired:
                    return None
                record = expired[0]
            record.status = "running"
            record.queued = False
            record.execution_owner = owner
            record.lease_owner = owner
            record.lease_expires_at = now + timedelta(seconds=lease_seconds)
            record.updated_at = utc_now()
            record.state["status"] = "running"
            return copy.deepcopy(record)

    def renew_lease(self, trigger_id: UUID, owner: str, lease_seconds: int) -> bool:
        with self._lock:
            now = datetime.now(UTC)
            record = self._records.get(trigger_id)
            if (
                record is None
                or record.status != "running"
                or record.lease_owner != owner
                or record.lease_expires_at is None
                or record.lease_expires_at <= now
            ):
                return False
            record.lease_expires_at = now + timedelta(seconds=lease_seconds)
            record.updated_at = utc_now()
            return True

    @staticmethod
    def _require_live_lease(record: RunRecord, owner: str) -> None:
        now = datetime.now(UTC)
        if (
            record.status != "running"
            or record.lease_owner != owner
            or record.lease_expires_at is None
            or record.lease_expires_at <= now
        ):
            raise LeaseLostError(
                f"planner run lease lost for trigger {record.trigger_id} and owner {owner}"
            )

    def mark_completed(
        self, trigger_id: UUID, state: PlannerState, owner: str
    ) -> RunRecord:
        with self._lock:
            record = self._records[trigger_id]
            self._require_live_lease(record, owner)
            record.status = "completed"
            record.current_step = state.get("current_step")
            record.terminal_status = state.get("terminal_status")
            record.execution_owner = owner
            record.updated_at = state.get("updated_at", utc_now())
            record.state = copy.deepcopy(state)
            record.queued = False
            record.lease_owner = None
            record.lease_expires_at = None
            return copy.deepcopy(record)

    def mark_failed(self, trigger_id: UUID, error: Exception, owner: str) -> RunRecord:
        with self._lock:
            record = self._records[trigger_id]
            self._require_live_lease(record, owner)
            record.status = "failed"
            record.last_error = str(error)
            record.execution_owner = owner
            record.updated_at = utc_now()
            record.state = {
                **record.state,
                "status": "failed",
                "errors": [*record.state.get("errors", []), str(error)],
                "updated_at": record.updated_at,
            }
            record.queued = False
            record.lease_owner = None
            record.lease_expires_at = None
            return copy.deepcopy(record)

    def get(self, trigger_id: UUID) -> RunRecord | None:
        with self._lock:
            record = self._records.get(trigger_id)
            return None if record is None else copy.deepcopy(record)


class PostgresRunStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def initialize(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
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
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS planner_graph_runs_status_idx "
                    "ON planner_graph_runs(status, queued, updated_at)"
                )
                conn.commit()

    def create_or_resume(
        self, trigger_id: UUID, initial_state: PlannerState
    ) -> tuple[RunRecord, bool]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO planner_graph_runs (
                        trigger_id, thread_id, status, run_mode, queued, submission_count, state
                    )
                    VALUES (%s, %s, 'queued', 'production', TRUE, 1, %s::jsonb)
                    ON CONFLICT (trigger_id) DO NOTHING
                    """,
                    (
                        trigger_id,
                        trigger_id,
                        json.dumps(initial_state),
                    ),
                )
                inserted = cur.rowcount > 0
                if not inserted:
                    cur.execute(
                        """
                        UPDATE planner_graph_runs
                           SET submission_count = submission_count + 1,
                               status = CASE WHEN status = 'failed' THEN 'queued' ELSE status END,
                               queued = CASE WHEN status = 'failed' THEN TRUE ELSE queued END,
                               updated_at = CASE WHEN status = 'failed' THEN now() ELSE updated_at END,
                               state = CASE WHEN status = 'failed' THEN %s::jsonb ELSE state END
                         WHERE trigger_id = %s
                        """,
                        (json.dumps(initial_state), trigger_id),
                    )
                conn.commit()
            record = self.get(trigger_id)
            if record is None:
                raise RuntimeError(
                    "planner_graph_runs row missing after create_or_resume"
                )
            should_enqueue = inserted or record.status == "queued"
            if not inserted and record.status in {"running", "completed"}:
                should_enqueue = False
            return record, should_enqueue

    def claim_next(self, owner: str, lease_seconds: int) -> RunRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT trigger_id
                      FROM planner_graph_runs
                     WHERE (
                           status = 'queued'
                       AND queued = TRUE
                       AND (lease_expires_at IS NULL OR lease_expires_at < now())
                     ) OR (
                           status = 'running'
                       AND lease_expires_at IS NOT NULL
                       AND lease_expires_at < now()
                     )
                     ORDER BY updated_at ASC
                     LIMIT 1
                     FOR UPDATE SKIP LOCKED
                    """
                )
                raw_row = cur.fetchone()
                if raw_row is None:
                    conn.rollback()
                    return None
                row = cast(dict[str, object], dict(cast(Any, raw_row)))
                trigger_id = cast(UUID, row["trigger_id"])
                cur.execute(
                    """
                    UPDATE planner_graph_runs
                       SET status = 'running',
                           queued = FALSE,
                           execution_owner = %s,
                           lease_owner = %s,
                           lease_expires_at = now() + (%s || ' seconds')::interval,
                           started_at = COALESCE(started_at, now()),
                           updated_at = now(),
                           state = jsonb_set(state, '{status}', '\"running\"', TRUE)
                     WHERE trigger_id = %s
                    """,
                    (owner, owner, str(lease_seconds), trigger_id),
                )
                conn.commit()
                return self.get(trigger_id)

    def renew_lease(self, trigger_id: UUID, owner: str, lease_seconds: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE planner_graph_runs
                       SET lease_expires_at = now() + (%s || ' seconds')::interval,
                           updated_at = now()
                     WHERE trigger_id = %s
                       AND status = 'running'
                       AND lease_owner = %s
                       AND lease_expires_at > now()
                     RETURNING trigger_id
                    """,
                    (str(lease_seconds), trigger_id, owner),
                )
                renewed = cur.fetchone() is not None
                conn.commit()
                return renewed

    def mark_completed(
        self, trigger_id: UUID, state: PlannerState, owner: str
    ) -> RunRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE planner_graph_runs
                       SET status = 'completed',
                           current_step = %s,
                           terminal_status = %s,
                           execution_owner = %s,
                           updated_at = now(),
                           completed_at = now(),
                           state = %s::jsonb,
                           lease_owner = NULL,
                           lease_expires_at = NULL,
                           queued = FALSE
                     WHERE trigger_id = %s
                       AND status = 'running'
                       AND lease_owner = %s
                       AND lease_expires_at > now()
                     RETURNING *
                    """,
                    (
                        state.get("current_step"),
                        state.get("terminal_status"),
                        owner,
                        json.dumps(state),
                        trigger_id,
                        owner,
                    ),
                )
                raw_row = cur.fetchone()
                if raw_row is None:
                    conn.rollback()
                    raise LeaseLostError(
                        f"planner run lease lost for trigger {trigger_id} and owner {owner}"
                    )
                conn.commit()
        return self._row_to_record(cast(dict[str, object], dict(cast(Any, raw_row))))

    def mark_failed(self, trigger_id: UUID, error: Exception, owner: str) -> RunRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE planner_graph_runs
                       SET status = 'failed',
                           last_error = %s,
                           execution_owner = %s,
                           updated_at = now(),
                           lease_owner = NULL,
                           lease_expires_at = NULL,
                           queued = FALSE,
                           state = jsonb_set(
                               jsonb_set(state, '{status}', '\"failed\"', TRUE),
                               '{updated_at}',
                               to_jsonb(%s::text),
                               TRUE
                           )
                     WHERE trigger_id = %s
                       AND status = 'running'
                       AND lease_owner = %s
                       AND lease_expires_at > now()
                     RETURNING *
                    """,
                    (str(error), owner, utc_now(), trigger_id, owner),
                )
                raw_row = cur.fetchone()
                if raw_row is None:
                    conn.rollback()
                    raise LeaseLostError(
                        f"planner run lease lost for trigger {trigger_id} and owner {owner}"
                    )
                conn.commit()
        return self._row_to_record(cast(dict[str, object], dict(cast(Any, raw_row))))

    def get(self, trigger_id: UUID) -> RunRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM planner_graph_runs WHERE trigger_id = %s",
                    (trigger_id,),
                )
                raw_row = cur.fetchone()
        if raw_row is None:
            return None
        row = cast(dict[str, object], dict(cast(Any, raw_row)))
        return self._row_to_record(row)

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return cast(Any, psycopg.connect(self.dsn, row_factory=dict_row))  # pyright: ignore[reportArgumentType]

    def _row_to_record(self, row: dict[str, object]) -> RunRecord:
        updated_at = cast(datetime, row["updated_at"]).isoformat()
        return RunRecord(
            trigger_id=cast(UUID, row["trigger_id"]),
            thread_id=cast(UUID, row["thread_id"]),
            status=cast(PlannerStatus, row["status"]),
            run_mode=cast(RunMode, row["run_mode"]),
            current_step=cast(str | None, row["current_step"]),
            terminal_status=cast(str | None, row["terminal_status"]),
            execution_owner=cast(str | None, row["execution_owner"]),
            last_error=cast(str | None, row["last_error"]),
            updated_at=updated_at,
            state=cast(
                PlannerState, copy.deepcopy(row["state"]) if row["state"] else {}
            ),
            queued=cast(bool, row["queued"]),
            submission_count=cast(int, row["submission_count"]),
            lease_owner=cast(str | None, row["lease_owner"]),
            lease_expires_at=cast(datetime | None, row["lease_expires_at"]),
        )
