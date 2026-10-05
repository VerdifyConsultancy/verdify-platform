"""Reproduce #778's typed causal join from immutable SELECT-only projections.

No network, DB or device operations. Original inputs and outputs are never
modified. A named controller refusal remains distinct from physical proof.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INPUTS = ROOT / "incident-778-inputs-20260927"
START = "2026-09-04T20:30:00+00:00"
END = "2026-09-05T00:45:00+00:00"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("offset required")
    return result


def row_hash(row: dict) -> str:
    return sha(json.dumps(row, sort_keys=True, separators=(",", ":")).encode())


def analyze(inputs: Path = INPUTS) -> dict:
    manifest_raw = (inputs / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    reference = json.loads((ROOT / "results-wetting-incident-2026-09-04-v3.json").read_bytes())
    if sha(manifest_raw) != reference["private_evidence"]["manifest_sha256"]:
        raise ValueError("frozen manifest changed")
    queries = json.loads((inputs / "queries.json").read_bytes())
    tables = {}
    hashes = {}
    for name, expected in manifest["extracts"].items():
        raw = (inputs / (name + ".csv")).read_bytes()
        rows = list(csv.DictReader(io.StringIO(raw.decode())))
        if (
            sha(raw) != expected["sha256"]
            or len(rows) != expected["rows"]
            or sha(queries[name].encode()) != expected["sql_sha256"]
            or expected["sha256"] != reference["private_evidence"]["exports"][name]["sha256"]
        ):
            raise ValueError("immutable input mismatch")
        tables[name] = rows
        hashes[name] = {"sha256": sha(raw), "rows": len(rows), "sql_sha256": expected["sql_sha256"]}

    def nearest(table: str, time: datetime) -> dict | None:
        # Explicit tolerances prevent stale or future context becoming consumed truth.
        rows = tables[table]
        if not rows:
            return None
        row = min(rows, key=lambda r: abs((timestamp(r["ts"]) - time).total_seconds()))
        delta = (timestamp(row["ts"]) - time).total_seconds()
        if abs(delta) > 90:
            return None
        return {"row_sha256": row_hash(row), "offset_seconds": delta, "row": row}

    timeline = []
    for action in tables["controller_actions"]:
        time = timestamp(action["ts"])
        snapshots = {}
        for row in tables["setpoint_snapshots"]:
            if timestamp(row["ts"]) <= time:
                snapshots[row["parameter"]] = row
        climate = nearest("climate", time)
        diagnostic = nearest("diagnostics", time)
        occupancy = [r for r in tables["system_state"] if r["entity"] == "occupancy" and timestamp(r["ts"]) <= time]
        timeline.append(
            {
                "ts": action["ts"],
                "action": action,
                "action_row_sha256": row_hash(action),
                "climate_nearest_observation": climate,
                "diagnostic_nearest_observation": diagnostic,
                "guard_snapshots_asof": {
                    key: {
                        "row": row,
                        "row_sha256": row_hash(row),
                        "age_seconds": (time - timestamp(row["ts"])).total_seconds(),
                        "meaning": "DB snapshot; not independent consumed-boundary attestation",
                    }
                    for key, row in sorted(snapshots.items())
                },
                "occupancy_asof": {
                    "row": occupancy[-1],
                    "row_sha256": row_hash(occupancy[-1]),
                    "meaning": "HA/ingestor observation; not direct occupied-pin readback",
                }
                if occupancy
                else None,
            }
        )
    blocked = [
        r
        for r in timeline
        if r["action"]["fog_block_reason"] == "vent_interlock"
        and r["action"]["fog"] == "false"
        and r["action"]["vent"] == "true"
    ]
    counts = {}
    for row in blocked:
        key = row["action"]["climate_action"]
        counts[key] = counts.get(key, 0) + 1
    gaps = [
        (timestamp(b["ts"]) - timestamp(a["ts"])).total_seconds() for a, b in zip(blocked, blocked[1:], strict=False)
    ]
    expected = reference["causal_result"]
    if len(blocked) != expected["interruption_action_rows"] or counts != expected["interruption_action_counts"]:
        raise ValueError("controller refusal reproduction mismatch")
    if row_hash(blocked[0]["action"]) != expected["first_action_row_sha256"]:
        raise ValueError("onset lineage mismatch")
    equipment = [
        {"row": row, "row_sha256": row_hash(row)}
        for row in tables["equipment_state"]
        if row["equipment"] in ("fog", "mister_center", "water_flowing", "vent", "fan1", "fan2")
    ]
    supplemental = {
        name: [{"row": r, "row_sha256": row_hash(r)} for r in tables[name]]
        for name in (
            "forecast_asof",
            "override_events",
            "water_meter_events",
            "plan_delivery",
            "setpoint_changes",
            "equipment_receipts",
        )
    }
    counters = {
        key: sorted({float(r[key]) for r in tables["climate"] if r[key]})
        for key in ("water_total_gal", "mister_water_today")
    }
    return {
        "analysis_contract": 4,
        "issue": 778,
        "window_start_utc": START,
        "window_end_utc_exclusive": END,
        "inputs": hashes,
        "manifest_sha256": sha(manifest_raw),
        "queries_sha256": sha((inputs / "queries.json").read_bytes()),
        "join_definition": "nearest climate/diagnostics within90s; asof guard snapshots and HA occupancy with explicit age/source; exact raw equipment and other event streams",
        "timeline": timeline,
        "equipment_events": equipment,
        "supplemental_event_streams": supplemental,
        "reproduced_refusal": {
            "action_rows": len(blocked),
            "action_counts": counts,
            "max_action_gap_seconds": max(gaps),
            "first_blocked_row_sha256": blocked[0]["action_row_sha256"],
            "last_blocked_row_sha256": blocked[-1]["action_row_sha256"],
        },
        "counter_observations": counters,
        "counter_observations_are_effective_limits": False,
        "supported_controller_cause": reference["causal_result"]["classification"],
        "historical_firmware_source": reference["source"],
        "disposition": "source_backed_controller_refusal_with_fail_closed_unresolved_physical_verification",
        "physical_wetting_proof_allowed": False,
        "readiness_constraint": {
            "consumer_issue": 749,
            "producer": "scripts/experiment_v2_proof_packet.py",
            "prerequisite": "wetting_incident_778_disposition",
            "complete": False,
            "release_requires": [
                "source/binary-bound deployed correction",
                "current safe fog admission/rail verification",
                "qualified equipment boundary and counter-reset evidence",
                "current full GateP readiness",
            ],
        },
        "unknowns": [
            "direct occupied-pin readback",
            "continuous physical actuator truth",
            "complete source receipt coverage",
            "effective hard-volume consumed readback/reset epoch",
            "matching retained plan_journal row for reported delivery planID",
        ],
        "hypotheses": {
            "vent_compatibility": "source-backed threshold contradiction with199 repeated controller vent_interlock refusals and both crossing transitions",
            "invalid_time": "244 valid SNTP observations; retained reset_reason is not an in-window reboot",
            "occupancy": "51 HA empty observations; direct pin unknown; refusal label points to vent interlock",
            "leak_or_irrigation_fertilizer_conflict": "not reported as controlling veto; independent physical fault not disproved",
            "hard_volume_or_soft_budget": "600 is meter cumulative total;157.3797 is separate mister-today estimate;300 budget snapshot does not establish exhausted600-gallon cap",
        },
        "tool_sha256": sha(Path(__file__).read_bytes()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=INPUTS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.inputs)
    with args.output.open("x") as out:
        out.write(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
