#!/usr/bin/env python3
"""Generate firmware/policy_consumer_manifest.json (Lane E, #586; audit §8.9).

Enumerates every ESPHome ``id(<global>)`` access site of the 48 policy-vector
wire fields across the firmware YAML sources and records, per site, whether it
has been migrated to the runtime-gated policy read (``pol(kPF_<field>, ...)``
/ ``polb(...)`` inside the control tick, ``verdify_policy::policy_read`` /
``policy_read_b`` in entity/readback/diagnostic lambdas).

Tranche 2 (#586) completed the migration, so the gate is ENFORCING: --check
fails when the committed manifest drifts from a fresh scan OR when any
planner-pushable read site is neither migrated to the snapshot nor covered by
an explicit allowlist rule below. The atomic pointer swap is only meaningful
because every consumer read goes through the one gate; no consumer can be
added or left unmigrated silently.

Allowlist rules (the only unmigrated accesses permitted, each carrying its
justification in ``ALLOWLIST_RULES``):
  legacy_write_path  write accesses (``id(x) = / += / -=``). These are the
                     legacy write path being demoted (Lane C #584/#597).
  boot_repair_rmw    a READ of global X on a line that also WRITES X — the
                     boot-time NVS repair idiom in greenhouse.yaml
                     (read-check-rewrite of the legacy store itself, before
                     the engine has even boot_init'd; not a policy consumer).

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

# The ONLY permitted unmigrated accesses. Machine-detected; justifications are
# emitted verbatim into the manifest so the audit reads them in one place.
ALLOWLIST_RULES: dict[str, str] = {
    "legacy_write_path": (
        "Write access to the legacy global — the write path being demoted "
        "(Lane C #584/#597). While an experiment is armed, planner-side "
        "writers are demoted host-side to proposals (MCP set_tunable / "
        "set_plan, forecast engine); a write that still lands here only "
        "touches the legacy global, which no armed consumer reads, so it "
        "cannot actuate until the experiment disarms."
    ),
    "boot_repair_rmw": (
        "Boot-time NVS repair in greenhouse.yaml on_boot: a read of the "
        "legacy global on the same line that rewrites that global "
        "(corruption check of the legacy store itself). Runs at boot "
        "priority 600, before the policy engine boot_init; it maintains the "
        "demoted write path's storage and is not a policy consumer."
    ),
}

ID_RE = re.compile(r"id\(\s*([a-z0-9_]+)\s*\)")
WRITE_RE = re.compile(r"^\s*(\+?=|-=)\s")

# A migrated read routes through the runtime gate on the same line:
# pol()/polb() lambdas in controls.yaml, policy_read()/policy_read_b()
# elsewhere — all of which name the kPF_<field> constant.


def classify_access(line: str, match_end: int) -> str:
    """read vs write: id(x) = / += / -= is a write; ==, >=, <= etc. are reads."""
    rest = line[match_end:]
    if WRITE_RE.match(rest) and not rest.lstrip().startswith(("==",)):
        return "write"
    return "read"


def build_manifest() -> dict:
    fields = []
    total = {
        "read_sites": 0,
        "migrated_reads": 0,
        "allowlisted_reads": 0,
        "write_sites": 0,
        "control_reads": 0,
        "migrated_control_reads": 0,
        "fields_clean": 0,
    }
    violations: list[str] = []
    file_lines = {rel: (REPO_ROOT / rel).read_text().splitlines() for rel in SCAN_FILES if (REPO_ROOT / rel).exists()}

    for defn in pv.wire_fields():
        name = defn.name
        global_id = None if name in NO_FIRMWARE_GLOBAL else GLOBAL_ID_OVERRIDES.get(name, name)
        sites: list[dict] = []
        if global_id is not None:
            marker = f"kPF_{name}"
            for rel, category in SCAN_FILES.items():
                for lineno, line in enumerate(file_lines.get(rel, []), start=1):
                    accesses = [
                        classify_access(line, match.end())
                        for match in ID_RE.finditer(line)
                        if match.group(1) == global_id
                    ]
                    line_has_write = "write" in accesses
                    for access in accesses:
                        migrated = access == "read" and marker in line
                        site = {
                            "file": rel,
                            "line": lineno,
                            "category": category,
                            "access": access,
                            "migrated_to_snapshot": migrated,
                        }
                        if access == "write":
                            site["allowlist"] = "legacy_write_path"
                        elif not migrated and line_has_write:
                            # Read of X on a line that rewrites X: boot repair RMW.
                            site["allowlist"] = "boot_repair_rmw"
                        elif not migrated:
                            violations.append(f"{rel}:{lineno} unmigrated {category} read of {name} ({global_id})")
                        sites.append(site)
        reads = [s for s in sites if s["access"] == "read"]
        migrated_reads = [s for s in reads if s["migrated_to_snapshot"]]
        allowlisted_reads = [s for s in reads if s.get("allowlist")]
        writes = [s for s in sites if s["access"] == "write"]
        control_reads = [s for s in reads if s["category"] == "control"]
        migrated_control = [s for s in control_reads if s["migrated_to_snapshot"]]
        clean = len(migrated_reads) + len(allowlisted_reads) == len(reads)
        total["read_sites"] += len(reads)
        total["migrated_reads"] += len(migrated_reads)
        total["allowlisted_reads"] += len(allowlisted_reads)
        total["write_sites"] += len(writes)
        total["control_reads"] += len(control_reads)
        total["migrated_control_reads"] += len(migrated_control)
        if clean:
            total["fields_clean"] += 1
        fields.append(
            {
                "name": name,
                "wire_id": defn.wire_id,
                "global_id": global_id,
                "read_sites": len(reads),
                "reads_migrated": len(migrated_reads),
                "reads_allowlisted": len(allowlisted_reads),
                "write_sites_allowlisted": len(writes),
                "clean": clean,
                "sites": sites,
            }
        )

    return {
        "_generated_by": "scripts/gen-policy-consumer-manifest.py — DO NOT EDIT (Lane E, #586)",
        "_purpose": (
            "Per-field id(<global>) consumer map for the 48-field policy wire schema. "
            "Tranche 2 complete (audit §8.9): EVERY planner-pushable read site is migrated "
            "to the runtime-gated policy read, and the CI gate is enforcing — an unmigrated, "
            "non-allowlisted read site fails --check. Writers and boot-repair RMW reads are "
            "the only allowlisted legacy accesses (see allowlist_rules)."
        ),
        "wire_schema_version": WIRE_SCHEMA_VERSION,
        "gate": "enforcing",
        "allowlist_rules": ALLOWLIST_RULES,
        "summary": {
            "fields": len(fields),
            "fields_clean": total["fields_clean"],
            "read_sites": total["read_sites"],
            "reads_migrated": total["migrated_reads"],
            "reads_allowlisted": total["allowlisted_reads"],
            "write_sites_allowlisted": total["write_sites"],
            "control_read_sites": total["control_reads"],
            "control_reads_migrated": total["migrated_control_reads"],
        },
        "fields": fields,
    }, violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the committed manifest matches a fresh scan")
    args = parser.parse_args()

    manifest, violations = build_manifest()
    content = json.dumps(manifest, indent=2, sort_keys=False) + "\n"
    if violations:
        print("ENFORCEMENT: unmigrated planner-pushable read sites (audit §8.9 forbids arming):")
        for violation in violations:
            print(f"  {violation}")
        if args.check:
            return 1
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
            "policy consumer manifest up to date (gate enforcing): "
            f"{summary['reads_migrated']}/{summary['read_sites']} reads migrated "
            f"(+{summary['reads_allowlisted']} allowlisted), "
            f"{summary['control_reads_migrated']}/{summary['control_read_sites']} control reads, "
            f"{summary['fields_clean']}/{summary['fields']} fields clean"
        )
        return 0
    MANIFEST_PATH.write_text(content)
    print(f"wrote {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
