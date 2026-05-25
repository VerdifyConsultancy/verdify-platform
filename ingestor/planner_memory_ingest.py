"""Planner-owned memory ingestion helpers for Verdify.

This module publishes validated Verdify facts to planner memory. It is not a
controller path and does not execute planner output.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

try:
    from config import GREENHOUSE_ID
except Exception:  # pragma: no cover - import path differs in isolated tests
    GREENHOUSE_ID = os.environ.get("GREENHOUSE_ID", "vallery")

log = logging.getLogger("planner_memory_ingest")

MAX_MEMORY_BODY_CHARS = 4000
MAX_MEMORY_SUMMARY_CHARS = 800
MAX_MEMORY_TITLE_CHARS = 160


class PlannerMemoryIngestError(RuntimeError):
    """Raised for local setup and remote planner-memory ingestion failures."""


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


def _env_first(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def planner_memory_base_url() -> str:
    url = _env_first("PLANNER_MEMORY_URL", "PLANNER_GRAPH_URL").rstrip("/")
    if not url:
        raise PlannerMemoryIngestError("PLANNER_MEMORY_URL is required for planner memory ingestion")
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


def _truncate(text: str, limit: int) -> str:
    stripped = " ".join((text or "").split()).strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: max(limit - 1, 0)].rstrip() + "..."


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
        raise PlannerMemoryIngestError(f"support docs file must contain a list: {path}")
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


def _audience_for(base_url: str) -> str:
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else base_url.rstrip("/")


def _metadata_identity_token(audience: str, timeout: float = 2.0) -> str | None:
    url = (
        "http://metadata/computeMetadata/v1/instance/service-accounts/default/identity"
        f"?audience={quote(audience, safe='')}&format=full"
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
        log.warning("planner memory service-account ID token failed: %s", exc)
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
    if configuration := _env_first("PLANNER_MEMORY_GCLOUD_CONFIGURATION", "PLANNER_GRAPH_GCLOUD_CONFIGURATION"):
        command.append(f"--configuration={configuration}")
    return command


def _gcloud_identity_token(audience: str) -> str | None:
    impersonate = _env_first("PLANNER_MEMORY_IMPERSONATE_SERVICE_ACCOUNT", "PLANNER_GRAPH_IMPERSONATE_SERVICE_ACCOUNT")
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
            log.debug("planner memory gcloud identity-token command failed before completion: %s", exc)
            continue
        token = proc.stdout.strip()
        if proc.returncode == 0 and token:
            return token
    return None


def build_auth_headers(base_url: str) -> AuthHeaders:
    """Resolve planner-memory auth without ever logging token material."""

    audience = _env_first("PLANNER_MEMORY_AUDIENCE", "PLANNER_GRAPH_AUDIENCE") or _audience_for(base_url)
    mode = _env_first("PLANNER_MEMORY_AUTH_MODE", "PLANNER_GRAPH_AUTH_MODE") or "auto"
    mode = mode.lower()
    if mode in {"none", "disabled", "off"}:
        return AuthHeaders(headers={}, mode=mode, source="none", audience=audience)

    direct = _env_first("PLANNER_MEMORY_ID_TOKEN", "PLANNER_GRAPH_ID_TOKEN")
    if direct:
        return AuthHeaders({"Authorization": f"Bearer {direct}"}, mode=mode, source="direct_env", audience=audience)

    token_file = _env_first("PLANNER_MEMORY_BEARER_TOKEN_FILE", "PLANNER_GRAPH_BEARER_TOKEN_FILE")
    if token_file:
        token = Path(token_file).read_text().strip()
        return AuthHeaders({"Authorization": f"Bearer {token}"}, mode=mode, source="bearer_file", audience=audience)

    if mode in {"bearer", "token"}:
        raise PlannerMemoryIngestError("PLANNER_MEMORY_ID_TOKEN or PLANNER_MEMORY_BEARER_TOKEN_FILE is required")

    credentials_file = (
        _env_first("PLANNER_MEMORY_GOOGLE_APPLICATION_CREDENTIALS", "PLANNER_GRAPH_GOOGLE_APPLICATION_CREDENTIALS")
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

    allow_gcloud = _env_first("PLANNER_MEMORY_ALLOW_GCLOUD_AUTH", "PLANNER_GRAPH_ALLOW_GCLOUD_AUTH").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if mode == "google_oidc" or allow_gcloud:
        token = _gcloud_identity_token(audience)
        if token:
            return AuthHeaders({"Authorization": f"Bearer {token}"}, mode=mode, source="gcloud", audience=audience)

    if mode == "auto":
        return AuthHeaders(headers={}, mode=mode, source="none", audience=audience)
    raise PlannerMemoryIngestError(f"could not resolve planner-memory auth for mode={mode!r}")


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
    resolved_base_url = (base_url or planner_memory_base_url()).rstrip("/")
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
