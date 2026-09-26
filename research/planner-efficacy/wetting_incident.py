"""#778: allowlisted log projection and reproducible, non-actuating incident report.

This program has no DB, Kubernetes, provider or device client. Feed authorized
pod logs to project-logs on stdin; only typed events and hashes leave the parser.
Unmatched/free-form log text is never saved. Analyze frozen public CSVs plus that
projection; output is always an unresolved hold, never physical-proof authority.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

START = datetime(2026, 9, 4, 20, 30, tzinfo=UTC)
END = datetime(2026, 9, 5, 0, 45, tzinfo=UTC)
DENVER = ZoneInfo("America/Denver")
ROOT = Path(__file__).resolve().parents[2]
APP_MESSAGE = re.compile(r"^\S+ \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} (?:INFO|WARNING|ERROR|DEBUG|CRITICAL) (.*)$")
PATTERNS = {
    "occupancy_push": re.compile(r"Occupancy: pushed (occupied|empty) to ESP32"),
    "occupancy_latch": re.compile(r"Occupancy: (?:replaying )?(occupied|empty) via"),
    "transport_lost": re.compile(r"ESP32 connection lost \(unexpected\)"),
    "transport_expected_disconnect": re.compile(r"ESP32 disconnected \(expected\)"),
    "transport_connected": re.compile(r"Connected to ESP32(?: \(gap: ([0-9]+)s since disconnect\))?"),
    "occupancy_interlock": re.compile(r"Occupancy inhibit: forcing fog"),
    "occupancy_interlock_cleared": re.compile(r"Occupancy inhibit cleared"),
    "leak_interlock": re.compile(r"Leak detected: forcing fog"),
    "leak_interlock_cleared": re.compile(r"Leak lock cleared"),
    "hard_ceiling": re.compile(r"SAF-4 daily volume hard-ceiling reached"),
    "hard_ceiling_cleared": re.compile(r"SAF-4 daily volume hard-ceiling cleared"),
}
HOURLY_FIELDS = (
    "climate_sample_count",
    "temp_avg_f",
    "vpd_avg_kpa",
    "temp_north_f",
    "temp_east_f",
    "temp_west_f",
    "runtime_fan1_min",
    "runtime_fan2_min",
    "runtime_vent_min",
    "runtime_fog_min",
    "runtime_mister_center_min",
    "runtime_water_flowing_min",
    "runtime_fert_master_valve_min",
    "runtime_occupancy_quiet_override_active_min",
)
WETTING_FIELDS = ("runtime_fog_min", "runtime_mister_center_min", "runtime_water_flowing_min")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def utc(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("an explicit timestamp offset is required")
    return result.astimezone(UTC)


def local_utc(value):
    local = datetime.fromisoformat(value)
    if local.tzinfo is not None:
        raise ValueError("expected the declared naive Denver timestamp")
    first, second = local.replace(tzinfo=DENVER, fold=0), local.replace(tzinfo=DENVER, fold=1)
    if (
        first.utcoffset() != second.utcoffset()
        or first.astimezone(UTC).astimezone(DENVER).replace(tzinfo=None) != local
    ):
        raise ValueError("ambiguous or nonexistent local time requires original UTC lineage")
    return first.astimezone(UTC)


def project_logs(lines):
    """Hash raw window records without retaining or echoing their message bodies."""
    window_hash = hashlib.sha256()
    events = []
    count = 0
    malformed = 0
    first = last = None
    for raw in lines:
        try:
            text = raw.decode("utf-8")
            ts = utc(text.split(" ", 1)[0])
        except (UnicodeError, ValueError):
            malformed += 1
            continue
        if not START <= ts <= END:
            continue
        window_hash.update(raw)
        count += 1
        first = min(first, ts) if first is not None else ts
        last = max(last, ts) if last is not None else ts
        message = APP_MESSAGE.match(text)
        if message is None:
            continue
        for kind, pattern in PATTERNS.items():
            match = pattern.match(message.group(1))
            if match is None:
                continue
            event = {"ts": ts.isoformat(), "kind": kind, "source_record_sha256": digest(raw)}
            if kind in {"occupancy_push", "occupancy_latch"}:
                event["state"] = match.group(1)
            if kind == "transport_connected":
                event["reported_gap_seconds"] = int(match.group(1)) if match.group(1) is not None else None
            events.append(event)
    return {
        "projection_contract": 1,
        "window_start_utc": START.isoformat(),
        "window_end_utc": END.isoformat(),
        "window_raw_stream_sha256": window_hash.hexdigest(),
        "window_timestamped_records": count,
        "first_window_record_utc": first.isoformat() if first else None,
        "last_window_record_utc": last.isoformat() if last else None,
        "unparseable_records_in_supplied_stream": malformed,
        "events": events,
        "raw_messages_retained": False,
        "meaning": "ingestor log observations; push is not firmware confirmation; absence is not guard clearance",
    }


def number(row, field):
    value = row[field]
    if value == "":
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def analyze(hourly_raw, climate_raw, projection_raw):
    projection = json.loads(projection_raw)
    if (
        projection["projection_contract"] != 1
        or utc(projection["window_start_utc"]) != START
        or utc(projection["window_end_utc"]) != END
    ):
        raise ValueError("unexpected log projection contract/window")
    hourly = []
    seen = set()
    for row in csv.DictReader(io.StringIO(hourly_raw.decode())):
        ts = utc(row["hour_start_utc"])
        if ts >= END or ts + timedelta(hours=1) <= START:
            continue
        if row["timezone"] != "America/Denver" or local_utc(row["hour_start_local"]) != ts or ts in seen:
            raise ValueError("hourly duplicate or UTC/local lineage mismatch")
        seen.add(ts)
        item = {
            "hour_start_utc": ts.isoformat(),
            "hour_start_local": row["hour_start_local"],
            **{field: number(row, field) for field in HOURLY_FIELDS},
        }
        item["recorded_zero_wetting"] = (
            all(item[field] == 0 for field in WETTING_FIELDS)
            if (item["climate_sample_count"] or 0) > 0 and all(item[field] is not None for field in WETTING_FIELDS)
            else None
        )
        hourly.append(item)
    climate = []
    seen = set()
    for row in csv.DictReader(io.StringIO(climate_raw.decode())):
        ts = local_utc(row["bucket_local"])
        if not START <= ts <= END:
            continue
        if ts in seen:
            raise ValueError("duplicate five-minute bucket")
        seen.add(ts)
        climate.append(
            {
                "bucket_utc": ts.isoformat(),
                "bucket_local": row["bucket_local"],
                **{
                    field: number(row, field)
                    for field in (
                        "temp_avg_f",
                        "vpd_avg_kpa",
                        "outdoor_temp_f",
                        "source_samples",
                        "water_total_gal",
                        "mister_water_today_gal",
                    )
                },
            }
        )
    valid_vpd = [r for r in climate if r["vpd_avg_kpa"] is not None and (r["source_samples"] or 0) > 0]
    peak = max(valid_vpd, key=lambda r: r["vpd_avg_kpa"]) if valid_vpd else None
    plateau = [
        r
        for r in climate
        if datetime(2026, 9, 4, 21, tzinfo=UTC) <= utc(r["bucket_utc"]) <= datetime(2026, 9, 5, tzinfo=UTC)
    ]
    counters = {}
    for field in ("water_total_gal", "mister_water_today_gal"):
        values = [r[field] for r in plateau if r[field] is not None]
        counters[field] = {
            "samples": len(values),
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "is_budget_limit": False,
        }
    counts = {}
    transport = []
    for event in projection["events"]:
        if not START <= utc(event["ts"]) <= END or event["kind"] not in PATTERNS:
            raise ValueError("invalid projected event")
        kind = event["kind"]
        if kind in {"occupancy_push", "occupancy_latch"}:
            if event["state"] not in {"empty", "occupied"}:
                raise ValueError("invalid projected occupancy state")
            kind += ":" + event["state"]
        counts[kind] = counts.get(kind, 0) + 1
        if kind.startswith("transport_"):
            transport.append({key: event[key] for key in ("ts", "kind", "reported_gap_seconds") if key in event})
    dry_hours = [
        r
        for r in hourly
        if utc(r["hour_start_utc"]).hour in (21, 22, 23) and utc(r["hour_start_utc"]).date() == START.date()
    ]
    interrupted = None
    if len(dry_hours) == 3 and all(r["recorded_zero_wetting"] is not None for r in dry_hours):
        interrupted = all(r["recorded_zero_wetting"] for r in dry_hours)
    return {
        "analysis_contract": 2,
        "issue": 778,
        "window_start_utc": START.isoformat(),
        "window_end_utc": END.isoformat(),
        "input_sha256": {
            "hourly_csv": digest(hourly_raw),
            "five_minute_csv": digest(climate_raw),
            "log_projection_json": digest(projection_raw),
        },
        "audited_source_file_sha256": {
            name: digest((ROOT / name).read_bytes())
            for name in (
                "firmware/greenhouse/controls.yaml",
                "firmware/lib/greenhouse_logic.h",
                "ingestor/ingestor.py",
                "ingestor/esp32_push.py",
                "ingestor/occupancy.py",
                "scripts/export-hourly-performance-dataset.py",
            )
        },
        "audited_source_matches_running_device_verified": False,
        "hourly_context_not_clipped": sorted(hourly, key=lambda r: r["hour_start_utc"]),
        "three_hour_recorded_zero_wetting": interrupted,
        "equipment_observation_coverage_verified": False,
        "five_minute_peak_vpd_bin": peak,
        "counter_plateau_15_to_18_local": counters,
        "log_event_counts": counts,
        "transport_observations": transport,
        "disposition": "unresolved_hold",
        "physical_wetting_proof_allowed": False,
        "missing_causal_inputs": [
            "firmware interlock/admission reasons",
            "effective consumed guards/limits",
            "raw commanded/actual equipment and reset epochs",
            "requested/sent/confirmed plan lineage",
            "manual/occupancy confirmation",
            "as-of forecast vintage",
        ],
        "limitations": [
            "Hourly exporter fills absent equipment intervals with zero; the CSV cannot distinguish confirmed off from missing state evidence.",
            "Binned public observations reproduce an interruption, not precise actuator transitions or its cause.",
            "House averages are not a fixed-panel or center-canopy physical outcome.",
            "Cumulative meter and mister-today estimates have different reset/scope semantics; neither establishes a limit.",
            "Repeated empty-occupancy pushes are command-log evidence, not firmware readback or exclusion of occupancy.",
            "Reported connection gaps are rounded log values, not proof of reboot or a causal control outage.",
            "ESP32 interlocks are written separately to DB/Loki; pod stdout absence cannot clear them.",
            "No cap increase, mode change, OTA or physical proof is authorized by this report.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    projection = sub.add_parser("project-logs")
    projection.add_argument("--output", type=Path, required=True)
    report = sub.add_parser("analyze")
    for name in ("hourly", "climate", "events", "output"):
        report.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "project-logs":
            result = project_logs(sys.stdin.buffer)
        else:
            result = analyze(args.hourly.read_bytes(), args.climate.read_bytes(), args.events.read_bytes())
        result["tool_sha256"] = digest(Path(__file__).read_bytes())
        # Refuse to overwrite a previous evidence capture, including failed attempts.
        with args.output.open("x", encoding="utf-8") as output:
            output.write(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    except (ValueError, TypeError, KeyError, OSError):
        parser.exit(2, "incident analysis failed: check input contract and unused output path\n")


if __name__ == "__main__":
    main()
