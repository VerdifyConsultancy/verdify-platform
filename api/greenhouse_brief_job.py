"""CronJob verdify-greenhouse-brief (#1346): write the greenhouse snapshot with the local Cortex model.

Every 5 minutes, as the dashboard login (the reads API-02, API-07, API-13, API-14 and API-IRR-01 serve, so the facts
are what Now shows), this builds a fact sheet and decides:
- the band verdicts or the open alerts changed since the newest ``ok`` snapshot: mark that row outdated and write a
  new one now; a mode change or a run starting or stopping also writes one now;
- the sheet is unchanged (same sha256, its time aside): confirm the newest row (``checked_at``) and call nothing;
- otherwise write one once the newest is 15 minutes old.

A write is one chat completion to Cortex ``llm.primary.longctx`` (OpenAI-compatible, Bearer key from the mounted
file), with no tools, temperature 0, a fixed seed, thinking off and at most 300 output tokens. Only ``content`` is
read; a reasoning block is dropped. The answer is refused (status ``rejected``) when it is not 2-5 plain sentences,
names a number or a time the facts do not hold (within rounding) or advises a settings change. A missing key or a
transport failure is status ``error``. Every attempt is one ``greenhouse_brief`` row; rows older than 30 days are
deleted. The text is never an input to the planner or the controller.

Exit 0 after a recorded attempt, a confirmation or a wait; 1 when the database cannot be read; 2 when the key file
is missing (fails closed, with an error row).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import httpx

from verdify_schemas.greenhouse_brief import GreenhouseBriefRow
from verdify_services import alerts, brief, reads
from verdify_services.irrigation import reads as irrigation_reads
from verdify_services.series import forecast_rows_sql

LOG = logging.getLogger("greenhouse_brief")
DENVER = ZoneInfo("America/Denver")
DASHBOARD_LOGIN = "verdify_dashboard_runtime_login"
ATTEST_SQL = "SELECT current_user = session_user AND verdify_dashboard_runtime.fn_attest_login()"
DEFAULT_KEY_FILE = "/vcortex-brief/api_key.txt"
DEADLINE_S = 90.0
MAX_TOKENS = 300
SEED = 1346
FORECAST_HOURS = 12
MAX_ALERTS = 5
MAX_NEXT = 3
MAX_SHEET_CHARS = 8000
ERROR_BODY_CHARS = 200

SYSTEM_PROMPT = (
    "You write the greenhouse snapshot shown at the top of the Verdify dashboard. "
    "Write one paragraph of 3 to 5 plain English sentences: first the current conditions against the band, "
    "then what the climate system is doing and why, then what comes next. "
    "Use only the facts given. Write every number exactly as the facts give it, with its unit, and write times "
    "exactly as given (24-hour HH:MM). Do not invent numbers, times or causes. "
    "Describe the greenhouse air and equipment only: do not name or describe plants or crops. "
    "Do not give advice and never suggest changing a setting. "
    "If data_freshness says readings are stale, say so in the first sentence. "
    "No lists, headings, Markdown or preamble: only the paragraph."
)


class KeyMissing(RuntimeError):
    pass


def read_key(path: str) -> str:
    try:
        key = Path(path).read_text().strip()
    except OSError as exc:
        raise KeyMissing(f"Cortex key file is not readable ({type(exc).__name__})") from None
    if not key:
        raise KeyMissing("Cortex key file is empty")
    return key


# ── The fact sheet (pure) ───────────────────────────────────────────────────


def _local(at: datetime | None) -> str | None:
    return None if at is None else at.astimezone(DENVER).strftime("%H:%M")


def _round(value: float | None, places: int) -> float | int | None:
    if value is None:
        return None
    return round(value) if places == 0 else round(value, places)


@dataclass(frozen=True)
class Gathered:
    now: reads.Now
    equipment: reads.Equipment | None = None
    plan: reads.Plan | None = None
    irrigation: irrigation_reads.IrrigationStatus | None = None
    alert_page: alerts.AlertPage | None = None
    forecast: Sequence[Mapping[str, Any]] | None = None


def _forecast(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    temps = [(row["temp_f"], row["ts"]) for row in rows if row.get("temp_f") is not None]
    if not temps:
        return None
    low, high = min(temps, key=lambda item: item[0]), max(temps, key=lambda item: item[0])
    gusts = [row["wind_gust_mph"] for row in rows if row.get("wind_gust_mph") is not None]
    rain = [row["precip_prob_pct"] for row in rows if row.get("precip_prob_pct") is not None]
    return {
        # The window is a fact too: the model writes "the next 12 hours", and the number check only passes numbers
        # the sheet holds as values, not the 12 in this block's key (#1346, 11 rejections on 2026-11-06/07).
        "hours_ahead": FORECAST_HOURS,
        "outdoor_low_f": _round(low[0], 0),
        "outdoor_low_at": _local(low[1]),
        "outdoor_high_f": _round(high[0], 0),
        "outdoor_high_at": _local(high[1]),
        "max_wind_gust_mph": _round(max(gusts), 0) if gusts else None,
        "max_chance_of_precipitation_pct": _round(max(rain), 0) if rain else None,
    }


def build_sheet(data: Gathered, at: datetime) -> dict[str, Any]:
    """The model-facing fact sheet: rounded values, local HH:MM times, nulls for unknowns, nothing plant-specific
    and no CO2, PAR or south-probe values."""
    now = data.now
    labels = {item.equipment: item.label for item in data.equipment.items} if data.equipment else {}

    def band(position: reads.BandPosition, places: int) -> dict[str, Any]:
        return {"low": _round(position.low, places), "high": _round(position.high, places), "verdict": position.verdict}

    climate = now.freshness.climate
    sheet: dict[str, Any] = {
        "as_of_local": _local(at),
        "data_freshness": {
            "indoor_readings_stale": climate.stale,
            "indoor_reading_age_min": _round(climate.age_s / 60, 0) if climate.stale and climate.age_s else None,
            "ingestor_running": not now.freshness.ingestor.stale,
        },
        "indoor": {
            "temperature_f": _round(now.indoor.temp_f.value, 1),
            "relative_humidity_pct": _round(now.indoor.rh_pct.value, 0),
            "vpd_kpa": _round(now.indoor.vpd_kpa.value, 2),
        },
        "band": {"temperature_f": band(now.band.temp_f, 1), "vpd_kpa": band(now.band.vpd_kpa, 2)},
        "outdoor": {
            "temperature_f": _round(now.outdoor.temp_f.value, 1),
            "relative_humidity_pct": _round(now.outdoor.rh_pct.value, 0),
        },
        "climate_mode": {
            "mode": now.mode.code,
            "stage": now.mode.stage,
            "reason": now.mode.reason,
            "since": _local(now.mode.since),
        },
        "running_now": [
            {"equipment": labels.get(item.equipment, item.equipment), "cause": item.cause, "since": _local(item.since)}
            for item in now.running
        ],
        "bursts": [{"kind": item.kind, "ends_at": _local(item.ends_at)} for item in now.overrides],
        "alerts_open": {"total": now.alerts.open, "critical": now.alerts.critical, "warning": now.alerts.warning},
        "leak_active": now.leak.active,
        "settings_not_yet_confirmed_by_controller": now.writer.differing,
    }
    if data.alert_page is not None:
        sheet["alerts"] = [
            {"severity": item.severity, "message": item.message[:160]} for item in data.alert_page.items[:MAX_ALERTS]
        ]
    if data.equipment is not None:
        sheet["lights"] = [
            {"light": item.label, "state": item.state} for item in data.equipment.items if item.group == "light"
        ]
    if data.plan is not None:
        record = data.plan.plan
        upcoming = sorted(
            (trigger for trigger in data.plan.health if trigger.expected_at > at), key=lambda t: t.expected_at
        )
        sheet["planner"] = {
            "plan_in_force": record is not None,
            "plan_trigger": record.trigger if record else None,
            "plan_valid_until": _local(record.valid_until) if record else None,
            "next_planner_runs": [
                {"trigger": trigger.trigger, "at": _local(trigger.expected_at)} for trigger in upcoming[:MAX_NEXT]
            ],
        }
    if data.irrigation is not None:
        status = data.irrigation
        names = {line.line_id: line.name for line in status.lines}
        next_runs = sorted((line.next_run for line in status.lines if line.next_run), key=lambda run: run.start_at)
        sheet["water_today"] = {
            "gallons_used": _round(status.limits.saf4_gal.used, 0),
            "daily_cap_gallons": _round(status.limits.saf4_gal.max, 0),
            "watering_now": [
                {"line": names.get(run.line_id, run.line_id), "cause": run.cause, "ends_at": _local(run.ends_at)}
                for run in status.active_runs
            ],
            "next_scheduled": [
                {"line": names.get(run.line_id, run.line_id), "at": _local(run.start_at), "minutes": run.minutes}
                for run in next_runs[:MAX_NEXT]
            ],
        }
    if data.forecast is not None:
        sheet["outdoor_forecast_next_12h"] = _forecast(data.forecast)
    return sheet


def event_of(now: reads.Now) -> dict[str, Any]:
    return brief.event_key(now.band.temp_f.verdict, now.band.vpd_kpa.verdict, now.alerts.open, now.alerts.critical)


def trigger_of(now: reads.Now) -> dict[str, Any]:
    """Changes that write a new snapshot at once (they do not make the old one outdated)."""
    return {"mode": now.mode.code, "running": sorted(item.equipment for item in now.running)}


def decide(newest: brief.Newest | None, event: Mapping, trigger: Mapping, sha: str, at: datetime) -> str:
    """first, event, unchanged (confirm only), cadence (write) or wait."""
    if newest is None:
        return "first"
    if newest.event != dict(event) or newest.trigger != dict(trigger):
        return "event"
    if newest.facts_sha256 == sha:
        return "unchanged"
    if at - newest.as_of >= brief.CADENCE:
        return "cadence"
    return "wait"


# ── The Cortex call ─────────────────────────────────────────────────────────


def request_body(sheet: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model": brief.MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Facts (JSON):\n" + brief.canonical(sheet) + "\n\nWrite the snapshot."},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        "top_p": 1,
        "seed": SEED,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def http_error(response: httpx.Response, key: str) -> str:
    """``HTTP <status>`` plus the start of the body, so a stored error tells a missing route (GPUStack answers 404
    while the route has no reachable instance) from an overload. The key never reaches the row."""
    try:
        body = " ".join(response.text.split())
    except Exception:  # an undecodable body adds nothing
        body = ""
    if key:
        body = body.replace(key, "<key>")
    return f"HTTP {response.status_code}" + (f": {body[:ERROR_BODY_CHARS]}" if body else "")


@dataclass(frozen=True)
class Answer:
    content: str | None
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    error: str | None


async def call_cortex(
    base_url: str, key: str, body: Mapping[str, Any], http: httpx.AsyncClient | None = None
) -> Answer:
    client = http or httpx.AsyncClient(timeout=httpx.Timeout(DEADLINE_S, connect=10.0))
    started = time.monotonic()
    try:
        response = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
    except httpx.HTTPError as exc:
        return Answer(None, int((time.monotonic() - started) * 1000), None, None, f"transport: {type(exc).__name__}")
    finally:
        if http is None:
            await client.aclose()
    latency = int((time.monotonic() - started) * 1000)
    if response.status_code != 200:
        return Answer(None, latency, None, None, http_error(response, key))
    try:
        payload = response.json()
        choice = payload["choices"][0]
        content = (choice.get("message") or {}).get("content") or ""
        usage = payload.get("usage") or {}
    except (ValueError, KeyError, IndexError, TypeError):
        return Answer(None, latency, None, None, "malformed response")
    if choice.get("finish_reason") == "length":
        return Answer(content, latency, usage.get("prompt_tokens"), usage.get("completion_tokens"), "cut at max_tokens")
    return Answer(content, latency, usage.get("prompt_tokens"), usage.get("completion_tokens"), None)


def outcome(answer: Answer, sheet: Mapping[str, Any]) -> tuple[str, str | None, str | None]:
    """(status, text, error) for one answer."""
    if answer.content is None:
        return "error", None, answer.error
    text, refused = brief.clean(answer.content)
    if answer.error:
        return "rejected", text or None, answer.error
    if refused:
        return "rejected", text or None, refused
    refused = brief.check_text(text, sheet)
    if refused:
        return "rejected", text, refused
    return "ok", text, None


# ── The run ─────────────────────────────────────────────────────────────────

INSERT_SQL = (
    "INSERT INTO public.greenhouse_brief (generated_at, valid_until, model, route, prompt_version, facts, "
    "facts_sha256, text, latency_ms, prompt_tokens, completion_tokens, status, error) "
    "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11, $12, $13) RETURNING id"
)
CONFIRM_SQL = "UPDATE public.greenhouse_brief SET checked_at = $2 WHERE id = $1"
OUTDATED_SQL = "UPDATE public.greenhouse_brief SET outdated_at = $2 WHERE id = $1 AND outdated_at IS NULL"
RETENTION_SQL = "DELETE FROM public.greenhouse_brief WHERE generated_at < $1"


async def _optional(name: str, read):
    try:
        return await read
    except Exception as exc:  # one missing section is a smaller sheet, not a failed run
        LOG.warning("read %s failed: %s", name, type(exc).__name__)
        return None


async def gather(conn: asyncpg.Connection) -> tuple[datetime, Gathered]:
    now = await reads.now(conn)
    at = now.server_time
    forecast = await _optional(
        "forecast",
        conn.fetch(
            forecast_rows_sql(("temp_f", "wind_gust_mph", "precip_prob_pct")), at, at + timedelta(hours=FORECAST_HOURS)
        ),
    )
    return at, Gathered(
        now=now,
        equipment=await _optional("equipment", reads.equipment(conn)),
        plan=await _optional("plan", reads.plan(conn, hours=FORECAST_HOURS)),
        irrigation=await _optional("irrigation", irrigation_reads.status(conn, None)),
        alert_page=await _optional("alerts", alerts.list(conn, "open", limit=MAX_ALERTS)),
        forecast=None if forecast is None else [dict(row) for row in forecast],
    )


async def insert(conn: asyncpg.Connection, row: GreenhouseBriefRow) -> int:
    return await conn.fetchval(
        INSERT_SQL,
        row.generated_at,
        row.valid_until,
        row.model,
        row.route,
        row.prompt_version,
        json.dumps(row.facts),
        row.facts_sha256,
        row.text,
        row.latency_ms,
        row.prompt_tokens,
        row.completion_tokens,
        row.status,
        row.error,
    )


async def run_once(
    conn: asyncpg.Connection, base_url: str, key_file: str, http: httpx.AsyncClient | None = None
) -> int:
    at, data = await gather(conn)
    sheet = build_sheet(data, at)
    if len(brief.canonical(sheet)) > MAX_SHEET_CHARS:
        sheet.pop("alerts", None)
    sha = brief.facts_sha256(sheet)
    event, trigger = event_of(data.now), trigger_of(data.now)
    newest = await brief.newest(conn)
    await conn.execute(RETENTION_SQL, at - brief.RETENTION)
    if newest is not None and newest.event is not None and newest.event != event:
        await conn.execute(OUTDATED_SQL, newest.id, at)
    decision = decide(newest, event, trigger, sha, at)
    LOG.info("decision %s", decision)
    if decision == "unchanged":
        await conn.execute(CONFIRM_SQL, newest.id, at)
        return 0
    if decision == "wait":
        return 0
    facts = {"as_of": at.isoformat(), "event": event, "trigger": trigger, "sheet": sheet}
    try:
        key = read_key(key_file)
    except KeyMissing as missing:
        LOG.error("FAIL CLOSED: %s", missing)
        answer, code = Answer(None, 0, None, None, f"FAIL CLOSED: {missing}"), 2
    else:
        answer, code = await call_cortex(base_url, key, request_body(sheet), http), 0
    status, text, error = outcome(answer, sheet)
    row = GreenhouseBriefRow(
        generated_at=at,
        valid_until=at + brief.STALE_AFTER,
        model=brief.MODEL,
        route=base_url,
        prompt_version=brief.PROMPT_VERSION,
        facts=facts,
        facts_sha256=sha,
        text=text[:4000] if text else None,
        latency_ms=answer.latency_ms,
        prompt_tokens=answer.prompt_tokens,
        completion_tokens=answer.completion_tokens,
        status=status,
        error=error[:2000] if error else None,
    )
    row_id = await insert(conn, row)
    LOG.info(
        "brief %s status=%s latency_ms=%s tokens=%s/%s%s",
        row_id,
        status,
        answer.latency_ms,
        answer.prompt_tokens,
        answer.completion_tokens,
        f" error={error}" if error else "",
    )
    return code


async def connect(env: Mapping[str, str]) -> asyncpg.Connection:
    if env.get("DB_USER") != DASHBOARD_LOGIN:
        raise RuntimeError(f"DB_USER must be {DASHBOARD_LOGIN}")
    conn = await asyncpg.connect(
        host=env["DB_HOST"],
        port=5432,
        database=env["DB_NAME"],
        user=env["DB_USER"],
        password=env["DB_PASS"],
        server_settings={"application_name": "verdify-greenhouse-brief", "jit": "off"},
        timeout=15,
    )
    if await conn.fetchval(ATTEST_SQL) is not True:
        await conn.close()
        raise RuntimeError(f"the database connection did not attest as {DASHBOARD_LOGIN}")
    return conn


async def main(env: Mapping[str, str] = os.environ) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        conn = await connect(env)
    except Exception as exc:
        LOG.error("database unavailable: %s", type(exc).__name__)
        return 1
    try:
        return await run_once(
            conn,
            env.get("CORTEX_BASE_URL") or brief.BASE_URL,
            env.get("CORTEX_API_KEY_FILE") or DEFAULT_KEY_FILE,
        )
    except Exception as exc:
        LOG.error("run failed: %s", type(exc).__name__)
        return 1
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
