from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TESTS_ROOT.parent
INGESTOR_ROOT = REPO_ROOT / "ingestor"
if str(INGESTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(INGESTOR_ROOT))

import planner_graph_shadow  # noqa: E402
import tasks  # noqa: E402


def test_build_outcome_memory_item_shapes_observed_outcome() -> None:
    row = {
        "plan_id": "iris-2026-05-22-001",
        "trigger_id": "11111111-1111-1111-1111-111111111111",
        "event_type": "SUNRISE",
        "expected_outcome": "Hold VPD near target through noon.",
        "actual_outcome": "VPD held near target and stress hours dropped.",
        "outcome_score": 9,
        "anchor_score": 8.5,
        "lesson_extracted": "Earlier venting helped.",
        "planner_instance": "local",
        "validated_at": datetime(2026, 5, 22, 14, 0, tzinfo=UTC),
    }

    item = planner_graph_shadow.build_outcome_memory_item(row)

    assert item.memory_type == "observed_outcome"
    assert item.source_type == "verdify_outcome"
    assert item.source_id == "plan-eval:iris-2026-05-22-001"
    assert item.trust_level == "observed_outcome"
    assert item.event_type == "SUNRISE"
    assert item.payload["outcome_score"] == 9
    assert item.valid_from == "2026-05-22T14:00:00+00:00"


def test_build_memory_ingest_request_wraps_items() -> None:
    item = planner_graph_shadow.PlannerMemoryItem(
        memory_type="support_doc",
        source_type="verdify_doc",
        source_id="doc-001",
        title="Doc",
        summary="Summary",
        body="Body",
        trust_level="verdify_context",
        tags=("reference",),
    )

    payload = planner_graph_shadow.build_memory_ingest_request(
        greenhouse_id="vallery",
        batch_id="batch-1",
        items=[item],
    )

    assert payload["greenhouse_id"] == "vallery"
    assert payload["source_system"] == "verdify"
    assert payload["batch_id"] == "batch-1"
    assert payload["items"][0]["source_id"] == "doc-001"
    assert payload["items"][0]["tags"] == ["reference"]


class _FakeAcquire:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def fetch(self, *args, **kwargs):  # noqa: ANN001, D401
        return self._rows


class _FakePool:
    def __init__(self, rows):
        self._rows = rows

    def acquire(self):
        return _FakeAcquire(self._rows)


@pytest.mark.asyncio
async def test_planner_memory_ingest_sync_advances_validated_watermark(monkeypatch) -> None:
    saved_state: dict[str, object] = {}
    rows = [
        {
            "plan_id": "iris-2026-05-22-001",
            "trigger_id": "11111111-1111-1111-1111-111111111111",
            "created_at": datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
            "validated_at": datetime(2026, 5, 22, 14, 0, tzinfo=UTC),
            "expected_outcome": "Expected",
            "actual_outcome": "Actual",
            "outcome_score": 8,
            "anchor_score": 7.5,
            "lesson_extracted": "Lesson",
            "event_type": "SUNRISE",
            "planner_instance": "local",
            "hypothesis": "Hypothesis",
        }
    ]

    monkeypatch.setattr(planner_graph_shadow, "planner_memory_ingest_enabled", lambda: True)
    monkeypatch.setattr(planner_graph_shadow, "planner_memory_ingest_outcomes_enabled", lambda: True)
    monkeypatch.setattr(planner_graph_shadow, "planner_memory_ingest_prior_plans_enabled", lambda: False)
    monkeypatch.setattr(planner_graph_shadow, "planner_memory_ingest_support_docs_enabled", lambda: False)
    monkeypatch.setattr(planner_graph_shadow, "planner_memory_ingest_max_batch_items", lambda: 10)
    monkeypatch.setattr(
        planner_graph_shadow,
        "load_memory_ingest_state",
        lambda: {"last_validated_at": None, "seeded_support_doc_ids": []},
    )
    monkeypatch.setattr(
        planner_graph_shadow,
        "save_memory_ingest_state",
        lambda state: saved_state.update(state),
    )
    monkeypatch.setattr(
        planner_graph_shadow,
        "ingest_planner_memory_batch",
        lambda **kwargs: planner_graph_shadow.HttpResult(
            200,
            {"accepted_count": 1, "duplicate_count": 0, "rejected_count": 0},
            12,
        ),
    )

    await tasks.planner_memory_ingest_sync(_FakePool(rows))

    assert saved_state["last_validated_at"] == "2026-05-22T14:00:00+00:00"


def test_task_loop_registers_planner_memory_ingest() -> None:
    src = (INGESTOR_ROOT / "ingestor.py").read_text()
    assert '("planner_memory_ingest", 300, planner_memory_ingest_sync)' in src
