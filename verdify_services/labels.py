"""Equipment names in words, served by API-13 (Equipment) and API-05 (the Graphs lanes).

Both reads label every actuator from this one map, so the client never makes up a label.
"""

from __future__ import annotations

EQUIPMENT_LABELS: dict[str, str] = {
    # climate
    "fan1": "Fan 1",
    "fan2": "Fan 2",
    "vent": "Vent",
    "heat1": "Heat 1 electric",
    "heat2": "Heat 2 gas",
    # water: the 11 wet relays, L01 to L11
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
    # light: the Lutron readback
    "grow_light_main": "Main",
    "grow_light_grow": "Grow",
    # flags (API-05 lanes)
    "occupancy": "Occupancy",
    "leak_detected": "Water flowing, valves closed",
    "water_flowing": "Water flowing",
}

# The wet relays and their irrigation line ids.
LINE_IDS: dict[str, str] = {
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
