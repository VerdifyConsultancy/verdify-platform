"""Deterministic execution path for Iris Slack commands."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from slack_config import SlackSettings, load_slack_settings
from slack_ops.briefs import build_operator_brief
from slack_ops.intents import parse_command, role_allows
from verdify_schemas.slack_ops import SlackCommandRequest, SlackCommandResponse, SlackParsedIntent


def _env_password() -> str:
    for path in ("/run/secrets/postgres_password", "/etc/verdify/postgres_password"):
        try:
            if os.path.exists(path):
                return open(path, encoding="utf-8").read().strip()
        except OSError:
            pass
    return os.environ.get("DB_PASSWORD") or os.environ.get("POSTGRES_PASSWORD", "verdify")


def get_db_dsn() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    name = os.environ.get("DB_NAME", "verdify")
    user = os.environ.get("DB_USER", "verdify")
    return f"postgresql://{user}:{_env_password()}@{host}:{port}/{name}"


def _dict(row: Any | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _text_response(
    text: str,
    *,
    ok: bool = True,
    status: str = "executed",
    intent: SlackParsedIntent | None = None,
    confirmation_id: UUID | None = None,
    record_type: str | None = None,
    record_id: str | None = None,
    routing: str = "deterministic",
) -> SlackCommandResponse:
    return SlackCommandResponse(
        ok=ok,
        text=text,
        status=status,
        normalized_intent=intent.normalized_intent if intent else None,
        requires_confirmation=bool(intent.requires_confirmation) if intent else False,
        confirmation_id=confirmation_id,
        record_type=record_type,
        record_id=record_id,
        model_routing=routing,  # type: ignore[arg-type]
    )


async def handle_slack_command(
    request: SlackCommandRequest,
    *,
    dsn: str | None = None,
    settings: SlackSettings | None = None,
    role_override: str | None = None,
    conn: asyncpg.Connection | None = None,
) -> SlackCommandResponse:
    """Parse, authorize, audit, and execute a Slack command."""

    settings = settings or load_slack_settings()
    own_conn = conn is None
    db = conn or await asyncpg.connect(dsn or get_db_dsn())
    try:
        role = role_override or await _fetch_role(db, request, settings)
        intent = parse_command(request.text)
        audit_id = await _insert_audit(db, request, intent, role, settings)

        if intent.blocked_reason:
            response = _text_response(intent.blocked_reason, ok=False, status="denied", intent=intent)
            await _finish_audit(db, audit_id, response, error=intent.blocked_reason)
            return response

        if intent.normalized_intent == "unknown":
            response = _text_response(
                "I could not map that to a deterministic greenhouse operation. Ask OpenClaw/Iris to reason, or use a supported command.",
                ok=False,
                status="failed",
                intent=intent,
                routing="openclaw",
            )
            await _finish_audit(db, audit_id, response, error="unknown_intent")
            return response

        if not role_allows(role, intent.required_role):
            response = _text_response(
                f"Denied: `{intent.normalized_intent}` requires `{intent.required_role}` role; your mapped role is `{role}`.",
                ok=False,
                status="denied",
                intent=intent,
            )
            await _finish_audit(db, audit_id, response, error="role_denied")
            return response

        if intent.requires_confirmation and intent.normalized_intent not in {
            "confirmation.confirm",
            "confirmation.cancel",
        }:
            prepared = await _prepare_confirmation_intent(db, intent, request)
            confirmation_id = await _create_confirmation(db, request, prepared, settings, audit_id)
            response = _text_response(
                _confirmation_text(prepared, confirmation_id),
                status="needs_confirmation",
                intent=prepared,
                confirmation_id=confirmation_id,
            )
            await _finish_audit(db, audit_id, response)
            return response

        response = await _execute(db, request, intent, role, audit_id, settings)
        await _finish_audit(db, audit_id, response)
        return response
    except Exception as exc:
        response = _text_response(f"Slack command failed: {exc}", ok=False, status="failed")
        try:
            await _finish_audit(db, locals().get("audit_id"), response, error=str(exc))
        except Exception:
            pass
        return response
    finally:
        if own_conn:
            await db.close()


async def _fetch_role(conn, req: SlackCommandRequest, settings: SlackSettings) -> str:
    row = await conn.fetchrow(
        """
        SELECT role
          FROM slack_user_roles
         WHERE greenhouse_id = $1
           AND slack_user_id = $2
           AND is_active
         ORDER BY updated_at DESC
         LIMIT 1
        """,
        settings.greenhouse_id,
        req.slack_user_id,
    )
    return row["role"] if row else settings.default_role


async def _insert_audit(conn, req: SlackCommandRequest, intent: SlackParsedIntent, role: str, settings: SlackSettings):
    row = await conn.fetchrow(
        """
        INSERT INTO slack_command_audit (
            greenhouse_id, channel_id, channel_name, message_ts, thread_ts,
            slack_team_id, slack_user_id, slack_user_name, role, command_text,
            normalized_intent, status, requires_confirmation, target_type,
            target_id, raw_event, model_routing, handled_by
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'parsed',$12,$13,$14,$15::jsonb,'deterministic','python')
        RETURNING id
        """,
        settings.greenhouse_id,
        req.channel_id,
        req.channel_name,
        req.message_ts,
        req.thread_ts,
        req.slack_team_id,
        req.slack_user_id,
        req.slack_user_name,
        role,
        req.text,
        intent.normalized_intent,
        intent.requires_confirmation,
        intent.target_type,
        intent.target_id,
        json.dumps(req.raw_event or {}),
    )
    return row["id"] if row else None


async def _finish_audit(conn, audit_id, response: SlackCommandResponse, *, error: str | None = None) -> None:
    if audit_id is None:
        return
    await conn.execute(
        """
        UPDATE slack_command_audit
           SET status = $2,
               confirmation_id = $3,
               record_type = $4,
               record_id = $5,
               response_text = $6,
               error = $7,
               model_routing = $8
         WHERE id = $1
        """,
        audit_id,
        response.status,
        response.confirmation_id,
        response.record_type,
        response.record_id,
        response.text,
        error,
        response.model_routing,
    )


async def _prepare_confirmation_intent(conn, intent: SlackParsedIntent, req: SlackCommandRequest) -> SlackParsedIntent:
    args = dict(intent.args)
    target_type = intent.target_type
    target_id = intent.target_id

    if intent.normalized_intent.startswith("crop."):
        crop = await _resolve_crop(conn, args.get("target") or args.get("name") or req.text)
        if crop:
            args["crop_id"] = crop["id"]
            args["crop_name"] = crop["name"]
            target_type = "crop"
            target_id = str(crop["id"])
    if intent.normalized_intent == "crop.transplant" and args.get("position"):
        position = await _resolve_position(conn, args["position"])
        if position:
            args["position_id"] = position["position_id"]
            args["position_label"] = position["position_label"]
    if intent.normalized_intent == "crop.harvest":
        args.update(_normalized_harvest_fields(args))
        warnings = await _harvest_warnings(conn, args)
        if warnings:
            args["warnings"] = warnings
    if intent.normalized_intent.startswith("alert."):
        target_type = "alert"
        target_id = str(args.get("alert_id") or "")

    return intent.model_copy(update={"args": args, "target_type": target_type, "target_id": target_id})


def _confirmation_text(intent: SlackParsedIntent, confirmation_id: UUID) -> str:
    action = intent.normalized_intent.replace(".", " ")
    target = intent.args.get("crop_name") or intent.args.get("target") or intent.target_id or "target"
    warnings = intent.args.get("warnings") or []
    warning_text = "\n" + "\n".join(f"- {w}" for w in warnings) if warnings else ""
    return f"Confirm `{action}` for `{target}` with `confirm {confirmation_id}`. Cancel with `cancel {confirmation_id}`.{warning_text}"


async def _create_confirmation(
    conn,
    req: SlackCommandRequest,
    intent: SlackParsedIntent,
    settings: SlackSettings,
    audit_id,
) -> UUID:
    confirmation_text = "pending"
    row = await conn.fetchrow(
        """
        INSERT INTO slack_confirmation_requests (
            expires_at, greenhouse_id, slack_team_id, slack_user_id, channel_id,
            message_ts, thread_ts, normalized_intent, target_type, target_id,
            payload, status, command_audit_id, confirmation_text
        )
        VALUES (
            now() + interval '10 minutes', $1, $2, $3, $4, $5, $6, $7,
            $8, $9, $10::jsonb, 'pending', $11, $12
        )
        RETURNING id
        """,
        settings.greenhouse_id,
        req.slack_team_id,
        req.slack_user_id,
        req.channel_id,
        req.message_ts,
        req.thread_ts,
        intent.normalized_intent,
        intent.target_type,
        intent.target_id,
        json.dumps(intent.args),
        audit_id,
        confirmation_text,
    )
    return row["id"]


async def _execute(
    conn,
    req: SlackCommandRequest,
    intent: SlackParsedIntent,
    role: str,
    audit_id,
    settings: SlackSettings,
) -> SlackCommandResponse:
    name = intent.normalized_intent
    if name in {"confirmation.confirm", "confirmation.cancel"}:
        return await _confirmation_action(conn, req, intent, role, audit_id, settings)
    if name == "status.get":
        return await _status(conn, intent)
    if name == "brief.get":
        text, _data = await build_operator_brief(
            conn,
            intent.args.get("period") or "current",
            timezone=settings.timezone,
        )
        return _text_response(text, intent=intent)
    if name == "plan.status.get":
        return await _plan_status(conn, intent)
    if name == "firmware.health.get":
        return await _firmware_health(conn, intent)
    if name == "zone.status.get":
        return await _zone_status(conn, intent)
    if name == "position.status.get":
        return await _position_status(conn, intent)
    if name == "equipment.status.get":
        return await _equipment_status(conn, intent)
    if name == "sensor.status.get":
        return await _sensor_status(conn, intent)
    if name == "crop.map.get":
        return await _crop_map(conn, intent)
    if name == "crop.empty_positions.get":
        return await _empty_positions(conn, intent)
    if name == "crop.harvest_due.get":
        return await _harvest_due(conn, intent)
    if name == "crop.scouting_due.get":
        return await _scouting_due(conn, intent)
    if name.startswith("alert."):
        return await _alert_action(conn, req, intent, audit_id)
    if name == "crop.observe":
        return await _crop_observe(conn, req, intent)
    if name == "crop.create":
        return await _crop_create(conn, req, intent)
    if name == "crop.clear":
        return await _crop_clear(conn, req, intent)
    if name == "crop.transplant":
        return await _crop_transplant(conn, req, intent)
    if name == "crop.harvest":
        return await _crop_harvest(conn, req, intent)
    if name == "crop.treatment.record":
        return await _crop_treatment(conn, req, intent)
    if name == "plan.trigger":
        row = await conn.fetchrow(
            """
            INSERT INTO planner_trigger_ledger (
                greenhouse_id, event_type, event_label, instance, expected_at,
                due_at, status, expected_action, trigger_id, notes
            )
            VALUES ('vallery', 'MANUAL', 'slack', 'slack', now(), now(), 'pending', 'plan', gen_random_uuid(), $1)
            RETURNING id
            """,
            json.dumps({"reason": intent.args.get("reason"), "slack_user_id": req.slack_user_id}),
        )
        return _text_response(
            f"Planner trigger queued from Slack (`{row['id']}`).",
            intent=intent,
            record_type="planner_trigger_ledger",
            record_id=str(row["id"]),
        )
    return _text_response("Unsupported deterministic command.", ok=False, status="failed", intent=intent)


async def _status(conn, intent):
    row = _dict(await conn.fetchrow("SELECT * FROM v_greenhouse_now LIMIT 1"))
    if not row:
        return _text_response(
            "No current greenhouse status row is available.", ok=False, status="failed", intent=intent
        )
    text = (
        "*Greenhouse status*: "
        f"{row.get('temp_avg', 'n/a')}F, RH {row.get('rh_avg', 'n/a')}%, "
        f"VPD {row.get('vpd_avg', 'n/a')} kPa, state `{row.get('state') or 'unknown'}`"
    )
    return _text_response(text, intent=intent)


async def _plan_status(conn, intent):
    row = await conn.fetchrow(
        "SELECT plan_id, created_at, source, planner_instance FROM plan_journal ORDER BY created_at DESC LIMIT 1"
    )
    if not row:
        return _text_response("No plan_journal rows found.", ok=False, status="failed", intent=intent)
    return _text_response(
        f"Latest plan `{row['plan_id']}` at {row['created_at']:%m-%d %H:%M UTC} from {row['source']}/{row['planner_instance']}.",
        intent=intent,
    )


async def _firmware_health(conn, intent):
    raw_row = await conn.fetchrow("SELECT * FROM diagnostics ORDER BY ts DESC LIMIT 1")
    row = _dict(raw_row)
    if not row:
        return _text_response("No firmware diagnostics row found.", ok=False, status="failed", intent=intent)
    return _text_response(
        f"Firmware diagnostics: ts {row['ts']:%m-%d %H:%M UTC}, "
        f"uptime {row.get('uptime_s')}s, heap {row.get('heap_bytes') or row.get('heap_min_free_kb')}.",
        intent=intent,
    )


async def _zone_status(conn, intent):
    rows = await conn.fetch(
        """
        SELECT zone_slug, count(*) AS positions,
               count(*) FILTER (WHERE is_occupied) AS occupied,
               string_agg(position_label || ':' || COALESCE(crop_name, 'empty'), ', ' ORDER BY position_label) AS map
          FROM v_position_current
         WHERE lower(zone_slug) = lower($1)
         GROUP BY zone_slug
        """,
        intent.args.get("zone"),
    )
    if not rows:
        return _text_response("No matching zone found.", ok=False, status="failed", intent=intent)
    r = rows[0]
    return _text_response(
        f"Zone `{r['zone_slug']}`: {r['occupied']}/{r['positions']} occupied. {r['map']}", intent=intent
    )


async def _position_status(conn, intent):
    row = await _resolve_position(conn, intent.args.get("position") or "")
    if not row:
        return _text_response("No matching position found.", ok=False, status="failed", intent=intent)
    crop = row.get("crop_name") or "empty"
    return _text_response(f"Position `{row['position_label']}` in `{row['zone_slug']}`: {crop}.", intent=intent)


async def _equipment_status(conn, intent):
    target = intent.args.get("target") or ""
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (equipment) equipment, state, ts
          FROM equipment_state
         WHERE ($1 = '' OR equipment ILIKE '%' || $1 || '%')
         ORDER BY equipment, ts DESC
         LIMIT 20
        """,
        target,
    )
    if not rows:
        return _text_response("No matching equipment state rows found.", ok=False, status="failed", intent=intent)
    text = "; ".join(f"`{r['equipment']}`={r['state']}" for r in rows)
    return _text_response(f"Equipment: {text}", intent=intent)


async def _sensor_status(conn, intent):
    target = intent.args.get("target") or ""
    rows = await conn.fetch(
        """
        SELECT sensor_id, type, is_stale, last_seen
          FROM v_sensor_staleness
         WHERE ($1 = '' OR sensor_id ILIKE '%' || $1 || '%' OR type ILIKE '%' || $1 || '%')
         ORDER BY is_stale DESC, sensor_id
         LIMIT 12
        """,
        target,
    )
    if not rows:
        return _text_response("No matching sensor rows found.", ok=False, status="failed", intent=intent)
    text = "; ".join(f"`{r['sensor_id']}`={'stale' if r['is_stale'] else 'ok'}" for r in rows)
    return _text_response(f"Sensors: {text}", intent=intent)


async def _crop_map(conn, intent):
    rows = await conn.fetch(
        """
        SELECT position_label, zone_slug, crop_name
          FROM v_position_current
         ORDER BY zone_slug, position_label
         LIMIT 80
        """
    )
    occupied = [f"{r['position_label']}={r['crop_name']}" for r in rows if r["crop_name"]]
    return _text_response(
        "Crop map: " + (", ".join(occupied[:60]) if occupied else "no occupied positions found"), intent=intent
    )


async def _empty_positions(conn, intent):
    rows = await conn.fetch(
        "SELECT position_label FROM v_position_current WHERE NOT is_occupied ORDER BY position_label LIMIT 80"
    )
    labels = [r["position_label"] for r in rows]
    return _text_response("Empty positions: " + (", ".join(labels) if labels else "none"), intent=intent)


async def _harvest_due(conn, intent):
    rows = await conn.fetch(
        """
        SELECT id, name, position, expected_harvest
          FROM crops
         WHERE is_active
           AND expected_harvest IS NOT NULL
           AND expected_harvest <= current_date + 7
         ORDER BY expected_harvest
         LIMIT 20
        """
    )
    if not rows:
        return _text_response("No crops are due for harvest in the next 7 days.", intent=intent)
    text = "; ".join(f"#{r['id']} {r['name']} at {r['position']} due {r['expected_harvest']}" for r in rows)
    return _text_response(f"Harvest due: {text}", intent=intent)


async def _scouting_due(conn, intent):
    rows = await conn.fetch("SELECT * FROM v_slack_crop_tasks_due WHERE task_type ILIKE '%scout%' LIMIT 20")
    if not rows:
        return _text_response("No scouting tasks are due in the next 24h.", intent=intent)
    text = "; ".join(f"#{r['id']} {r['crop_name'] or r['position_label']} due {r['due_at']:%m-%d %H:%M}" for r in rows)
    return _text_response(f"Scouting due: {text}", intent=intent)


async def _alert_action(conn, req: SlackCommandRequest, intent: SlackParsedIntent, audit_id):
    alert_id = intent.args.get("alert_id")
    if not alert_id:
        return _text_response("Missing alert id.", ok=False, status="failed", intent=intent)
    note = intent.args.get("note")
    action = intent.normalized_intent.removeprefix("alert.")
    snoozed_until = None
    assigned_to = intent.args.get("assigned_to")

    if action == "ack":
        await conn.execute(
            "UPDATE alert_log SET disposition='acknowledged', acknowledged_at=now(), acknowledged_by=$2 WHERE id=$1 AND resolved_at IS NULL",
            alert_id,
            req.slack_user_name or req.slack_user_id,
        )
    elif action == "resolve":
        await conn.execute(
            "UPDATE alert_log SET disposition='resolved', resolved_at=now(), resolved_by=$2, resolution=$3 WHERE id=$1",
            alert_id,
            req.slack_user_name or req.slack_user_id,
            note or "Resolved from Slack",
        )
    elif action == "snooze":
        snoozed_until = _duration_to_until(intent.args.get("duration") or "1h")
        await conn.execute(
            "UPDATE alert_log SET disposition='acknowledged', slack_snoozed_until=$2, slack_snoozed_by=$3 WHERE id=$1 AND resolved_at IS NULL",
            alert_id,
            snoozed_until,
            req.slack_user_id,
        )
    elif action == "assign":
        await conn.execute(
            "UPDATE alert_log SET slack_assigned_to=$2 WHERE id=$1 AND resolved_at IS NULL", alert_id, assigned_to
        )
    elif action == "false_positive":
        await conn.execute(
            "UPDATE alert_log SET disposition='resolved', resolved_at=now(), resolved_by=$2, resolution='false positive from Slack' WHERE id=$1",
            alert_id,
            req.slack_user_name or req.slack_user_id,
        )
    else:
        return _text_response("Unsupported alert action.", ok=False, status="failed", intent=intent)

    row = await conn.fetchrow(
        """
        INSERT INTO slack_alert_actions (
            alert_id, action, slack_user_id, slack_user_name, channel_id,
            message_ts, thread_ts, note, snoozed_until, assigned_to, command_audit_id
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        RETURNING id
        """,
        alert_id,
        action,
        req.slack_user_id,
        req.slack_user_name,
        req.channel_id,
        req.message_ts,
        req.thread_ts,
        note,
        snoozed_until,
        assigned_to,
        audit_id,
    )
    return _text_response(
        f"Alert #{alert_id} {action} recorded.",
        intent=intent,
        record_type="slack_alert_actions",
        record_id=str(row["id"]),
    )


async def _crop_observe(conn, req, intent):
    crop = await _resolve_crop(conn, intent.args.get("text") or "")
    if not crop:
        return _text_response("Could not resolve crop for observation.", ok=False, status="failed", intent=intent)
    row = await conn.fetchrow(
        """
        INSERT INTO observations (
            crop_id, greenhouse_id, zone, position, zone_id, position_id,
            obs_type, notes, observer, source, slack_channel_id,
            slack_message_ts, slack_thread_ts, slack_user_id
        )
        VALUES ($1,'vallery',$2,$3,$4,$5,'note',$6,$7,'slack',$8,$9,$10,$11)
        RETURNING id
        """,
        crop["id"],
        crop["zone"],
        crop["position"],
        crop["zone_id"],
        crop["position_id"],
        intent.args.get("text"),
        req.slack_user_name or req.slack_user_id,
        req.channel_id,
        req.message_ts,
        req.thread_ts,
        req.slack_user_id,
    )
    return _text_response(
        f"Observation recorded for `{crop['name']}` as #{row['id']}.",
        intent=intent,
        record_type="observations",
        record_id=str(row["id"]),
    )


async def _crop_create(conn, req, intent):
    position = await _resolve_position(conn, intent.args.get("position") or "")
    if not position:
        return _text_response("Could not resolve target position.", ok=False, status="failed", intent=intent)
    row = await conn.fetchrow(
        """
        INSERT INTO crops (name, position, zone, planted_date, stage, position_id, zone_id, greenhouse_id, notes)
        VALUES ($1,$2,$3,current_date,'seedling',$4,$5,'vallery',$6)
        RETURNING id
        """,
        intent.args.get("name") or "unknown crop",
        position["position_label"],
        position["zone_slug"],
        position["position_id"],
        position["zone_id"],
        f"Created from Slack by {req.slack_user_name or req.slack_user_id}",
    )
    return _text_response(
        f"Crop `{intent.args.get('name')}` planted at `{position['position_label']}` as #{row['id']}.",
        intent=intent,
        record_type="crops",
        record_id=str(row["id"]),
    )


async def _crop_clear(conn, req, intent):
    crop = (
        await _crop_by_id(conn, intent.args.get("crop_id"))
        if intent.args.get("crop_id")
        else await _resolve_crop(conn, intent.args.get("target") or "")
    )
    if not crop:
        return _text_response("Could not resolve crop to clear.", ok=False, status="failed", intent=intent)
    await conn.execute(
        "UPDATE crops SET is_active=false, stage='cleared', cleared_at=now(), updated_at=now() WHERE id=$1", crop["id"]
    )
    row = await conn.fetchrow(
        """
        INSERT INTO crop_events (
            crop_id, greenhouse_id, position_id, event_type, old_stage, new_stage,
            operator, source, notes, slack_channel_id, slack_message_ts, slack_thread_ts, slack_user_id
        )
        VALUES ($1,'vallery',$2,'removed',$3,'cleared',$4,'slack',$5,$6,$7,$8,$9)
        RETURNING id
        """,
        crop["id"],
        crop["position_id"],
        crop["stage"],
        req.slack_user_name or req.slack_user_id,
        "Cleared from Slack",
        req.channel_id,
        req.message_ts,
        req.thread_ts,
        req.slack_user_id,
    )
    return _text_response(
        f"Cleared `{crop['name']}` from `{crop['position']}`.",
        intent=intent,
        record_type="crop_events",
        record_id=str(row["id"]),
    )


async def _crop_transplant(conn, req, intent):
    crop = (
        await _crop_by_id(conn, intent.args.get("crop_id"))
        if intent.args.get("crop_id")
        else await _resolve_crop(conn, intent.args.get("target") or "")
    )
    pos = await _resolve_position(conn, intent.args.get("position") or intent.args.get("position_label") or "")
    if not crop or not pos:
        return _text_response("Could not resolve crop or target position.", ok=False, status="failed", intent=intent)
    await conn.execute(
        "UPDATE crops SET position=$2, zone=$3, position_id=$4, zone_id=$5, updated_at=now() WHERE id=$1",
        crop["id"],
        pos["position_label"],
        pos["zone_slug"],
        pos["position_id"],
        pos["zone_id"],
    )
    row = await conn.fetchrow(
        """
        INSERT INTO crop_events (
            crop_id, greenhouse_id, position_id, event_type, operator,
            source, notes, slack_channel_id, slack_message_ts, slack_thread_ts, slack_user_id
        )
        VALUES ($1,'vallery',$2,'transplanted',$3,'slack',$4,$5,$6,$7,$8)
        RETURNING id
        """,
        crop["id"],
        pos["position_id"],
        req.slack_user_name or req.slack_user_id,
        f"Transplanted from {crop['position']} to {pos['position_label']}",
        req.channel_id,
        req.message_ts,
        req.thread_ts,
        req.slack_user_id,
    )
    return _text_response(
        f"Transplanted `{crop['name']}` to `{pos['position_label']}`.",
        intent=intent,
        record_type="crop_events",
        record_id=str(row["id"]),
    )


async def _crop_harvest(conn, req, intent):
    crop = (
        await _crop_by_id(conn, intent.args.get("crop_id"))
        if intent.args.get("crop_id")
        else await _resolve_crop(conn, intent.args.get("target") or "")
    )
    if not crop:
        return _text_response("Could not resolve crop to harvest.", ok=False, status="failed", intent=intent)
    fields = _normalized_harvest_fields(intent.args)
    row = await conn.fetchrow(
        """
        INSERT INTO harvests (
            crop_id, weight_kg, unit_count, quality_grade, salable_weight_kg,
            cull_weight_kg, cull_reason, quality_reason, zone, destination,
            labor_minutes, operator, position_id, greenhouse_id, notes,
            slack_channel_id, slack_message_ts, slack_thread_ts, slack_user_id
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,'vallery',$14,$15,$16,$17,$18)
        RETURNING id
        """,
        crop["id"],
        fields.get("weight_kg"),
        fields.get("unit_count"),
        intent.args.get("quality_grade"),
        fields.get("salable_weight_kg"),
        fields.get("cull_weight_kg"),
        intent.args.get("cull_reason"),
        intent.args.get("quality_reason"),
        crop["zone"],
        intent.args.get("destination"),
        intent.args.get("labor_minutes"),
        req.slack_user_name or req.slack_user_id,
        crop["position_id"],
        intent.args.get("notes"),
        req.channel_id,
        req.message_ts,
        req.thread_ts,
        req.slack_user_id,
    )
    await _complete_related_tasks(conn, crop["id"], row["id"], req.slack_user_name or req.slack_user_id)
    return _text_response(
        f"Harvest recorded for `{crop['name']}` as #{row['id']}.",
        intent=intent,
        record_type="harvests",
        record_id=str(row["id"]),
    )


async def _crop_treatment(conn, req, intent):
    crop = await _resolve_crop(conn, intent.args.get("text") or "")
    if not crop:
        return _text_response("Could not resolve crop for treatment.", ok=False, status="failed", intent=intent)
    row = await conn.fetchrow(
        """
        INSERT INTO treatments (
            crop_id, product, method, zone, applicator, position_id,
            greenhouse_id, notes, slack_channel_id, slack_message_ts,
            slack_thread_ts, slack_user_id
        )
        VALUES ($1,'unspecified','slack',$2,$3,$4,'vallery',$5,$6,$7,$8,$9)
        RETURNING id
        """,
        crop["id"],
        crop["zone"],
        req.slack_user_name or req.slack_user_id,
        crop["position_id"],
        intent.args.get("text"),
        req.channel_id,
        req.message_ts,
        req.thread_ts,
        req.slack_user_id,
    )
    return _text_response(
        f"Treatment note recorded for `{crop['name']}` as #{row['id']}.",
        intent=intent,
        record_type="treatments",
        record_id=str(row["id"]),
    )


async def _confirmation_action(conn, req, intent, role, audit_id, settings):
    confirmation_id = intent.args.get("confirmation_id")
    if not confirmation_id:
        return _text_response("Missing confirmation id.", ok=False, status="failed", intent=intent)
    row = await conn.fetchrow("SELECT * FROM slack_confirmation_requests WHERE id=$1", UUID(str(confirmation_id)))
    if not row:
        return _text_response("Confirmation request not found.", ok=False, status="failed", intent=intent)
    if row["slack_user_id"] != req.slack_user_id:
        return _text_response(
            "Only the requesting Slack user can confirm or cancel this action.",
            ok=False,
            status="denied",
            intent=intent,
        )
    if row["status"] != "pending":
        status = row["status"] if row["status"] in {"canceled", "expired"} else "failed"
        return _text_response(f"Confirmation is already `{row['status']}`.", ok=False, status=status, intent=intent)
    if row["expires_at"] < datetime.now(UTC):
        await conn.execute("UPDATE slack_confirmation_requests SET status='expired' WHERE id=$1", row["id"])
        return _text_response("Confirmation expired.", ok=False, status="expired", intent=intent)
    if intent.normalized_intent == "confirmation.cancel":
        await conn.execute(
            "UPDATE slack_confirmation_requests SET status='canceled', canceled_at=now() WHERE id=$1", row["id"]
        )
        return _text_response("Confirmation canceled.", status="canceled", intent=intent, confirmation_id=row["id"])

    target = _intent_from_confirmation(row)
    if not role_allows(role, target.required_role):
        return _text_response(
            "Your role no longer allows that confirmed action.", ok=False, status="denied", intent=target
        )
    await conn.execute(
        "UPDATE slack_confirmation_requests SET status='confirmed', confirmed_at=now() WHERE id=$1", row["id"]
    )
    result = await _execute(conn, req, target, role, audit_id, settings)
    return result.model_copy(update={"confirmation_id": row["id"]})


def _intent_from_confirmation(row) -> SlackParsedIntent:
    intent = row["normalized_intent"]
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    role = "operator" if intent.startswith("alert.") or intent == "plan.trigger" else "grower"
    if intent == "alert.false_positive":
        role = "coordinator"
    return SlackParsedIntent(
        normalized_intent=intent,
        args=payload or {},
        required_role=role,  # type: ignore[arg-type]
        requires_confirmation=False,
        target_type=row["target_type"],
        target_id=row["target_id"],
    )


def _duration_to_until(value: str) -> datetime:
    match = re.match(r"^(\d+)\s*([mhd])", value.strip().lower())
    amount = int(match.group(1)) if match else 1
    unit = match.group(2) if match else "h"
    delta = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount)}[unit]
    return datetime.now(UTC) + delta


def _kg(amount: float | None, unit: str | None) -> float | None:
    if amount is None or not unit:
        return None
    unit = unit.lower()
    if unit == "kg":
        return float(amount)
    if unit == "g":
        return float(amount) / 1000.0
    if unit in {"lb", "lbs"}:
        return float(amount) * 0.45359237
    if unit == "oz":
        return float(amount) * 0.0283495231
    return None


def _normalized_harvest_fields(args: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    amount = args.get("amount")
    unit = args.get("unit")
    if unit in {"unit", "units"}:
        fields["unit_count"] = int(amount) if amount is not None else None
    else:
        fields["weight_kg"] = _kg(amount, unit)
    for source, target in (("salable", "salable_weight_kg"), ("cull", "cull_weight_kg")):
        value = args.get(source)
        if isinstance(value, dict):
            fields[target] = _kg(value.get("amount"), value.get("unit"))
    return {k: v for k, v in fields.items() if v is not None}


async def _resolve_position(conn, label: str) -> dict[str, Any] | None:
    if not label:
        return None
    row = await conn.fetchrow(
        """
        SELECT *
          FROM v_position_current
         WHERE lower(position_label) = lower($1)
            OR position_id::text = $1
         LIMIT 1
        """,
        str(label).strip(),
    )
    return _dict(row)


async def _crop_by_id(conn, crop_id) -> dict[str, Any] | None:
    if not crop_id:
        return None
    row = await conn.fetchrow("SELECT * FROM crops WHERE id=$1 AND greenhouse_id='vallery'", int(crop_id))
    return _dict(row)


async def _resolve_crop(conn, target: str) -> dict[str, Any] | None:
    target = (target or "").strip()
    if not target:
        return None
    match_id = re.search(r"#?(\d+)", target)
    if match_id:
        row = await _crop_by_id(conn, int(match_id.group(1)))
        if row:
            return row
    row = await conn.fetchrow(
        """
        SELECT *
          FROM crops
         WHERE greenhouse_id='vallery'
           AND is_active
           AND (
                lower(name) = lower($1)
             OR lower(position) = lower($1)
             OR lower($1) LIKE '%' || lower(name) || '%'
             OR lower($1) LIKE '%' || lower(position) || '%'
           )
         ORDER BY updated_at DESC NULLS LAST
         LIMIT 1
        """,
        target,
    )
    return _dict(row)


async def _harvest_warnings(conn, args: dict[str, Any]) -> list[str]:
    crop_id = args.get("crop_id")
    if not crop_id:
        return []
    row = await conn.fetchrow("SELECT expected_harvest FROM crops WHERE id=$1", crop_id)
    if row and row["expected_harvest"] and row["expected_harvest"] > datetime.now(UTC).date():
        return [f"Expected harvest date is {row['expected_harvest']}."]
    return []


async def _complete_related_tasks(conn, crop_id: int, harvest_id: int, completed_by: str) -> None:
    await conn.execute(
        """
        UPDATE crop_tasks
           SET status='completed',
               completed_at=now(),
               completed_by=$3,
               related_harvest_id=$2
         WHERE crop_id=$1
           AND status IN ('open','snoozed')
           AND task_type ILIKE '%harvest%'
        """,
        crop_id,
        harvest_id,
        completed_by,
    )
