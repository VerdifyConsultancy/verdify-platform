"""Plain names for every actuator and flag the dashboard shows (VD-54, VD-56).

API-13 (``verdify_services.reads.equipment``) and API-05 (``verdify_services.series``)
both serve these, so every surface names an actuator the same way and the client
never makes a label up. Keys are ``equipment_state.equipment`` values.
"""

from __future__ import annotations

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
        # Water: the eleven wet relays, L01-L11.
        "drip_wall": "Wall drip L01",
        "drip_wall_fert": "Wall fert drip L02",
        "fert_master_valve": "Fert master L03",
        "mister_center": "Center mister L04",
        "drip_center": "Center drip L05",
        "drip_center_fert": "Center fert drip L06",
        "mister_south": "South mister L07",
        "mister_south_fert": "South fert mister L08",
        "mister_west": "West mister L09",
        "mister_west_fert": "West fert mister L10",
        "fog": "Fog L11",
        # Light: the Lutron readback.
        "grow_light_main": "Main",
        "grow_light_grow": "Grow",
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
