#!/usr/bin/env /srv/greenhouse/.venv/bin/python3
"""Map the irrigation/fertigation objective to current proof and blockers.

Exit status is strict:
  0 = every objective item is proven complete
  1 = one or more items are still blocked or failed
  2 = audit could not run
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

FEEDBACK_DIAGNOSTIC_DETAIL_KEYS = {
    "south_soil_probe_1": (
        "positive_samples_24h",
        "last_positive_ts",
        "soil_ec_south_1_last_positive_ts",
        "soil_temp_south_1",
        "soil_ec_south_1",
        "south_2_reference_positive_samples_24h",
        "south_2_reference_last_positive_ts",
        "soil_moisture_south_2_reference",
    ),
    "center_root_zone_moisture": ("last_valid_sample_ts", "latest_raw_value"),
    "center_runoff_ph": ("last_valid_sample_ts", "latest_raw_value"),
    "center_runoff_ec": ("last_valid_sample_ts", "latest_raw_value"),
}

FEEDBACK_HISTORY_COLUMNS = {
    "south_soil_probe_1": ("soil_moisture_south_1", "soil_ec_south_1", "soil_temp_south_1"),
    "center_root_zone_moisture": ("moisture_center",),
    "center_runoff_ph": ("ph_runoff_center",),
    "center_runoff_ec": ("ec_runoff_center",),
}

MAX_DISCOVERY_EVIDENCE = 8


@dataclass
class ObjectiveResult:
    id: int
    requirement: str
    status: str
    evidence: list[str]
    blockers: list[str]


def _load_script(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _checks_by_name(checks: list[Any]) -> dict[str, Any]:
    return {check.name: check for check in checks}


def _result_from_checks(
    objective_id: int,
    requirement: str,
    check_names: tuple[str, ...],
    checks_by_name: dict[str, Any],
) -> ObjectiveResult:
    evidence: list[str] = []
    blockers: list[str] = []
    missing: list[str] = []
    for name in check_names:
        check = checks_by_name.get(name)
        if check is None:
            missing.append(name)
            continue
        evidence.append(f"{check.status.upper()} {check.name}: {check.detail}")
        if check.status != "pass":
            blockers.append(f"{check.name}: {check.status} {check.detail}")
    if missing:
        blockers.extend(f"missing stack check: {name}" for name in missing)
    return ObjectiveResult(
        objective_id,
        requirement,
        "pass" if not blockers else "fail",
        evidence,
        blockers,
    )


def _topology_result(checks_by_name: dict[str, Any]) -> ObjectiveResult:
    requirement = "Align wall drip topology as one shared south/west path."
    check = checks_by_name.get("current schedule view")
    evidence = []
    blockers = []
    if check is None:
        blockers.append("missing stack check: current schedule view")
    else:
        evidence.append(f"{check.status.upper()} {check.name}: {check.detail}")
        if check.status != "pass" or "wall_serves=south,west" not in check.detail:
            blockers.append(f"current schedule view does not prove wall_serves=south,west: {check.detail}")
    return ObjectiveResult(6, requirement, "pass" if not blockers else "fail", evidence, blockers)


def _feedback_result(report: dict[str, Any]) -> ObjectiveResult:
    requirement = "Repair/replace south soil probe 1 and add center root-zone/runoff feedback."
    evidence: list[str] = []
    blockers: list[str] = []

    db_status = report.get("db_status") or {}
    not_ok = []
    for key in ("south_soil_probe_1", "center_root_zone_moisture", "center_runoff_ph", "center_runoff_ec"):
        row = db_status.get(key) or {}
        status = row.get("status", "missing")
        value = row.get("latest_value") if row.get("latest_value") is not None else "-"
        last_sample = row.get("last_sample_ts") or "-"
        evidence.append(f"{key}: status={status} value={value} last_sample={last_sample}")
        evidence.extend(_feedback_detail_evidence(key, row))
        if status != "ok":
            not_ok.append(f"{key}:{status}")

    if not_ok:
        blockers.append("feedback rows not ok: " + ",".join(not_ok))

    alerts = report.get("open_feedback_alerts") or []
    evidence.append(f"open_irrigation_feedback_gap_alerts={len(alerts)}")
    if alerts:
        blockers.append("open irrigation_feedback_gap alerts: " + ",".join(alert["sensor_id"] for alert in alerts))

    field_items = report.get("field_work_items") or []
    incomplete_requirements = []
    missing_validation_logs = []
    for item in field_items:
        requirement_id = item.get("requirement_id")
        current_status = item.get("current_status")
        service_type = item.get("service_type") or "-"
        evidence.append(f"{requirement_id}: status={current_status} service={service_type}")
        if current_status != "complete":
            incomplete_requirements.append(f"{requirement_id}:{current_status}")
        if service_type != "validation":
            missing_validation_logs.append(str(requirement_id))
    if incomplete_requirements:
        blockers.append("open field requirements: " + ",".join(incomplete_requirements))
    if missing_validation_logs:
        blockers.append("missing validation maintenance logs: " + ",".join(missing_validation_logs))

    registry_targets = report.get("sensor_registry_feedback_targets") or []
    registry_not_active = []
    for item in registry_targets:
        source_column = item.get("source_column")
        active = bool(item.get("active"))
        evidence.append(f"{source_column}: active={str(active).lower()} sensor_id={item.get('sensor_id')}")
        if not active:
            registry_not_active.append(str(source_column))
    if registry_not_active:
        blockers.append("registry targets not active: " + ",".join(registry_not_active))

    evidence.extend(_feedback_source_history_evidence(report))
    evidence.extend(_feedback_source_evidence(report))
    evidence.extend(_feedback_discovery_evidence(report))
    return ObjectiveResult(5, requirement, "pass" if not blockers else "blocked", evidence, blockers)


def _feedback_detail_evidence(key: str, row: dict[str, Any]) -> list[str]:
    evidence = []
    status = row.get("status", "missing")
    required_action = row.get("required_action")
    if status != "ok" and required_action:
        evidence.append(f"{key} action: {required_action}")

    details = row.get("details") or {}
    if not isinstance(details, dict):
        return evidence

    parts = []
    for detail_key in FEEDBACK_DIAGNOSTIC_DETAIL_KEYS.get(key, ()):
        value = details.get(detail_key)
        if value is not None:
            parts.append(f"{detail_key}={value}")
    if parts:
        evidence.append(f"{key} details: " + " ".join(parts))
    return evidence


def _feedback_source_history_evidence(report: dict[str, Any]) -> list[str]:
    evidence = []
    history = report.get("db_source_history") or {}
    for key in ("south_soil_probe_1", "center_root_zone_moisture", "center_runoff_ph", "center_runoff_ec"):
        for column in FEEDBACK_HISTORY_COLUMNS.get(key, ()):
            item = history.get(column)
            if not item:
                continue
            last_sample = item.get("last_sample_ts") or "-"
            last_valid = item.get("last_valid_ts") or "-"
            evidence.append(
                f"db history {column}: lifetime_samples={item.get('lifetime_samples', 0)} "
                f"samples_24h={item.get('samples_24h', 0)} "
                f"valid_samples_24h={item.get('valid_samples_24h', 0)} "
                f"last_sample={last_sample} last_valid={last_valid}"
            )
    return evidence


def _feedback_source_evidence(report: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    if report.get("ha_error"):
        evidence.append(f"ha lookup error: {report['ha_error']}")
    if report.get("mqtt_error"):
        evidence.append(f"mqtt lookup warning: {report['mqtt_error']}")
    if report.get("esphome_error"):
        evidence.append(f"esphome lookup warning: {report['esphome_error']}")

    for key in ("south_soil_probe_1", "center_root_zone_moisture", "center_runoff_ph", "center_runoff_ec"):
        ha_items = (report.get("ha_candidates") or {}).get(key, [])
        ha_present = [item for item in ha_items if item.get("present") == "true"]
        if ha_present:
            values = []
            for item in ha_present:
                state = item.get("state") if item.get("state") is not None else "-"
                unit = f" {item['unit']}" if item.get("unit") else ""
                values.append(f"{item['entity_id']}={state}{unit}")
            evidence.append(f"ha {key}: " + "; ".join(values))
        elif ha_items:
            evidence.append(f"ha {key}: accepted entities absent")

        mqtt_items = (report.get("mqtt_candidates") or {}).get(key, [])
        mqtt_live = [item for item in mqtt_items if item.get("live_value") is not None]
        mqtt_retained = [item for item in mqtt_items if item.get("retained_value") is not None]
        if mqtt_live:
            evidence.append(
                f"mqtt {key}: " + "; ".join(f"{item['topic']} live={item['live_value']}" for item in mqtt_live)
            )
        elif mqtt_retained:
            evidence.append(
                f"mqtt {key}: "
                + "; ".join(f"{item['topic']} retained={item['retained_value']}" for item in mqtt_retained)
            )
        elif mqtt_items:
            evidence.append(f"mqtt {key}: accepted topics absent")

        esphome_items = (report.get("esphome_candidates") or {}).get(key, [])
        esphome_present = [item for item in esphome_items if item.get("present")]
        if esphome_present:
            values = []
            for item in esphome_present:
                state = item.get("state") if item.get("state") is not None else "-"
                missing = item.get("missing_state")
                missing_text = "" if missing is None else f", missing_state={str(missing).lower()}"
                values.append(f"{item['object_id']}={state}{missing_text}")
            evidence.append(f"esphome {key}: " + "; ".join(values))
        elif esphome_items:
            evidence.append(f"esphome {key}: accepted object IDs absent")

    return evidence


def _feedback_discovery_evidence(report: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    evidence.extend(
        _discovery_group_evidence(
            "ha",
            report.get("ha_discovered_feedback_candidates") or [],
            "entity_id",
        )
    )
    evidence.extend(
        _discovery_group_evidence(
            "mqtt",
            report.get("mqtt_discovered_feedback_candidates") or [],
            "topic",
        )
    )
    evidence.extend(
        _discovery_group_evidence(
            "esphome",
            report.get("esphome_discovered_feedback_entities") or [],
            "object_id",
        )
    )
    return evidence


def _discovery_group_evidence(label: str, items: list[dict[str, Any]], key: str) -> list[str]:
    if not items:
        return []

    evidence: list[str] = []
    accepted = [item for item in items if item.get("accepted_for")]
    near_misses = [item for item in items if not item.get("accepted_for")]
    if accepted:
        evidence.append(f"{label} discovered accepted: {_discovery_item_summary(accepted, key)}")
    if near_misses:
        evidence.append(f"{label} discovered near_miss: {_discovery_item_summary(near_misses, key)}")
    return evidence


def _discovery_item_summary(items: list[dict[str, Any]], key: str) -> str:
    names = [str(item.get(key) or "-") for item in items]
    visible = names[:MAX_DISCOVERY_EVIDENCE]
    suffix = "" if len(names) <= MAX_DISCOVERY_EVIDENCE else f", +{len(names) - MAX_DISCOVERY_EVIDENCE} more"
    return f"count={len(names)} values=" + ",".join(visible) + suffix


def evaluate_objectives(checks: list[Any], feedback_report: dict[str, Any]) -> list[ObjectiveResult]:
    checks_by_name = _checks_by_name(checks)
    return [
        _result_from_checks(
            1,
            "Make one canonical irrigation schedule/log source.",
            (
                "legacy schedule/log retired",
                "data trust ledger canonical irrigation logging",
                "current schedule view",
                "planner context canonical irrigation source",
                "schema contract legacy irrigation retired",
                "schema snapshot irrigation contract",
            ),
            checks_by_name,
        ),
        _result_from_checks(
            2,
            "Add equipment-derived v_irrigation_fertigation_runs with run pairing, overlap, and meter delta.",
            ("equipment-derived fertigation runs", "fertigation run reconstruction coherence"),
            checks_by_name,
        ),
        _result_from_checks(
            3,
            "Fix irrigation cfg readback ingestion before trusting confirmation alerts.",
            ("irrigation cfg readbacks", "irrigation setpoint confirmations"),
            checks_by_name,
        ),
        _result_from_checks(
            4,
            "Include fert relays and fert_master_valve in daily runtime/water accounting.",
            ("daily runtime/water accounting",),
            checks_by_name,
        ),
        _feedback_result(feedback_report),
        _topology_result(checks_by_name),
        _result_from_checks(
            7,
            "Publish full irrigation/fertigation Grafana dashboards to the website Irrigation page.",
            (
                "irrigation dashboard/site artifacts",
                "irrigation page discoverability",
                "live public irrigation page",
                "live public irrigation discoverability",
                "live graphs DNS routing",
                "live irrigation dashboard render",
                "irrigation acceptance tooling",
            ),
            checks_by_name,
        ),
    ]


def only_physical_feedback_blocked(results: list[ObjectiveResult]) -> bool:
    by_id = {result.id: result for result in results}
    if set(by_id) != {1, 2, 3, 4, 5, 6, 7}:
        return False
    return by_id[5].status == "blocked" and all(by_id[item_id].status == "pass" for item_id in (1, 2, 3, 4, 6, 7))


def print_text(results: list[ObjectiveResult]) -> None:
    complete = all(result.status == "pass" for result in results)
    print("Irrigation Completion Audit")
    print(f"complete={str(complete).lower()}")
    for result in results:
        print(f"{result.status.upper():7} {result.id}. {result.requirement}")
        for line in result.evidence:
            print(f"  evidence: {line}")
        for line in result.blockers:
            print(f"  blocker: {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--live-site", action="store_true", help="Include live public site/Grafana render checks")
    parser.add_argument(
        "--allow-physical-blocker",
        action="store_true",
        help="Exit 0 when the only incomplete objective is item 5 physical feedback",
    )
    parser.add_argument(
        "--mqtt-live-timeout-s",
        type=int,
        default=5,
        help="Seconds to wait for non-retained MQTT feedback source evidence",
    )
    parser.add_argument("--direct-db", action="store_true", help="Use local psql instead of docker exec when available")
    args = parser.parse_args()
    if args.mqtt_live_timeout_s < 0:
        parser.error("--mqtt-live-timeout-s must be >= 0")

    try:
        stack = _load_script(
            "validate_irrigation_stack_for_completion", REPO_ROOT / "scripts/validate-irrigation-stack.py"
        )
        feedback = _load_script(
            "validate_irrigation_feedback_for_completion",
            REPO_ROOT / "scripts/validate-irrigation-feedback.py",
        )
        checks = stack.run_checks(software_only=True, live_site=args.live_site, direct_db=args.direct_db)
        feedback_report, _ready = feedback.build_report(
            include_ha=True,
            discover_ha=True,
            status_only=False,
            discover_mqtt=True,
            discover_mqtt_all=True,
            mqtt_live_timeout_s=args.mqtt_live_timeout_s,
            discover_esphome=True,
            include_db_history=True,
        )
        results = evaluate_objectives(checks, feedback_report)
    except Exception as exc:
        if args.json:
            print(json.dumps({"complete": False, "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"Irrigation completion audit could not run: {exc}", file=sys.stderr)
        return 2

    complete = all(result.status == "pass" for result in results)
    physical_blocker_only = only_physical_feedback_blocked(results)
    if args.json:
        print(
            json.dumps(
                {
                    "complete": complete,
                    "physical_blocker_only": physical_blocker_only,
                    "results": [asdict(result) for result in results],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print_text(results)
    return 0 if complete or (args.allow_physical_blocker and physical_blocker_only) else 1


if __name__ == "__main__":
    raise SystemExit(main())
