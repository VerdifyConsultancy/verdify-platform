"""Shadow-mode Verdify caller for the remote planner_graph service.

This module is intentionally side-effect free unless explicitly enabled with
``PLANNER_GRAPH_SHADOW_ENABLED=1``. It submits bounded planner requests to the
remote planner service, polls for a terminal proposal, compares that proposal
against Verdify's local planner result, and stores the comparison in
``plan_delivery_log_shadow``. It never executes the remote proposal.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import asyncpg
from pydantic import ValidationError

from verdify_schemas.plan import Plan
from verdify_schemas.tunable_registry import (
    BAND_OWNED_REG,
    PLANNER_PUSHABLE_REG,
    REGISTRY,
    TIER1_REG,
    registry_value_error,
)

try:
    from config import DB_DSN, GREENHOUSE_ID
except Exception:  # pragma: no cover - import path differs in isolated tests
    DB_DSN = os.environ.get("DB_DSN", "postgresql://verdify@localhost:5432/verdify")
    GREENHOUSE_ID = os.environ.get("GREENHOUSE_ID", "vallery")

log = logging.getLogger("planner_graph_shadow")

DENVER = ZoneInfo("America/Denver")
CONTRACT_VERSION = "2026-05-19"
CONTEXT_VERSION = "verdify-context-v1"
DEFAULT_EVENT_TYPES = "SUNRISE,SUNSET,MIDNIGHT,SOLAR_MAX,TRANSITION,FORECAST_DEVIATION,MANUAL"
TERMINAL_REMOTE_STATUSES = {"completed", "failed"}
MAX_SECTION_CHARS = 4000
MAX_MEMORY_BODY_CHARS = 4000
MAX_MEMORY_SUMMARY_CHARS = 800
MAX_MEMORY_TITLE_CHARS = 160


class PlannerGraphShadowError(RuntimeError):
    """Raised for local shadow-run setup and remote planner call failures."""


@dataclass(frozen=True)
class AuthHeaders:
    headers: dict[str, str]
    mode: str
    source: str
    audience: str | None = None


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: dict[str, Any] | str
    elapsed_ms: int


@dataclass(frozen=True)
class PlannerMemoryItem:
    memory_type: str
    source_type: str
    source_id: str
    title: str
    summary: str
    body: str
    trust_level: str
    trigger_id: str | None = None
    event_type: str | None = None
    tags: tuple[str, ...] = ()
    importance: int = 3
    confidence: float | None = None
    payload: dict[str, Any] | None = None
    valid_from: str | None = None
    expires_at: str | None = None


def shadow_enabled() -> bool:
    return os.getenv("PLANNER_GRAPH_SHADOW_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def eligible_event(event_type: str) -> bool:
    raw = os.getenv("PLANNER_GRAPH_SHADOW_EVENT_TYPES", DEFAULT_EVENT_TYPES)
    allowed = {part.strip().upper() for part in raw.split(",") if part.strip()}
    return "*" in allowed or event_type.upper() in allowed


def planner_graph_base_url() -> str:
    url = os.getenv("PLANNER_GRAPH_URL", "").strip().rstrip("/")
    if not url:
        raise PlannerGraphShadowError("PLANNER_GRAPH_URL is required when planner_graph shadow is enabled")
    return url


def planner_memory_ingest_enabled() -> bool:
    return os.getenv("PLANNER_MEMORY_INGEST_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def planner_memory_ingest_outcomes_enabled() -> bool:
    return os.getenv("PLANNER_MEMORY_INGEST_OUTCOMES_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def planner_memory_ingest_prior_plans_enabled() -> bool:
    return os.getenv("PLANNER_MEMORY_INGEST_PRIOR_PLANS_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def planner_memory_ingest_support_docs_enabled() -> bool:
    return os.getenv("PLANNER_MEMORY_INGEST_SUPPORT_DOCS_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def planner_memory_ingest_max_batch_items() -> int:
    raw = os.getenv("PLANNER_MEMORY_INGEST_MAX_BATCH_ITEMS", "10").strip()
    try:
        return max(1, min(int(raw), 100))
    except ValueError:
        return 10


def planner_memory_support_docs_file() -> Path | None:
    raw = os.getenv("PLANNER_MEMORY_SUPPORT_DOCS_FILE", "").strip()
    return Path(raw) if raw else None


def planner_memory_state_file() -> Path:
    raw = os.getenv("PLANNER_MEMORY_INGEST_STATE_FILE", "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent.parent / "state" / "planner-memory-ingest-state.json"


def expected_action_for_event(event_type: str) -> str:
    if event_type in {"SUNRISE", "SUNSET", "MIDNIGHT"}:
        return "set_plan"
    if event_type in {"HEARTBEAT", "FORECAST"}:
        return "acknowledge_trigger"
    return "any"


def _section_title(line: str) -> str | None:
    stripped = line.strip()
    m = re.fullmatch(r"-{3,}\s*(.*?)\s*-{3,}", stripped)
    if m:
        return m.group(1).strip()
    m = re.fullmatch(r"={3,}\s*(.*?)\s*={3,}", stripped)
    if m:
        return m.group(1).strip()
    return None


def split_context_sections(context: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = "preamble"
    sections[current] = []
    for line in (context or "").splitlines():
        title = _section_title(line)
        if title:
            current = title
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return {title: "\n".join(lines).strip()[:MAX_SECTION_CHARS] for title, lines in sections.items()}


def _first_matching_section(sections: dict[str, str], *needles: str) -> str:
    for title, body in sections.items():
        haystack = title.upper()
        if any(needle.upper() in haystack for needle in needles):
            return body
    return ""


def _dict_summary(text: str, fallback: str) -> dict[str, str]:
    return {"summary": (text or fallback)[:MAX_SECTION_CHARS]}


def _list_summary(text: str, fallback: str) -> list[str]:
    body = text or fallback
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return lines[:20] or [fallback]


def _request_id(trigger_id: str) -> str:
    return f"planner-graph-shadow-{trigger_id}"


def _truncate(text: str, limit: int) -> str:
    stripped = " ".join((text or "").split()).strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: max(limit - 1, 0)].rstrip() + "…"


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def build_memory_ingest_request(
    *,
    greenhouse_id: str,
    batch_id: str,
    items: list[PlannerMemoryItem],
) -> dict[str, Any]:
    return {
        "greenhouse_id": greenhouse_id,
        "source_system": "verdify",
        "batch_id": batch_id,
        "items": [
            {
                "memory_type": item.memory_type,
                "source_type": item.source_type,
                "source_id": item.source_id,
                "trigger_id": item.trigger_id,
                "event_type": item.event_type,
                "title": item.title,
                "summary": item.summary,
                "body": item.body,
                "tags": list(item.tags),
                "importance": item.importance,
                "confidence": item.confidence,
                "trust_level": item.trust_level,
                "payload": item.payload or {},
                "valid_from": item.valid_from,
                "expires_at": item.expires_at,
            }
            for item in items
        ],
    }


def build_outcome_memory_item(row: dict[str, Any]) -> PlannerMemoryItem:
    plan_id = str(row.get("plan_id") or "unknown-plan")
    trigger_id = str(row["trigger_id"]) if row.get("trigger_id") else None
    event_type = str(row.get("event_type") or "UNKNOWN")
    score = row.get("outcome_score")
    actual_outcome = str(row.get("actual_outcome") or "").strip()
    expected_outcome = str(row.get("expected_outcome") or "").strip()
    lesson = str(row.get("lesson_extracted") or "").strip()
    validated_at = _iso_or_none(row.get("validated_at"))
    summary_parts = [
        f"Plan {plan_id} validated for {event_type}.",
        f"Outcome score {score}/10." if score is not None else "Outcome score unavailable.",
    ]
    if actual_outcome:
        summary_parts.append(_truncate(actual_outcome, 220))
    body_parts = [
        f"Observed outcome for Verdify plan {plan_id}.",
        f"Event type: {event_type}.",
    ]
    if expected_outcome:
        body_parts.append(f"Expected outcome: {expected_outcome}")
    if actual_outcome:
        body_parts.append(f"Actual outcome: {actual_outcome}")
    if lesson:
        body_parts.append(f"Lesson extracted: {lesson}")
    return PlannerMemoryItem(
        memory_type="observed_outcome",
        source_type="verdify_outcome",
        source_id=f"plan-eval:{plan_id}",
        trigger_id=trigger_id,
        event_type=event_type,
        title=_truncate(f"Observed outcome for {plan_id}", MAX_MEMORY_TITLE_CHARS),
        summary=_truncate(" ".join(summary_parts), MAX_MEMORY_SUMMARY_CHARS),
        body=_truncate("\n".join(body_parts), MAX_MEMORY_BODY_CHARS),
        tags=("observed-outcome", event_type.lower(), "verdify"),
        importance=4 if score is not None and float(score) >= 8 else 3,
        confidence=1.0,
        trust_level="observed_outcome",
        payload={
            "plan_id": plan_id,
            "outcome_score": score,
            "anchor_score": row.get("anchor_score"),
            "planner_instance": row.get("planner_instance"),
            "validated_at": validated_at,
        },
        valid_from=validated_at,
    )


def build_prior_plan_memory_item(row: dict[str, Any]) -> PlannerMemoryItem:
    plan_id = str(row.get("plan_id") or "unknown-plan")
    trigger_id = str(row["trigger_id"]) if row.get("trigger_id") else None
    event_type = str(row.get("event_type") or "UNKNOWN")
    hypothesis = str(row.get("hypothesis") or "").strip()
    expected_outcome = str(row.get("expected_outcome") or "").strip()
    created_at = _iso_or_none(row.get("created_at"))
    score = row.get("outcome_score")
    summary = f"Prior {event_type} plan {plan_id}."
    if score is not None:
        summary += f" Outcome score {score}/10."
    return PlannerMemoryItem(
        memory_type="prior_plan",
        source_type="verdify_plan_summary",
        source_id=f"plan-summary:{plan_id}",
        trigger_id=trigger_id,
        event_type=event_type,
        title=_truncate(f"Prior plan summary for {plan_id}", MAX_MEMORY_TITLE_CHARS),
        summary=_truncate(summary, MAX_MEMORY_SUMMARY_CHARS),
        body=_truncate(
            "\n".join(
                part
                for part in (
                    f"Prior Verdify plan summary for {plan_id}.",
                    f"Hypothesis: {hypothesis}" if hypothesis else "",
                    f"Expected outcome: {expected_outcome}" if expected_outcome else "",
                )
                if part
            ),
            MAX_MEMORY_BODY_CHARS,
        ),
        tags=("prior-plan", event_type.lower(), "verdify"),
        importance=2,
        confidence=0.85,
        trust_level="verdify_context",
        payload={
            "plan_id": plan_id,
            "planner_instance": row.get("planner_instance"),
            "created_at": created_at,
            "outcome_score": score,
        },
        valid_from=created_at,
    )


def load_support_doc_items(path: Path, *, greenhouse_id: str) -> list[PlannerMemoryItem]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise PlannerGraphShadowError(f"support docs file must contain a list: {path}")
    items: list[PlannerMemoryItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        source_id = str(entry.get("source_id") or "").strip()
        title = str(entry.get("title") or "").strip()
        summary = str(entry.get("summary") or "").strip()
        body = str(entry.get("body") or "").strip()
        if not source_id or not title or not summary or not body:
            continue
        tags = tuple(str(tag).strip() for tag in entry.get("tags", []) if str(tag).strip())
        items.append(
            PlannerMemoryItem(
                memory_type="support_doc",
                source_type=str(entry.get("source_type") or "verdify_doc").strip(),
                source_id=source_id,
                event_type=str(entry.get("event_type") or "").strip() or None,
                title=_truncate(title, MAX_MEMORY_TITLE_CHARS),
                summary=_truncate(summary, MAX_MEMORY_SUMMARY_CHARS),
                body=_truncate(body, MAX_MEMORY_BODY_CHARS),
                tags=tags,
                importance=int(entry.get("importance") or 3),
                confidence=float(entry["confidence"]) if entry.get("confidence") is not None else 0.85,
                trust_level="verdify_context",
                payload=entry.get("payload") if isinstance(entry.get("payload"), dict) else {},
                valid_from=_iso_or_none(entry.get("valid_from")),
                expires_at=_iso_or_none(entry.get("expires_at")),
            )
        )
    return items


def load_memory_ingest_state() -> dict[str, Any]:
    path = planner_memory_state_file()
    try:
        if path.exists():
            raw = json.loads(path.read_text())
            if isinstance(raw, dict):
                return raw
    except Exception as exc:
        log.warning("planner memory ingest state read failed: %s", exc)
    return {"last_validated_at": None, "seeded_support_doc_ids": []}


def save_memory_ingest_state(state: dict[str, Any]) -> None:
    path = planner_memory_state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


def build_planner_request(
    *,
    event_type: str,
    event_label: str,
    context: str,
    trigger_id: str,
    planner_instance: str,
    active_plan_summary: dict[str, float | int | str | bool] | None = None,
) -> dict[str, Any]:
    """Build a bounded planner_graph request from Verdify's existing context pack."""

    normalized_trigger_id = str(uuid.UUID(trigger_id))
    sections = split_context_sections(context)
    climate_text = "\n\n".join(
        part
        for part in (
            _first_matching_section(sections, "SYSTEM HEALTH"),
            _first_matching_section(sections, "ZONE CONDITIONS"),
            _first_matching_section(sections, "COMPLIANCE"),
        )
        if part
    )
    forecast_text = "\n\n".join(
        part
        for part in (
            _first_matching_section(sections, "FORECAST ALERTS"),
            _first_matching_section(sections, "72H HOURLY FORECAST"),
            _first_matching_section(sections, "FORECAST CALIBRATION"),
            _first_matching_section(sections, "FORECAST BIAS"),
        )
        if part
    )
    return {
        "trigger": {
            "trigger_id": normalized_trigger_id,
            "greenhouse_id": GREENHOUSE_ID,
            "event_type": event_type,
            "event_label": event_label,
            "expected_action": expected_action_for_event(event_type),
            "triggered_at": datetime.now(DENVER).isoformat(),
            "planner_instance": planner_instance,
            "source": "verdify-hermes-shadow-sidecar",
        },
        "planner": {
            "run_mode": "shadow",
            "contract_version": CONTRACT_VERSION,
            "context_version": CONTEXT_VERSION,
            "request_id": _request_id(normalized_trigger_id),
            "trace_id": f"verdify:{normalized_trigger_id}",
            "compare_against": "verdify-hermes-current",
        },
        "context": {
            "climate_snapshot": _dict_summary(climate_text, "No climate summary available in gathered context."),
            "scorecard_summary": _dict_summary(
                _first_matching_section(sections, "PLANNER SCORECARD"),
                "No scorecard summary available in gathered context.",
            ),
            "forecast_summary": _dict_summary(forecast_text, "No forecast summary available in gathered context."),
            # planner_graph currently materializes set_plan.params from this
            # section, so keep it execution-shaped instead of prose-shaped.
            "active_plan_summary": active_plan_summary
            or _dict_summary(
                _first_matching_section(sections, "ACTIVE PLAN"),
                "No active plan summary available in gathered context.",
            ),
            "alerts_summary": _list_summary(
                _first_matching_section(sections, "FORECAST ALERTS"),
                "No forecast alerts found in gathered context.",
            ),
            "clamp_summary": _dict_summary(
                _first_matching_section(sections, "RECENT CLAMPS"),
                "No clamp summary available in gathered context.",
            ),
            "guardrail_audit_summary": _dict_summary(
                _first_matching_section(sections, "GUARDRAIL-AWARE TRANSITION AUDIT"),
                "No guardrail audit summary available in gathered context.",
            ),
            "retrieval_refs": [
                {
                    "id": "relevant-lessons",
                    "snippet": _first_matching_section(sections, "RELEVANT LESSONS")[:1000]
                    or "No lesson retrieval summary available.",
                }
            ],
            "recent_delivery_summary": _dict_summary(
                _first_matching_section(sections, "YOUR RECENT DELIVERIES"),
                "No recent delivery summary available in gathered context.",
            ),
            "operator_notes": [],
            "site_refs": [
                {
                    "id": "greenhouse-playbook",
                    "snippet": "Verdify greenhouse playbook and MCP contract remain the execution boundary.",
                }
            ],
        },
    }


def _audience_for(base_url: str) -> str:
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else base_url.rstrip("/")


def _metadata_identity_token(audience: str, timeout: float = 2.0) -> str | None:
    url = (
        "http://metadata/computeMetadata/v1/instance/service-accounts/default/identity"
        f"?audience={urllib.request.quote(audience, safe='')}&format=full"
    )
    req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8").strip()
    except Exception:
        return None


def _service_account_identity_token(credentials_file: str, audience: str) -> str | None:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        credentials = service_account.IDTokenCredentials.from_service_account_file(
            credentials_file,
            target_audience=audience,
        )
        credentials.refresh(Request())
        return credentials.token
    except Exception as exc:
        log.warning("planner_graph service-account ID token failed: %s", exc)
        return None


def _adc_identity_token(audience: str) -> str | None:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token

        return id_token.fetch_id_token(Request(), audience)
    except Exception:
        return None


def _gcloud_base_command() -> list[str]:
    command = ["gcloud"]
    if configuration := os.getenv("PLANNER_GRAPH_GCLOUD_CONFIGURATION", "").strip():
        command.append(f"--configuration={configuration}")
    return command


def _gcloud_identity_token(audience: str) -> str | None:
    impersonate = os.getenv("PLANNER_GRAPH_IMPERSONATE_SERVICE_ACCOUNT", "").strip()
    audience_command = _gcloud_base_command() + ["auth", "print-identity-token", f"--audiences={audience}"]
    if impersonate:
        audience_command.append(f"--impersonate-service-account={impersonate}")
    fallback_command = _gcloud_base_command() + ["auth", "print-identity-token"]
    if impersonate:
        fallback_command.append(f"--impersonate-service-account={impersonate}")
    commands = [audience_command, fallback_command]
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
        except Exception as exc:
            log.debug("planner_graph gcloud identity-token command failed before completion: %s", exc)
            continue
        token = proc.stdout.strip()
        if proc.returncode == 0 and token:
            return token
    return None


def build_auth_headers(base_url: str) -> AuthHeaders:
    """Resolve planner auth without ever logging token material."""

    audience = os.getenv("PLANNER_GRAPH_AUDIENCE", "").strip() or _audience_for(base_url)
    mode = os.getenv("PLANNER_GRAPH_AUTH_MODE", "auto").strip().lower()
    if mode in {"none", "disabled", "off"}:
        return AuthHeaders(headers={}, mode=mode, source="none", audience=audience)

    direct = os.getenv("PLANNER_GRAPH_ID_TOKEN", "").strip()
    if direct:
        return AuthHeaders({"Authorization": f"Bearer {direct}"}, mode=mode, source="direct_env", audience=audience)

    token_file = os.getenv("PLANNER_GRAPH_BEARER_TOKEN_FILE", "").strip()
    if token_file:
        token = Path(token_file).read_text().strip()
        return AuthHeaders({"Authorization": f"Bearer {token}"}, mode=mode, source="bearer_file", audience=audience)

    if mode in {"bearer", "token"}:
        raise PlannerGraphShadowError("PLANNER_GRAPH_ID_TOKEN or PLANNER_GRAPH_BEARER_TOKEN_FILE is required")

    credentials_file = (
        os.getenv("PLANNER_GRAPH_GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    )
    if credentials_file:
        token = _service_account_identity_token(credentials_file, audience)
        if token:
            return AuthHeaders(
                {"Authorization": f"Bearer {token}"},
                mode=mode,
                source="service_account_file",
                audience=audience,
            )

    token = _metadata_identity_token(audience)
    if token:
        return AuthHeaders({"Authorization": f"Bearer {token}"}, mode=mode, source="metadata", audience=audience)

    token = _adc_identity_token(audience)
    if token:
        return AuthHeaders({"Authorization": f"Bearer {token}"}, mode=mode, source="adc", audience=audience)

    allow_gcloud = os.getenv("PLANNER_GRAPH_ALLOW_GCLOUD_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}
    if mode == "google_oidc" or allow_gcloud:
        token = _gcloud_identity_token(audience)
        if token:
            return AuthHeaders({"Authorization": f"Bearer {token}"}, mode=mode, source="gcloud", audience=audience)

    if mode == "auto":
        return AuthHeaders(headers={}, mode=mode, source="none", audience=audience)
    raise PlannerGraphShadowError(f"could not resolve planner_graph auth for mode={mode!r}")


def _decode_json(raw: str) -> dict[str, Any] | str:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    return parsed if isinstance(parsed, dict) else raw


def _request_json(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    timeout: float,
) -> HttpResult:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {"Content-Type": "application/json", **headers}
    req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return HttpResult(resp.getcode(), _decode_json(raw), int((time.monotonic() - started) * 1000))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return HttpResult(exc.code, _decode_json(raw), int((time.monotonic() - started) * 1000))


def submit_planner_run(base_url: str, payload: dict[str, Any], auth: AuthHeaders, timeout: float = 10.0) -> HttpResult:
    return _request_json(
        method="POST",
        url=f"{base_url.rstrip('/')}/planner-runs",
        headers=auth.headers,
        payload=payload,
        timeout=timeout,
    )


def submit_planner_memory_ingest(
    base_url: str,
    payload: dict[str, Any],
    auth: AuthHeaders,
    timeout: float = 10.0,
) -> HttpResult:
    return _request_json(
        method="POST",
        url=f"{base_url.rstrip('/')}/planner-memory/ingest",
        headers=auth.headers,
        payload=payload,
        timeout=timeout,
    )


def get_planner_run(base_url: str, trigger_id: str, auth: AuthHeaders, timeout: float = 10.0) -> HttpResult:
    return _request_json(
        method="GET",
        url=f"{base_url.rstrip('/')}/planner-runs/{trigger_id}",
        headers=auth.headers,
        timeout=timeout,
    )


def poll_planner_run(
    base_url: str,
    trigger_id: str,
    auth: AuthHeaders,
    *,
    poll_interval_s: float,
    poll_timeout_s: float,
    request_timeout_s: float,
) -> tuple[HttpResult, int]:
    deadline = time.monotonic() + poll_timeout_s
    polls = 0
    last: HttpResult | None = None
    while time.monotonic() <= deadline:
        polls += 1
        last = get_planner_run(base_url, trigger_id, auth, timeout=request_timeout_s)
        if last.status >= 400:
            return last, polls
        if isinstance(last.body, dict) and str(last.body.get("status", "")) in TERMINAL_REMOTE_STATUSES:
            return last, polls
        time.sleep(poll_interval_s)
    if last is not None:
        return last, polls
    raise PlannerGraphShadowError(f"planner_graph run {trigger_id} did not produce a poll result")


def _remote_action(remote_terminal: dict[str, Any] | str | None) -> dict[str, Any]:
    if not isinstance(remote_terminal, dict):
        return {"action_type": "unknown", "payload": {}, "rationale": ""}
    action = remote_terminal.get("primary_action")
    return action if isinstance(action, dict) else {"action_type": "unknown", "payload": {}, "rationale": ""}


def _payload_targets(action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if action_type == "set_tunable":
        return {
            "parameters": [payload.get("parameter")],
            "values": {str(payload.get("parameter")): payload.get("value")},
        }
    if action_type == "set_plan":
        transitions = payload.get("transitions")
        if isinstance(transitions, str):
            try:
                transitions = json.loads(transitions)
            except json.JSONDecodeError:
                transitions = []
        params: set[str] = set()
        if isinstance(transitions, list):
            for transition in transitions:
                if isinstance(transition, dict) and isinstance(transition.get("params"), dict):
                    params.update(str(key) for key in transition["params"])
        return {"plan_id": payload.get("plan_id"), "parameters": sorted(params)}
    return {}


def validate_remote_action(action: dict[str, Any], expected_trigger_id: str) -> dict[str, Any]:
    action_type = str(action.get("action_type") or "unknown")
    payload = action.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    errors: list[str] = []
    payload_trigger_id = payload.get("trigger_id")
    if action_type in {"set_plan", "set_tunable", "acknowledge_trigger"} and not payload_trigger_id:
        errors.append("payload trigger_id is required")
    if payload_trigger_id and str(payload_trigger_id) != expected_trigger_id:
        errors.append("payload trigger_id does not match run trigger_id")

    if action_type == "set_plan":
        transitions = payload.get("transitions")
        if isinstance(transitions, str):
            try:
                transitions = json.loads(transitions)
            except json.JSONDecodeError as exc:
                errors.append(f"transitions is invalid JSON: {exc}")
                transitions = []
        try:
            plan = Plan.model_validate(
                {
                    "plan_id": payload.get("plan_id"),
                    "hypothesis": payload.get("hypothesis") or "",
                    "experiment": payload.get("experiment") or None,
                    "expected_outcome": payload.get("expected_outcome") or None,
                    "transitions": transitions,
                }
            )
        except ValidationError as exc:
            errors.append("Plan validation failed: " + "; ".join(err["msg"] for err in exc.errors()[:5]))
            plan = None
        if plan is not None:
            if not any(param not in BAND_OWNED_REG for wp in plan.transitions for param in wp.params):
                errors.append("Plan contains only dispatcher-owned policy params")
            missing = []
            for idx, wp in enumerate(plan.transitions):
                missing_params = sorted(TIER1_REG - set(wp.params))
                if missing_params:
                    missing.append({"transition_index": idx, "missing": missing_params})
            if missing:
                errors.append(f"Plan transitions missing required Tier 1 params: {missing[:3]}")
            non_policy = sorted(
                {
                    param
                    for wp in plan.transitions
                    for param in wp.params
                    if param not in BAND_OWNED_REG and param not in PLANNER_PUSHABLE_REG
                }
            )
            if non_policy:
                errors.append(f"Plan contains non-policy tunables: {non_policy}")
    elif action_type == "set_tunable":
        parameter = payload.get("parameter")
        value = payload.get("value")
        if parameter not in PLANNER_PUSHABLE_REG:
            errors.append(f"{parameter!r} is not planner-pushable")
        elif err := registry_value_error(str(parameter), value):
            errors.append(err)
        if not payload.get("reason"):
            errors.append("reason is required")
    elif action_type == "acknowledge_trigger":
        if not payload.get("reason"):
            errors.append("reason is required")
    elif action_type == "fail":
        errors.append("remote planner returned fail action")
    else:
        errors.append(f"unsupported action_type: {action_type}")

    return {"would_accept_remote": not errors, "rejection_reasons": errors}


def default_active_plan_summary() -> dict[str, float]:
    return {name: float(REGISTRY[name].default) for name in sorted(TIER1_REG)}


async def fetch_active_plan_summary(conn: asyncpg.Connection) -> dict[str, float]:
    """Return current Tier 1 planner params in the shape planner_graph echoes into set_plan."""

    values = default_active_plan_summary()
    try:
        rows = await conn.fetch(
            """
            SELECT parameter, value
              FROM v_active_plan
             WHERE parameter = ANY($1::text[])
            """,
            list(TIER1_REG),
        )
    except Exception as exc:
        log.warning("planner_graph shadow could not read v_active_plan; using registry defaults: %s", exc)
        return values
    for row in rows:
        values[row["parameter"]] = float(row["value"])
    return values


async def _fetch_set_plan_output(conn: asyncpg.Connection, trigger_id: str, plan_id: str | None) -> dict[str, Any]:
    if not plan_id:
        plan_id = await conn.fetchval(
            """
            SELECT plan_id
              FROM plan_journal
             WHERE trigger_id = $1::uuid
             ORDER BY created_at DESC
             LIMIT 1
            """,
            trigger_id,
        )
    if not plan_id:
        return {"action_type": "pending", "payload": {}, "reason": "plan_written status without plan_id"}

    journal = await conn.fetchrow(
        """
        SELECT plan_id, hypothesis, experiment, expected_outcome, created_at
          FROM plan_journal
         WHERE plan_id = $1
         LIMIT 1
        """,
        plan_id,
    )
    rows = await conn.fetch(
        """
        SELECT ts, parameter, value, reason
          FROM setpoint_plan
         WHERE plan_id = $1
           AND is_active = true
         ORDER BY ts, parameter
        """,
        plan_id,
    )
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        ts = row["ts"].isoformat()
        grouped.setdefault(ts, {"ts": ts, "params": {}, "reason": row["reason"]})
        grouped[ts]["params"][row["parameter"]] = float(row["value"])
    return {
        "action_type": "set_plan",
        "payload": {
            "plan_id": plan_id,
            "hypothesis": journal["hypothesis"] if journal else "",
            "experiment": journal["experiment"] if journal else None,
            "expected_outcome": journal["expected_outcome"] if journal else None,
            "transitions": list(grouped.values()),
        },
    }


async def fetch_local_output(
    conn: asyncpg.Connection,
    trigger_id: str,
    *,
    wait_timeout_s: float = 0.0,
    poll_interval_s: float = 5.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + wait_timeout_s
    while True:
        row = await conn.fetchrow(
            """
            SELECT id, event_type, status, gateway_body, resulting_plan_id, acked_at, plan_written_at
              FROM plan_delivery_log
             WHERE trigger_id = $1::uuid
             ORDER BY delivered_at DESC
             LIMIT 1
            """,
            trigger_id,
        )
        if row is None:
            return {"action_type": "pending", "payload": {}, "reason": "no production delivery row found"}
        status = row["status"]
        plan_id = row["resulting_plan_id"]
        if status == "plan_written" or plan_id:
            return await _fetch_set_plan_output(conn, trigger_id, plan_id)
        if status == "acked":
            return {"action_type": "acknowledge_trigger", "payload": {"reason": row["gateway_body"] or ""}}
        if status in {"timed_out", "delivery_failed"}:
            return {"action_type": "fail", "payload": {"status": status, "reason": row["gateway_body"] or ""}}
        if time.monotonic() >= deadline:
            return {"action_type": "pending", "payload": {"status": status}, "reason": "local planner not terminal yet"}
        await asyncio.sleep(poll_interval_s)


def compare_outputs(
    *,
    remote_terminal: dict[str, Any] | str | None,
    local_output: dict[str, Any],
    validation: dict[str, Any],
) -> dict[str, Any]:
    remote = _remote_action(remote_terminal)
    remote_action = str(remote.get("action_type") or "unknown")
    remote_payload = remote.get("payload") if isinstance(remote.get("payload"), dict) else {}
    local_action = str(local_output.get("action_type") or "unknown")
    local_payload = local_output.get("payload") if isinstance(local_output.get("payload"), dict) else {}
    remote_targets = _payload_targets(remote_action, remote_payload)
    local_targets = _payload_targets(local_action, local_payload)
    same_action = remote_action == local_action
    material_difference = (not same_action) or remote_targets != local_targets
    if same_action and not material_difference and validation["would_accept_remote"]:
        judgement = "same"
    elif not validation["would_accept_remote"]:
        judgement = "worse"
    else:
        judgement = "unclear"
    return {
        "remote_action_type": remote_action,
        "local_action_type": local_action,
        "same_action_type": same_action,
        "remote_targets": remote_targets,
        "local_targets": local_targets,
        "material_payload_difference": material_difference,
        "would_accept_remote": validation["would_accept_remote"],
        "validation_rejection_reasons": validation["rejection_reasons"],
        "judgement": judgement,
    }


def build_shadow_record(
    *,
    event_label: str,
    trigger_id: str,
    submit: HttpResult | None,
    remote_terminal: HttpResult | None,
    auth: AuthHeaders,
    local_output: dict[str, Any],
    diff: dict[str, Any],
    request_payload: dict[str, Any],
    started_at: datetime,
    error: str | None = None,
    poll_count: int = 0,
) -> dict[str, Any]:
    terminal_body = remote_terminal.body if remote_terminal else None
    remote_status = terminal_body.get("status") if isinstance(terminal_body, dict) else None
    body = {
        "trigger_id": trigger_id,
        "request_metadata": {
            "contract_version": CONTRACT_VERSION,
            "context_version": CONTEXT_VERSION,
            "request_id": request_payload.get("planner", {}).get("request_id"),
            "trace_id": request_payload.get("planner", {}).get("trace_id"),
            "auth_mode": auth.mode,
            "auth_source": auth.source,
            "auth_audience": auth.audience,
        },
        "remote_accepted": submit.body if submit else None,
        "remote_terminal_status": terminal_body,
        "remote_primary_action": _remote_action(terminal_body),
        "local_planner_output": local_output,
        "diff_summary": diff,
        "validation_outcome": {
            "would_accept_remote": diff.get("would_accept_remote"),
            "rejection_reasons": diff.get("validation_rejection_reasons", []),
        },
        "timestamps": {
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(DENVER).isoformat(),
        },
        "latency": {
            "submit_elapsed_ms": submit.elapsed_ms if submit else None,
            "terminal_elapsed_ms": remote_terminal.elapsed_ms if remote_terminal else None,
            "poll_count": poll_count,
        },
        "error": error,
    }
    status = 200 if remote_status == "completed" else (remote_terminal.status if remote_terminal else 0)
    return {
        "event_type": "PLANNER_GRAPH_SHADOW",
        "event_label": event_label,
        "session_key": f"planner_graph:trigger:{trigger_id}",
        "gateway_status": status,
        "gateway_body": json.dumps(body, sort_keys=True, default=str),
        "trigger_id": trigger_id,
        "instance": "planner_graph",
    }


async def persist_shadow_record(conn: asyncpg.Connection, record: dict[str, Any]) -> int:
    matched_id = await conn.fetchval(
        "SELECT id FROM plan_delivery_log WHERE trigger_id = $1::uuid ORDER BY delivered_at DESC LIMIT 1",
        record["trigger_id"],
    )
    return await conn.fetchval(
        """
        INSERT INTO plan_delivery_log_shadow
          (event_type, event_label, session_key, gateway_status, gateway_body,
           trigger_id, instance, matched_prod_delivery_log_id)
        VALUES ($1, $2, $3, $4, $5, $6::uuid, $7, $8)
        RETURNING id
        """,
        record["event_type"],
        record.get("event_label"),
        record.get("session_key"),
        record.get("gateway_status"),
        record.get("gateway_body"),
        record.get("trigger_id"),
        record.get("instance"),
        matched_id,
    )


async def run_planner_graph_shadow(
    *,
    event_type: str,
    event_label: str,
    context: str,
    trigger_id: str,
    planner_instance: str,
    base_url: str | None = None,
    persist: bool = True,
    local_wait_timeout_s: float | None = None,
) -> dict[str, Any]:
    normalized_trigger_id = str(uuid.UUID(trigger_id))
    started_at = datetime.now(DENVER)
    base_url = (base_url or planner_graph_base_url()).rstrip("/")
    request_timeout = float(os.getenv("PLANNER_GRAPH_REQUEST_TIMEOUT_S", "10"))
    poll_interval = float(os.getenv("PLANNER_GRAPH_POLL_INTERVAL_S", "2"))
    poll_timeout = float(os.getenv("PLANNER_GRAPH_POLL_TIMEOUT_S", "120"))
    if local_wait_timeout_s is None:
        local_wait_timeout_s = float(os.getenv("PLANNER_GRAPH_LOCAL_WAIT_TIMEOUT_S", "0"))

    auth = build_auth_headers(base_url)
    active_plan_summary = default_active_plan_summary()
    try:
        conn = await asyncpg.connect(DB_DSN)
        try:
            active_plan_summary = await fetch_active_plan_summary(conn)
        finally:
            await conn.close()
    except Exception as exc:
        log.warning("planner_graph shadow using default Tier 1 params; active plan lookup failed: %s", exc)
    payload = build_planner_request(
        event_type=event_type,
        event_label=event_label,
        context=context,
        trigger_id=normalized_trigger_id,
        planner_instance=planner_instance,
        active_plan_summary=active_plan_summary,
    )

    submit: HttpResult | None = None
    terminal: HttpResult | None = None
    local_output: dict[str, Any] = {"action_type": "pending", "payload": {}}
    diff: dict[str, Any] = {
        "remote_action_type": "unknown",
        "local_action_type": "pending",
        "same_action_type": False,
        "would_accept_remote": False,
        "validation_rejection_reasons": ["shadow run did not complete"],
        "judgement": "worse",
    }
    error: str | None = None
    poll_count = 0

    try:
        submit = submit_planner_run(base_url, payload, auth, timeout=request_timeout)
        if submit.status >= 400:
            raise PlannerGraphShadowError(f"submit failed: HTTP {submit.status}: {submit.body}")
        terminal, poll_count = poll_planner_run(
            base_url,
            normalized_trigger_id,
            auth,
            poll_interval_s=poll_interval,
            poll_timeout_s=poll_timeout,
            request_timeout_s=request_timeout,
        )
        if terminal.status >= 400:
            raise PlannerGraphShadowError(f"poll failed: HTTP {terminal.status}: {terminal.body}")
        remote_action = _remote_action(terminal.body)
        validation = validate_remote_action(remote_action, normalized_trigger_id)
        conn = await asyncpg.connect(DB_DSN)
        try:
            local_output = await fetch_local_output(
                conn,
                normalized_trigger_id,
                wait_timeout_s=local_wait_timeout_s,
                poll_interval_s=min(5.0, max(0.5, poll_interval)),
            )
            diff = compare_outputs(remote_terminal=terminal.body, local_output=local_output, validation=validation)
            record = build_shadow_record(
                event_label=event_label,
                trigger_id=normalized_trigger_id,
                submit=submit,
                remote_terminal=terminal,
                auth=auth,
                local_output=local_output,
                diff=diff,
                request_payload=payload,
                started_at=started_at,
                poll_count=poll_count,
            )
            shadow_id = await persist_shadow_record(conn, record) if persist else None
        finally:
            await conn.close()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        log.warning("planner_graph shadow failed for %s: %s", normalized_trigger_id, error)
        if persist:
            record = build_shadow_record(
                event_label=event_label,
                trigger_id=normalized_trigger_id,
                submit=submit,
                remote_terminal=terminal,
                auth=auth,
                local_output=local_output,
                diff=diff,
                request_payload=payload,
                started_at=started_at,
                error=error,
                poll_count=poll_count,
            )
            conn = await asyncpg.connect(DB_DSN)
            try:
                shadow_id = await persist_shadow_record(conn, record)
            finally:
                await conn.close()
        else:
            shadow_id = None

    return {
        "trigger_id": normalized_trigger_id,
        "remote_status": terminal.body.get("status") if terminal and isinstance(terminal.body, dict) else None,
        "remote_action": diff.get("remote_action_type"),
        "local_action": diff.get("local_action_type"),
        "would_accept_remote": diff.get("would_accept_remote"),
        "judgement": diff.get("judgement"),
        "shadow_id": shadow_id,
        "error": error,
        "auth_source": auth.source,
        "poll_count": poll_count,
    }


def run_planner_graph_shadow_sync(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(run_planner_graph_shadow(**kwargs))


def ingest_planner_memory_batch(
    *,
    items: list[PlannerMemoryItem],
    batch_id: str,
    base_url: str | None = None,
    timeout: float | None = None,
) -> HttpResult:
    if not items:
        return HttpResult(
            status=200, body={"accepted_count": 0, "duplicate_count": 0, "rejected_count": 0}, elapsed_ms=0
        )
    resolved_base_url = (base_url or planner_graph_base_url()).rstrip("/")
    auth = build_auth_headers(resolved_base_url)
    payload = build_memory_ingest_request(
        greenhouse_id=GREENHOUSE_ID,
        batch_id=batch_id,
        items=items,
    )
    return submit_planner_memory_ingest(
        resolved_base_url,
        payload,
        auth,
        timeout=timeout or float(os.getenv("PLANNER_MEMORY_INGEST_REQUEST_TIMEOUT_S", "10")),
    )


def maybe_start_planner_graph_shadow(
    *,
    event_type: str,
    event_label: str,
    context: str,
    delivery_result: dict[str, Any],
) -> None:
    """Start a best-effort daemon sidecar after the local planner delivery attempt."""

    if not shadow_enabled() or not eligible_event(event_type):
        return
    trigger_id = delivery_result.get("trigger_id")
    if not trigger_id:
        log.warning("planner_graph shadow skipped: delivery_result had no trigger_id")
        return
    planner_instance = str(delivery_result.get("instance") or "local")

    def _target() -> None:
        try:
            run_planner_graph_shadow_sync(
                event_type=event_type,
                event_label=event_label,
                context=context,
                trigger_id=str(trigger_id),
                planner_instance=planner_instance,
            )
        except Exception:
            log.exception("planner_graph shadow sidecar crashed for trigger_id=%s", trigger_id)

    thread = threading.Thread(target=_target, name=f"planner-graph-shadow-{trigger_id}", daemon=True)
    thread.start()
