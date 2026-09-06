"""Pure HA power evidence conversion; no token, network, DB or device access.

HA last_updated is a gateway state timestamp, not a commissioned physical sample
clock. A repeated old value cannot become fresh merely because we fetched it.
The 300-second bound is a diagnostic hold limit, not a scientific coverage rule.
"""

from datetime import datetime
from math import isfinite

from pydantic import ValidationError

from verdify_schemas.external import HAEntityState
from verdify_schemas.telemetry import EnergySample

REVISION = "ha_shelly_power_v1"
MAX_AGE_SECONDS = 300
POWER_ENTITIES = (
    "sensor.shellyproem50_ac15186daafc_energy_meter_0_power",
    "sensor.shellyproem50_ac15186daafc_energy_meter_1_power",
)
WRITE_FIELDS = (
    "ts",
    "watts_total",
    "watts_heat",
    "watts_fans",
    "watts_other",
    "kwh_today",
    "measurement_revision",
    "ch0_power_w",
    "ch1_power_w",
    "ch0_source_ts",
    "ch1_source_ts",
    "ch0_entity_id",
    "ch1_entity_id",
    "ch0_quality",
    "ch1_quality",
)
INSERT_SQL = (
    "INSERT INTO public.v_runtime_energy_write ("
    + ",".join(WRITE_FIELDS)
    + ") VALUES ("
    + ",".join(f"${index}" for index in range(1, len(WRITE_FIELDS) + 1))
    + ")"
)


def channel(raw, entity_id: str, observed_at: datetime):
    if raw is None:
        return None, None, "missing"
    try:
        state = HAEntityState.model_validate(raw)
    except ValidationError:
        return None, None, "invalid"
    if state.entity_id != entity_id or state.attributes.get("unit_of_measurement") != "W":
        return None, None, "invalid"
    value = state.as_float()
    if value is None:
        return None, state.last_updated, "unavailable"
    if not isfinite(value):
        return None, state.last_updated, "nonfinite"
    source_ts = state.last_updated
    if source_ts is None:
        return value, None, "unknown_time"
    age = (observed_at - source_ts).total_seconds()
    if age < 0:
        return value, source_ts, "future"
    if age >= MAX_AGE_SECONDS:
        return value, source_ts, "stale"
    return value, source_ts, "ok"


def build_sample(states: dict, observed_at: datetime) -> EnergySample:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("energy observation requires an aware timestamp")
    a, a_ts, a_quality = channel(states.get(POWER_ENTITIES[0]), POWER_ENTITIES[0], observed_at)
    b, b_ts, b_quality = channel(states.get(POWER_ENTITIES[1]), POWER_ENTITIES[1], observed_at)
    total = a + b if a_quality == b_quality == "ok" else None
    if total is not None and not isfinite(total):
        total = None
    return EnergySample(
        ts=observed_at,
        watts_total=total,
        measurement_revision=REVISION,
        ch0_power_w=a,
        ch1_power_w=b,
        ch0_source_ts=a_ts,
        ch1_source_ts=b_ts,
        ch0_entity_id=POWER_ENTITIES[0],
        ch1_entity_id=POWER_ENTITIES[1],
        ch0_quality=a_quality,
        ch1_quality=b_quality,
        # No verified daily-reset counter or installed circuit attribution.
        kwh_today=None,
        watts_heat=None,
        watts_fans=None,
        watts_other=None,
    )
