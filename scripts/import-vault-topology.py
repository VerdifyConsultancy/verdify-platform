#!/usr/bin/env python3
"""import-vault-topology.py — Sprint 22 Phase 3.

One-pass importer that seeds the topology tables introduced in migrations
085-087 from the canonical Obsidian vault content at
/mnt/iris/verdify-vault/website/greenhouse/.

What it seeds (upsert semantics — re-runnable):
  - zones          from zones/*.md frontmatter
  - shelves        derived from zones[*].position_scheme
  - positions      derived from position_scheme templates
  - sensors        from equipment.md Sensors table + per-zone sensor lines
  - equipment      from equipment.md Climate/Misting/Lighting/Water/Controller
  - switches       from equipment.md Relay Map section
  - water_systems  from zones + equipment + switches cross-reference
  - pressure_groups  constants driven by firmware constraint
  - crop_catalog   from crops/*.md frontmatter

Post-seed backfill (nullable FKs on existing tables):
  - crops.zone_id, crops.position_id, crops.crop_catalog_id
  - observations.zone_id, observations.position_id
  - alert_log.zone_id
  - crop_target_profiles.crop_catalog_id

Writes a report to --report PATH with row counts and any unmatched rows.

Usage:
    python scripts/import-vault-topology.py \\
        --greenhouse-id vallery \\
        --dsn postgres://verdify:pass@127.0.0.1:5432/verdify_topology_test \\
        --report state/topology-import-report.md \\
        --dry-run           # Optional: parse + preview, no writes
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg
import yaml

VAULT_ROOT = Path("/mnt/iris/verdify-vault/website/greenhouse")
ZONES_DIR = VAULT_ROOT / "zones"
CROPS_DIR = VAULT_ROOT / "crops"
EQUIPMENT_FILE = VAULT_ROOT / "equipment.md"


# ─────────────────────────────────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────────────────────────────────


def _extract_frontmatter(md_path: Path) -> dict[str, Any]:
    text = md_path.read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    return yaml.safe_load(m.group(1)) or {}


def _extract_body(md_path: Path) -> str:
    text = md_path.read_text()
    m = re.match(r"^---\n.*?\n---\n(.*)$", text, re.DOTALL)
    return m.group(1) if m else text


def _parse_modbus_addr(sensor_str: str | None) -> int | None:
    """'Modbus addr 4 (temp, RH, VPD)' → 4; 'None (avg of N/S/E/W)' → None."""
    if not sensor_str:
        return None
    m = re.search(r"Modbus addr (\d+)", sensor_str)
    return int(m.group(1)) if m else None


def _parse_peak_temp(peak_temp: str | None) -> float | None:
    """'100°F+' → 100.0; '~91°F (...)' → 91.0."""
    if not peak_temp:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", str(peak_temp))
    return float(m.group(1)) if m else None


# ─────────────────────────────────────────────────────────────────────────
# Position scheme expansion
# ─────────────────────────────────────────────────────────────────────────

# A position_scheme field like "SOUTH-SHELF-{T|B}{1..4}, SOUTH-FLOOR-{N}"
# encodes multiple shelf/position templates. We expand each into:
#   (shelf_slug, shelf_name, shelf_kind, [position labels...])

# Per zone, the default floor-position count when the scheme uses {N} without
# an explicit upper bound. The actual physical count is knowable only via
# survey; we seed a reasonable default and let Phase 4 CRUD adjust.
DEFAULT_FLOOR_POSITIONS = 4
DEFAULT_HANG_POSITIONS = 2


@dataclass
class ShelfSpec:
    slug: str
    name: str
    kind: str
    tier: int | None
    position_scheme: str
    position_labels: list[str]
    mount_type: str


def _expand_positions(zone_slug: str, scheme: str) -> list[ShelfSpec]:
    """Expand a position_scheme string into concrete ShelfSpec records.

    Handles the three patterns we see in the vault today:
      1. "SOUTH-SHELF-{T|B}{1..4}, SOUTH-FLOOR-{N}" — two-tier shelf + floor
      2. "EAST-HYDRO-{1..60} + EAST-SHELF-{T|B}{1..3}" — hydro bank + shelf
      3. "CENTER-HANG-{1|2}, CENTER-FLOOR-{N}" — hang + floor
      4. "No planting positions" — empty
    """
    shelves: list[ShelfSpec] = []
    if not scheme or scheme.lower().startswith("no planting"):
        return shelves

    zone_upper = zone_slug.upper()
    # Split by `,` or `+`
    for chunk in re.split(r"[,+]", scheme):
        chunk = chunk.strip()
        if not chunk:
            continue

        # Pattern: "{ZONE}-HYDRO-{1..60}" — single-range, flat
        m = re.match(rf"{zone_upper}-HYDRO-\{{(\d+)\.\.(\d+)\}}", chunk)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            shelves.append(
                ShelfSpec(
                    slug=f"{zone_slug}_hydro",
                    name=f"{zone_slug.title()} Hydro",
                    kind="nft",
                    tier=None,
                    position_scheme=chunk,
                    position_labels=[f"{zone_upper}-HYDRO-{i}" for i in range(lo, hi + 1)],
                    mount_type="nft_port",
                )
            )
            continue

        # Pattern: "{ZONE}-SHELF-{T|B}{1..4}" — top + bottom tiers
        m = re.match(rf"{zone_upper}-SHELF-\{{([A-Z]\|[A-Z])\}}\{{(\d+)\.\.(\d+)\}}", chunk)
        if m:
            tiers_raw, lo, hi = m.group(1), int(m.group(2)), int(m.group(3))
            tiers = tiers_raw.split("|")
            tier_map = {"T": ("top", 1), "B": ("bottom", 0), "M": ("middle", 2)}
            for t in tiers:
                label, tier_num = tier_map.get(t, (t.lower(), None))
                shelves.append(
                    ShelfSpec(
                        slug=f"{zone_slug}_shelf_{label}",
                        name=f"{zone_slug.title()} Shelf ({label.title()})",
                        kind="shelf",
                        tier=tier_num,
                        position_scheme=chunk,
                        position_labels=[f"{zone_upper}-SHELF-{t}{i}" for i in range(lo, hi + 1)],
                        mount_type="shelf_slot",
                    )
                )
            continue

        # Pattern: "{ZONE}-FLOOR-{N}" — open count floor
        if re.match(rf"{zone_upper}-FLOOR-\{{N\}}", chunk):
            shelves.append(
                ShelfSpec(
                    slug=f"{zone_slug}_floor",
                    name=f"{zone_slug.title()} Floor",
                    kind="floor",
                    tier=0,
                    position_scheme=chunk,
                    position_labels=[f"{zone_upper}-FLOOR-{i}" for i in range(1, DEFAULT_FLOOR_POSITIONS + 1)],
                    mount_type="pot",
                )
            )
            continue

        # Pattern: "{ZONE}-HANG-{1|2}" — explicit enumerated list
        m = re.match(rf"{zone_upper}-HANG-\{{([\d|]+)\}}", chunk)
        if m:
            nums = m.group(1).split("|")
            shelves.append(
                ShelfSpec(
                    slug=f"{zone_slug}_hang",
                    name=f"{zone_slug.title()} Hang",
                    kind="hang",
                    tier=None,
                    position_scheme=chunk,
                    position_labels=[f"{zone_upper}-HANG-{n}" for n in nums],
                    mount_type="hanging_hook",
                )
            )
            continue

    return shelves


# ─────────────────────────────────────────────────────────────────────────
# Equipment + relay map parsing
# ─────────────────────────────────────────────────────────────────────────

# Hand-curated equipment catalog derived from equipment.md + zone pages.
# Keyed by equipment slug (must match telemetry.EquipmentId Literal).
STATIC_EQUIPMENT: list[dict[str, Any]] = [
    # Climate control
    {
        "slug": "heat1",
        "zone": None,
        "kind": "heater",
        "name": "Electric Heater",
        "model": "Generic",
        "manufacturer": None,
        "watts": 1500,
        "cost_per_hour_usd": 0.167,
    },
    {
        "slug": "heat2",
        "zone": None,
        "kind": "heater",
        "name": "Gas Furnace",
        "model": "Lennox LF24-75A-5",
        "manufacturer": "Lennox",
        "watts": None,
        "cost_per_hour_usd": 0.623,
    },
    {
        "slug": "fan1",
        "zone": "south",
        "kind": "fan",
        "name": "Exhaust Fan 1",
        "model": 'KEN BROWN 18" Shutter',
        "manufacturer": "KEN BROWN",
        "watts": 52,
        "cost_per_hour_usd": 0.006,
    },
    {
        "slug": "fan2",
        "zone": "south",
        "kind": "fan",
        "name": "Exhaust Fan 2",
        "model": 'KEN BROWN 18" Shutter',
        "manufacturer": "KEN BROWN",
        "watts": 52,
        "cost_per_hour_usd": 0.006,
    },
    {
        "slug": "vent",
        "zone": "north",
        "kind": "vent",
        "name": "Intake Vent",
        "model": "Mechanical actuator",
        "manufacturer": None,
        "watts": 10,
        "cost_per_hour_usd": 0.001,
    },
    {
        "slug": "fog",
        "zone": "center",
        "kind": "fog",
        "name": "Fog Machine",
        "model": "AquaFog XE 2000 HumidiFan",
        "manufacturer": "AquaFog",
        "watts": 1644,
        "cost_per_hour_usd": 0.182,
    },
    # Misters
    {
        "slug": "mister_south",
        "zone": "south",
        "kind": "mister",
        "name": "South Misters",
        "model": "Micro Drip 360",
        "manufacturer": None,
        "watts": None,
        "cost_per_hour_usd": None,
        "specs": {"head_count": 6, "nozzle_count": 30, "mount": "Wall-mounted, 2 rows"},
    },
    {
        "slug": "mister_west",
        "zone": "west",
        "kind": "mister",
        "name": "West Misters",
        "model": "Micro Drip 360",
        "manufacturer": None,
        "watts": None,
        "cost_per_hour_usd": None,
        "specs": {"head_count": 3, "nozzle_count": 15, "mount": "Overhead"},
    },
    {
        "slug": "mister_center",
        "zone": "center",
        "kind": "mister",
        "name": "Center Misters",
        "model": "Micro Drip 360",
        "manufacturer": None,
        "watts": None,
        "cost_per_hour_usd": None,
        "specs": {"head_count": 5, "nozzle_count": 25, "mount": "Overhead"},
    },
    # Drip lines
    {
        "slug": "drip_wall",
        "zone": None,
        "kind": "drip",
        "name": "Wall Drip",
        "model": None,
        "manufacturer": None,
        "watts": None,
        "cost_per_hour_usd": None,
        "specs": {"serves_zones": ["south", "west"], "control": "Scheduled daily 6 AM × 10 min"},
    },
    {
        "slug": "drip_center",
        "zone": "center",
        "kind": "drip",
        "name": "Center Drip",
        "model": None,
        "manufacturer": None,
        "watts": None,
        "cost_per_hour_usd": None,
        "specs": {
            "direct_wet_gated": True,
            "serves": "Center hanging/root-zone wetting path",
        },
    },
    # Fertigation master valve
    {
        "slug": "fert_master_valve",
        "zone": None,
        "kind": "valve",
        "name": "Fertigation Master Valve",
        "model": None,
        "manufacturer": None,
        "watts": None,
        "cost_per_hour_usd": None,
        "specs": {"purpose": "Gates ALL fert delivery"},
    },
    # Fertigation paths — share nozzles with clean-water equipment but gated by fert_master_valve
    {
        "slug": "mister_south_fert",
        "zone": "south",
        "kind": "valve",
        "name": "South Misters (fert path)",
        "model": None,
        "manufacturer": None,
        "watts": None,
        "cost_per_hour_usd": None,
        "specs": {"shares_heads_with": "mister_south", "fert_gated": True},
    },
    {
        "slug": "mister_west_fert",
        "zone": "west",
        "kind": "valve",
        "name": "West Misters (fert path)",
        "model": None,
        "manufacturer": None,
        "watts": None,
        "cost_per_hour_usd": None,
        "specs": {"shares_heads_with": "mister_west", "fert_gated": True},
    },
    {
        "slug": "drip_wall_fert",
        "zone": None,
        "kind": "valve",
        "name": "Wall Drip (fert path)",
        "model": None,
        "manufacturer": None,
        "watts": None,
        "cost_per_hour_usd": None,
        "specs": {"shares_heads_with": "drip_wall", "fert_gated": True},
    },
    {
        "slug": "drip_center_fert",
        "zone": "center",
        "kind": "valve",
        "name": "Center Drip (fert path)",
        "model": None,
        "manufacturer": None,
        "watts": None,
        "cost_per_hour_usd": None,
        "specs": {
            "shares_heads_with": "drip_center",
            "fert_gated": True,
            "direct_wet_gated": True,
        },
    },
    # Lighting circuits
    {
        "slug": "grow_light_grow",
        "zone": None,
        "kind": "light",
        "name": "Grow Light Circuit (2FT)",
        "model": "Barrina T8 24W",
        "manufacturer": "Barrina",
        "watts": 816,
        "cost_per_hour_usd": None,
        "specs": {"fixture_count": 34, "size": "2FT", "cri": 80},
    },
    {
        "slug": "grow_light_main",
        "zone": None,
        "kind": "light",
        "name": "Main Lighting Circuit (4FT)",
        "model": "Barrina T8 42W",
        "manufacturer": "Barrina",
        "watts": 630,
        "cost_per_hour_usd": None,
        "specs": {"fixture_count": 15, "size": "4FT", "cri": 98},
    },
]


# Static relay map (from equipment.md Relay Map section). Each entry is one pin.
STATIC_RELAY_MAP: list[dict[str, Any]] = [
    # pcf_out_1 (0x20)
    {
        "board": "pcf_out_1",
        "pin": 0,
        "equipment": "mister_west",
        "purpose": "West misters (clean)",
        "state_source_column": "mister_west",
    },
    {
        "board": "pcf_out_1",
        "pin": 1,
        "equipment": "mister_west_fert",
        "purpose": "West misters (fert)",
        "state_source_column": "mister_west_fert",
    },
    {
        "board": "pcf_out_1",
        "pin": 2,
        "equipment": "mister_south_fert",
        "purpose": "South misters (fert)",
        "state_source_column": "mister_south_fert",
    },
    {
        "board": "pcf_out_1",
        "pin": 3,
        "equipment": "mister_south",
        "purpose": "South misters (clean)",
        "state_source_column": "mister_south",
    },
    {
        "board": "pcf_out_1",
        "pin": 4,
        "equipment": "drip_wall",
        "purpose": "Wall drip (clean)",
        "state_source_column": "drip_wall",
    },
    {
        "board": "pcf_out_1",
        "pin": 5,
        "equipment": "mister_center",
        "purpose": "Center misters (clean)",
        "state_source_column": "mister_center",
    },
    {
        "board": "pcf_out_1",
        "pin": 6,
        "equipment": "drip_center_fert",
        "purpose": "Center drip (fert)",
        "state_source_column": "drip_center_fert",
    },
    {
        "board": "pcf_out_1",
        "pin": 7,
        "equipment": "drip_center",
        "purpose": "Center drip (clean)",
        "state_source_column": "drip_center",
    },
    # pcf_out_2 (0x21)
    {
        "board": "pcf_out_2",
        "pin": 0,
        "equipment": "drip_wall_fert",
        "purpose": "Wall drip (fert)",
        "state_source_column": "drip_wall_fert",
    },
    {
        "board": "pcf_out_2",
        "pin": 1,
        "equipment": "fert_master_valve",
        "purpose": "Fertigation master valve",
        "state_source_column": None,
    },
    {
        "board": "pcf_out_2",
        "pin": 2,
        "equipment": "heat2",
        "purpose": "Gas furnace (Heat2)",
        "state_source_column": "heat2",
    },
    {"board": "pcf_out_2", "pin": 3, "equipment": "fan1", "purpose": "Exhaust fan 1", "state_source_column": "fan1"},
    {"board": "pcf_out_2", "pin": 4, "equipment": "fan2", "purpose": "Exhaust fan 2", "state_source_column": "fan2"},
    {"board": "pcf_out_2", "pin": 5, "equipment": "vent", "purpose": "Intake vent", "state_source_column": "vent"},
    {"board": "pcf_out_2", "pin": 6, "equipment": "fog", "purpose": "Fog machine", "state_source_column": "fog"},
    {
        "board": "pcf_out_2",
        "pin": 7,
        "equipment": "heat1",
        "purpose": "Electric heater (Heat1)",
        "state_source_column": "heat1",
    },
]

# Water systems — derived from relay map + zone pages.
# kind: mister/drip/fog/fertigation/nft/manual
# pressure_group: mister_manifold | drip_manifold | None
STATIC_WATER_SYSTEMS: list[dict[str, Any]] = [
    {
        "slug": "south_mister_clean",
        "zone": "south",
        "equipment": "mister_south",
        "pressure_group": "mister_manifold",
        "kind": "mister",
        "name": "South Misters (clean)",
        "nozzle_count": 30,
        "head_count": 6,
        "mount": "Wall-mounted, 2 rows",
        "is_fert_path": False,
        "effectiveness_note": "Most effective zone — 0.15 kPa avg VPD drop per pulse",
    },
    {
        "slug": "south_mister_fert",
        "zone": "south",
        "equipment": "mister_south_fert",
        "pressure_group": "mister_manifold",
        "kind": "fertigation",
        "name": "South Misters (fert)",
        "nozzle_count": 30,
        "head_count": 6,
        "mount": "Wall-mounted, 2 rows",
        "is_fert_path": True,
        "effectiveness_note": None,
    },
    {
        "slug": "west_mister_clean",
        "zone": "west",
        "equipment": "mister_west",
        "pressure_group": "mister_manifold",
        "kind": "mister",
        "name": "West Misters (clean)",
        "nozzle_count": 15,
        "head_count": 3,
        "mount": "Overhead",
        "is_fert_path": False,
        "effectiveness_note": None,
    },
    {
        "slug": "west_mister_fert",
        "zone": "west",
        "equipment": "mister_west_fert",
        "pressure_group": "mister_manifold",
        "kind": "fertigation",
        "name": "West Misters (fert)",
        "nozzle_count": 15,
        "head_count": 3,
        "mount": "Overhead",
        "is_fert_path": True,
        "effectiveness_note": None,
    },
    {
        "slug": "center_mister_clean",
        "zone": "center",
        "equipment": "mister_center",
        "pressure_group": "mister_manifold",
        "kind": "mister",
        "name": "Center Misters (clean)",
        "nozzle_count": 25,
        "head_count": 5,
        "mount": "Overhead",
        "is_fert_path": False,
        "effectiveness_note": None,
    },
    {
        "slug": "center_drip_clean",
        "zone": "center",
        "equipment": "drip_center",
        "pressure_group": "drip_manifold",
        "kind": "drip",
        "name": "Center Drip (clean)",
        "nozzle_count": None,
        "head_count": None,
        "mount": "Center hanging/root-zone wetting path",
        "is_fert_path": False,
        "effectiveness_note": "Direct-wet gated by center start offset and drydown window",
    },
    {
        "slug": "center_drip_fert",
        "zone": "center",
        "equipment": "drip_center_fert",
        "pressure_group": "drip_manifold",
        "kind": "fertigation",
        "name": "Center Drip (fert)",
        "nozzle_count": None,
        "head_count": None,
        "mount": "Same center wetting path as center_drip_clean, fert supply path",
        "is_fert_path": True,
        "effectiveness_note": "Requires fert_master_valve and center direct-wet permission",
    },
    {
        "slug": "wall_drip_clean",
        "zone": None,
        "equipment": "drip_wall",
        "pressure_group": "drip_manifold",
        "kind": "drip",
        "name": "Wall Drip (clean)",
        "nozzle_count": None,
        "head_count": None,
        "mount": "Shared: south + west zones",
        "is_fert_path": False,
        "effectiveness_note": "Shared with west zone — ONE zone",
    },
    {
        "slug": "wall_drip_fert",
        "zone": None,
        "equipment": "drip_wall_fert",
        "pressure_group": "drip_manifold",
        "kind": "fertigation",
        "name": "Wall Drip (fert)",
        "nozzle_count": None,
        "head_count": None,
        "mount": "Same heads as wall_drip_clean, fert supply path",
        "is_fert_path": True,
        "effectiveness_note": None,
    },
    {
        "slug": "center_fog",
        "zone": "center",
        "equipment": "fog",
        "pressure_group": None,
        "kind": "fog",
        "name": "Center Fog",
        "nozzle_count": None,
        "head_count": None,
        "mount": "Ceiling-mounted",
        "is_fert_path": False,
        "effectiveness_note": "AquaFog XE 2000 HumidiFan",
    },
]


STATIC_PRESSURE_GROUPS: list[dict[str, Any]] = [
    {
        "slug": "mister_manifold",
        "name": "Mister Pressure Manifold",
        "constraint_kind": "mister_max_1",
        "max_concurrent": 1,
        "description": "Solenoid manifold: only one mister zone firing at a time (pressure constraint).",
    },
    {
        "slug": "drip_manifold",
        "name": "Drip Pressure Manifold",
        "constraint_kind": "drip_max_1",
        "max_concurrent": 1,
        "description": "Solenoid manifold: only one drip zone firing at a time.",
    },
]


STATIC_SENSORS: list[dict[str, Any]] = [
    # Climate probes — 6 Tzone RS485 (SHT3X) — per zone where available
    {
        "slug": "climate.north",
        "zone": "north",
        "kind": "climate_probe",
        "protocol": "modbus_rtu",
        "model": "Tzone RS485 (SHT3X)",
        "modbus_addr": 2,
        "unit": "°F / % RH",
        "source_table": "climate",
        "source_column": "temp_north",
        "expected_interval_s": 10,
        "accuracy": "±0.3°C, ±2% RH",
    },
    {
        "slug": "climate.south",
        "zone": "south",
        "kind": "climate_probe",
        "protocol": "modbus_rtu",
        "model": "Tzone RS485 (SHT3X)",
        "modbus_addr": 4,
        "unit": "°F / % RH",
        "source_table": "climate",
        "source_column": "temp_south",
        "expected_interval_s": 10,
        "accuracy": "±0.3°C, ±2% RH",
    },
    {
        "slug": "climate.east",
        "zone": "east",
        "kind": "climate_probe",
        "protocol": "modbus_rtu",
        "model": "Tzone RS485 (SHT3X)",
        "modbus_addr": 5,
        "unit": "°F / % RH",
        "source_table": "climate",
        "source_column": "temp_east",
        "expected_interval_s": 10,
        "accuracy": "±0.3°C, ±2% RH",
    },
    {
        "slug": "climate.west",
        "zone": "west",
        "kind": "climate_probe",
        "protocol": "modbus_rtu",
        "model": "Tzone RS485 (SHT3X)",
        "modbus_addr": 3,
        "unit": "°F / % RH",
        "source_table": "climate",
        "source_column": "temp_west",
        "expected_interval_s": 10,
        "accuracy": "±0.3°C, ±2% RH",
    },
    {
        "slug": "climate.control",
        "zone": None,
        "kind": "climate_probe",
        "protocol": "modbus_rtu",
        "model": "Tzone RS485 (SHT3X)",
        "modbus_addr": 6,
        "unit": "°F / % RH",
        "source_table": "climate",
        "source_column": "temp_control",
        "expected_interval_s": 10,
        "accuracy": "±0.3°C, ±2% RH",
    },
    {
        "slug": "climate.intake",
        "zone": None,
        "kind": "climate_probe",
        "protocol": "modbus_rtu",
        "model": "Tzone RS485 (SHT3X)",
        "modbus_addr": 1,
        "unit": "°F / % RH",
        "source_table": "climate",
        "source_column": "temp_intake",
        "expected_interval_s": 10,
        "accuracy": "±0.3°C, ±2% RH",
    },
    # Soil probes
    {
        "slug": "soil.south",
        "zone": "south",
        "kind": "soil_probe",
        "protocol": "modbus_rtu",
        "model": "DFRobot SEN0601",
        "modbus_addr": 7,
        "unit": "%VWC / °F / µS/cm",
        "source_table": "climate",
        "source_column": "soil_moisture_south_1",
        "expected_interval_s": 30,
        "accuracy": "moisture, temp, EC",
    },
    {
        "slug": "soil.south_2",
        "zone": "south",
        "kind": "soil_probe",
        "protocol": "modbus_rtu",
        "model": "DFRobot SEN0600",
        "modbus_addr": 8,
        "unit": "%VWC / °F / µS/cm",
        "source_table": "climate",
        "source_column": "soil_moisture_south_2",
        "expected_interval_s": 30,
        "accuracy": "moisture, temp",
    },
    {
        "slug": "soil.west",
        "zone": "west",
        "kind": "soil_probe",
        "protocol": "modbus_rtu",
        "model": "DFRobot SEN0600",
        "modbus_addr": 9,
        "unit": "%VWC / °F / µS/cm",
        "source_table": "climate",
        "source_column": "soil_moisture_west",
        "expected_interval_s": 30,
        "accuracy": "moisture, temp",
    },
    # CO2
    {
        "slug": "co2.indoor",
        "zone": None,
        "kind": "co2",
        "protocol": "adc",
        "model": "Kincony analog",
        "gpio_pin": 36,
        "unit": "ppm",
        "source_table": "climate",
        "source_column": None,
        "expected_interval_s": 30,
        "accuracy": "0–10K ppm",
    },
    # Light (indoor)
    {
        "slug": "light.indoor",
        "zone": None,
        "kind": "light",
        "protocol": "adc",
        "model": "Kincony LDR",
        "gpio_pin": 35,
        "unit": "lux",
        "source_table": "climate",
        "source_column": "lux",
        "expected_interval_s": 30,
        "accuracy": "Poor (saturates 28K lux)",
    },
    # Water flow
    {
        "slug": "water.flow",
        "zone": None,
        "kind": "flow",
        "protocol": "gpio_pulse",
        "model": "DAE AS200U-75P",
        "gpio_pin": 33,
        "unit": "gallons",
        "source_table": "climate",
        "source_column": "water_total_gal",
        "expected_interval_s": 30,
        "accuracy": "Pulse counter",
    },
    # Hydro quality
    {
        "slug": "hydro.quality",
        "zone": "east",
        "kind": "hydro_quality",
        "protocol": "http_api",
        "model": "YINMIK",
        "source_table": "climate",
        "source_column": "hydro_ph",
        "expected_interval_s": 300,
        "accuracy": "pH, EC, TDS, ORP, temp",
    },
    # Weather (Tempest)
    {
        "slug": "weather.tempest",
        "zone": None,
        "kind": "weather",
        "protocol": "http_api",
        "model": "Tempest",
        "source_table": "climate",
        "source_column": "outdoor_temp_f",
        "expected_interval_s": 60,
        "accuracy": "20 outdoor metrics",
    },
    # Energy (Shelly EM50)
    {
        "slug": "energy.shelly",
        "zone": None,
        "kind": "energy",
        "protocol": "http_api",
        "model": "Shelly EM50",
        "source_table": "energy",
        "source_column": "watts_total",
        "expected_interval_s": 300,
        "accuracy": "3 circuits",
    },
    # Cameras
    {
        "slug": "camera.east",
        "zone": "east",
        "kind": "camera",
        "protocol": "frigate",
        "model": "Amcrest IP8M-T2599EW-AI-V3",
        "unit": "4K",
        "expected_interval_s": None,
        "accuracy": "125° FOV turret",
    },
    {
        "slug": "camera.south",
        "zone": "south",
        "kind": "camera",
        "protocol": "frigate",
        "model": "Amcrest IP8M-T2599EW-AI-V3",
        "unit": "4K",
        "expected_interval_s": None,
        "accuracy": "125° FOV turret",
    },
    # Derived (VPD / DLI / averages)
    {
        "slug": "derived.vpd_avg",
        "zone": None,
        "kind": "derived",
        "protocol": "derived",
        "source_table": "climate",
        "source_column": "vpd_avg",
        "expected_interval_s": 30,
        "accuracy": "Magnus formula on ESP32",
    },
    {
        "slug": "derived.temp_avg",
        "zone": None,
        "kind": "derived",
        "protocol": "derived",
        "source_table": "climate",
        "source_column": "temp_avg",
        "expected_interval_s": 30,
        "accuracy": "Zone average",
    },
]


# ─────────────────────────────────────────────────────────────────────────
# Zone parsing
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class ZoneSpec:
    slug: str
    name: str
    orientation: str | None
    sensor_modbus_addr: int | None
    peak_temp_f: float | None
    status: str
    notes: str | None
    shelves: list[ShelfSpec] = field(default_factory=list)


def parse_zones() -> list[ZoneSpec]:
    zones: list[ZoneSpec] = []
    for md in sorted(ZONES_DIR.glob("*.md")):
        if md.name == "index.md":
            continue
        fm = _extract_frontmatter(md)
        if fm.get("type") != "zone":
            continue
        slug = fm.get("zone")
        if not slug:
            continue

        status = "offline" if fm.get("status") == "OFFLINE" or "offline" in (fm.get("tags") or []) else "active"
        shelves = _expand_positions(slug, fm.get("position_scheme") or "")
        zones.append(
            ZoneSpec(
                slug=slug,
                name=fm.get("title", f"{slug.title()} Zone").strip('"'),
                orientation=fm.get("orientation"),
                sensor_modbus_addr=_parse_modbus_addr(fm.get("sensor")),
                peak_temp_f=_parse_peak_temp(fm.get("peak_temp")),
                status=status,
                notes=None,
                shelves=shelves,
            )
        )
    return zones


# ─────────────────────────────────────────────────────────────────────────
# Crop catalog parsing
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class CropCatalogSpec:
    slug: str
    common_name: str
    scientific_name: str | None
    category: str
    season: str
    cycle_days_min: int | None
    cycle_days_max: int | None


# Map vault `crop:` slugs to crop_catalog category.
CATEGORY_MAP = {
    "tomatoes": "fruit",
    "peppers": "fruit",
    "cucumbers": "vine",
    "strawberries": "fruit",
    "lettuce": "leafy_green",
    "basil": "herb",
    "herbs": "herb",
    "canna": "ornamental",
    "orchid": "ornamental",
}

# Map vault `season:` to the DB season enum.
SEASON_MAP = {
    "warm": "warm",
    "cool": "cool",
    "hot": "hot",
    "perennial": "year_round",
    "tropical": "hot",
    "varies": "year_round",
}


def parse_crop_catalog() -> list[CropCatalogSpec]:
    crops: list[CropCatalogSpec] = []
    for md in sorted(CROPS_DIR.glob("*.md")):
        if md.name == "index.md":
            continue
        fm = _extract_frontmatter(md)
        if fm.get("type") != "crop-profile":
            continue
        slug_raw = fm.get("crop")
        if not slug_raw:
            continue
        slug = slug_raw.replace("-", "_")
        cycle_days = fm.get("cycle_days")
        cycle_min, cycle_max = None, None
        if isinstance(cycle_days, str):
            m = re.match(r"(\d+)-(\d+)", cycle_days)
            if m:
                cycle_min, cycle_max = int(m.group(1)), int(m.group(2))
        crops.append(
            CropCatalogSpec(
                slug=slug,
                common_name=fm.get("title", slug.title()).strip('"'),
                scientific_name=None,  # Not in frontmatter today; enrich later
                category=CATEGORY_MAP.get(slug, "ornamental"),
                season=SEASON_MAP.get(fm.get("season"), "year_round"),
                cycle_days_min=cycle_min,
                cycle_days_max=cycle_max,
            )
        )
    return crops


# ─────────────────────────────────────────────────────────────────────────
# Upsert + backfill
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class ImportStats:
    greenhouse: int = 0
    zones: int = 0
    shelves: int = 0
    positions: int = 0
    sensors: int = 0
    equipment: int = 0
    switches: int = 0
    water_systems: int = 0
    pressure_groups: int = 0
    crop_catalog: int = 0
    backfill_crops_zone: int = 0
    backfill_crops_position: int = 0
    backfill_crops_catalog: int = 0
    backfill_observations_zone: int = 0
    backfill_observations_position: int = 0
    backfill_alert_log_zone: int = 0
    backfill_ctp_catalog: int = 0
    orphans_crops_no_position: int = 0
    orphans_crops_no_catalog: int = 0


async def upsert_all(
    conn: asyncpg.Connection,
    greenhouse_id: str,
    zones: list[ZoneSpec],
    crops: list[CropCatalogSpec],
    stats: ImportStats,
    dry_run: bool,
) -> None:
    # ── zones ─────────────────────────────────────────────────────────
    for z in zones:
        if dry_run:
            stats.zones += 1
            continue
        await conn.execute(
            """
            INSERT INTO zones (greenhouse_id, slug, name, orientation, sensor_modbus_addr,
                               peak_temp_f, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (greenhouse_id, slug) DO UPDATE SET
                name = EXCLUDED.name,
                orientation = EXCLUDED.orientation,
                sensor_modbus_addr = EXCLUDED.sensor_modbus_addr,
                peak_temp_f = EXCLUDED.peak_temp_f,
                status = EXCLUDED.status
            """,
            greenhouse_id,
            z.slug,
            z.name,
            z.orientation,
            z.sensor_modbus_addr,
            z.peak_temp_f,
            z.status,
        )
        stats.zones += 1

    # ── shelves + positions ──────────────────────────────────────────
    for z in zones:
        if not dry_run:
            zone_id = await conn.fetchval(
                "SELECT id FROM zones WHERE greenhouse_id=$1 AND slug=$2",
                greenhouse_id,
                z.slug,
            )
            if zone_id is None:
                continue
        else:
            zone_id = None

        for sh in z.shelves:
            if not dry_run:
                await conn.execute(
                    """
                    INSERT INTO shelves (greenhouse_id, zone_id, slug, name, kind, tier, position_scheme)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (greenhouse_id, slug) DO UPDATE SET
                        zone_id = EXCLUDED.zone_id,
                        name = EXCLUDED.name,
                        kind = EXCLUDED.kind,
                        tier = EXCLUDED.tier,
                        position_scheme = EXCLUDED.position_scheme
                    """,
                    greenhouse_id,
                    zone_id,
                    sh.slug,
                    sh.name,
                    sh.kind,
                    sh.tier,
                    sh.position_scheme,
                )
                shelf_id = await conn.fetchval(
                    "SELECT id FROM shelves WHERE greenhouse_id=$1 AND slug=$2",
                    greenhouse_id,
                    sh.slug,
                )
            else:
                shelf_id = None
            stats.shelves += 1

            for idx, label in enumerate(sh.position_labels, start=1):
                if not dry_run:
                    await conn.execute(
                        """
                        INSERT INTO positions (greenhouse_id, shelf_id, label, slot_number, mount_type)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (greenhouse_id, label) DO UPDATE SET
                            shelf_id = EXCLUDED.shelf_id,
                            slot_number = EXCLUDED.slot_number,
                            mount_type = EXCLUDED.mount_type
                        """,
                        greenhouse_id,
                        shelf_id,
                        label,
                        idx,
                        sh.mount_type,
                    )
                stats.positions += 1

    # ── pressure groups ──────────────────────────────────────────────
    for pg in STATIC_PRESSURE_GROUPS:
        if not dry_run:
            await conn.execute(
                """
                INSERT INTO pressure_groups (greenhouse_id, slug, name, constraint_kind,
                                             max_concurrent, description)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (greenhouse_id, slug) DO UPDATE SET
                    name = EXCLUDED.name,
                    constraint_kind = EXCLUDED.constraint_kind,
                    max_concurrent = EXCLUDED.max_concurrent,
                    description = EXCLUDED.description
                """,
                greenhouse_id,
                pg["slug"],
                pg["name"],
                pg["constraint_kind"],
                pg["max_concurrent"],
                pg["description"],
            )
        stats.pressure_groups += 1

    # ── equipment ─────────────────────────────────────────────────────
    for eq in STATIC_EQUIPMENT:
        zone_id = None
        if eq.get("zone") and not dry_run:
            zone_id = await conn.fetchval(
                "SELECT id FROM zones WHERE greenhouse_id=$1 AND slug=$2",
                greenhouse_id,
                eq["zone"],
            )
        if not dry_run:
            import json

            await conn.execute(
                """
                INSERT INTO equipment (greenhouse_id, slug, zone_id, kind, name, model,
                                       manufacturer, watts, cost_per_hour_usd, specs)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
                ON CONFLICT (greenhouse_id, slug) DO UPDATE SET
                    zone_id = EXCLUDED.zone_id,
                    kind = EXCLUDED.kind,
                    name = EXCLUDED.name,
                    model = EXCLUDED.model,
                    manufacturer = EXCLUDED.manufacturer,
                    watts = EXCLUDED.watts,
                    cost_per_hour_usd = EXCLUDED.cost_per_hour_usd,
                    specs = EXCLUDED.specs
                """,
                greenhouse_id,
                eq["slug"],
                zone_id,
                eq["kind"],
                eq["name"],
                eq.get("model"),
                eq.get("manufacturer"),
                eq.get("watts"),
                eq.get("cost_per_hour_usd"),
                json.dumps(eq.get("specs") or {}),
            )
        stats.equipment += 1

    # ── switches ──────────────────────────────────────────────────────
    for sw in STATIC_RELAY_MAP:
        eq_slug = sw["equipment"]
        eq_id = None
        if eq_slug and not dry_run:
            eq_id = await conn.fetchval(
                "SELECT id FROM equipment WHERE greenhouse_id=$1 AND slug=$2",
                greenhouse_id,
                eq_slug,
            )
        slug = f"{sw['board']}.{sw['pin']}"
        if not dry_run:
            await conn.execute(
                """
                INSERT INTO switches (greenhouse_id, slug, equipment_id, board, pin, purpose,
                                      state_source_column)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (greenhouse_id, slug) DO UPDATE SET
                    equipment_id = EXCLUDED.equipment_id,
                    board = EXCLUDED.board,
                    pin = EXCLUDED.pin,
                    purpose = EXCLUDED.purpose,
                    state_source_column = EXCLUDED.state_source_column
                """,
                greenhouse_id,
                slug,
                eq_id,
                sw["board"],
                sw["pin"],
                sw["purpose"],
                sw["state_source_column"],
            )
        stats.switches += 1

    # ── water_systems ────────────────────────────────────────────────
    for ws in STATIC_WATER_SYSTEMS:
        zone_id, eq_id, pg_id = None, None, None
        if not dry_run:
            if ws.get("zone"):
                zone_id = await conn.fetchval(
                    "SELECT id FROM zones WHERE greenhouse_id=$1 AND slug=$2",
                    greenhouse_id,
                    ws["zone"],
                )
            if ws.get("equipment"):
                eq_id = await conn.fetchval(
                    "SELECT id FROM equipment WHERE greenhouse_id=$1 AND slug=$2",
                    greenhouse_id,
                    ws["equipment"],
                )
            if ws.get("pressure_group"):
                pg_id = await conn.fetchval(
                    "SELECT id FROM pressure_groups WHERE greenhouse_id=$1 AND slug=$2",
                    greenhouse_id,
                    ws["pressure_group"],
                )
            await conn.execute(
                """
                INSERT INTO water_systems (greenhouse_id, slug, zone_id, equipment_id, pressure_group_id,
                                           kind, name, nozzle_count, head_count, mount,
                                           is_fert_path, effectiveness_note)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (greenhouse_id, slug) DO UPDATE SET
                    zone_id = EXCLUDED.zone_id,
                    equipment_id = EXCLUDED.equipment_id,
                    pressure_group_id = EXCLUDED.pressure_group_id,
                    kind = EXCLUDED.kind,
                    name = EXCLUDED.name,
                    nozzle_count = EXCLUDED.nozzle_count,
                    head_count = EXCLUDED.head_count,
                    mount = EXCLUDED.mount,
                    is_fert_path = EXCLUDED.is_fert_path,
                    effectiveness_note = EXCLUDED.effectiveness_note
                """,
                greenhouse_id,
                ws["slug"],
                zone_id,
                eq_id,
                pg_id,
                ws["kind"],
                ws["name"],
                ws["nozzle_count"],
                ws["head_count"],
                ws["mount"],
                ws["is_fert_path"],
                ws["effectiveness_note"],
            )
        stats.water_systems += 1

    # ── sensors ──────────────────────────────────────────────────────
    for sn in STATIC_SENSORS:
        zone_id = None
        if sn.get("zone") and not dry_run:
            zone_id = await conn.fetchval(
                "SELECT id FROM zones WHERE greenhouse_id=$1 AND slug=$2",
                greenhouse_id,
                sn["zone"],
            )
        if not dry_run:
            await conn.execute(
                """
                INSERT INTO sensors (greenhouse_id, slug, zone_id, kind, protocol, model,
                                     modbus_addr, gpio_pin, unit, source_table, source_column,
                                     expected_interval_s, accuracy)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                ON CONFLICT (greenhouse_id, slug) DO UPDATE SET
                    zone_id = EXCLUDED.zone_id,
                    kind = EXCLUDED.kind,
                    protocol = EXCLUDED.protocol,
                    model = EXCLUDED.model,
                    modbus_addr = EXCLUDED.modbus_addr,
                    gpio_pin = EXCLUDED.gpio_pin,
                    unit = EXCLUDED.unit,
                    source_table = EXCLUDED.source_table,
                    source_column = EXCLUDED.source_column,
                    expected_interval_s = EXCLUDED.expected_interval_s,
                    accuracy = EXCLUDED.accuracy
                """,
                greenhouse_id,
                sn["slug"],
                zone_id,
                sn["kind"],
                sn["protocol"],
                sn.get("model"),
                sn.get("modbus_addr"),
                sn.get("gpio_pin"),
                sn.get("unit"),
                sn.get("source_table"),
                sn.get("source_column"),
                sn.get("expected_interval_s"),
                sn.get("accuracy"),
            )
        stats.sensors += 1

    # ── crop_catalog ─────────────────────────────────────────────────
    for c in crops:
        if not dry_run:
            await conn.execute(
                """
                INSERT INTO crop_catalog (slug, common_name, scientific_name, category, season,
                                          cycle_days_min, cycle_days_max)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (slug) DO UPDATE SET
                    common_name = EXCLUDED.common_name,
                    scientific_name = COALESCE(EXCLUDED.scientific_name, crop_catalog.scientific_name),
                    category = EXCLUDED.category,
                    season = EXCLUDED.season,
                    cycle_days_min = EXCLUDED.cycle_days_min,
                    cycle_days_max = EXCLUDED.cycle_days_max
                """,
                c.slug,
                c.common_name,
                c.scientific_name,
                c.category,
                c.season,
                c.cycle_days_min,
                c.cycle_days_max,
            )
        stats.crop_catalog += 1


async def backfill_fks(
    conn: asyncpg.Connection,
    greenhouse_id: str,
    stats: ImportStats,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Populate new FK columns on existing tables by resolving legacy string fields."""
    unmatched: list[dict[str, Any]] = []

    if dry_run:
        return unmatched

    # crops.zone_id ← crops.zone
    stats.backfill_crops_zone = (
        await conn.fetchval(
            """
        WITH updated AS (
            UPDATE crops c SET zone_id = z.id
            FROM zones z
            WHERE c.zone_id IS NULL
              AND c.zone = z.slug
              AND c.greenhouse_id = z.greenhouse_id
              AND c.greenhouse_id = $1
            RETURNING c.id
        )
        SELECT COUNT(*)::int FROM updated
        """,
            greenhouse_id,
        )
        or 0
    )

    # crops.position_id ← crops.position (exact, then zone-prefix fallback)
    # Pass 1: exact label match.
    # Pass 2: crops.position is missing zone prefix ("HYDRO-31" vs "EAST-HYDRO-31")
    # — scope by crops.zone to avoid cross-zone mis-matches.
    stats.backfill_crops_position = (
        await conn.fetchval(
            """
        WITH updated AS (
            UPDATE crops c SET position_id = p.id
            FROM positions p, shelves sh, zones z
            WHERE c.position_id IS NULL
              AND p.shelf_id = sh.id
              AND sh.zone_id = z.id
              AND z.greenhouse_id = c.greenhouse_id
              AND c.greenhouse_id = $1
              AND (
                  p.label = c.position
                  OR p.label = upper(c.zone) || '-' || c.position
              )
              AND z.slug = c.zone
            RETURNING c.id
        )
        SELECT COUNT(*)::int FROM updated
        """,
            greenhouse_id,
        )
        or 0
    )

    # crops.crop_catalog_id ← crops.name (multi-strategy fuzzy).
    # Handles: exact slug, underscored, hyphenated, bi-directional prefix
    # (strawberry ↔ strawberries, pepper ↔ peppers), substring containment
    # (vanda orchids contains "orchid"), word-split (canna lilies → canna).
    stats.backfill_crops_catalog = (
        await conn.fetchval(
            """
        WITH updated AS (
            UPDATE crops c SET crop_catalog_id = cc.id
            FROM crop_catalog cc
            WHERE c.crop_catalog_id IS NULL
              AND (
                  lower(replace(c.name, ' ', '_')) = cc.slug
                  OR lower(replace(c.name, '-', '_')) = cc.slug
                  OR lower(c.name) LIKE cc.slug || '%'
                  OR cc.slug LIKE lower(c.name) || '%'
                  OR cc.slug = ANY(string_to_array(lower(c.name), ' '))
                  OR position(cc.slug IN lower(c.name)) > 0
                  OR position(lower(c.name) IN cc.slug) > 0
                  -- Singular/plural stem match (-y ↔ -ies, -s)
                  OR (length(lower(c.name)) >= 5
                      AND length(cc.slug) >= 5
                      AND substring(lower(c.name) FROM 1 FOR 5)
                          = substring(cc.slug FROM 1 FOR 5))
              )
              AND c.greenhouse_id = $1
            RETURNING c.id
        )
        SELECT COUNT(*)::int FROM updated
        """,
            greenhouse_id,
        )
        or 0
    )

    # observations.zone_id ← observations.zone
    stats.backfill_observations_zone = (
        await conn.fetchval(
            """
        WITH updated AS (
            UPDATE observations o SET zone_id = z.id
            FROM zones z
            WHERE o.zone_id IS NULL
              AND o.zone = z.slug
              AND o.greenhouse_id = z.greenhouse_id
              AND o.greenhouse_id = $1
            RETURNING o.id
        )
        SELECT COUNT(*)::int FROM updated
        """,
            greenhouse_id,
        )
        or 0
    )

    # observations.position_id ← observations.position (exact + zone-prefix)
    stats.backfill_observations_position = (
        await conn.fetchval(
            """
        WITH updated AS (
            UPDATE observations o SET position_id = p.id
            FROM positions p, shelves sh, zones z
            WHERE o.position_id IS NULL
              AND o.position IS NOT NULL
              AND p.shelf_id = sh.id
              AND sh.zone_id = z.id
              AND z.greenhouse_id = o.greenhouse_id
              AND o.greenhouse_id = $1
              AND (
                  p.label = o.position
                  OR (o.zone IS NOT NULL
                      AND z.slug = o.zone
                      AND p.label = upper(o.zone) || '-' || o.position)
              )
            RETURNING o.id
        )
        SELECT COUNT(*)::int FROM updated
        """,
            greenhouse_id,
        )
        or 0
    )

    # alert_log.zone_id ← alert_log.zone
    stats.backfill_alert_log_zone = (
        await conn.fetchval(
            """
        WITH updated AS (
            UPDATE alert_log a SET zone_id = z.id
            FROM zones z
            WHERE a.zone_id IS NULL
              AND a.zone = z.slug
              AND a.greenhouse_id = z.greenhouse_id
              AND a.greenhouse_id = $1
            RETURNING a.id
        )
        SELECT COUNT(*)::int FROM updated
        """,
            greenhouse_id,
        )
        or 0
    )

    # crop_target_profiles.crop_catalog_id ← crop_type
    stats.backfill_ctp_catalog = (
        await conn.fetchval(
            """
        WITH updated AS (
            UPDATE crop_target_profiles t SET crop_catalog_id = cc.id
            FROM crop_catalog cc
            WHERE t.crop_catalog_id IS NULL
              AND lower(t.crop_type) = cc.slug
              AND t.greenhouse_id = $1
            RETURNING t.id
        )
        SELECT COUNT(*)::int FROM updated
        """,
            greenhouse_id,
        )
        or 0
    )

    # Orphan counts
    stats.orphans_crops_no_position = await conn.fetchval(
        "SELECT COUNT(*)::int FROM crops WHERE is_active AND position_id IS NULL AND greenhouse_id=$1",
        greenhouse_id,
    )
    stats.orphans_crops_no_catalog = await conn.fetchval(
        "SELECT COUNT(*)::int FROM crops WHERE is_active AND crop_catalog_id IS NULL AND greenhouse_id=$1",
        greenhouse_id,
    )

    # Collect unmatched rows for the report
    rows = await conn.fetch(
        """
        SELECT id, name, zone, position
        FROM crops
        WHERE is_active AND greenhouse_id=$1 AND (position_id IS NULL OR crop_catalog_id IS NULL)
        ORDER BY id
        LIMIT 50
        """,
        greenhouse_id,
    )
    for r in rows:
        unmatched.append(dict(r))

    return unmatched


# ─────────────────────────────────────────────────────────────────────────
# Report rendering
# ─────────────────────────────────────────────────────────────────────────


def render_report(
    greenhouse_id: str,
    stats: ImportStats,
    unmatched_crops: list[dict[str, Any]],
    zones: list[ZoneSpec],
    crops: list[CropCatalogSpec],
    dry_run: bool,
) -> str:
    lines: list[str] = []
    lines.append(f"# Topology Import Report — {greenhouse_id}")
    lines.append("")
    lines.append(f"**Mode:** {'dry-run (no writes)' if dry_run else 'live (upsert applied)'}")
    lines.append("")
    lines.append("## Row counts")
    lines.append("")
    lines.append("| Table | Count |")
    lines.append("|-------|-------|")
    lines.append(f"| zones | {stats.zones} |")
    lines.append(f"| shelves | {stats.shelves} |")
    lines.append(f"| positions | {stats.positions} |")
    lines.append(f"| sensors | {stats.sensors} |")
    lines.append(f"| equipment | {stats.equipment} |")
    lines.append(f"| switches | {stats.switches} |")
    lines.append(f"| water_systems | {stats.water_systems} |")
    lines.append(f"| pressure_groups | {stats.pressure_groups} |")
    lines.append(f"| crop_catalog | {stats.crop_catalog} |")
    lines.append("")
    lines.append("## Backfill (existing-row FK population)")
    lines.append("")
    lines.append("| Column | Rows updated |")
    lines.append("|--------|--------------|")
    lines.append(f"| crops.zone_id | {stats.backfill_crops_zone} |")
    lines.append(f"| crops.position_id | {stats.backfill_crops_position} |")
    lines.append(f"| crops.crop_catalog_id | {stats.backfill_crops_catalog} |")
    lines.append(f"| observations.zone_id | {stats.backfill_observations_zone} |")
    lines.append(f"| observations.position_id | {stats.backfill_observations_position} |")
    lines.append(f"| alert_log.zone_id | {stats.backfill_alert_log_zone} |")
    lines.append(f"| crop_target_profiles.crop_catalog_id | {stats.backfill_ctp_catalog} |")
    lines.append("")
    lines.append("## Orphans (active crops missing FK)")
    lines.append("")
    lines.append(f"- Active crops without position_id: **{stats.orphans_crops_no_position}**")
    lines.append(f"- Active crops without crop_catalog_id: **{stats.orphans_crops_no_catalog}**")
    lines.append("")
    if unmatched_crops:
        lines.append("### Unmatched crops (first 50)")
        lines.append("")
        lines.append("| id | name | zone | position |")
        lines.append("|----|------|------|----------|")
        for r in unmatched_crops:
            lines.append(f"| {r['id']} | {r['name']} | {r['zone']} | {r['position']} |")
        lines.append("")
    lines.append("## Zones parsed")
    lines.append("")
    for z in zones:
        n_positions = sum(len(sh.position_labels) for sh in z.shelves)
        lines.append(
            f"- **{z.slug}** — {len(z.shelves)} shelves, {n_positions} positions, status={z.status}",
        )
    lines.append("")
    lines.append("## Crop catalog parsed")
    lines.append("")
    for c in crops:
        lines.append(f"- **{c.slug}** — {c.common_name} ({c.category}, {c.season})")
    lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────


async def run(args: argparse.Namespace) -> int:
    zones = parse_zones()
    crops = parse_crop_catalog()
    if not zones:
        print("ERROR: no zones parsed from vault. Check VAULT_ROOT.", file=sys.stderr)
        return 1

    stats = ImportStats()
    unmatched: list[dict[str, Any]] = []

    if args.dry_run:
        # Populate stats without DB
        for z in zones:
            stats.zones += 1
            stats.shelves += len(z.shelves)
            stats.positions += sum(len(sh.position_labels) for sh in z.shelves)
        stats.sensors = len(STATIC_SENSORS)
        stats.equipment = len(STATIC_EQUIPMENT)
        stats.switches = len(STATIC_RELAY_MAP)
        stats.water_systems = len(STATIC_WATER_SYSTEMS)
        stats.pressure_groups = len(STATIC_PRESSURE_GROUPS)
        stats.crop_catalog = len(crops)
    else:
        conn = await asyncpg.connect(args.dsn)
        try:
            # Ensure greenhouse row exists (required by all FKs).
            await conn.execute(
                """
                INSERT INTO greenhouses (id, name) VALUES ($1, $2)
                ON CONFLICT (id) DO NOTHING
                """,
                args.greenhouse_id,
                args.greenhouse_id.title() + " Greenhouse",
            )
            async with conn.transaction():
                await upsert_all(conn, args.greenhouse_id, zones, crops, stats, dry_run=False)
                unmatched = await backfill_fks(conn, args.greenhouse_id, stats, dry_run=False)
        finally:
            await conn.close()

    report = render_report(args.greenhouse_id, stats, unmatched, zones, crops, args.dry_run)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report)
    print(f"Report written to {report_path}")
    print(f"  zones={stats.zones} shelves={stats.shelves} positions={stats.positions}")
    print(f"  equipment={stats.equipment} switches={stats.switches} water_systems={stats.water_systems}")
    print(f"  sensors={stats.sensors} crop_catalog={stats.crop_catalog}")
    print(
        f"  backfill: crops.zone={stats.backfill_crops_zone} "
        f"position={stats.backfill_crops_position} catalog={stats.backfill_crops_catalog}"
    )
    print(
        f"  orphans: active_crops_no_position={stats.orphans_crops_no_position} "
        f"no_catalog={stats.orphans_crops_no_catalog}"
    )
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--greenhouse-id", default="vallery")
    p.add_argument(
        "--dsn",
        default="postgres://verdify:verdify@127.0.0.1:5432/verdify_topology_test",
    )
    p.add_argument("--report", default="state/topology-import-report.md")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
