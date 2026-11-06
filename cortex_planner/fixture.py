"""A deterministic, synthetic November trigger for tests and the offline dry render.

Shapes follow production (2026-11-05): cold nights with heat1 cycling and heat2 as backup,
night VPD under the band, idle fog and mist, a 7-day hourly forecast and a Hermes plan in force.
No production rows are copied; every value comes from smooth functions of the hour.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from .inputs import PackInputs, Trigger

AS_OF = datetime(2026, 11, 5, 14, 7, tzinfo=UTC)  # 07:07 MST, just after sunrise
SUN = {"sunrise": 393.0, "noon": 704.0, "sunset": 1015.0}
INTENT = {
    "forecast_temp_bias_f": 0.0,
    "forecast_vpd_bias_kpa": 0.0,
    "solar_precool_gain_f": 0.5,
    "thermal_lead_time_min": 30.0,
    "economizer_temp_advantage_f": 8.0,
    "economizer_dewpoint_advantage_f": 6.0,
    "moisture_engage_vpd_excess_kpa": 0.05,
    "all_zone_vpd_excess_kpa": 0.25,
    "mist_duty_limit_pct": 15.0,
    "fog_escalate_vpd_excess_kpa": 0.3,
    "dew_margin_floor_f": 9.0,
    "wet_cutoff_hour": 19.0,
    "daily_mist_budget_gal": 120.0,
    "resource_sensitivity": 0.35,
    "relay_churn_penalty": 0.7,
}


def _local_hour(ts: datetime) -> float:
    local = ts - timedelta(hours=7)
    return local.hour + local.minute / 60


def _outdoor(ts: datetime) -> tuple[float, float]:
    h = _local_hour(ts)
    day = (ts - AS_OF).total_seconds() / 86400
    temp = 42 + 14 * math.sin((h - 9) / 24 * 2 * math.pi) + 4 * math.sin(day / 3)
    rh = 55 - 20 * math.sin((h - 9) / 24 * 2 * math.pi)
    return temp, max(10.0, min(98.0, rh))


def _indoor(ts: datetime) -> tuple[float, float, float]:
    h = _local_hour(ts)
    temp = 70 + 8 * max(0.0, math.sin((h - 7) / 12 * math.pi))
    rh = 68 - 22 * max(0.0, math.sin((h - 7) / 12 * math.pi))
    svp = 0.6108 * math.exp(17.27 * ((temp - 32) / 1.8) / ((temp - 32) / 1.8 + 237.3))
    vpd = svp * (1 - rh / 100)
    return temp, rh, vpd


def _band(ts: datetime) -> dict[str, float]:
    h = _local_hour(ts)
    w = max(0.0, math.sin((h - 7) / 12 * math.pi))
    return {
        "temp_low": 68 + 7 * w,
        "temp_high": 78 + 7 * w,
        "vpd_low": 0.75 + 0.15 * w,
        "vpd_high": 1.15 + 0.15 * w,
        "house_vpd_low": 0.53,
        "house_vpd_high": 1.08,
    }


def _dp(temp_f: float, rh: float) -> float:
    t = (temp_f - 32) / 1.8
    gamma = math.log(rh / 100) + 17.62 * t / (243.12 + t)
    return 243.12 * gamma / (17.62 - gamma) * 1.8 + 32


def sample_inputs(event_type: str = "SUNRISE", as_of: datetime = AS_OF) -> PackInputs:
    start = (as_of - timedelta(days=14)).replace(minute=0, second=0, microsecond=0)
    hourly = []
    band = {}
    h = start
    while h < as_of.replace(minute=0, second=0, microsecond=0):
        t, rh, vpd = _indoor(h)
        tout, rhout = _outdoor(h)
        hourly.append(
            {
                "h": h,
                "t": round(t, 2),
                "rh": round(rh, 1),
                "vpd": round(vpd, 3),
                "dp": round(_dp(t, rh), 1),
                "tout": round(tout, 1),
                "rhout": round(rhout, 1),
                "solar": round(max(0.0, 600 * math.sin((_local_hour(h) - 6.5) / 10.5 * math.pi)), 0),
                "water": 0.0,
            }
        )
        h += timedelta(hours=1)
    h = start
    while h <= as_of + timedelta(hours=73):
        band[h.isoformat().replace("+00:00", "Z")] = {k: round(v, 3) for k, v in _band(h).items()}
        h += timedelta(hours=1)
    recent = []
    ts = as_of - timedelta(hours=6)
    while ts <= as_of:
        t, rh, vpd = _indoor(ts)
        recent.append(
            {
                "ts": ts,
                "t": round(t, 2),
                "rh": round(rh, 1),
                "vpd": round(vpd, 3),
                "dp": round(_dp(t, rh), 1),
                "tout": round(_outdoor(ts)[0], 1),
            }
        )
        ts += timedelta(minutes=5)
    fetched = as_of - timedelta(minutes=37)
    forecast = []
    ts = as_of.replace(minute=0, second=0, microsecond=0)
    while ts < as_of + timedelta(days=7):
        tout, rhout = _outdoor(ts)
        svp = 0.6108 * math.exp(17.27 * ((tout - 32) / 1.8) / ((tout - 32) / 1.8 + 237.3))
        forecast.append(
            {
                "ts": ts,
                "temp_f": round(tout, 1),
                "rh_pct": round(rhout, 0),
                "vpd_kpa": round(svp * (1 - rhout / 100), 2),
                "dew_point_f": round(_dp(tout, rhout), 1),
                "solar_w_m2": round(max(0.0, 600 * math.sin((_local_hour(ts) - 6.5) / 10.5 * math.pi)), 0),
                "cloud_cover_pct": float(int(30 + 30 * math.sin(ts.timestamp() / 40000)) // 1),
                "wind_speed_mph": round(6 + 4 * math.sin(ts.timestamp() / 20000), 0),
                "precip_prob_pct": 0.0,
            }
        )
        ts += timedelta(hours=1)
    # heat1 on most of each night, heat2 on briefly before dawn; fog rarely.
    equipment: dict[str, list] = {
        e: [(start, False)] for e in ("heat1", "heat2", "fog", "mister_any", "vent", "fan1", "fan2")
    }
    day = start.replace(hour=0)
    while day < as_of:
        dusk = day + timedelta(hours=24 + 0)  # 17:00 local == 00:00Z
        equipment["heat1"] += [(dusk, True), (dusk + timedelta(hours=14, minutes=30), False)]
        equipment["heat2"] += [(dusk + timedelta(hours=11), True), (dusk + timedelta(hours=11, minutes=40), False)]
        equipment["vent"] += [(dusk - timedelta(hours=5), True), (dusk - timedelta(hours=4, minutes=20), False)]
        day += timedelta(days=1)
    equipment = {k: sorted((t, s) for t, s in v if t < as_of) for k, v in equipment.items()}
    intents = []
    plan_ts = [
        as_of - timedelta(minutes=2),
        as_of + timedelta(hours=5),
        as_of + timedelta(hours=10),
        as_of + timedelta(hours=16),
    ]
    for i, ts in enumerate(plan_ts):
        intent = dict(INTENT)
        intent["thermal_lead_time_min"] = 30.0 + 5 * i
        intents.append({"ts": (ts - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:00-07:00"), "climate_intent": intent})
    plans = []
    for k in range(6, 0, -1):
        created = as_of - timedelta(hours=12 * k)
        intent = dict(INTENT)
        intent["daily_mist_budget_gal"] = 120.0 + 10 * (k % 3)
        intent["fog_escalate_vpd_excess_kpa"] = 0.3 - 0.05 * (k % 2)
        plans.append(
            {
                "plan_id": f"iris-fixture-{k}",
                "created_at": created,
                "planner_instance": "opus",
                "climate_intents": [
                    {"ts": (created - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:00-07:00"), "climate_intent": intent}
                ],
                "hypothesis_structured": {
                    "predictions": [{"metric": "vpd_below_band_hours", "window": "tonight", "value": 6}]
                },
                "valid_from": created,
                "expires_at": created + timedelta(hours=12),
                "lifecycle_status": "superseded",
            }
        )
    plans.append(
        {
            "plan_id": "iris-fixture-0",
            "created_at": as_of - timedelta(minutes=5),
            "planner_instance": "opus",
            "climate_intents": intents,
            "hypothesis_structured": None,
            "valid_from": as_of - timedelta(minutes=2),
            "expires_at": as_of + timedelta(hours=24),
            "lifecycle_status": "effective",
        }
    )
    t, rh, vpd = _indoor(as_of)
    now = {
        "ts": as_of - timedelta(minutes=1),
        "temp_avg": round(t, 1),
        "rh_avg": round(rh, 1),
        "vpd_avg": round(vpd, 3),
        "dew_point": round(_dp(t, rh), 1),
        "outdoor_temp_f": round(_outdoor(as_of)[0], 1),
        "outdoor_rh_pct": round(_outdoor(as_of)[1], 1),
    }
    for zone, off in (("north", -0.6), ("east", 0.2), ("west", 0.4), ("south", 2.5)):
        now[f"temp_{zone}"] = round(t + off, 1)
        now[f"rh_{zone}"] = round(rh - off, 1)
        now[f"vpd_{zone}"] = round(vpd + off / 20, 3)
    return PackInputs(
        trigger=Trigger("00000000-0000-4000-8000-000000000001", event_type, None, as_of),
        as_of=as_of,
        sun=dict(SUN),
        now=now,
        equipment_on_now=["heat1", "grow_light_main"],
        hourly=hourly,
        recent=recent,
        band=band,
        forecast_fetched_at=fetched,
        forecast=forecast,
        forecast_bias=[
            {"lead": "1-6h", "n": 168, "temp_err": -0.8, "rh_err": 3.0},
            {"lead": "7-24h", "n": 400, "temp_err": -1.2, "rh_err": 4.0},
            {"lead": "25-48h", "n": 400, "temp_err": -1.9, "rh_err": 5.0},
            {"lead": "49-72h", "n": 380, "temp_err": -2.4, "rh_err": 6.0},
        ],
        equipment=equipment,
        night_vpd_bias_now=0.0,
        plans=plans,
        clamps=[
            {
                "ts": as_of - timedelta(hours=3),
                "parameter": "mister_engage_kpa",
                "requested": 1.4,
                "applied": 1.3,
                "reason": "band coupling",
                "plan_id": "iris-fixture-1",
            }
        ],
        alerts=[
            {
                "ts": as_of - timedelta(hours=30),
                "alert_type": "soil_sensor_offline",
                "severity": "warning",
                "sensor_id": "soil.west",
                "message": "probe offline",
            }
        ],
        crops=[
            {
                "name": "Vanda Orchids",
                "variety": "Mixed Vanda",
                "zone": "center",
                "position": "CENTER-HANG",
                "stage": "vegetative",
                "count": 1,
            }
        ],
    )


def sample_proposal(anchor_times: list[str], **changes) -> dict:
    """A valid answer for the given anchors (tests and the offline dry render)."""
    intent = {k: v for k, v in INTENT.items() if k != "wet_cutoff_hour"}
    intent["night_vpd_bias_kpa"] = 0.1
    raw = {
        "key_facts": ["Night VPD sat below band for 5 h.", "Forecast is dry and cold tonight."],
        "hypothesis": {
            "conditions": "A larger night bias dries the canopy without dropping temperature.",
            "predictions": [{"metric": "vpd_below_band_hours", "window": "tonight", "value": 2.0}],
        },
        "waypoints": [
            {
                "ts": ts,
                "climate_intent": dict(intent),
                "changed": ["night_vpd_bias_kpa"] if i == 0 else [],
                "reasons": {"night_vpd_bias_kpa": "Nights ran wet."} if i == 0 else {},
            }
            for i, ts in enumerate(anchor_times[:3])
        ],
        "expected_outcome": {"vpd_below_band_hours": 2.0, "cold_hours": 0.0, "water_gal": 40.0},
        "confidence": 0.6,
    }
    raw.update(changes)
    return raw
