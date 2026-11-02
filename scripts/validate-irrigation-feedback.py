#!/usr/bin/env python3
"""Validate irrigation feedback instrumentation after physical bring-up.

Feedback evidence comes from the database and the controller's Native API
(ESPHome) only; Home Assistant and MQTT lookups were removed with VD-48.

Exit status is intentionally operational:
  0 = all required feedback rows are ok and, unless --status-only is used,
      no feedback-gap alerts are open
  1 = one or more physical feedback requirements are still missing/failing,
      or feedback-gap alerts are still open
  2 = validation could not run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
from typing import Any

REQUIRED_FEEDBACK_KEYS = (
    "south_soil_probe_1",
    "center_root_zone_moisture",
    "center_runoff_ph",
    "center_runoff_ec",
)

FIELD_REQUIREMENT_EQUIPMENT = {
    "south_soil_probe_1_repair": "south_soil_probe_1",
    "center_root_zone_runoff_feedback": "center_root_zone_runoff_feedback",
}

FEEDBACK_SOURCE_COLUMNS = (
    "soil_moisture_south_1",
    "soil_ec_south_1",
    "soil_temp_south_1",
    "moisture_center",
    "ph_runoff_center",
    "ec_runoff_center",
)

FEEDBACK_VALUE_RULES = {
    "south_soil_probe_1": "moisture must be >0-100%, EC must be >0, and south_1 temperature must keep updating",
    "center_root_zone_moisture": "moisture_center must be 0-100%",
    "center_runoff_ph": "ph_runoff_center must be 0-14",
    "center_runoff_ec": "ec_runoff_center must be nonnegative",
}

FEEDBACK_HISTORY_COLUMNS = {
    "south_soil_probe_1": ("soil_moisture_south_1", "soil_ec_south_1", "soil_temp_south_1"),
    "center_root_zone_moisture": ("moisture_center",),
    "center_runoff_ph": ("ph_runoff_center",),
    "center_runoff_ec": ("ec_runoff_center",),
}

ESPHOME_CANDIDATES = {
    "south_soil_probe_1": (
        "south_1_soil_moisture____",
        "south_1_soil_temp___f_",
        "south_1_soil_ec___s_cm_",
    ),
    "center_root_zone_moisture": (
        "center_soil_moisture____",
        "center_root_zone_moisture____",
        "center_root_zone_soil_moisture____",
        "center_rootzone_moisture____",
        "center_moisture____",
        "center_vwc",
        "center_substrate_vwc",
        "center_substrate_moisture",
        "center_substrate_moisture____",
        "center_root_zone_vwc",
        "middle_substrate_vwc",
        "middle_substrate_moisture",
        "middle_substrate_moisture____",
    ),
    "center_runoff_ph": (
        "center_runoff_ph",
        "center_runoff_p_h",
        "center_run_off_ph",
        "center_run_off_p_h",
        "center_drain_ph",
        "center_drain_p_h",
        "center_drainage_ph",
        "center_leachate_ph",
        "center_effluent_ph",
        "center_tray_ph",
    ),
    "center_runoff_ec": (
        "center_runoff_ec",
        "center_runoff_ec_ms_cm",
        "center_runoff_ec_us_cm",
        "center_runoff_ec_u_s_cm",
        "center_runoff_ec___s_cm_",
        "center_runoff_ec____s___cm_",
        "center_run_off_ec",
        "center_run_off_ec_ms_cm",
        "center_run_off_ec_us_cm",
        "center_run_off_ec_u_s_cm",
        "center_run_off_ec___s_cm_",
        "center_run_off_ec____s___cm_",
        "center_runoff_conductivity",
        "center_runoff_electrical_conductivity",
        "center_drain_ec",
        "center_drain_ec_ms_cm",
        "center_drain_ec_us_cm",
        "center_drain_ec_u_s_cm",
        "center_drain_ec___s_cm_",
        "center_drain_ec____s___cm_",
        "center_drainage_ec",
        "center_leachate_ec",
        "center_effluent_ec",
        "center_tray_ec",
    ),
}

ACCEPTED_ESPHOME_OBJECT_IDS = {
    object_id: feedback_key for feedback_key, object_ids in ESPHOME_CANDIDATES.items() for object_id in object_ids
}
ESPHOME_STATE_TIMEOUT_S = 3.0

DISCOVERY_LOCATION_TERMS = {
    "center",
    "centre",
    "drain",
    "drainage",
    "effluent",
    "hydroponic",
    "leach",
    "leachate",
    "middle",
    "reservoir",
    "root",
    "rootzone",
    "runoff",
    "soil",
    "substrate",
    "tray",
}
DISCOVERY_SIGNAL_TERMS = {"conductance", "conductivity", "ec", "moisture", "ph", "runoff", "salinity", "tds", "vwc"}
DISCOVERY_SIGNAL_PHRASES = ("p h", "us/cm")


def _psql(sql: str) -> list[list[str]]:
    if shutil.which("psql") and sys.argv.count("--direct-db"):
        cmd = ["psql", "-t", "-A", "-F", "\t", "-c", sql]
    else:
        cmd = [
            "docker",
            "exec",
            "verdify-timescaledb",
            "psql",
            "-U",
            "verdify",
            "-d",
            "verdify",
            "-t",
            "-A",
            "-F",
            "\t",
            "-c",
            sql,
        ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=45, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return [line.split("\t") for line in result.stdout.splitlines() if line.strip()]


def _parse_details(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def _format_details(details: dict[str, Any]) -> str:
    parts = []
    for key in sorted(details):
        value = details[key]
        if value is None:
            continue
        parts.append(f"{key}={value}")
    return " ".join(parts)


def _db_feedback_status() -> dict[str, dict[str, Any]]:
    rows = _psql(
        """
        SELECT feedback_key,
               status,
               COALESCE(latest_value::text, ''),
               COALESCE(last_sample_ts::text, ''),
               required_action,
               COALESCE(details::text, '{}')
          FROM v_irrigation_sensor_feedback_status
         ORDER BY feedback_key
        """
    )
    return {
        feedback_key: {
            "status": status,
            "latest_value": latest_value or None,
            "last_sample_ts": last_sample_ts or None,
            "required_action": required_action,
            "details": _parse_details(details),
        }
        for feedback_key, status, latest_value, last_sample_ts, required_action, details in rows
    }


def _open_feedback_alerts() -> list[dict[str, str]]:
    rows = _psql(
        """
        SELECT sensor_id, severity, disposition, message
          FROM alert_log
         WHERE alert_type = 'irrigation_feedback_gap'
           AND resolved_at IS NULL
         ORDER BY sensor_id
        """
    )
    return [
        {"sensor_id": sensor_id, "severity": severity, "disposition": disposition, "message": message}
        for sensor_id, severity, disposition, message in rows
    ]


def _field_work_items() -> list[dict[str, str | None]]:
    rows = _psql(
        """
        WITH wanted(requirement_id, equipment) AS (
            VALUES
                ('south_soil_probe_1_repair', 'south_soil_probe_1'),
                ('center_root_zone_runoff_feedback', 'center_root_zone_runoff_feedback')
        )
        SELECT wanted.requirement_id,
               wanted.equipment,
               COALESCE(ir.current_status, 'missing'),
               COALESCE(ir.blocks_story, ''),
               COALESCE(ir.recommended_source, ''),
               COALESCE(ml.service_type, ''),
               COALESCE(ml.description, ''),
               COALESCE(ml.next_due::text, ''),
               COALESCE(ml.notes, '')
          FROM wanted
          LEFT JOIN instrumentation_requirements ir USING (requirement_id)
          LEFT JOIN LATERAL (
              SELECT service_type, description, next_due, notes
                FROM maintenance_log
               WHERE maintenance_log.equipment = wanted.equipment
                 AND maintenance_log.greenhouse_id = 'vallery'
               ORDER BY ts DESC, id DESC
               LIMIT 1
          ) ml ON true
         ORDER BY wanted.requirement_id
        """
    )
    return [
        {
            "requirement_id": requirement_id,
            "equipment": equipment,
            "current_status": current_status,
            "blocks_story": blocks_story or None,
            "recommended_source": recommended_source or None,
            "service_type": service_type or None,
            "description": description or None,
            "next_due": next_due or None,
            "notes": notes or None,
        }
        for (
            requirement_id,
            equipment,
            current_status,
            blocks_story,
            recommended_source,
            service_type,
            description,
            next_due,
            notes,
        ) in rows
    ]


def _sensor_registry_feedback_targets() -> list[dict[str, str | bool | None]]:
    rows = _psql(
        """
        SELECT sensor_id,
               COALESCE(entity_id, ''),
               type,
               COALESCE(zone, ''),
               source_column,
               COALESCE(unit, ''),
               expected_interval_s::text,
               COALESCE(active, false)::text,
               COALESCE(notes, '')
          FROM sensor_registry
         WHERE source_table = 'climate'
           AND source_column IN (
               'soil_moisture_south_1',
               'soil_ec_south_1',
               'soil_temp_south_1',
               'moisture_center',
               'ph_runoff_center',
               'ec_runoff_center'
           )
         ORDER BY zone, source_column, sensor_id
        """
    )
    return [
        {
            "sensor_id": sensor_id,
            "entity_id": entity_id or None,
            "type": typ,
            "zone": zone or None,
            "source_column": source_column,
            "unit": unit or None,
            "expected_interval_s": expected_interval_s,
            "active": active == "true",
            "notes": notes or None,
        }
        for sensor_id, entity_id, typ, zone, source_column, unit, expected_interval_s, active, notes in rows
    ]


def _db_source_history() -> dict[str, dict[str, str | int | None]]:
    rows = _psql(
        """
        SELECT
               COALESCE(max(ts) FILTER (WHERE soil_moisture_south_1 IS NOT NULL)::text, ''),
               COALESCE(max(ts) FILTER (
                   WHERE soil_moisture_south_1 > 0
                     AND soil_moisture_south_1 <= 100
               )::text, ''),
               count(*) FILTER (WHERE soil_moisture_south_1 IS NOT NULL)::text,
               count(*) FILTER (
                   WHERE ts >= now() - interval '24 hours'
                     AND soil_moisture_south_1 IS NOT NULL
               )::text,
               count(*) FILTER (
                   WHERE ts >= now() - interval '24 hours'
                     AND soil_moisture_south_1 > 0
                     AND soil_moisture_south_1 <= 100
               )::text,
               COALESCE(max(ts) FILTER (WHERE soil_ec_south_1 IS NOT NULL)::text, ''),
               COALESCE(max(ts) FILTER (WHERE soil_ec_south_1 > 0)::text, ''),
               count(*) FILTER (WHERE soil_ec_south_1 IS NOT NULL)::text,
               count(*) FILTER (
                   WHERE ts >= now() - interval '24 hours'
                     AND soil_ec_south_1 IS NOT NULL
               )::text,
               count(*) FILTER (
                   WHERE ts >= now() - interval '24 hours'
                     AND soil_ec_south_1 > 0
               )::text,
               COALESCE(max(ts) FILTER (WHERE soil_temp_south_1 IS NOT NULL)::text, ''),
               COALESCE(max(ts) FILTER (WHERE soil_temp_south_1 IS NOT NULL)::text, ''),
               count(*) FILTER (WHERE soil_temp_south_1 IS NOT NULL)::text,
               count(*) FILTER (
                   WHERE ts >= now() - interval '24 hours'
                     AND soil_temp_south_1 IS NOT NULL
               )::text,
               count(*) FILTER (
                   WHERE ts >= now() - interval '24 hours'
                     AND soil_temp_south_1 IS NOT NULL
               )::text,
               COALESCE(max(ts) FILTER (WHERE moisture_center IS NOT NULL)::text, ''),
               COALESCE(max(ts) FILTER (
                   WHERE moisture_center >= 0
                     AND moisture_center <= 100
               )::text, ''),
               count(*) FILTER (WHERE moisture_center IS NOT NULL)::text,
               count(*) FILTER (
                   WHERE ts >= now() - interval '24 hours'
                     AND moisture_center IS NOT NULL
               )::text,
               count(*) FILTER (
                   WHERE ts >= now() - interval '24 hours'
                     AND moisture_center >= 0
                     AND moisture_center <= 100
               )::text,
               COALESCE(max(ts) FILTER (WHERE ph_runoff_center IS NOT NULL)::text, ''),
               COALESCE(max(ts) FILTER (
                   WHERE ph_runoff_center >= 0
                     AND ph_runoff_center <= 14
               )::text, ''),
               count(*) FILTER (WHERE ph_runoff_center IS NOT NULL)::text,
               count(*) FILTER (
                   WHERE ts >= now() - interval '24 hours'
                     AND ph_runoff_center IS NOT NULL
               )::text,
               count(*) FILTER (
                   WHERE ts >= now() - interval '24 hours'
                     AND ph_runoff_center >= 0
                     AND ph_runoff_center <= 14
               )::text,
               COALESCE(max(ts) FILTER (WHERE ec_runoff_center IS NOT NULL)::text, ''),
               COALESCE(max(ts) FILTER (WHERE ec_runoff_center >= 0)::text, ''),
               count(*) FILTER (WHERE ec_runoff_center IS NOT NULL)::text,
               count(*) FILTER (
                   WHERE ts >= now() - interval '24 hours'
                     AND ec_runoff_center IS NOT NULL
               )::text,
               count(*) FILTER (
                   WHERE ts >= now() - interval '24 hours'
                     AND ec_runoff_center >= 0
               )::text
          FROM climate
         WHERE greenhouse_id = 'vallery'
        """
    )
    if not rows:
        return {}
    row = rows[0]
    columns = (
        "soil_moisture_south_1",
        "soil_ec_south_1",
        "soil_temp_south_1",
        "moisture_center",
        "ph_runoff_center",
        "ec_runoff_center",
    )
    history: dict[str, dict[str, str | int | None]] = {}
    for offset, source_column in enumerate(columns):
        start = offset * 5
        last_sample_ts, last_valid_ts, lifetime_samples, samples_24h, valid_samples_24h = row[start : start + 5]
        history[source_column] = {
            "last_sample_ts": last_sample_ts or None,
            "last_valid_ts": last_valid_ts or None,
            "lifetime_samples": int(lifetime_samples or 0),
            "samples_24h": int(samples_24h or 0),
            "valid_samples_24h": int(valid_samples_24h or 0),
        }
    return history


def _discovery_tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", text) if token}


def _esphome_config() -> tuple[str, int, str]:
    rows = _psql(
        """
        SELECT esp32_host, esp32_port, esp32_api_key
          FROM greenhouses
         WHERE id = 'vallery'
        """
    )
    if not rows or len(rows[0]) < 3:
        raise RuntimeError("greenhouse ESP32 config row missing")
    host, port, key = rows[0]
    return host, int(port), key


async def _fetch_esphome_entities_async() -> list[dict[str, Any]]:
    from aioesphomeapi import APIClient

    host, port, key = _esphome_config()
    client = APIClient(address=host, port=port, password="", noise_psk=key)
    try:
        await client.connect(login=True)
        entities, _services = await client.list_entities_services()
        states: dict[int, dict[str, Any]] = {}
        entity_keys = {
            getattr(entity, "key", None)
            for entity in entities
            if type(entity).__name__ == "SensorInfo" and getattr(entity, "key", None) is not None
        }
        state_event = asyncio.Event()

        def on_state(state: Any) -> None:
            state_key = getattr(state, "key", None)
            if state_key not in entity_keys:
                return
            states[state_key] = {
                "state": getattr(state, "state", None),
                "missing_state": getattr(state, "missing_state", None),
            }
            if entity_keys <= states.keys():
                state_event.set()

        if entity_keys:
            client.subscribe_states(on_state)
            try:
                await asyncio.wait_for(state_event.wait(), timeout=ESPHOME_STATE_TIMEOUT_S)
            except TimeoutError:
                pass
        return [
            {
                "type": type(entity).__name__,
                "object_id": getattr(entity, "object_id", "") or "",
                "name": getattr(entity, "name", "") or "",
                "key": getattr(entity, "key", None),
                "state": states.get(getattr(entity, "key", None), {}).get("state"),
                "missing_state": states.get(getattr(entity, "key", None), {}).get("missing_state"),
            }
            for entity in entities
        ]
    finally:
        await client.disconnect()


def _fetch_esphome_entities() -> list[dict[str, Any]]:
    return asyncio.run(_fetch_esphome_entities_async())


def _format_esphome_entity(object_id: str, entity: dict[str, Any] | None) -> dict[str, Any]:
    if entity is None:
        return {
            "object_id": object_id,
            "present": False,
            "name": None,
            "type": None,
            "state": None,
            "missing_state": None,
        }
    return {
        "object_id": object_id,
        "present": True,
        "name": entity.get("name"),
        "type": entity.get("type"),
        "state": entity.get("state"),
        "missing_state": entity.get("missing_state"),
    }


def _esphome_candidate_entities(entities: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_object_id = {entity["object_id"]: entity for entity in entities}
    out: dict[str, list[dict[str, Any]]] = {}
    for feedback_key, object_ids in ESPHOME_CANDIDATES.items():
        out[feedback_key] = [_format_esphome_entity(object_id, by_object_id.get(object_id)) for object_id in object_ids]
    return out


def _esphome_discovery_text(entity: dict[str, Any]) -> str:
    parts = [
        str(entity.get("object_id") or ""),
        str(entity.get("name") or ""),
        str(entity.get("type") or ""),
    ]
    return " ".join(parts).lower().replace("_", " ")


def _discover_esphome_feedback_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    discovered: list[dict[str, Any]] = []
    for entity in entities:
        object_id = str(entity.get("object_id") or "")
        if not object_id:
            continue
        accepted_for = ACCEPTED_ESPHOME_OBJECT_IDS.get(object_id)
        text = _esphome_discovery_text(entity)
        tokens = _discovery_tokens(text)
        plausible_feedback = bool(tokens & DISCOVERY_LOCATION_TERMS) and (
            bool(tokens & DISCOVERY_SIGNAL_TERMS) or any(phrase in text for phrase in DISCOVERY_SIGNAL_PHRASES)
        )
        if not accepted_for and not plausible_feedback:
            continue
        discovered.append(
            {
                "object_id": object_id,
                "name": entity.get("name"),
                "type": entity.get("type"),
                "state": entity.get("state"),
                "missing_state": entity.get("missing_state"),
                "accepted_for": [accepted_for] if accepted_for else [],
            }
        )
    return sorted(discovered, key=lambda item: (item["type"] or "", item["object_id"]))


def build_report(
    status_only: bool = False,
    discover_esphome: bool = False,
    include_db_history: bool = False,
) -> tuple[dict[str, Any], bool]:
    db_status = _db_feedback_status()
    alerts = _open_feedback_alerts()
    field_work_items = _field_work_items()
    registry_targets = _sensor_registry_feedback_targets()
    db_source_history = _db_source_history() if include_db_history else {}
    missing_rows = [key for key in REQUIRED_FEEDBACK_KEYS if key not in db_status]
    not_ok = [key for key in REQUIRED_FEEDBACK_KEYS if db_status.get(key, {}).get("status") != "ok"]
    esphome_states: dict[str, Any] = {}
    esphome_discovered: list[dict[str, Any]] = []
    esphome_error = None
    if discover_esphome:
        try:
            esphome_entities = _fetch_esphome_entities()
            esphome_states = _esphome_candidate_entities(esphome_entities)
            esphome_discovered = _discover_esphome_feedback_entities(esphome_entities)
        except (OSError, RuntimeError, TimeoutError) as exc:
            esphome_error = str(exc)

    physical_ready = not missing_rows and not not_ok
    ready = physical_ready and (status_only or not alerts)
    report = {
        "ready": ready,
        "physical_ready": physical_ready,
        "status_only": status_only,
        "db_status": db_status,
        "missing_status_rows": missing_rows,
        "not_ok_feedback_keys": not_ok,
        "open_feedback_alerts": alerts,
        "field_work_items": field_work_items,
        "sensor_registry_feedback_targets": registry_targets,
        "db_source_history": db_source_history,
        "esphome_candidates": esphome_states,
        "esphome_discovered_feedback_entities": esphome_discovered,
        "esphome_error": esphome_error,
    }
    return report, ready


def print_text(report: dict[str, Any]) -> None:
    print(f"Irrigation feedback ready: {str(report['ready']).lower()}")
    print("\nDB feedback status:")
    for key in REQUIRED_FEEDBACK_KEYS:
        row = report["db_status"].get(key)
        if not row:
            print(f"  {key}: missing status row")
            continue
        value = row["latest_value"] if row["latest_value"] is not None else "-"
        last_sample = row["last_sample_ts"] if row["last_sample_ts"] is not None else "-"
        print(f"  {key}: {row['status']} value={value} last_sample={last_sample}")
        if row["status"] != "ok":
            print(f"    action: {row['required_action']}")
            details = _format_details(row.get("details") or {})
            if details:
                print(f"    details: {details}")

    history = report.get("db_source_history") or {}
    if history:
        print("\nDB source history:")
        for key in REQUIRED_FEEDBACK_KEYS:
            columns = FEEDBACK_HISTORY_COLUMNS.get(key, ())
            print(f"  {key}:")
            for column in columns:
                print(f"    {_source_history_line(report, column)}")

    alerts = report["open_feedback_alerts"]
    print(f"\nOpen irrigation_feedback_gap alerts: {len(alerts)}")
    for alert in alerts:
        print(f"  {alert['severity']} {alert['sensor_id']}: {alert['message']}")

    field_items = report.get("field_work_items") or []
    if field_items and not report["physical_ready"]:
        print("\nField work items:")
        for item in field_items:
            due = item["next_due"] or "-"
            service = item["service_type"] or "-"
            print(
                f"  {item['requirement_id']}: status={item['current_status']} "
                f"equipment={item['equipment']} service={service} due={due}"
            )
            if item.get("description"):
                print(f"    work: {item['description']}")
            elif item.get("recommended_source"):
                print(f"    work: {item['recommended_source']}")
            if item.get("notes"):
                print(f"    notes: {item['notes']}")

    registry_targets = report.get("sensor_registry_feedback_targets") or []
    if registry_targets and not report["physical_ready"]:
        print("\nSensor registry feedback targets:")
        for item in registry_targets:
            active = str(item["active"]).lower()
            entity = item["entity_id"] or "-"
            zone = item["zone"] or "-"
            notes = f" notes={item['notes']}" if item.get("notes") else ""
            print(
                f"  {item['source_column']}: sensor_id={item['sensor_id']} "
                f"active={active} zone={zone} entity={entity}{notes}"
            )

    if report.get("esphome_error"):
        print(f"\nESPHome entity lookup warning: {report['esphome_error']}")
    esphome_candidates = report.get("esphome_candidates") or {}
    if esphome_candidates:
        print("\nESPHome candidate entities:")
        for key in REQUIRED_FEEDBACK_KEYS:
            print(f"  {key}:")
            for item in esphome_candidates.get(key, []):
                present = str(item["present"]).lower()
                name = item["name"] or "-"
                typ = item["type"] or "-"
                state = "-" if item.get("state") is None else item["state"]
                missing = (
                    "" if item.get("missing_state") is None else f" missing_state={str(item['missing_state']).lower()}"
                )
                print(f"    {item['object_id']} present={present} type={typ} state={state}{missing} name={name}")

    esphome_discovered = report.get("esphome_discovered_feedback_entities") or []
    if esphome_discovered:
        print("\nDiscovered ESPHome feedback-like entities:")
        for item in esphome_discovered:
            accepted_for = ",".join(item["accepted_for"]) if item["accepted_for"] else "near_miss"
            name = item["name"] or "-"
            state = "-" if item.get("state") is None else item["state"]
            missing = (
                "" if item.get("missing_state") is None else f" missing_state={str(item['missing_state']).lower()}"
            )
            print(
                f"  {item['type']} {item['object_id']} accepted_for={accepted_for} state={state}{missing} name={name}"
            )


def _status_line(report: dict[str, Any], key: str) -> str:
    row = report["db_status"].get(key) or {}
    value = row.get("latest_value") if row.get("latest_value") is not None else "-"
    last_sample = row.get("last_sample_ts") or "-"
    return f"{key}: status={row.get('status', 'missing')} value={value} last_sample={last_sample}"


def _join_expected(values: tuple[str, ...] | list[str]) -> str:
    return ", ".join(values) if values else "-"


def _print_accepted_sources(key: str) -> None:
    print(f"   Accepted ESPHome object IDs: {_join_expected(ESPHOME_CANDIDATES.get(key, ()))}")


def _south_probe_evidence_line(report: dict[str, Any]) -> str:
    row = report["db_status"].get("south_soil_probe_1") or {}
    details = row.get("details") or {}
    keys = (
        "positive_samples_24h",
        "last_positive_ts",
        "soil_ec_south_1_last_positive_ts",
        "soil_temp_south_1",
        "soil_ec_south_1",
        "south_2_reference_positive_samples_24h",
        "south_2_reference_last_positive_ts",
        "soil_moisture_south_2_reference",
    )
    parts = []
    for key in keys:
        value = details.get(key)
        if value is not None:
            parts.append(f"{key}={value}")
    return "; ".join(parts) if parts else "details unavailable"


def _esphome_state_by_object(report: dict[str, Any], key: str) -> str:
    items = report.get("esphome_candidates", {}).get(key, [])
    if not items:
        return "no ESPHome candidates checked"
    parts = []
    for item in items:
        state = "-" if item.get("state") is None else item["state"]
        present = str(item.get("present", False)).lower()
        missing = item.get("missing_state")
        missing_text = "" if missing is None else f", missing_state={str(missing).lower()}"
        parts.append(f"{item['object_id']} present={present}, state={state}{missing_text}")
    return "; ".join(parts)


def _source_history_line(report: dict[str, Any], source_column: str) -> str:
    item = (report.get("db_source_history") or {}).get(source_column)
    if not item:
        return f"{source_column}: history not checked"
    last_sample = item.get("last_sample_ts") or "-"
    last_valid = item.get("last_valid_ts") or "-"
    return (
        f"{source_column}: lifetime_samples={item['lifetime_samples']} "
        f"samples_24h={item['samples_24h']} valid_samples_24h={item['valid_samples_24h']} "
        f"last_sample={last_sample} last_valid={last_valid}"
    )


def _print_tracking_records(report: dict[str, Any]) -> None:
    field_items = report.get("field_work_items") or []
    if field_items:
        print("   Field requirements:")
        for item in field_items:
            due = item.get("next_due") or "-"
            service = item.get("service_type") or "-"
            print(
                f"     {item['requirement_id']}: status={item['current_status']} "
                f"equipment={item['equipment']} service={service} due={due}"
            )
    registry_targets = report.get("sensor_registry_feedback_targets") or []
    if registry_targets:
        print("   Sensor registry targets:")
        for item in registry_targets:
            active = str(item["active"]).lower()
            entity = item["entity_id"] or "-"
            zone = item["zone"] or "-"
            print(
                f"     {item['source_column']}: sensor_id={item['sensor_id']} "
                f"active={active} zone={zone} entity={entity}"
            )


def _accepted_for_text(item: dict[str, Any]) -> str:
    accepted_for = item.get("accepted_for") or []
    return ",".join(accepted_for) if accepted_for else "near_miss"


def _print_discovery_sweep(report: dict[str, Any]) -> None:
    """Print broader feedback-like entity discovery for the field handoff."""
    print("   Discovery sweep:")
    printed = False

    esphome_discovered = report.get("esphome_discovered_feedback_entities") or []
    if esphome_discovered:
        printed = True
        print("     ESPHome feedback-like entities:")
        for item in esphome_discovered:
            state = item.get("state") if item.get("state") is not None else "-"
            missing = item.get("missing_state")
            missing_text = "" if missing is None else f" missing_state={str(missing).lower()}"
            typ = item.get("type") or "-"
            name = item.get("name") or "-"
            print(
                f"       {typ} {item['object_id']} accepted_for={_accepted_for_text(item)} "
                f"state={state}{missing_text} name={name}"
            )

    if report.get("esphome_error"):
        print(f"     discovery warning: {report['esphome_error']}")

    if not printed and not report.get("esphome_error"):
        print("     no feedback-like ESPHome discoveries in this run")


def print_work_order(report: dict[str, Any]) -> None:
    """Print a concise handoff for the physical feedback repair/install."""
    print("Irrigation Feedback Field Work Order")
    print(f"physical_ready={str(report['physical_ready']).lower()} ready={str(report['ready']).lower()}")
    print("Valid-value gate:")
    for key in REQUIRED_FEEDBACK_KEYS:
        print(f"   {key}: {FEEDBACK_VALUE_RULES[key]}")
    print()
    print("1. South soil probe 1: repair or replace SEN0601/address-7")
    print(f"   DB: {_status_line(report, 'south_soil_probe_1')}")
    print(f"   Evidence: {_south_probe_evidence_line(report)}")
    print("   DB source history:")
    for column in FEEDBACK_HISTORY_COLUMNS["south_soil_probe_1"]:
        print(f"     {_source_history_line(report, column)}")
    _print_accepted_sources("south_soil_probe_1")
    print(f"   ESPHome: {_esphome_state_by_object(report, 'south_soil_probe_1')}")
    print("   Field action: reseat wiring/media contact, then replace the probe if moisture and EC stay zero.")
    print(
        "   Pass criteria: south_soil_probe_1 becomes ok; moisture and EC are nonzero in DB and live entity evidence."
    )
    print()
    print("2. Center feedback: install/map root-zone moisture plus runoff pH and EC")
    for key in ("center_root_zone_moisture", "center_runoff_ph", "center_runoff_ec"):
        print(f"   DB: {_status_line(report, key)}")
        for column in FEEDBACK_HISTORY_COLUMNS[key]:
            print(f"   DB source history: {_source_history_line(report, column)}")
        _print_accepted_sources(key)
        print(f"   ESPHome: {_esphome_state_by_object(report, key)}")
    print(
        "   Field action: add the sensors to the ESP32 Modbus bus and expose one accepted ESPHome object ID per signal."
    )
    print("   Pass criteria: center moisture, runoff pH, and runoff EC status rows all become ok.")
    print()
    print("3. Deploy boundary for accepted aliases")
    print("   Accepted ESPHome aliases come from ESPHOME_FEEDBACK_MAP in ingestor/entity_map.py.")
    print("   After alias changes merge through the normal deploy path, restart only verdify-ingestor:")
    print("   sudo systemctl restart verdify-ingestor")
    print("   Alias-only feedback changes do not require verdify-mcp unless schemas or mcp/server.py changed.")
    print("   Do not restart from a dirty shared worktree; rerun discovery and feedback-check after deploy.")
    print("   Final acceptance is a post-deploy proof, not a deploy target; run it only after the")
    print("   branch is merged, required services are restarted, and the public site/dashboard artifacts are live.")
    print()
    print("4. Tracking records that must close")
    _print_tracking_records(report)
    print()
    print("5. Discovery sweep to catch newly installed or misnamed sources")
    _print_discovery_sweep(report)
    print()
    print("6. Closure after physical work")
    print("   make irrigation-feedback-watch-field-proof")
    print("   make irrigation-feedback-finalize-dry-run")
    print("   make irrigation-feedback-finalize")
    print("   make irrigation-feedback-proof-json")
    print("   make irrigation-sensor-health-proof")
    print("   make irrigation-stack-proof")
    print("   make irrigation-completion-audit-proof")
    print("   make irrigation-completion-audit")
    print("   make irrigation-acceptance")
    print("   make irrigation-full-acceptance")
    print("   make irrigation-post-deploy-acceptance-plan")
    print("   make irrigation-post-deploy-acceptance")
    print(
        "   Finalize target runs dry-run before mutation; acceptance runs persisted field watch, "
        "sensor-health proof, finalizer, feedback JSON proof, live stack proof, "
        "completion audit proof, and strict completion audit."
    )
    print("   Full/post-deploy acceptance adds lint, tests, and migration replay before the same final gate.")
    print("   Plan target is print-only; it does not run checks, wait on sensors, or invoke the finalizer.")
    print("   Dry-run pass criteria: expected_open_feedback_alerts_after_finalize=0.")
    print("   Expected final state: no open irrigation_feedback_gap alerts, field requirements complete,")
    print("   registry targets validated, validation maintenance logs present, and strict live stack audit passing.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--work-order", action="store_true", help="Emit concise field repair/install work order")
    parser.add_argument(
        "--discover-esphome",
        action="store_true",
        help="List ESPHome-native candidate entities exposed by the greenhouse controller",
    )
    parser.add_argument(
        "--include-db-history",
        action="store_true",
        help="Include lifetime and 24-hour climate-column sample history for feedback source columns",
    )
    parser.add_argument("--direct-db", action="store_true", help="Use local psql instead of docker exec when available")
    parser.add_argument(
        "--status-only", action="store_true", help="Ignore open feedback alerts; only require status rows to be ok"
    )
    parser.add_argument("--watch", action="store_true", help="Poll until feedback is ready or timeout is reached")
    parser.add_argument("--timeout-s", type=int, default=1800, help="Maximum watch duration in seconds")
    parser.add_argument("--interval-s", type=int, default=60, help="Seconds between watch polls")
    args = parser.parse_args()

    if args.timeout_s < 0:
        parser.error("--timeout-s must be >= 0")
    if args.interval_s < 1:
        parser.error("--interval-s must be >= 1")
    if args.json and args.work_order:
        parser.error("--work-order is text output; remove --json")
    if args.watch and args.work_order:
        parser.error("--work-order is a point-in-time handoff; remove --watch")

    started = time.monotonic()
    attempt = 0
    report: dict[str, Any] | None = None
    while True:
        attempt += 1
        try:
            report, ready = build_report(
                status_only=args.status_only,
                discover_esphome=args.discover_esphome or args.work_order,
                include_db_history=args.include_db_history or args.work_order,
            )
        except Exception as exc:
            if args.json:
                print(json.dumps({"ready": False, "error": str(exc)}, indent=2, sort_keys=True))
            else:
                print(f"Irrigation feedback validation could not run: {exc}", file=sys.stderr)
            return 2

        if not args.watch:
            break
        if not args.json:
            elapsed = int(time.monotonic() - started)
            print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] attempt {attempt}, elapsed {elapsed}s")
            print_text(report)
        if ready:
            break
        elapsed = time.monotonic() - started
        if elapsed >= args.timeout_s:
            break
        sleep_s = min(args.interval_s, max(0, args.timeout_s - elapsed))
        time.sleep(sleep_s)

    if report is None:
        return 2
    if args.work_order:
        print_work_order(report)
        return 0
    if args.json:
        if args.watch:
            report = {**report, "watch_attempts": attempt, "watch_elapsed_s": int(time.monotonic() - started)}
        print(json.dumps(report, indent=2, sort_keys=True))
    elif not args.watch:
        print_text(report)
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
