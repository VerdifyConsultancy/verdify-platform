"""Plain names for every actuator and flag the dashboard shows (VD-54, VD-56).

API-13 (``verdify_services.reads.equipment``) and API-05 (``verdify_services.series``)
both serve these, so every surface names an actuator the same way and the client
never makes a label up. Keys are ``equipment_state.equipment`` values.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType

EQUIPMENT_LABELS: Mapping[str, str] = MappingProxyType(
    {
        # Climate.
        "fan1": "Fan 1",
        "fan2": "Fan 2",
        "vent": "Vent",
        "heat1": "Heat 1 electric",
        "heat2": "Heat 2 gas",
        # Water: the eleven wet relays, L01-L11, by the names the operator uses; never the relay code (#1341).
        "drip_wall": "Wall drip",
        "drip_wall_fert": "Wall drip (fertilized)",
        "fert_master_valve": "Fertilizer valve",
        "mister_center": "Center mister",
        "drip_center": "Center drip",
        "drip_center_fert": "Center drip (fertilized)",
        "mister_south": "South mister",
        "mister_south_fert": "South mister (fertilized)",
        "mister_west": "West mister",
        "mister_west_fert": "West mister (fertilized)",
        "fog": "Fog",
        # Light: the Lutron readback.
        "grow_light_main": "Main",
        "grow_light_grow": "Grow",
        # Light: the controller's switch intent (Q-09), compared with the readback on the lanes (#1356).
        "grow_light_main_intent": "Main, controller intent",
        "grow_light_grow_intent": "Grow, controller intent",
        # The API-05 flags.
        "occupancy": "Occupancy",
        "leak_detected": "Water flowing, valves closed",
        "water_flowing": "Water flowing",
    }
)

# Each wet relay's irrigation line, in line order (verdify_schemas.irrigation.LINE_IDS).
EQUIPMENT_LINE_IDS: Mapping[str, str] = MappingProxyType(
    {
        "drip_wall": "L01",
        "drip_wall_fert": "L02",
        "fert_master_valve": "L03",
        "mister_center": "L04",
        "drip_center": "L05",
        "drip_center_fert": "L06",
        "mister_south": "L07",
        "mister_south_fert": "L08",
        "mister_west": "L09",
        "mister_west_fert": "L10",
        "fog": "L11",
    }
)

# Each line's display name (#1341): the line's actuator label, so a line reads the same on every surface. Codes stay
# the machine key (``line_id``) in structured fields; words a person reads carry the name.
LINE_NAMES: Mapping[str, str] = MappingProxyType(
    {line_id: EQUIPMENT_LABELS[equipment] for equipment, line_id in EQUIPMENT_LINE_IDS.items()}
)

_LINE_RANGE = re.compile(r"\b(L(?:0[1-9]|1[01]))\s*[-\u2013]\s*(L(?:0[1-9]|1[01]))\b")
_LINE_CODE = re.compile(r"\bL(?:0[1-9]|1[01])\b")


def line_name(line_id: str) -> str:
    """A line's display name ("Wall drip"); an id that is not a line stays as given."""
    return LINE_NAMES.get(line_id, line_id)


def name_line_codes(text: str) -> str:
    """Words for a person with every relay code replaced by the line's name: "L01 is disabled" reads "Wall drip is
    disabled", and a range "L04-L10" reads "Center mister through West mister (fertilized)"."""
    text = _LINE_RANGE.sub(lambda m: f"{line_name(m.group(1))} through {line_name(m.group(2))}", text)
    return _LINE_CODE.sub(lambda m: line_name(m.group(0)), text)
