"""CLI for evaluating planner outputs against saved fixtures.

This script runs the planner's generation path against fixture cases and scores
the results against stored expectations. It connects prompt iteration to a
repeatable quality loop instead of one-off manual inspection.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from planner_graph.clients.openai import OpenAIPlannerClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run saved planner eval fixtures against the OpenAI planner path."
    )
    parser.add_argument(
        "fixtures",
        nargs="+",
        help="Paths to eval fixture JSON files. Each fixture must include `request` and `expectations`.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the eval summary JSON.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional model override for the eval run.",
    )
    return parser.parse_args()


def load_fixture(path: str) -> dict[str, object]:
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return cast(dict[str, object], raw)


def build_state_from_request(request_payload: dict[str, object]) -> dict[str, object]:
    trigger = cast(dict[str, object], request_payload["trigger"])
    planner = cast(dict[str, object], request_payload["planner"])
    context = cast(dict[str, object], request_payload["context"])
    return {
        "trigger_id": trigger["trigger_id"],
        "greenhouse_id": trigger["greenhouse_id"],
        "event_type": trigger["event_type"],
        "event_label": trigger.get("event_label") or trigger["event_type"],
        "expected_action": trigger.get("expected_action", "any"),
        "triggered_at": trigger["triggered_at"],
        "planner_instance": trigger.get("planner_instance", "planner_graph"),
        "request_id": planner.get("request_id", ""),
        "trace_id": planner.get("trace_id", ""),
        "contract_version": planner["contract_version"],
        "context_version": planner["context_version"],
        "climate_snapshot": context["climate_snapshot"],
        "scorecard_summary": context["scorecard_summary"],
        "forecast_summary": context["forecast_summary"],
        "active_plan_summary": context["active_plan_summary"],
        "alerts_summary": context["alerts_summary"],
        "clamp_summary": context["clamp_summary"],
        "guardrail_audit_summary": context["guardrail_audit_summary"],
        "recent_delivery_summary": context.get("recent_delivery_summary", {}),
        "operator_notes": context.get("operator_notes", []),
        "retrieved_lessons": context.get("retrieval_refs", []),
        "retrieved_docs": context.get("site_refs", []),
    }


def dotted_get(data: object, path: str) -> object:
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def is_missing_or_empty(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def expectation_failures(
    diagnosis: dict[str, object],
    draft: dict[str, object],
    expectations: dict[str, object],
) -> list[str]:
    failures: list[str] = []

    expected_action = expectations.get("selected_action")
    actual_action = draft.get("selected_action")
    if isinstance(expected_action, str) and actual_action != expected_action:
        failures.append(
            f"selected_action expected {expected_action!r} but got {actual_action!r}"
        )
    expected_actions = expectations.get("selected_action_in")
    if isinstance(expected_actions, list):
        valid_actions = [
            action for action in expected_actions if isinstance(action, str)
        ]
        if valid_actions and actual_action not in valid_actions:
            failures.append(
                f"selected_action expected one of {valid_actions!r} but got {actual_action!r}"
            )

    min_confidence = expectations.get("min_confidence")
    if min_confidence is not None:
        confidence = float(cast(float | int | str, draft.get("confidence", 0)))
        if confidence < float(cast(float | int | str, min_confidence)):
            failures.append(
                f"confidence expected >= {min_confidence} but got {confidence}"
            )
    max_confidence = expectations.get("max_confidence")
    if max_confidence is not None:
        confidence = float(cast(float | int | str, draft.get("confidence", 0)))
        if confidence > float(cast(float | int | str, max_confidence)):
            failures.append(
                f"confidence expected <= {max_confidence} but got {confidence}"
            )

    rationale_contains = expectations.get("rationale_contains", [])
    rationale = str(draft.get("rationale", ""))
    if isinstance(rationale_contains, list):
        for phrase in rationale_contains:
            if isinstance(phrase, str) and phrase.lower() not in rationale.lower():
                failures.append(f"rationale missing phrase {phrase!r}")
    rationale_not_contains = expectations.get("rationale_not_contains", [])
    if isinstance(rationale_not_contains, list):
        for phrase in rationale_not_contains:
            if isinstance(phrase, str) and phrase.lower() in rationale.lower():
                failures.append(f"rationale should not include phrase {phrase!r}")

    diagnosis_contains = expectations.get("diagnosis_contains", [])
    diagnosis_blob = json.dumps(diagnosis, sort_keys=True).lower()
    if isinstance(diagnosis_contains, list):
        for phrase in diagnosis_contains:
            if isinstance(phrase, str) and phrase.lower() not in diagnosis_blob:
                failures.append(f"diagnosis missing phrase {phrase!r}")
    diagnosis_not_contains = expectations.get("diagnosis_not_contains", [])
    if isinstance(diagnosis_not_contains, list):
        for phrase in diagnosis_not_contains:
            if isinstance(phrase, str) and phrase.lower() in diagnosis_blob:
                failures.append(f"diagnosis should not include phrase {phrase!r}")

    payload_fields = expectations.get("payload_fields", {})
    if isinstance(payload_fields, dict):
        for path, expected_value in payload_fields.items():
            if not isinstance(path, str):
                continue
            actual_value = dotted_get(draft, path)
            if actual_value != expected_value:
                failures.append(
                    f"{path} expected {expected_value!r} but got {actual_value!r}"
                )

    required_payload_fields = expectations.get("required_payload_fields", [])
    if isinstance(required_payload_fields, list):
        for path in required_payload_fields:
            if not isinstance(path, str):
                continue
            actual_value = dotted_get(draft, path)
            if is_missing_or_empty(actual_value):
                failures.append(f"{path} is required but missing or empty")

    forbidden_payload_fields = expectations.get("forbidden_payload_fields", [])
    if isinstance(forbidden_payload_fields, list):
        for path in forbidden_payload_fields:
            if not isinstance(path, str):
                continue
            actual_value = dotted_get(draft, path)
            if not is_missing_or_empty(actual_value):
                failures.append(
                    f"{path} should be absent or empty but was {actual_value!r}"
                )

    empty_payload_fields = expectations.get("empty_payload_fields", [])
    if isinstance(empty_payload_fields, list):
        for path in empty_payload_fields:
            if not isinstance(path, str):
                continue
            actual_value = dotted_get(draft, path)
            if not is_missing_or_empty(actual_value):
                failures.append(f"{path} expected to be empty but was {actual_value!r}")

    return failures


def score_fixture(
    client: OpenAIPlannerClient,
    fixture_name: str,
    fixture: dict[str, object],
) -> dict[str, object]:
    request_payload = cast(dict[str, object], fixture["request"])
    expectations = cast(dict[str, object], fixture["expectations"])
    state = build_state_from_request(request_payload)
    diagnosis = client.diagnose(state)
    state["diagnosis"] = diagnosis
    draft = client.draft_plan(state)

    actual_action = cast(str, draft["selected_action"])
    expected_action = cast(
        str,
        expectations.get("selected_action")
        or "/".join(cast(list[str], expectations.get("selected_action_in", []))),
    )
    rationale = cast(str, draft["rationale"])
    confidence = float(cast(float | int | str, draft["confidence"]))
    failures = expectation_failures(diagnosis, draft, expectations)
    passed = len(failures) == 0 and bool(rationale) and confidence > 0
    return {
        "fixture": fixture_name,
        "expected_action": expected_action,
        "actual_action": actual_action,
        "passed": passed,
        "failures": failures,
        "confidence": confidence,
        "rationale": rationale,
        "diagnosis": diagnosis,
        "draft": draft,
    }


def main() -> int:
    args = parse_args()
    client = OpenAIPlannerClient()
    if args.model is not None:
        client.model = args.model

    results: list[dict[str, object]] = []
    for path in args.fixtures:
        fixture = load_fixture(path)
        results.append(score_fixture(client, Path(path).name, fixture))

    summary = {
        "passed": sum(1 for result in results if result["passed"]),
        "failed": sum(1 for result in results if not result["passed"]),
        "results": results,
    }
    rendered = json.dumps(summary, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
