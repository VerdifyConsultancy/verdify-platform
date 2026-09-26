#!/usr/bin/env python3
"""Generate firmware/policy_consumer_manifest.json (Lane E, #586; audit §8.9).

Enumerates every ESPHome ``id(<global>)`` read site of the 48 policy-vector
wire fields across the firmware YAML sources and records, per site, whether it
has been migrated to the per-tick policy snapshot (a gated
``pol(kPF_<field>, id(<global>))`` / ``polb(...)`` read in controls.yaml).

The committed JSON is the tranche-2 migration checklist required by §8.9
before any experiment may be ARMED (shadow mode does not require it): the
atomic pointer swap is only meaningful once every control-path consumer reads
the one snapshot. CI runs ``--check`` and fails when the committed manifest
drifts from the regenerated scan, so no consumer can be added or migrated
silently.

Site categories:
  control      controls.yaml — the live control path (must ALL be migrated
               before arming; the exemplar 11-field set is done in Lane E).
  readback     sensors.yaml cfg_* confirmation sensors (diagnostics; §8.9
               keeps legacy globals as diagnostics while armed).
  entity       tunables.yaml number/switch getters + set_action writers.
  boot_or_diag greenhouse.yaml on_boot repair + display sensors.

Every wire field maps to a firmware global under wire schema v2:
``direct_wet_stress_latest_hour`` (the v1 reserved zero-consumer row, audit
§1.5) was retired from the wire schema by the #588 decision — its former
wire_id 6 lives permanently in ``RETIRED_WIRE_IDS``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas import policy_vector as pv  # noqa: E402
from verdify_schemas.tunable_registry import WIRE_SCHEMA_VERSION  # noqa: E402

MANIFEST_PATH = REPO_ROOT / "firmware" / "policy_consumer_manifest.json"

SCAN_FILES: dict[str, str] = {
    "firmware/greenhouse/controls.yaml": "control",
    "firmware/greenhouse/sensors.yaml": "readback",
    "firmware/greenhouse/tunables.yaml": "entity",
    "firmware/greenhouse/hardware.yaml": "control",
    "firmware/greenhouse/external_sensors.yaml": "control",
    "firmware/greenhouse/policy_engine.yaml": "control",
    "firmware/greenhouse.yaml": "boot_or_diag",
}

# Registry wire-field name -> ESPHome global id, where they differ.
GLOBAL_ID_OVERRIDES: dict[str, str] = {
    "enthalpy_open": "enthalpy_open_kjkg",
    "enthalpy_close": "enthalpy_close_kjkg",
    "heat_hysteresis": "heat_hysteresis_f",
    "temp_hysteresis": "hyst_temp_f",
    "vpd_hysteresis": "hyst_vpd_kpa",
    "sw_cool_all_fans_at_high_enabled": "cool_all_fans_at_high_enabled",
    "sw_mister_closes_vent": "mister_closes_vent",
    "sw_fog_closes_vent": "fog_closes_vent",
    "sw_direct_wet_gate_enabled": "direct_wet_gate_enabled",
    "sw_direct_wet_stress_override_enabled": "direct_wet_stress_override_enabled",
}

# Wire fields with NO firmware global. Empty since wire schema v2 retired the
# one reserved zero-consumer row (#588); a nonempty set here means a new field
# was wired before its firmware landed and must be tracked explicitly.
NO_FIRMWARE_GLOBAL: frozenset[str] = frozenset()

ID_RE = re.compile(r"id\(\s*([a-z0-9_]+)\s*\)")
WRITE_RE = re.compile(r"^\s*(\+?=|-=)\s")


def classify_access(line: str, match_end: int) -> str:
    """read vs write: id(x) = / += / -= is a write; ==, >=, <= etc. are reads."""
    rest = line[match_end:]
    if WRITE_RE.match(rest) and not rest.lstrip().startswith(("==",)):
        return "write"
    return "read"


def build_manifest() -> dict:
    fields = []
    total = {"control_reads": 0, "migrated_control_reads": 0, "fields_fully_migrated": 0}
    file_lines = {rel: (REPO_ROOT / rel).read_text().splitlines() for rel in SCAN_FILES if (REPO_ROOT / rel).exists()}

    for defn in pv.wire_fields():
        name = defn.name
        global_id = None if name in NO_FIRMWARE_GLOBAL else GLOBAL_ID_OVERRIDES.get(name, name)
        sites: list[dict] = []
        if global_id is not None:
            marker = f"kPF_{name}"
            for rel, category in SCAN_FILES.items():
                for lineno, line in enumerate(file_lines.get(rel, []), start=1):
                    for match in ID_RE.finditer(line):
                        if match.group(1) != global_id:
                            continue
                        access = classify_access(line, match.end())
                        migrated = access == "read" and marker in line
                        sites.append(
                            {
                                "file": rel,
                                "line": lineno,
                                "category": category,
                                "access": access,
                                "migrated_to_snapshot": migrated,
                            }
                        )
        control_reads = [s for s in sites if s["category"] == "control" and s["access"] == "read"]
        migrated_reads = [s for s in control_reads if s["migrated_to_snapshot"]]
        control_migrated = bool(control_reads) and len(migrated_reads) == len(control_reads)
        total["control_reads"] += len(control_reads)
        total["migrated_control_reads"] += len(migrated_reads)
        if control_migrated or (global_id is not None and not control_reads):
            total["fields_fully_migrated"] += 1
        fields.append(
            {
                "name": name,
                "wire_id": defn.wire_id,
                "global_id": global_id,
                "control_read_sites": len(control_reads),
                "control_reads_migrated": len(migrated_reads),
                "control_migrated": control_migrated if control_reads else (global_id is not None),
                "tranche": 1 if control_reads and control_migrated else 2,
                "sites": sites,
            }
        )

    return {
        "_generated_by": "scripts/gen-policy-consumer-manifest.py — DO NOT EDIT (Lane E, #586)",
        "_purpose": (
            "Per-field id(<global>) consumer map for the 48-field policy wire schema. "
            "control category reads must all be migrated_to_snapshot before an experiment "
            "manifest may be armed (audit §8.9); readback/entity/boot sites stay on legacy "
            "globals as diagnostics."
        ),
        "wire_schema_version": WIRE_SCHEMA_VERSION,
        "summary": {
            "fields": len(fields),
            "fields_fully_migrated_or_trivial": total["fields_fully_migrated"],
            "control_read_sites": total["control_reads"],
            "control_reads_migrated": total["migrated_control_reads"],
        },
        "fields": fields,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the committed manifest matches a fresh scan")
    args = parser.parse_args()

    content = json.dumps(build_manifest(), indent=2, sort_keys=False) + "\n"
    if args.check:
        current = MANIFEST_PATH.read_text() if MANIFEST_PATH.exists() else None
        if current != content:
            print(
                "DRIFT: firmware/policy_consumer_manifest.json is stale — regenerate with "
                "python3 scripts/gen-policy-consumer-manifest.py"
            )
            return 1
        summary = json.loads(content)["summary"]
        print(
            "policy consumer manifest up to date: "
            f"{summary['control_reads_migrated']}/{summary['control_read_sites']} control reads migrated, "
            f"{summary['fields_fully_migrated_or_trivial']}/{summary['fields']} fields clean"
        )
        return 0
    MANIFEST_PATH.write_text(content)
    print(f"wrote {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
