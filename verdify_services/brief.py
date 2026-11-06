"""The greenhouse snapshot on Now (#1346): its read, its freshness rules and its guardrails.

CronJob ``verdify-greenhouse-brief`` (``api/greenhouse_brief_job.py``) builds a fact sheet from the operator reads every
5 minutes and, on a change, asks the local Cortex model for three to five plain sentences. Each attempt is one row of
``public.greenhouse_brief`` (migration 301). API-02 serves the newest ``ok`` row as ``Now.snapshot`` and MCP's
``get_greenhouse_snapshot`` returns the same block, through ``newest()`` and ``snapshot()`` here.

Freshness (the issue's fallback rules), measured from ``as_of``, the newer of the row's ``generated_at`` and
``checked_at`` (a later run that found the same fact sheet):
- older than two cadences (30 min), or a later attempt failed or was rejected: ``stale``, shown muted with "as of";
- older than 2 h: not served at all (``None``), so Now keeps only its deterministic status line;
- a band verdict or the open-alert count changed since it was written: ``outdated``, never shown. The job marks the
  row (``outdated_at``) on its next run; API-02 also compares the row's ``event`` with the live one.

The text is read-only. Nothing here or in the job feeds the planner, Iris or the controller.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict

MODEL = "llm.primary.longctx"
BASE_URL = "https://cortex.vallery.net/v1"
PROMPT_VERSION = "brief-v1"
CADENCE = timedelta(minutes=15)
STALE_AFTER = 2 * CADENCE
HIDE_AFTER = timedelta(hours=2)
RETENTION = timedelta(days=30)
MIN_SENTENCES, MAX_SENTENCES = 2, 5
MAX_TEXT_CHARS = 1200

StaleReason = Literal["old", "failed"]


class Snapshot(BaseModel):
    """OpenAPI ``Snapshot``: the newest written snapshot with the time it describes."""

    model_config = ConfigDict(extra="forbid")

    text: str
    generated_at: AwareDatetime
    as_of: AwareDatetime
    model: str
    stale: bool
    stale_reason: StaleReason | None
    outdated: bool


class SnapshotRead(BaseModel):
    """MCP ``get_greenhouse_snapshot``: the same block API-02 serves as ``Now.snapshot``, with the read's time."""

    model_config = ConfigDict(extra="forbid")

    server_time: AwareDatetime
    snapshot: Snapshot | None


@dataclass(frozen=True)
class Newest:
    """The newest ``ok`` row and the outcome of the newest attempt of any kind."""

    id: int
    generated_at: datetime
    checked_at: datetime | None
    outdated_at: datetime | None
    model: str
    text: str
    facts_sha256: str
    event: dict[str, Any] | None
    trigger: dict[str, Any] | None
    newest_status: str | None
    newest_at: datetime | None

    @property
    def as_of(self) -> datetime:
        return max(self.generated_at, self.checked_at or self.generated_at)


NEWEST_SQL = """
SELECT ok.id, ok.generated_at, ok.checked_at, ok.outdated_at, ok.model, ok.text, ok.facts_sha256,
       ok.facts -> 'event' AS event, ok.facts -> 'trigger' AS trigger,
       latest.status AS newest_status, latest.generated_at AS newest_at
  FROM (SELECT id, generated_at, checked_at, outdated_at, model, text, facts_sha256, facts
          FROM public.greenhouse_brief WHERE status = 'ok' ORDER BY generated_at DESC LIMIT 1) ok
  LEFT JOIN LATERAL (SELECT status, generated_at FROM public.greenhouse_brief
                      ORDER BY generated_at DESC LIMIT 1) latest ON true
"""


def _json(value: object) -> dict[str, Any] | None:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else None


async def newest(conn: asyncpg.Connection) -> Newest | None:
    """The newest ``ok`` row, or None. A database without migration 301 (or a login without its grant) has none:
    the read runs in its own savepoint, so API-02's snapshot transaction survives."""
    try:
        async with conn.transaction():
            row = await conn.fetchrow(NEWEST_SQL)
    except (asyncpg.UndefinedTableError, asyncpg.InsufficientPrivilegeError):
        return None
    if row is None:
        return None
    return Newest(
        id=row["id"],
        generated_at=row["generated_at"],
        checked_at=row["checked_at"],
        outdated_at=row["outdated_at"],
        model=row["model"],
        text=row["text"],
        facts_sha256=row["facts_sha256"],
        event=_json(row["event"]),
        trigger=_json(row["trigger"]),
        newest_status=row["newest_status"],
        newest_at=row["newest_at"],
    )


def event_key(temp_verdict: str, vpd_verdict: str, alerts_open: int, alerts_critical: int) -> dict[str, Any]:
    """What makes a written snapshot outdated: a band verdict or the open alerts changed."""
    return {
        "temperature": temp_verdict,
        "vpd": vpd_verdict,
        "alerts_open": alerts_open,
        "alerts_critical": alerts_critical,
    }


def snapshot(row: Newest | None, at: datetime, event: Mapping[str, Any] | None = None) -> Snapshot | None:
    """The served block, or None when there is no ``ok`` row or it is older than 2 h (pure)."""
    if row is None:
        return None
    as_of = row.as_of
    age = at - as_of
    if age > HIDE_AFTER:
        return None
    failed = row.newest_status not in (None, "ok") and row.newest_at is not None and row.newest_at > as_of
    reason: StaleReason | None = "failed" if failed else "old" if age > STALE_AFTER else None
    outdated = row.outdated_at is not None or (event is not None and row.event is not None and dict(event) != row.event)
    return Snapshot(
        text=row.text,
        generated_at=row.generated_at,
        as_of=as_of,
        model=row.model,
        stale=reason is not None,
        stale_reason=reason,
        outdated=outdated,
    )


async def read(conn: asyncpg.Connection) -> SnapshotRead:
    """The newest snapshot as MCP serves it. ``outdated`` is the job's mark (``outdated_at``, set within 5 minutes of
    a change); API-02 also compares the live band verdicts and alert count."""
    at = await conn.fetchval("SELECT now()")
    return SnapshotRead(server_time=at, snapshot=snapshot(await newest(conn), at))


# ── The fact sheet's hash and the guardrails ────────────────────────────────


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def facts_sha256(sheet: Mapping[str, Any]) -> str:
    """The hash of the model-facing sheet. Its time is left out, so an unchanged greenhouse skips the call."""
    return hashlib.sha256(canonical({k: v for k, v in sheet.items() if k != "as_of_local"}).encode()).hexdigest()


_TIME = re.compile(r"\b(\d{1,2}):(\d{2})(?:\s?([AaPp])\.?\s?[Mm]\.?)?")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_ADVICE = re.compile(
    r"\b(you should|you could|you may want|you might want|we recommend|i recommend|recommend(?:ed|s)? "
    r"(?:that|to|raising|lowering|increasing|decreasing|setting|changing|adjusting)|consider "
    r"(?:raising|lowering|increasing|decreasing|adjusting|changing|setting|turning)|"
    r"should be (?:raised|lowered|increased|decreased|adjusted|changed|set)|"
    r"(?:adjust|change|raise|lower|increase|decrease|set) (?:the |a |your )?(?:setpoint|setting|band|target|threshold|"
    r"tunable)s?)\b",
    re.IGNORECASE,
)
_MARKUP_LINE = re.compile(r"^\s*(?:[-*#>]|\d+[.)])\s", re.M)
_FORBIDDEN_MARKUP = ("```", "**", "__")


def _walk(value: object) -> Iterable[object]:
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item)
    else:
        yield value


def fact_numbers(sheet: Mapping[str, Any]) -> tuple[list[float], set[tuple[int, int]]]:
    """Every number in the sheet (values, and numbers inside its strings) and every HH:MM time."""
    numbers: list[float] = []
    times: set[tuple[int, int]] = set()
    for value in _walk(sheet):
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            if math.isfinite(value):
                numbers.append(float(value))
            continue
        text = str(value)
        for hour, minute, _ in _TIME.findall(text):
            times.add((int(hour), int(minute)))
        for token in _NUMBER.findall(_TIME.sub(" ", text)):
            numbers.append(float(token))
    return numbers, times


def clean(content: str) -> tuple[str, str | None]:
    """The answer as one paragraph, and why it is refused before any other check: Markdown (a list, a heading, a
    code fence, bold) or nothing left once a <think> block a reasoning parser left in ``content`` is dropped."""
    content = re.sub(r"<think>.*?</think>", " ", content or "", flags=re.S | re.I)
    if re.search(r"</?think>", content, re.I):
        return "", "reasoning in the answer"
    if _MARKUP_LINE.search(content) or any(mark in content for mark in _FORBIDDEN_MARKUP):
        return " ".join(content.split()), "Markdown in the answer"
    text = " ".join(content.split())
    return text, None if text else "empty answer"


def check_text(text: str, sheet: Mapping[str, Any]) -> str | None:
    """None when the text may be shown; else why it is rejected (stored as the row's ``error``)."""
    if not text:
        return "empty answer"
    if len(text) > MAX_TEXT_CHARS:
        return f"longer than {MAX_TEXT_CHARS} characters"
    sentences = [part for part in _SENTENCE_END.split(text.strip()) if part.strip()]
    if not MIN_SENTENCES <= len(sentences) <= MAX_SENTENCES:
        return f"{len(sentences)} sentences, not {MIN_SENTENCES}-{MAX_SENTENCES}"
    advice = _ADVICE.search(text)
    if advice:
        return f"advice to change settings: {advice.group(0)!r}"
    numbers, times = fact_numbers(sheet)
    for hour, minute, meridiem in _TIME.findall(text):
        h, m = int(hour), int(minute)
        if meridiem:
            h = h % 12 + (12 if meridiem.lower() == "p" else 0)
        if (h, m) not in times:
            return f"time {hour}:{minute} is not in the facts"
    for token in _NUMBER.findall(_TIME.sub(" ", text)):
        value = float(token)
        places = len(token.split(".", 1)[1]) if "." in token else 0
        tolerance = 0.5 * 10**-places + 1e-9
        if not any(abs(value - fact) <= tolerance for fact in numbers):
            return f"number {token} is not in the facts"
    return None
