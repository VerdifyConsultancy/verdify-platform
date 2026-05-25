"""Deterministic Slack command execution service."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg

from slack_config import SlackSettings, load_slack_settings
from slack_ops.intents import parse_command, role_allows
from verdify_schemas.slack_ops import (
    SlackCommandRequest,
    SlackCommandResponse,
    SlackParsedIntent,
    SlackRole,
)

DEFAULT_GREENHOUSE = "vallery"


def get_db_dsn() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    db_pass = os.environ.get("DB_PASS") or _read_env_password() or "verdify"
    host = os.environ.get("DB_HOST", "localhost")
    name = os.environ.get("DB_NAME", "verdify")
    user = os.environ.get("DB_USER", "verdify")
    return f"postgresql://{user}:{db_pass}@{host}:5432/{name}"


def _read_env_password() -> str | None:
    for path in (Path("/srv/verdify/.env"), Path("/mnt/iris/verdify/.env")):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if line.strip().startswith(("POSTGRES_PASSWORD=", "DB_PASS=")):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


async def handle_slack_command(
    request: SlackCommandRequest | dict[str, Any],
    *,
    dsn: str | None = None,
    settings: SlackSettings | None = None,
    role_override: SlackRole | None = None,
) -> SlackCommandResponse:
    """Parse, authorize, audit, and optionally execute a Slack operation."""

    req = request if isinstance(request, SlackCommandRequest) else SlackCommandRequest.model_validate(request)
    parsed = parse_command(req.command_text)
    selected_settings = settings or load_slack_settings()
    conn: asyncpg.Connection | None = None
    audit_id: int | None = None
    role: SlackRole = role_override or _default_role(selected_settings)

    if req.execute:
        conn = await asyncpg.connect(dsn or get_db_dsn())
        role = role_override or await _fetch_role(conn, req.slack_user_id, req.slack_team_id)

    try:
        if parsed.unsafe_blocked:
            text = parsed.reason or "Blocked: Slack cannot directly control relays or equipment."
            audit_id = await _audit_if_needed(conn, req, parsed, role, "unsafe_blocked", response_text=text)
            return _response(False, True, parsed, "unsafe_blocked", role, text, audit_id=audit_id)

        if parsed.name == "unknown":
            text = "I do not have a deterministic greenhouse operation for that. Iris should handle this with context."
            audit_id = await _audit_if_needed(conn, req, parsed, role, "unsupported", response_text=text)
            return _response(False, False, parsed, "unsupported", role, text, audit_id=audit_id)

        if parsed.write and not role_allows(role, parsed.required_role):
            text = f"Denied: `{parsed.name}` requires `{parsed.required_role}` role; your role is `{role}`."
            audit_id = await _audit_if_needed(conn, req, parsed, role, "denied", response_text=text)
            return _response(False, True, parsed, "denied", role, text, audit_id=audit_id)

        if not req.execute:
            status = "needs_confirmation" if parsed.requires_confirmation else "parsed"
            text = _parsed_text(parsed)
            return _response(True, True, parsed, status, role, text)

        assert conn is not None
        audit_id = await _audit_if_needed(conn, req, parsed, role, "parsed")

        prepared_confirmation = await _prepare_confirmation_intent(conn, parsed)
        if prepared_confirmation.requires_confirmation:
            parsed = prepared_confirmation
            confirmation_id = await _create_confirmation(conn, req, parsed, audit_id)
            text = _confirmation_text(parsed, confirmation_id)
            await _finish_audit(
                conn, audit_id, "needs_confirmation", response_text=text, confirmation_id=confirmation_id
            )
            return _response(
                True,
                True,
                parsed,
                "needs_confirmation",
                role,
                text,
                audit_id=audit_id,
                confirmation_id=confirmation_id,
            )

        result = await _execute(conn, req, parsed, role, audit_id)
        await _finish_audit(
            conn,
            audit_id,
            result.status,
            response_text=result.text,
            record_type=result.record_type,
            record_id=result.record_id,
            error="; ".join(result.errors) if result.errors else None,
        )
        return result.model_copy(update={"audit_id": audit_id})
    except Exception as exc:
        text = f"Slack operation failed: {exc}"
        if conn and audit_id:
            await _finish_audit(conn, audit_id, "error", response_text=text, error=str(exc))
        return _response(False, True, parsed, "error", role, text, audit_id=audit_id, errors=[str(exc)])
    finally:
        if conn:
            await conn.close()


def _default_role(settings: SlackSettings) -> SlackRole:
    try:
        import yaml

        raw = yaml.safe_load(settings.config_path.read_text()) or {}
        role = ((raw.get("commands") or {}).get("default_role") or "viewer").strip()
        if role in {"viewer", "operator", "grower", "coordinator"}:
            return role  # type: ignore[return-value]
    except Exception:
        pass
    return "viewer"


def _response(
    ok: bool,
    handled: bool,
    intent: SlackParsedIntent,
    status: str,
    role: SlackRole,
    text: str,
    *,
    audit_id: int | None = None,
    confirmation_id: Any = None,
    record_type: str | None = None,
    record_id: str | None = None,
    data: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> SlackCommandResponse:
    return SlackCommandResponse.model_validate(
        {
            "ok": ok,
            "handled": handled,
            "intent": intent.model_dump(mode="python"),
            "status": status,
            "role": role,
            "text": text,
            "audit_id": audit_id,
            "confirmation_id": confirmation_id,
            "record_type": record_type,
            "record_id": record_id,
            "data": data or {},
            "errors": errors or [],
        }
    )


def _parsed_text(intent: SlackParsedIntent) -> str:
    if intent.requires_confirmation:
        return f"Parsed `{intent.name}`; execution requires confirmation."
    if intent.requires_ai:
        return f"Parsed `{intent.name}`; Iris should use OpenClaw reasoning with deterministic context."
    return f"Parsed `{intent.name}`."


def _confirmation_text(intent: SlackParsedIntent, confirmation_id: Any) -> str:
    summary = _confirmation_summary(intent)
    return f"{summary}\nReply `confirm {confirmation_id}` to execute or `cancel {confirmation_id}` to cancel."


def _confirmation_summary(intent: SlackParsedIntent) -> str:
    args = intent.args
    if intent.name == "crop.clear":
        return f"Confirmation required: clear crop #{args.get('crop_id')} `{args.get('crop_name')}` at `{args.get('position')}`."
    if intent.name == "crop.transplant":
        return (
            f"Confirmation required: transplant crop #{args.get('crop_id')} `{args.get('crop_name')}` "
            f"from `{args.get('position')}` to `{args.get('new_position')}`."
        )
    if intent.name == "crop.harvest":
        amount = _format_harvest_amount(args)
        warning = f" {args.get('harvest_warning')}" if args.get("harvest_warning") else ""
        return (
            f"Confirmation required: record harvest{amount} for crop #{args.get('crop_id')} "
            f"`{args.get('crop_name')}` at `{args.get('position')}`.{warning}"
        )
    if intent.name == "alert.snooze":
        return f"Confirmation required: snooze critical alert #{args.get('alert_id')} for `{args.get('duration')}`."
    if intent.name == "alert.false_positive":
        return f"Confirmation required: mark alert #{args.get('alert_id')} false positive."
    if intent.name == "plan.trigger":
        return f"Confirmation required: run planner because `{args.get('reason')}`."
    return f"Confirmation required for `{intent.name}` on `{intent.target_id or intent.target_type or 'target'}`."


def _crop_identity(crop: asyncpg.Record) -> dict[str, Any]:
    return {
        "crop_id": int(crop["id"]),
        "crop_name": crop["name"],
        "position": crop["position"],
        "zone": crop["zone"],
        "position_id": crop["position_id"],
        "zone_id": crop["zone_id"],
    }


def _kg_from_amount(amount: float | int | None, unit: str | None) -> float | None:
    if amount is None or unit is None:
        return None
    normalized = unit.lower()
    value = float(amount)
    if normalized == "kg":
        return value
    if normalized == "g":
        return value / 1000.0
    if normalized in {"lb", "lbs"}:
        return value * 0.45359237
    if normalized == "oz":
        return value * 0.028349523125
    return None


def _format_harvest_amount(args: dict[str, Any]) -> str:
    amount = args.get("amount")
    unit = args.get("unit")
    if amount is None or not unit:
        return ""
    return f" {amount:g} {unit}" if isinstance(amount, float) else f" {amount} {unit}"


def _normalized_harvest_fields(args: dict[str, Any]) -> dict[str, Any]:
    unit = str(args.get("unit") or "").lower() or None
    amount = args.get("amount")
    fields: dict[str, Any] = {
        "quality_grade": args.get("quality_grade"),
        "destination": args.get("destination"),
        "labor_minutes": args.get("labor_minutes"),
        "notes": args.get("details") or args.get("notes"),
        "cull_reason": args.get("cull_reason"),
        "quality_reason": args.get("quality_reason"),
    }
    if unit == "unit" or unit == "units":
        fields["unit_count"] = int(amount) if amount is not None else None
    else:
        fields["weight_kg"] = _kg_from_amount(amount, unit)

    for source_key, dest_key in (
        ("salable_amount", "salable_weight_kg"),
        ("cull_amount", "cull_weight_kg"),
    ):
        value = args.get(source_key) or {}
        if isinstance(value, dict):
            fields[dest_key] = _kg_from_amount(value.get("amount"), value.get("unit"))
    return fields


async def _resolve_position(conn: asyncpg.Connection, label: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT * FROM v_position_current WHERE greenhouse_id=$1 AND upper(position_label)=upper($2) LIMIT 1",
        DEFAULT_GREENHOUSE,
        label.strip(),
    )


async def _crop_by_id_or_target(
    conn: asyncpg.Connection,
    crop_id: int,
    target: str,
) -> asyncpg.Record | None:
    if crop_id:
        return await conn.fetchrow(
            "SELECT * FROM crops WHERE id=$1 AND greenhouse_id=$2 AND is_active",
            crop_id,
            DEFAULT_GREENHOUSE,
        )
    return await _resolve_crop(conn, target)


async def _harvest_warnings(conn: asyncpg.Connection, crop_id: int) -> list[str]:
    row = await conn.fetchrow(
        "SELECT expected_harvest FROM crops WHERE id=$1 AND greenhouse_id=$2",
        crop_id,
        DEFAULT_GREENHOUSE,
    )
    if not row or row["expected_harvest"] is None:
        return []
    if row["expected_harvest"] > datetime.now(UTC).date():
        return [f"Expected harvest is {row['expected_harvest']}."]
    return []


async def _annotate_latest_crop_event(
    conn: asyncpg.Connection,
    crop_id: int,
    event_type: str,
    req: SlackCommandRequest,
    *,
    notes: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO crop_events (
            crop_id, event_type, operator, source, notes, greenhouse_id,
            slack_channel_id, slack_message_ts, slack_thread_ts, slack_user_id
        )
        VALUES ($1,$2,$3,'slack',$4,$5,$6,$7,$8,$9)
        """,
        crop_id,
        event_type,
        req.slack_user_name or req.slack_user_id or "Slack",
        notes,
        DEFAULT_GREENHOUSE,
        req.channel_id,
        req.message_ts,
        req.thread_ts,
        req.slack_user_id,
    )


async def _complete_related_tasks(
    conn: asyncpg.Connection,
    crop_id: int,
    req: SlackCommandRequest,
    *,
    related_field: str | None,
    related_id: int | None = None,
) -> None:
    related_sql = ""
    params: list[Any] = [crop_id, req.slack_user_name or req.slack_user_id or "Slack"]
    if related_field and related_id is not None:
        related_sql = f", {related_field} = $3"
        params.append(related_id)
    await conn.execute(
        f"""
        UPDATE crop_tasks
           SET status='completed',
               completed_at=now(),
               completed_by=$2
               {related_sql}
         WHERE crop_id=$1
           AND status='open'
        """,
        *params,
    )


async def _prepare_confirmation_intent(conn: asyncpg.Connection, intent: SlackParsedIntent) -> SlackParsedIntent:
    """Resolve risky writes into exact entities before creating a confirmation."""

    if intent.name == "alert.snooze":
        alert = await conn.fetchrow("SELECT id, severity FROM alert_log WHERE id=$1", int(intent.args["alert_id"]))
        if alert and alert["severity"] == "critical":
            return intent.model_copy(update={"requires_confirmation": True})
        return intent

    if not intent.requires_confirmation:
        return intent

    if intent.name in {"crop.clear", "crop.transplant", "crop.harvest"}:
        crop = await _resolve_crop(conn, str(intent.args["target"]))
        if not crop:
            raise ValueError(f"Could not resolve crop or position `{intent.args['target']}`.")
        args = {**intent.args, **_crop_identity(crop)}
        target_id = str(crop["id"])
        if intent.name == "crop.transplant":
            target_position = await _resolve_position(conn, str(intent.args["position"]))
            if not target_position:
                raise ValueError(f"Target position `{intent.args['position']}` was not found.")
            if target_position["is_occupied"] and target_position["crop_id"] != crop["id"]:
                raise ValueError(
                    f"Target position `{target_position['position_label']}` is occupied by {target_position['crop_name']}."
                )
            args.update(
                {
                    "new_position_id": target_position["position_id"],
                    "new_position": target_position["position_label"],
                    "new_zone_id": target_position["zone_id"],
                    "new_zone": target_position["zone_slug"],
                }
            )
        elif intent.name == "crop.harvest":
            args.update(_normalized_harvest_fields(intent.args))
            warnings = await _harvest_warnings(conn, int(crop["id"]))
            if warnings:
                args["harvest_warning"] = " ".join(warnings)
        return intent.model_copy(update={"args": args, "target_type": "crop", "target_id": target_id})

    if intent.name in {"alert.false_positive", "plan.trigger"}:
        return intent

    return intent


async def _fetch_role(conn: asyncpg.Connection, slack_user_id: str | None, slack_team_id: str | None) -> SlackRole:
    if not slack_user_id:
        return "viewer"
    row = await conn.fetchrow(
        """
        SELECT role
          FROM slack_user_roles
         WHERE greenhouse_id = $1
           AND slack_user_id = $2
           AND is_active
           AND ($3::text IS NULL OR slack_team_id IS NULL OR slack_team_id = $3)
         ORDER BY updated_at DESC
         LIMIT 1
        """,
        DEFAULT_GREENHOUSE,
        slack_user_id,
        slack_team_id,
    )
    role = row["role"] if row else "viewer"
    return role if role in {"viewer", "operator", "grower", "coordinator"} else "viewer"


async def _audit_if_needed(
    conn: asyncpg.Connection | None,
    req: SlackCommandRequest,
    intent: SlackParsedIntent,
    role: SlackRole,
    status: str,
    *,
    response_text: str | None = None,
) -> int | None:
    if conn is None:
        return None
    return await conn.fetchval(
        """
        INSERT INTO slack_command_audit (
            greenhouse_id, channel_id, channel_name, message_ts, thread_ts,
            slack_team_id, slack_user_id, slack_user_name, role, command_text,
            normalized_intent, status, requires_confirmation, target_type,
            target_id, response_text, raw_event, model_routing
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::jsonb,$18)
        RETURNING id
        """,
        DEFAULT_GREENHOUSE,
        req.channel_id,
        req.channel_name,
        req.message_ts,
        req.thread_ts,
        req.slack_team_id,
        req.slack_user_id,
        req.slack_user_name,
        role,
        req.command_text,
        intent.name,
        status,
        intent.requires_confirmation,
        intent.target_type,
        intent.target_id,
        response_text,
        json.dumps(req.raw_event),
        "openclaw_ai" if intent.requires_ai else "deterministic",
    )


async def _finish_audit(
    conn: asyncpg.Connection,
    audit_id: int | None,
    status: str,
    *,
    response_text: str | None = None,
    confirmation_id: Any = None,
    record_type: str | None = None,
    record_id: str | None = None,
    error: str | None = None,
) -> None:
    if audit_id is None:
        return
    await conn.execute(
        """
        UPDATE slack_command_audit
           SET status = $2,
               response_text = COALESCE($3, response_text),
               confirmation_id = COALESCE($4::uuid, confirmation_id),
               record_type = COALESCE($5, record_type),
               record_id = COALESCE($6, record_id),
               error = COALESCE($7, error)
         WHERE id = $1
        """,
        audit_id,
        status,
        response_text,
        str(confirmation_id) if confirmation_id else None,
        record_type,
        record_id,
        error,
    )


async def _create_confirmation(
    conn: asyncpg.Connection,
    req: SlackCommandRequest,
    intent: SlackParsedIntent,
    audit_id: int | None,
) -> str:
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    confirmation_text = f"Confirm `{intent.name}` for `{intent.target_id or intent.target_type or 'target'}`"
    return str(
        await conn.fetchval(
            """
            INSERT INTO slack_confirmation_requests (
                expires_at, greenhouse_id, slack_team_id, slack_user_id, channel_id,
                message_ts, thread_ts, normalized_intent, target_type, target_id,
                payload, command_audit_id, confirmation_text
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13)
            RETURNING id
            """,
            expires_at,
            DEFAULT_GREENHOUSE,
            req.slack_team_id,
            req.slack_user_id or "unknown",
            req.channel_id,
            req.message_ts,
            req.thread_ts,
            intent.name,
            intent.target_type,
            intent.target_id,
            json.dumps(intent.args),
            audit_id,
            confirmation_text,
        )
    )


async def _execute(
    conn: asyncpg.Connection,
    req: SlackCommandRequest,
    intent: SlackParsedIntent,
    role: SlackRole,
    audit_id: int | None,
) -> SlackCommandResponse:
    name = intent.name
    if name == "status.get":
        return await _greenhouse_status(conn, intent, role)
    if name == "brief.get":
        return await _brief(conn, intent, role)
    if name == "plan.status.get":
        return await _plan_status(conn, intent, role)
    if name == "firmware.health.get":
        return await _firmware_health(conn, intent, role)
    if name == "crop.map.get":
        return await _planting_map(conn, intent, role, occupied_only=False)
    if name == "crop.empty_positions.get":
        return await _planting_map(conn, intent, role, occupied_only=False, empty_only=True)
    if name == "crop.harvest_due.get":
        return await _harvest_due(conn, intent, role)
    if name == "crop.scouting_due.get":
        return await _tasks_due(conn, intent, role, task_type="scouting")
    if name == "position.status.get":
        return await _position_status(conn, intent, role)
    if name == "zone.status.get":
        return await _zone_status(conn, intent, role)
    if name == "equipment.status.get":
        return await _equipment_status(conn, intent, role)
    if name == "sensor.status.get":
        return await _sensor_status(conn, intent, role)
    if name.startswith("alert."):
        return await _alert_action(conn, req, intent, role, audit_id)
    if name == "crop.observe":
        return await _record_observation(conn, req, intent, role)
    if name == "crop.create":
        return await _plant_crop(conn, req, intent, role)
    if name == "crop.treatment.record":
        return await _record_treatment(conn, req, intent, role)
    if name in {"crop.clear", "crop.transplant", "crop.harvest"}:
        return await _crop_lifecycle_action(conn, req, intent, role)
    if name in {"confirmation.confirm", "confirmation.cancel"}:
        return await _confirmation_action(conn, req, intent, role, audit_id)
    if name == "plan.trigger":
        text = "Planner trigger request recorded. Iris/OpenClaw should run the planner through the audited Hermes trigger path."
        return _response(True, True, intent, "executed", role, text)
    if intent.requires_ai:
        text = (
            "This needs Iris reasoning. Use the deterministic status/plan/crop tools for facts, then answer in thread."
        )
        return _response(True, False, intent, "parsed", role, text)
    text = f"Unsupported deterministic intent `{name}`."
    return _response(False, False, intent, "unsupported", role, text)


async def _greenhouse_status(
    conn: asyncpg.Connection, intent: SlackParsedIntent, role: SlackRole
) -> SlackCommandResponse:
    now = await conn.fetchrow("SELECT * FROM v_greenhouse_now LIMIT 1")
    alerts = await conn.fetch(
        """
        SELECT id, alert_type, severity, message
          FROM alert_log
         WHERE resolved_at IS NULL
           AND disposition IN ('open', 'acknowledged')
         ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, ts DESC
         LIMIT 5
        """
    )
    tasks = await conn.fetch("SELECT * FROM v_slack_crop_tasks_due LIMIT 5")
    if not now:
        return _response(False, True, intent, "not_found", role, "No current greenhouse snapshot is available.")
    row = dict(now)
    lines = [
        "*Greenhouse status*",
        f"Climate: {row.get('temp_avg')} F, RH {row.get('rh_avg')}%, VPD {row.get('vpd_avg')} kPa, state `{row.get('state')}`.",
        f"Outdoor: {row.get('outdoor_temp_f')} F, RH {row.get('outdoor_rh_pct')}%, wind {row.get('wind_mph')} mph.",
        f"Water today: {row.get('mister_water_today')} gal. Open alerts: {row.get('open_alerts')}.",
    ]
    if alerts:
        lines.append("Top alerts: " + "; ".join(f"#{a['id']} {a['severity']} {a['alert_type']}" for a in alerts))
    if tasks:
        lines.append(
            "Due crop tasks: "
            + "; ".join(f"#{t['id']} {t['task_type']} {t['crop_name'] or t['position_label']}" for t in tasks)
        )
    return _response(True, True, intent, "executed", role, "\n".join(lines), data={"greenhouse_now": row})


async def _brief(conn: asyncpg.Connection, intent: SlackParsedIntent, role: SlackRole) -> SlackCommandResponse:
    status = await _greenhouse_status(conn, intent, role)
    period = intent.args.get("period", "brief")
    due = await conn.fetch("SELECT * FROM v_slack_crop_tasks_due LIMIT 10")
    extra = "\n".join(f"- {r['task_type']}: {r['crop_name'] or r['position_label']} due {r['due_at']}" for r in due)
    text = f"*{period.title()} greenhouse brief*\n{status.text}"
    if extra:
        text += f"\nTasks due:\n{extra}"
    return status.model_copy(update={"text": text})


async def _plan_status(conn: asyncpg.Connection, intent: SlackParsedIntent, role: SlackRole) -> SlackCommandResponse:
    rows = await conn.fetch("SELECT * FROM v_active_plan ORDER BY parameter")
    latest = await conn.fetchrow(
        "SELECT * FROM plan_delivery_log ORDER BY COALESCE(delivered_at, created_at) DESC NULLS LAST, id DESC LIMIT 1"
    )
    if not rows:
        return _response(False, True, intent, "not_found", role, "No active plan rows are available.")
    first = rows[0]
    params = {
        r["parameter"]: r["value"] for r in rows if r["parameter"] in {"temp_low", "temp_high", "vpd_low", "vpd_high"}
    }
    text = (
        f"*Plan status*: `{first['plan_id']}` created {first['created_at']} with {len(rows)} active rows.\n"
        f"Bands/tunables: {params}\n"
        f"Latest delivery: {dict(latest) if latest else 'none'}"
    )
    return _response(True, True, intent, "executed", role, text, data={"active_rows": [dict(r) for r in rows[:40]]})


async def _firmware_health(
    conn: asyncpg.Connection, intent: SlackParsedIntent, role: SlackRole
) -> SlackCommandResponse:
    diag = await conn.fetchrow("SELECT * FROM diagnostics ORDER BY ts DESC LIMIT 1")
    state = await conn.fetchrow("SELECT * FROM v_greenhouse_now LIMIT 1")
    if not diag and not state:
        return _response(False, True, intent, "not_found", role, "No firmware diagnostics are available.")
    text = (
        "*Firmware health*\n"
        f"State: `{state['state'] if state else 'unknown'}`. "
        f"Uptime: {state['uptime_s'] if state else None}s. Heap: {state['heap_kb'] if state else None}. "
        f"WiFi RSSI: {state['wifi_rssi'] if state else None}."
    )
    return _response(True, True, intent, "executed", role, text, data={"diagnostics": dict(diag) if diag else {}})


async def _planting_map(
    conn: asyncpg.Connection,
    intent: SlackParsedIntent,
    role: SlackRole,
    *,
    occupied_only: bool = False,
    empty_only: bool = False,
) -> SlackCommandResponse:
    where = "WHERE greenhouse_id = $1"
    if occupied_only:
        where += " AND is_occupied"
    if empty_only:
        where += " AND NOT is_occupied"
    rows = await conn.fetch(
        f"SELECT * FROM v_position_current {where} ORDER BY zone_slug, shelf_slug, position_label LIMIT 80",
        DEFAULT_GREENHOUSE,
    )
    label = "Empty positions" if empty_only else "Planting map"
    if not rows:
        return _response(True, True, intent, "executed", role, f"{label}: none found.", data={"positions": []})
    chunks = []
    for row in rows[:30]:
        crop = row["crop_name"] or "empty"
        stage = f" ({row['crop_stage']})" if row["crop_stage"] else ""
        chunks.append(f"{row['position_label']}: {crop}{stage}")
    text = f"*{label}*\n" + "\n".join(chunks)
    if len(rows) > 30:
        text += f"\n...and {len(rows) - 30} more."
    return _response(True, True, intent, "executed", role, text, data={"positions": [dict(r) for r in rows]})


async def _harvest_due(conn: asyncpg.Connection, intent: SlackParsedIntent, role: SlackRole) -> SlackCommandResponse:
    rows = await conn.fetch(
        """
        SELECT id, name, variety, position, zone, expected_harvest, stage
          FROM crops
         WHERE greenhouse_id = $1 AND is_active AND expected_harvest <= CURRENT_DATE
         ORDER BY expected_harvest, zone, position
         LIMIT 30
        """,
        DEFAULT_GREENHOUSE,
    )
    if not rows:
        return _response(True, True, intent, "executed", role, "No active crops are due for harvest today.")
    text = "*Crops due for harvest*\n" + "\n".join(
        f"#{r['id']} {r['name']} {r['variety'] or ''} at {r['position']} due {r['expected_harvest']}" for r in rows
    )
    return _response(True, True, intent, "executed", role, text, data={"crops": [dict(r) for r in rows]})


async def _tasks_due(
    conn: asyncpg.Connection, intent: SlackParsedIntent, role: SlackRole, task_type: str
) -> SlackCommandResponse:
    rows = await conn.fetch("SELECT * FROM v_slack_crop_tasks_due WHERE task_type = $1 LIMIT 30", task_type)
    if not rows:
        return _response(True, True, intent, "executed", role, f"No `{task_type}` tasks are due.")
    text = f"*{task_type.title()} tasks due*\n" + "\n".join(
        f"#{r['id']} {r['crop_name'] or r['position_label']} due {r['due_at']}" for r in rows
    )
    return _response(True, True, intent, "executed", role, text, data={"tasks": [dict(r) for r in rows]})


async def _position_status(
    conn: asyncpg.Connection, intent: SlackParsedIntent, role: SlackRole
) -> SlackCommandResponse:
    label = str(intent.args["position"]).upper()
    row = await conn.fetchrow("SELECT * FROM v_position_current WHERE upper(position_label) = $1", label)
    if not row:
        return _response(False, True, intent, "not_found", role, f"Position `{label}` was not found.")
    obs = []
    if row["crop_id"]:
        obs = await conn.fetch(
            "SELECT id, ts, obs_type, health_score, severity, notes FROM observations WHERE crop_id=$1 ORDER BY ts DESC LIMIT 3",
            row["crop_id"],
        )
    text = f"*Position {label}*: {row['crop_name'] or 'empty'}"
    if row["crop_name"]:
        text += f" ({row['crop_stage']}, planted {row['crop_planted_date']})"
    if obs:
        text += "\nRecent observations: " + "; ".join(f"#{o['id']} {o['obs_type']} {o['health_score']}" for o in obs)
    return _response(
        True, True, intent, "executed", role, text, data={"position": dict(row), "observations": [dict(o) for o in obs]}
    )


async def _zone_status(conn: asyncpg.Connection, intent: SlackParsedIntent, role: SlackRole) -> SlackCommandResponse:
    zone = intent.args["zone"]
    rows = await conn.fetch("SELECT * FROM v_position_current WHERE zone_slug = $1 LIMIT 50", zone)
    if not rows:
        return _response(False, True, intent, "not_found", role, f"Zone `{zone}` was not found.")
    occupied = [r for r in rows if r["is_occupied"]]
    text = f"*Zone {zone}*: {len(occupied)} occupied positions, {len(rows) - len(occupied)} empty."
    if occupied:
        text += "\n" + "; ".join(f"{r['position_label']} {r['crop_name']}" for r in occupied[:12])
    return _response(True, True, intent, "executed", role, text, data={"positions": [dict(r) for r in rows]})


async def _equipment_status(
    conn: asyncpg.Connection, intent: SlackParsedIntent, role: SlackRole
) -> SlackCommandResponse:
    equipment = intent.args["equipment"]
    row = await conn.fetchrow(
        "SELECT * FROM v_equipment_now WHERE lower(equipment) = lower($1) OR lower(equipment) LIKE '%' || lower($1) || '%' ORDER BY equipment LIMIT 1",
        equipment,
    )
    if not row:
        return _response(False, True, intent, "not_found", role, f"Equipment `{equipment}` was not found.")
    text = f"*Equipment {row['equipment']}*: {'on' if row['state'] else 'off'} since {row['since']} ({row['seconds_ago']}s ago)."
    return _response(True, True, intent, "executed", role, text, data={"equipment": dict(row)})


async def _sensor_status(conn: asyncpg.Connection, intent: SlackParsedIntent, role: SlackRole) -> SlackCommandResponse:
    sensor = intent.args["sensor"]
    row = await conn.fetchrow(
        """
        SELECT sensor_id, description, type, zone, position, expected_interval_s,
               active, source_table, source_column
          FROM sensor_registry
         WHERE lower(sensor_id) = lower($1) OR lower(COALESCE(description, '')) LIKE '%' || lower($1) || '%'
         ORDER BY sensor_id
         LIMIT 1
        """,
        sensor,
    )
    if not row:
        return _response(False, True, intent, "not_found", role, f"Sensor `{sensor}` was not found.")
    state = "active" if row["active"] else "inactive"
    text = (
        f"*Sensor {row['sensor_id']}*: {state}, type={row['type']}, zone={row['zone']}, "
        f"source={row['source_table']}.{row['source_column']}, expected every {row['expected_interval_s']}s."
    )
    return _response(True, True, intent, "executed", role, text, data={"sensor": dict(row)})


async def _alert_action(
    conn: asyncpg.Connection,
    req: SlackCommandRequest,
    intent: SlackParsedIntent,
    role: SlackRole,
    audit_id: int | None,
) -> SlackCommandResponse:
    alert_id = int(intent.args["alert_id"])
    alert = await conn.fetchrow("SELECT id, severity, disposition FROM alert_log WHERE id=$1", alert_id)
    if not alert:
        return _response(False, True, intent, "not_found", role, f"Alert #{alert_id} was not found.")
    action = intent.name.split(".", 1)[1]
    if action == "ack":
        await conn.execute(
            "UPDATE alert_log SET disposition='acknowledged', acknowledged_at=now(), acknowledged_by=$2 WHERE id=$1 AND resolved_at IS NULL",
            alert_id,
            req.slack_user_name or req.slack_user_id or "slack",
        )
        action_name = "acknowledge"
    elif action == "snooze":
        snoozed_until = _duration_to_until(str(intent.args["duration"]))
        await conn.execute(
            "UPDATE alert_log SET slack_snoozed_until=$2, slack_snoozed_by=$3 WHERE id=$1",
            alert_id,
            snoozed_until,
            req.slack_user_name or req.slack_user_id,
        )
        action_name = "snooze"
    elif action == "assign":
        await conn.execute("UPDATE alert_log SET slack_assigned_to=$2 WHERE id=$1", alert_id, intent.args["assignee"])
        action_name = "assign"
        snoozed_until = None
    elif action == "note":
        await conn.execute(
            "UPDATE alert_log SET notes=concat_ws(E'\\n', notes, $2) WHERE id=$1", alert_id, intent.args["note"]
        )
        action_name = "note"
        snoozed_until = None
    elif action == "resolve":
        await conn.execute(
            "UPDATE alert_log SET disposition='resolved', resolved_at=now(), resolved_by=$2, resolution=COALESCE(resolution, 'resolved from Slack') WHERE id=$1",
            alert_id,
            req.slack_user_name or req.slack_user_id or "slack",
        )
        action_name = "resolve"
        snoozed_until = None
    elif action == "false_positive":
        await conn.execute(
            "UPDATE alert_log SET disposition='false_positive', resolved_at=now(), resolved_by=$2, resolution='false positive from Slack' WHERE id=$1",
            alert_id,
            req.slack_user_name or req.slack_user_id or "slack",
        )
        action_name = "false_positive"
        snoozed_until = None
    else:
        return _response(False, True, intent, "unsupported", role, f"Unsupported alert action `{action}`.")
    await conn.execute(
        """
        INSERT INTO slack_alert_actions (
            alert_id, action, slack_user_id, slack_user_name, channel_id, message_ts,
            thread_ts, note, snoozed_until, assigned_to, command_audit_id
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        """,
        alert_id,
        action_name,
        req.slack_user_id,
        req.slack_user_name,
        req.channel_id,
        req.message_ts,
        req.thread_ts,
        intent.args.get("note"),
        locals().get("snoozed_until"),
        intent.args.get("assignee"),
        audit_id,
    )
    return _response(
        True,
        True,
        intent,
        "executed",
        role,
        f"Alert #{alert_id} `{action_name}` recorded.",
        record_type="alert",
        record_id=str(alert_id),
    )


async def _crop_lifecycle_action(
    conn: asyncpg.Connection,
    req: SlackCommandRequest,
    intent: SlackParsedIntent,
    role: SlackRole,
) -> SlackCommandResponse:
    if intent.name == "crop.clear":
        return await _clear_crop(conn, req, intent, role)
    if intent.name == "crop.transplant":
        return await _transplant_crop(conn, req, intent, role)
    if intent.name == "crop.harvest":
        return await _harvest_crop(conn, req, intent, role)
    return _response(False, True, intent, "unsupported", role, f"Unsupported crop lifecycle action `{intent.name}`.")


async def _clear_crop(
    conn: asyncpg.Connection,
    req: SlackCommandRequest,
    intent: SlackParsedIntent,
    role: SlackRole,
) -> SlackCommandResponse:
    crop_id = int(intent.args.get("crop_id") or 0)
    crop = await _crop_by_id_or_target(conn, crop_id, str(intent.args.get("target") or ""))
    if not crop:
        return _response(False, True, intent, "not_found", role, "Crop was not found or is already inactive.")
    row = await conn.fetchrow(
        "UPDATE crops SET is_active = FALSE, updated_at = now() WHERE id = $1 AND is_active RETURNING id, cleared_at, name, position",
        int(crop["id"]),
    )
    if not row:
        return _response(False, True, intent, "not_found", role, f"Crop #{crop['id']} was already cleared.")
    await _annotate_latest_crop_event(
        conn,
        int(crop["id"]),
        "removed",
        req,
        notes=f"Cleared from Slack confirmation by {req.slack_user_name or req.slack_user_id or 'unknown'}",
    )
    await _complete_related_tasks(conn, int(crop["id"]), req, related_field=None)
    text = f"Cleared crop #{row['id']} `{row['name']}` from `{row['position']}`."
    return _response(True, True, intent, "executed", role, text, record_type="crop", record_id=str(row["id"]))


async def _transplant_crop(
    conn: asyncpg.Connection,
    req: SlackCommandRequest,
    intent: SlackParsedIntent,
    role: SlackRole,
) -> SlackCommandResponse:
    crop_id = int(intent.args.get("crop_id") or 0)
    crop = await _crop_by_id_or_target(conn, crop_id, str(intent.args.get("target") or ""))
    if not crop:
        return _response(False, True, intent, "not_found", role, "Active crop was not found.")
    target = await _resolve_position(conn, str(intent.args.get("new_position") or intent.args.get("position")))
    if not target:
        return _response(
            False, True, intent, "not_found", role, f"Position `{intent.args.get('position')}` was not found."
        )
    if target["is_occupied"] and target["crop_id"] != crop["id"]:
        return _response(
            False,
            True,
            intent,
            "ambiguous",
            role,
            f"Position `{target['position_label']}` is occupied by {target['crop_name']}.",
        )

    old_position = crop["position"]
    await conn.execute(
        """
        UPDATE crops
           SET position_id = $1,
               zone_id = $2,
               position = $3,
               zone = $4,
               updated_at = now()
         WHERE id = $5 AND is_active
        """,
        target["position_id"],
        target["zone_id"],
        target["position_label"],
        target["zone_slug"],
        int(crop["id"]),
    )
    event_id = await conn.fetchval(
        """
        INSERT INTO crop_events (
            crop_id, event_type, operator, source, notes, greenhouse_id, position_id,
            slack_channel_id, slack_message_ts, slack_thread_ts, slack_user_id
        )
        VALUES ($1, 'transplanted', $2, 'slack', $3, $4, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        int(crop["id"]),
        req.slack_user_name or req.slack_user_id or "Slack",
        f"Transplanted from {old_position} to {target['position_label']}",
        DEFAULT_GREENHOUSE,
        target["position_id"],
        req.channel_id,
        req.message_ts,
        req.thread_ts,
        req.slack_user_id,
    )
    text = f"Transplanted crop #{crop['id']} `{crop['name']}` from `{old_position}` to `{target['position_label']}`."
    return _response(True, True, intent, "executed", role, text, record_type="crop_event", record_id=str(event_id))


async def _harvest_crop(
    conn: asyncpg.Connection,
    req: SlackCommandRequest,
    intent: SlackParsedIntent,
    role: SlackRole,
) -> SlackCommandResponse:
    crop_id = int(intent.args.get("crop_id") or 0)
    crop = await _crop_by_id_or_target(conn, crop_id, str(intent.args.get("target") or ""))
    if not crop:
        return _response(False, True, intent, "not_found", role, "Crop was not found.")
    fields = _normalized_harvest_fields(intent.args)
    row = await conn.fetchrow(
        """
        INSERT INTO harvests (
            crop_id, weight_kg, unit_count, quality_grade,
            salable_weight_kg, cull_weight_kg, cull_reason, quality_reason,
            zone, destination, labor_minutes, operator, notes, position_id,
            greenhouse_id, slack_channel_id, slack_message_ts, slack_thread_ts, slack_user_id
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
        RETURNING id
        """,
        int(crop["id"]),
        fields.get("weight_kg"),
        fields.get("unit_count"),
        fields.get("quality_grade"),
        fields.get("salable_weight_kg"),
        fields.get("cull_weight_kg"),
        fields.get("cull_reason"),
        fields.get("quality_reason"),
        crop["zone"],
        fields.get("destination"),
        fields.get("labor_minutes"),
        req.slack_user_name or req.slack_user_id or "Slack",
        fields.get("notes"),
        crop["position_id"],
        DEFAULT_GREENHOUSE,
        req.channel_id,
        req.message_ts,
        req.thread_ts,
        req.slack_user_id,
    )
    event_id = await conn.fetchval(
        """
        INSERT INTO crop_events (
            crop_id, event_type, operator, source, notes, greenhouse_id, position_id,
            slack_channel_id, slack_message_ts, slack_thread_ts, slack_user_id
        )
        VALUES ($1, 'harvested', $2, 'slack', $3, $4, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        int(crop["id"]),
        req.slack_user_name or req.slack_user_id or "Slack",
        f"Harvest recorded from Slack: harvest #{row['id']}",
        DEFAULT_GREENHOUSE,
        crop["position_id"],
        req.channel_id,
        req.message_ts,
        req.thread_ts,
        req.slack_user_id,
    )
    await conn.execute(
        """
        UPDATE crop_tasks
           SET status='completed', completed_at=now(), completed_by=$2, related_harvest_id=$3
         WHERE crop_id=$1 AND status='open' AND task_type IN ('harvest_due', 'harvest_overdue')
        """,
        int(crop["id"]),
        req.slack_user_name or req.slack_user_id or "Slack",
        int(row["id"]),
    )
    text = f"Recorded harvest #{row['id']} for crop #{crop['id']} `{crop['name']}` at `{crop['position']}`."
    return _response(
        True,
        True,
        intent,
        "executed",
        role,
        text,
        record_type="harvest",
        record_id=str(row["id"]),
        data={"event_id": event_id},
    )


async def _record_observation(
    conn: asyncpg.Connection,
    req: SlackCommandRequest,
    intent: SlackParsedIntent,
    role: SlackRole,
) -> SlackCommandResponse:
    crop = await _resolve_crop(conn, intent.args["target"])
    if not crop:
        return _response(
            False, True, intent, "not_found", role, f"Could not resolve crop or position `{intent.args['target']}`."
        )
    row = await conn.fetchrow(
        """
        INSERT INTO observations (
            crop_id, greenhouse_id, zone, position, zone_id, position_id, obs_type,
            notes, severity, observer, health_score, affected_pct, source,
            slack_channel_id, slack_message_ts, slack_thread_ts, slack_user_id
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,'slack',$13,$14,$15,$16)
        RETURNING id
        """,
        crop["id"],
        DEFAULT_GREENHOUSE,
        crop["zone"],
        crop["position"],
        crop["zone_id"],
        crop["position_id"],
        intent.args.get("obs_type", "health_check"),
        intent.args.get("notes"),
        intent.args.get("severity"),
        req.slack_user_name or req.slack_user_id or "Slack",
        intent.args.get("health_score"),
        intent.args.get("affected_pct"),
        req.channel_id,
        req.message_ts,
        req.thread_ts,
        req.slack_user_id,
    )
    return _response(
        True,
        True,
        intent,
        "executed",
        role,
        f"Recorded observation #{row['id']} for {crop['name']} at {crop['position']}.",
        record_type="observation",
        record_id=str(row["id"]),
    )


async def _plant_crop(
    conn: asyncpg.Connection,
    req: SlackCommandRequest,
    intent: SlackParsedIntent,
    role: SlackRole,
) -> SlackCommandResponse:
    pos = await conn.fetchrow(
        "SELECT * FROM v_position_current WHERE upper(position_label)=$1", intent.args["position"]
    )
    if not pos:
        return _response(False, True, intent, "not_found", role, f"Position `{intent.args['position']}` was not found.")
    if pos["is_occupied"]:
        return _response(
            False,
            True,
            intent,
            "ambiguous",
            role,
            f"Position `{intent.args['position']}` is already occupied by {pos['crop_name']}.",
        )
    row = await conn.fetchrow(
        """
        INSERT INTO crops (
            name, position, zone, planted_date, stage, count, greenhouse_id,
            position_id, zone_id, notes
        )
        VALUES ($1,$2,$3,CURRENT_DATE,$4,$5,$6,$7,$8,$9)
        RETURNING id, name
        """,
        intent.args["crop"],
        pos["position_label"],
        pos["zone_slug"],
        intent.args.get("stage") or "seedling",
        intent.args.get("count"),
        DEFAULT_GREENHOUSE,
        pos["position_id"],
        pos["zone_id"],
        f"Created from Slack by {req.slack_user_name or req.slack_user_id or 'unknown'}",
    )
    return _response(
        True,
        True,
        intent,
        "executed",
        role,
        f"Planted crop #{row['id']} `{row['name']}` at {pos['position_label']}.",
        record_type="crop",
        record_id=str(row["id"]),
    )


async def _record_treatment(
    conn: asyncpg.Connection,
    req: SlackCommandRequest,
    intent: SlackParsedIntent,
    role: SlackRole,
) -> SlackCommandResponse:
    crop = await _resolve_crop(conn, intent.args["target"])
    if not crop:
        return _response(
            False, True, intent, "not_found", role, f"Could not resolve crop or position `{intent.args['target']}`."
        )
    notes = str(intent.args.get("notes") or "")
    product = notes.split(",", 1)[0].strip()[:200] or "Slack-recorded treatment"
    row = await conn.fetchrow(
        """
        INSERT INTO treatments (
            product, crop_id, position_id, greenhouse_id, zone, applicator, notes,
            slack_channel_id, slack_message_ts, slack_thread_ts, slack_user_id
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        RETURNING id
        """,
        product,
        crop["id"],
        crop["position_id"],
        DEFAULT_GREENHOUSE,
        crop["zone"],
        req.slack_user_name or req.slack_user_id or "Slack",
        notes,
        req.channel_id,
        req.message_ts,
        req.thread_ts,
        req.slack_user_id,
    )
    return _response(
        True,
        True,
        intent,
        "executed",
        role,
        f"Recorded treatment #{row['id']} for {crop['name']}.",
        record_type="treatment",
        record_id=str(row["id"]),
    )


async def _confirmation_action(
    conn: asyncpg.Connection,
    req: SlackCommandRequest,
    intent: SlackParsedIntent,
    role: SlackRole,
    audit_id: int | None,
) -> SlackCommandResponse:
    confirmation_id = intent.args["confirmation_id"]
    row = await conn.fetchrow("SELECT * FROM slack_confirmation_requests WHERE id=$1::uuid", confirmation_id)
    if not row:
        return _response(False, True, intent, "not_found", role, f"Confirmation `{confirmation_id}` was not found.")
    if not req.slack_user_id or row["slack_user_id"] != req.slack_user_id:
        return _response(False, True, intent, "denied", role, "Only the original requester can use this confirmation.")
    if intent.name == "confirmation.cancel":
        if row["status"] != "pending":
            return _response(False, True, intent, "denied", role, f"Confirmation `{confirmation_id}` is not pending.")
        await conn.execute(
            "UPDATE slack_confirmation_requests SET status='canceled', canceled_at=now() WHERE id=$1::uuid",
            confirmation_id,
        )
        if row["command_audit_id"]:
            await _finish_audit(
                conn,
                int(row["command_audit_id"]),
                "denied",
                response_text=f"Canceled confirmation `{confirmation_id}`.",
                confirmation_id=confirmation_id,
            )
        return _response(
            True,
            True,
            intent,
            "executed",
            role,
            f"Canceled confirmation `{confirmation_id}`.",
            confirmation_id=confirmation_id,
        )
    if row["status"] != "pending" or row["expires_at"] <= datetime.now(UTC):
        await conn.execute(
            "UPDATE slack_confirmation_requests SET status='expired' WHERE id=$1::uuid AND status='pending'",
            confirmation_id,
        )
        return _response(False, True, intent, "denied", role, f"Confirmation `{confirmation_id}` is not pending.")

    target_intent = _intent_from_confirmation(row)
    if not role_allows(role, target_intent.required_role):
        text = (
            f"Denied: confirmation target `{target_intent.name}` requires `{target_intent.required_role}` role; "
            f"your role is `{role}`."
        )
        if row["command_audit_id"]:
            await _finish_audit(
                conn,
                int(row["command_audit_id"]),
                "denied",
                response_text=text,
                confirmation_id=confirmation_id,
            )
        return _response(False, True, target_intent, "denied", role, text, confirmation_id=confirmation_id)

    await conn.execute(
        "UPDATE slack_confirmation_requests SET status='confirmed', confirmed_at=now() WHERE id=$1::uuid",
        confirmation_id,
    )
    result = await _execute(conn, req, target_intent, role, audit_id)
    if row["command_audit_id"]:
        await _finish_audit(
            conn,
            int(row["command_audit_id"]),
            result.status,
            response_text=result.text,
            confirmation_id=confirmation_id,
            record_type=result.record_type,
            record_id=result.record_id,
            error="; ".join(result.errors) if result.errors else None,
        )
    payload = result.model_dump(mode="python")
    payload["confirmation_id"] = confirmation_id
    return SlackCommandResponse.model_validate(payload)


def _intent_from_confirmation(row: Any) -> SlackParsedIntent:
    payload = row["payload"] or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    intent_name = row["normalized_intent"]
    required_role: SlackRole = "viewer"
    write = False
    if str(intent_name).startswith("alert."):
        required_role = "coordinator" if intent_name == "alert.false_positive" else "operator"
        write = True
    elif str(intent_name).startswith("crop."):
        required_role = "grower"
        write = True
    elif intent_name == "plan.trigger":
        required_role = "operator"
        write = True
    return SlackParsedIntent.model_validate(
        {
            "name": intent_name,
            "args": payload,
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "required_role": required_role,
            "write": write,
            "requires_confirmation": False,
            "requires_ai": False,
            "unsafe_blocked": False,
            "reason": None,
        }
    )


async def _resolve_crop(conn: asyncpg.Connection, target: str) -> asyncpg.Record | None:
    cleaned = target.strip()
    if cleaned.isdigit():
        return await conn.fetchrow(
            "SELECT * FROM crops WHERE id=$1::int AND greenhouse_id=$2", int(cleaned), DEFAULT_GREENHOUSE
        )
    return await conn.fetchrow(
        """
        SELECT c.*
          FROM crops c
          LEFT JOIN v_position_current p ON p.crop_id = c.id
         WHERE c.greenhouse_id = $1
           AND c.is_active
           AND (
                upper(c.position) = upper($2)
             OR upper(p.position_label) = upper($2)
             OR lower(c.name) LIKE '%' || lower($2) || '%'
           )
         ORDER BY CASE WHEN upper(c.position) = upper($2) THEN 0 ELSE 1 END, c.id DESC
         LIMIT 1
        """,
        DEFAULT_GREENHOUSE,
        cleaned,
    )


def _duration_to_until(value: str) -> datetime:
    match = re.search(r"(\d+)\s*([a-z]+)", value.lower())
    if not match:
        return datetime.now(UTC) + timedelta(hours=2)
    amount = int(match.group(1))
    unit = match.group(2)
    if unit.startswith("m"):
        return datetime.now(UTC) + timedelta(minutes=amount)
    if unit.startswith("d"):
        return datetime.now(UTC) + timedelta(days=amount)
    return datetime.now(UTC) + timedelta(hours=amount)
