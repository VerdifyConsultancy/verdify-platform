"""Retrieval step for bounded memory and historical references.

This node pulls in small, relevant memories or support references that can
improve planning quality without turning the graph into an unbounded retrieval
pipeline. It connects the current trigger to prior lessons and context hints.
"""

from __future__ import annotations

from typing import cast

from planner_graph.memory import MemoryItem, MemoryQuery
from planner_graph.nodes import copy_state
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import PlannerState, utc_now


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def _normalize_ref(raw: object, *, snippet_limit: int) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    raw_id = (
        raw.get("id")
        or raw.get("memory_id")
        or raw.get("source_id")
        or raw.get("title")
    )
    raw_snippet = raw.get("snippet") or raw.get("summary") or raw.get("title")
    if not isinstance(raw_id, str) or not isinstance(raw_snippet, str):
        return None
    return {"id": raw_id, "snippet": _truncate(raw_snippet, snippet_limit)}


def _memory_item_ref(item: MemoryItem, *, snippet_limit: int) -> dict[str, str]:
    return {
        "id": str(item.memory_id),
        "snippet": _truncate(item.snippet or item.summary or item.title, snippet_limit),
    }


def _merge_refs(
    memory_items: list[MemoryItem],
    existing: object,
    *,
    max_items: int,
    snippet_limit: int,
) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in memory_items:
        normalized = _memory_item_ref(item, snippet_limit=snippet_limit)
        if normalized["id"] not in seen:
            seen.add(normalized["id"])
            merged.append(normalized)
        if len(merged) >= max_items:
            return merged
    if isinstance(existing, list):
        for raw in existing:
            normalized = _normalize_ref(raw, snippet_limit=snippet_limit)
            if normalized is None or normalized["id"] in seen:
                continue
            seen.add(normalized["id"])
            merged.append(normalized)
            if len(merged) >= max_items:
                break
    return merged


def build_retrieve_memory(runtime: PlannerRuntime):
    def retrieve_memory(state: PlannerState) -> PlannerState:
        runtime.hooks.before_node("retrieve_memory")
        event_type = state.get("event_type")
        greenhouse_id = state.get("greenhouse_id")
        if event_type is None or greenhouse_id is None:
            raise ValueError("retrieve_memory requires event_type and greenhouse_id")
        next_state = copy_state(state)
        next_state["current_step"] = "retrieve_memory"
        next_state["retrieval_queries"] = [
            f"event_type:{event_type}",
            f"greenhouse:{greenhouse_id}",
        ]
        if runtime.settings.planner_memory_backend == "disabled":
            next_state["updated_at"] = utc_now()
            return next_state
        query = MemoryQuery(
            greenhouse_id=str(greenhouse_id),
            event_type=str(event_type),
            query_text=f"event_type:{event_type} greenhouse:{greenhouse_id}",
            top_k=runtime.settings.planner_memory_top_k,
            trigger_id=str(state.get("trigger_id", "")) or None,
        )
        try:
            result = runtime.memory.retrieve(query)
        except Exception as error:  # pragma: no cover - validated via unit tests
            warnings = list(cast(list[str], next_state.get("warnings", [])))
            warnings.append(f"memory retrieval unavailable: {error}")
            next_state["warnings"] = warnings
            next_state["updated_at"] = utc_now()
            return next_state
        next_state["retrieval_queries"] = [
            *cast(list[str], next_state.get("retrieval_queries", [])),
            f"memory_backend:{result.metadata.get('backend', runtime.settings.planner_memory_backend)}",
        ]
        next_state["retrieved_lessons"] = _merge_refs(
            result.lessons,
            next_state.get("retrieved_lessons", []),
            max_items=runtime.settings.planner_memory_top_k,
            snippet_limit=runtime.settings.planner_memory_max_snippet_chars,
        )
        next_state["retrieved_docs"] = _merge_refs(
            result.docs,
            next_state.get("retrieved_docs", []),
            max_items=runtime.settings.planner_memory_top_k,
            snippet_limit=runtime.settings.planner_memory_max_snippet_chars,
        )
        next_state["retrieved_plan_refs"] = _merge_refs(
            result.plan_refs,
            next_state.get("retrieved_plan_refs", []),
            max_items=runtime.settings.planner_memory_top_k,
            snippet_limit=runtime.settings.planner_memory_max_snippet_chars,
        )
        next_state["updated_at"] = utc_now()
        return next_state

    return retrieve_memory
