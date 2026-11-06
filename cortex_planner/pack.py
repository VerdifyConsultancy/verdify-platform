"""Pure section builders and the budgeted pack (pack v1, #1366).

Every builder takes `PackInputs` and a reduction level and returns text; level 0 is the full
section, each higher level is smaller (fewer or coarser rows). `build_pack` renders each section,
raises its level until it fits its Qwen-token budget, cuts it with a marker only as a last
resort, and records what happened per section. The same inputs always give the same bytes.

Encoding rules (measured in #1366): pipe tables with one header row, fixed precision, VPD as
centi-kPa integers, signed band deltas instead of repeated band columns, blanks for zero, no
JSON in data sections.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from verdify_schemas.planner_proposal import INTENT_FIELDS, PlannerIntent

from . import PACK_VERSION, tokens
from .inputs import PackInputs, iso
from .sections import BY_KEY, DROP_ORDER, PACK_HARD_CAP, SECTIONS

STATIC = Path(__file__).resolve().parent / "static"
UNTRUSTED_ZONES = ("south",)
HEAT = ("heat1", "heat2")
TABLE_EQUIPMENT = ("heat1", "heat2", "fog", "mister_any", "vent", "fan1", "fan2")

EVENT_GOALS = {
    "SUNRISE": "Plan the day: sunrise through tonight. Expect the solar ramp and the afternoon VPD peak.",
    "SUNSET": "Plan the night: sunset through tomorrow morning. Night VPD below band is the main risk.",
    "MIDNIGHT": "Check the night plan against the last hours and the overnight minimum; adjust only what changed.",
    "SOLAR_MAX": "Mid-day check: peak solar load now; adjust the afternoon and evening waypoints.",
    "TRANSITION": "A band or mode transition fired: keep the plan unless the transition changes a driver.",
    "FORECAST_DEVIATION": "The forecast moved away from what the plan assumed: change only the affected waypoints.",
    "WEEKLY": "Weekly review: use the 14-day scorecard to adjust slow-moving fields; keep tactics stable.",
    "MANUAL": "Operator-requested replan: plan from now through the next 24 h.",
}

# field -> (what it drives, what should move it)
FIELD_GUIDE = {
    "forecast_temp_bias_f": ("stage-2 cooling margin, all-fans switch", "forecast temp bias by lead (S6)"),
    "forecast_vpd_bias_kpa": ("dry-forecast pressure on wet assist", "forecast VPD bias by lead (S6)"),
    "solar_precool_gain_f": ("earlier stage-2 cooling", "forecast solar peak and Tmax"),
    "thermal_lead_time_min": ("anticipation lead", "forecast overnight minimum, night cooling rate"),
    "economizer_temp_advantage_f": ("vent_prefer_temp_delta_f, cold_vent_guard", "outdoor vs indoor temp"),
    "economizer_dewpoint_advantage_f": ("vent_prefer_dp_delta_f", "outdoor vs indoor dew point"),
    "moisture_engage_vpd_excess_kpa": ("mister_engage_kpa = vpd_high + x", "VPD above band (S7, S11), dew margin"),
    "all_zone_vpd_excess_kpa": ("mister_all_kpa = vpd_high + x", "same drivers; must be >= moisture_engage"),
    "mist_duty_limit_pct": ("mister_pulse_on_s", "forecast VPD peak"),
    "fog_escalate_vpd_excess_kpa": ("fog_escalation_kpa", "forecast VPD peak, dew margin"),
    "dew_margin_floor_f": ("direct-wet dew guard", "night dew margin, disease risk"),
    "daily_mist_budget_gal": ("mister_water_budget_gal", "forecast VPD peak, water use (S11)"),
    "resource_sensitivity": ("mist pulse gap, min fog off", "water and energy use (S11)"),
    "relay_churn_penalty": ("hysteresis, dwell, delays", "relay cycling (S7)"),
    "night_vpd_bias_kpa": (
        "overnight VPD band offset (night dryness floor)",
        "tonight's band shortfall (S8), outdoor dew point",
    ),
}


# ── small helpers ────────────────────────────────────────────────────────────


def _n(value, digits: int = 0, blank_zero: bool = False) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return ""
    value = float(value)
    if blank_zero and abs(value) < 0.5 * 10**-digits:
        return ""
    text = f"{value:.{digits}f}"
    return "0" if text in ("-0", "-0.0") else text


def _c(vpd_kpa) -> str:
    """kPa -> centi-kPa integer text."""
    return "" if vpd_kpa is None else _n(float(vpd_kpa) * 100)


def table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    lines = ["|".join(header)]
    lines.extend("|".join(row) for row in rows)
    return "\n".join(lines)


def _hour_key(ts: datetime) -> str:
    return iso(ts.astimezone(UTC).replace(minute=0, second=0, microsecond=0))


def band_at(inputs: PackInputs, ts: datetime) -> dict[str, float] | None:
    return inputs.band.get(_hour_key(ts))


def temp_delta(t, band) -> float | None:
    if t is None or not band:
        return None
    if t < band["temp_low"]:
        return t - band["temp_low"]
    if t > band["temp_high"]:
        return t - band["temp_high"]
    return None


def vpd_delta(v, band) -> float | None:
    if v is None or not band:
        return None
    if v < band["vpd_low"]:
        return v - band["vpd_low"]
    if v > band["vpd_high"]:
        return v - band["vpd_high"]
    return None


def minutes_on(events: Sequence[tuple[datetime, bool]], start: datetime, end: datetime) -> float:
    """Minutes an equipment was on in [start, end) from its transition list."""
    total = 0.0
    state = False
    since = start
    for ts, on in events:
        if ts <= start:
            state = on
            continue
        if ts >= end:
            break
        if state:
            total += (ts - since).total_seconds()
        state, since = on, ts
    if state:
        total += (end - max(since, start)).total_seconds()
    return total / 60.0


def dew_point_f(temp_f, rh) -> float | None:
    if temp_f is None or rh is None or rh <= 0:
        return None
    t = (temp_f - 32) / 1.8
    gamma = math.log(rh / 100.0) + 17.62 * t / (243.12 + t)
    return (243.12 * gamma / (17.62 - gamma)) * 1.8 + 32


class Clock:
    def __init__(self, inputs: PackInputs):
        self.tz = ZoneInfo(inputs.timezone)
        self.inputs = inputs

    def local(self, ts: datetime) -> datetime:
        return ts.astimezone(self.tz)

    def hhmm(self, ts: datetime) -> str:
        return self.local(ts).strftime("%a %H:%M")

    def hour(self, ts: datetime) -> str:
        return self.local(ts).strftime("%a %Hh")

    def at_minutes(self, day: datetime, minutes: float) -> datetime:
        base = self.local(day).replace(hour=0, minute=0, second=0, microsecond=0)
        return (base + timedelta(minutes=round(minutes))).astimezone(UTC)

    def is_day(self, ts: datetime) -> bool:
        sun = self.inputs.sun
        local = self.local(ts)
        minute = local.hour * 60 + local.minute
        return sun.get("sunrise", 420) <= minute < sun.get("sunset", 1020)


def anchors(inputs: PackInputs) -> list[str]:
    """Waypoint anchors the model must use: the next quarter hour, then solar events within 36 h."""
    clock = Clock(inputs)
    start = inputs.as_of.astimezone(UTC)
    first = start.replace(second=0, microsecond=0) + timedelta(minutes=(15 - start.minute % 15) % 15 or 15)
    sun = inputs.sun
    events: list[datetime] = []
    for offset in range(0, 3):
        day = start + timedelta(days=offset)
        for key in ("sunrise", "noon", "sunset"):
            if key in sun:
                events.append(clock.at_minutes(day, sun[key]))
        if "sunset" in sun and "sunrise" in sun:
            midnight = (sun["sunset"] + sun["sunrise"] + 1440) / 2
            events.append(clock.at_minutes(day, midnight))
    events = sorted({e for e in events if first + timedelta(minutes=30) <= e <= start + timedelta(hours=36)})
    out = [first] + events[:5]
    return [clock.local(ts).replace(second=0, microsecond=0).isoformat() for ts in out]


def _intent_bounds(name: str) -> tuple[float | None, float | None, float]:
    info = PlannerIntent.model_fields[name]
    lo = hi = None
    for meta in info.metadata:
        lo = getattr(meta, "ge", lo) if getattr(meta, "ge", None) is not None else lo
        hi = getattr(meta, "le", hi) if getattr(meta, "le", None) is not None else hi
    return lo, hi, info.default


def active_plan(inputs: PackInputs) -> dict | None:
    for plan in reversed(inputs.plans):
        if plan.get("lifecycle_status") in (None, "effective", "active"):
            return plan
    return inputs.plans[-1] if inputs.plans else None


def active_waypoints(inputs: PackInputs) -> list[dict]:
    plan = active_plan(inputs)
    if not plan:
        return []
    from .inputs import parse_ts

    waypoints = sorted(plan.get("climate_intents") or [], key=lambda w: parse_ts(w["ts"]))
    keep = []
    for index, waypoint in enumerate(waypoints):
        nxt = parse_ts(waypoints[index + 1]["ts"]) if index + 1 < len(waypoints) else None
        if nxt is None or nxt > inputs.as_of:
            keep.append(waypoint)
    return keep


# ── section builders ─────────────────────────────────────────────────────────


def s0_system(inputs: PackInputs, level: int) -> str:
    return (STATIC / "system.md").read_text().strip()


def s1_decision(inputs: PackInputs, level: int) -> str:
    clock = Clock(inputs)
    from .inputs import parse_ts

    waypoints = active_waypoints(inputs)[: 4 if level == 0 else 2]
    header = ["field", "min", "max", "default"] + [
        f"W{i + 1} {clock.hhmm(parse_ts(w['ts']))}" for i, w in enumerate(waypoints)
    ]
    if level == 0:
        header += ["drives", "driver"]
    rows = []
    for name in INTENT_FIELDS:
        lo, hi, default = _intent_bounds(name)
        row = [name, _n(lo, 2), _n(hi, 2), _n(default, 2)]
        for waypoint in waypoints:
            if name == "night_vpd_bias_kpa":
                value = inputs.night_vpd_bias_now
            else:
                value = (waypoint.get("climate_intent") or {}).get(name)
            row.append(_n(value, 2))
        if level == 0:
            row += list(FIELD_GUIDE.get(name, ("", "")))
        rows.append(row)
    lines = [
        "Output fields (one set per waypoint). W columns are the incumbent plan's values per active waypoint;"
        " night_vpd_bias_kpa incumbent is the value the device holds now.",
        table(header, rows),
    ]
    if not waypoints:
        lines.append("No active plan: incumbents are the defaults.")
    return "\n".join(lines)


def s2_card(inputs: PackInputs, level: int) -> str:
    return (STATIC / "greenhouse-card.md").read_text().strip()


def s3_playbook(inputs: PackInputs, level: int) -> str:
    return (STATIC / "playbook-digest.md").read_text().strip()


def s4_trigger(inputs: PackInputs, level: int) -> str:
    clock = Clock(inputs)
    sun = inputs.sun
    t = inputs.trigger

    def mm(minutes):
        return "" if minutes is None else f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"

    lines = [
        f"event {t.event_type}" + (f" ({t.event_label})" if t.event_label else ""),
        f"as_of {clock.local(inputs.as_of).strftime('%Y-%m-%d %a %H:%M %Z')}",
        f"sun today: sunrise {mm(sun.get('sunrise'))}, solar noon {mm(sun.get('noon'))}, sunset {mm(sun.get('sunset'))}"
        " (tomorrow within 2 min of today)",
    ]
    plan = active_plan(inputs)
    if plan:
        from .inputs import parse_ts

        remaining = [clock.hhmm(parse_ts(w["ts"])) for w in active_waypoints(inputs)]
        lines.append(
            f"active plan {plan.get('plan_id')} ({plan.get('planner_instance') or '?'}): waypoints {', '.join(remaining)}"
        )
    else:
        lines.append("active plan: none")
    lines.append("anchors (use these exact ts strings):")
    lines.extend(f"A{i + 1} {a}" for i, a in enumerate(anchors(inputs)))
    return "\n".join(lines)


def s5_now(inputs: PackInputs, level: int) -> str:
    now = inputs.now
    lines = []
    if not now:
        return "No current climate row."
    from .inputs import parse_ts

    ts = parse_ts(now["ts"]) if now.get("ts") else inputs.as_of
    age = (inputs.as_of - ts).total_seconds() / 60
    band = band_at(inputs, inputs.as_of)
    t, dp = now.get("temp_avg"), now.get("dew_point")
    zones = []
    for zone in ("north", "east", "west", "south"):
        mark = " (untrusted)" if zone in UNTRUSTED_ZONES else ""
        zones.append(
            [zone + mark, _n(now.get(f"temp_{zone}"), 1), _n(now.get(f"rh_{zone}")), _c(now.get(f"vpd_{zone}"))]
        )
    lines.append(
        f"house T {_n(t, 1)} RH {_n(now.get('rh_avg'))} VPDc {_c(now.get('vpd_avg'))} DP {_n(dp, 1)}"
        f" dew margin {_n(None if t is None or dp is None else t - dp, 1)}; outdoor T {_n(now.get('outdoor_temp_f'), 1)}"
        f" RH {_n(now.get('outdoor_rh_pct'))} DP {_n(dew_point_f(now.get('outdoor_temp_f'), now.get('outdoor_rh_pct')), 1)}"
    )
    lines.append(table(["zone", "T", "RH", "VPDc"], zones))
    lines.append("on now: " + (", ".join(sorted(inputs.equipment_on_now)) or "nothing"))
    if band:
        lines.append(
            f"served band (graded): T {_n(band['temp_low'], 1)}-{_n(band['temp_high'], 1)},"
            f" VPDc {_c(band['vpd_low'])}-{_c(band['vpd_high'])}"
        )
        if band.get("house_vpd_low") is not None:
            lines.append(
                f"house VPD control envelope (what the mister thresholds anchor to, not graded):"
                f" VPDc {_c(band['house_vpd_low'])}-{_c(band.get('house_vpd_high'))}"
            )
    fc_age = (inputs.as_of - inputs.forecast_fetched_at).total_seconds() / 60 if inputs.forecast_fetched_at else None
    lines.append(
        f"freshness: climate {_n(age)} min old; forecast fetched {_n(fc_age)} min ago"
        + ("; STALE climate" if age > 15 else "")
    )
    return "\n".join(lines)


def _forecast_row(clock: Clock, inputs: PackInputs, row: dict) -> list[str]:
    band = band_at(inputs, row["ts"])
    return [
        clock.hour(row["ts"]),
        _n(row.get("temp_f")),
        _n(row.get("rh_pct")),
        _c(row.get("vpd_kpa")),
        _n(row.get("dew_point_f")),
        _n(row.get("solar_w_m2"), blank_zero=True),
        _n(row.get("cloud_cover_pct")),
        _n(row.get("wind_speed_mph")),
        _n(row.get("precip_prob_pct"), blank_zero=True),
        f"{_n(band['temp_low'])}-{_n(band['temp_high'])}" if band else "",
        f"{_c(band['vpd_low'])}-{_c(band['vpd_high'])}" if band else "",
    ]


def s6_forecast(inputs: PackInputs, level: int) -> str:
    clock = Clock(inputs)
    start = inputs.as_of.replace(minute=0, second=0, microsecond=0)
    rows = [r for r in inputs.forecast if r["ts"] >= start]
    hourly_end = start + timedelta(hours=36 if level < 2 else 24)
    step36 = 3 if level < 3 else 6
    near = [r for r in rows if r["ts"] < hourly_end]
    far = [
        r
        for r in rows
        if hourly_end <= r["ts"] < start + timedelta(hours=72)
        and int((r["ts"] - start).total_seconds() // 3600) % step36 == 0
    ]
    header = ["time", "T", "RH", "VPDc", "DP", "sol", "cld", "wind", "pp", "bandT", "bandVc"]
    out = [
        f"Forecast fetched {clock.hhmm(inputs.forecast_fetched_at) if inputs.forecast_fetched_at else '?'}."
        " Hourly to +36 h, then every 3 h to +72 h; band = served band for that hour.",
        table(header, [_forecast_row(clock, inputs, r) for r in near + far]),
    ]
    if level < 1:
        days: dict[str, list[dict]] = {}
        for r in rows:
            if r["ts"] >= start + timedelta(hours=72):
                days.setdefault(clock.local(r["ts"]).strftime("%a %m-%d"), []).append(r)
        if days:
            drows = []
            for day, items in list(days.items())[:4]:
                temps = [r["temp_f"] for r in items if r.get("temp_f") is not None]
                vpds = [r["vpd_kpa"] for r in items if r.get("vpd_kpa") is not None]
                rhs = [r["rh_pct"] for r in items if r.get("rh_pct") is not None]
                drows.append(
                    [
                        day,
                        _n(min(temps) if temps else None),
                        _n(max(temps) if temps else None),
                        _n(sum(rhs) / len(rhs) if rhs else None),
                        _c(max(vpds) if vpds else None),
                    ]
                )
            out.append("Days 4-7:")
            out.append(table(["day", "Tmin", "Tmax", "RHavg", "VPDcmax"], drows))
    if inputs.forecast_bias:
        out.append("Forecast minus observed, last 7 days, by lead:")
        out.append(
            table(
                ["lead", "n", "dT", "dRH"],
                [
                    [b["lead"], _n(b.get("n")), _n(b.get("temp_err"), 1), _n(b.get("rh_err"))]
                    for b in inputs.forecast_bias
                ],
            )
        )
    return "\n".join(out)


def _hourly_rows(inputs: PackInputs, start: datetime, end: datetime, step_h: int) -> list[dict]:
    rows = [r for r in inputs.hourly if start <= r["h"] < end]
    if step_h <= 1:
        return rows
    out = []
    for i in range(0, len(rows), step_h):
        chunk = rows[i : i + step_h]

        def mean(key, chunk=chunk):
            vals = [r[key] for r in chunk if r.get(key) is not None]
            return sum(vals) / len(vals) if vals else None

        merged = {"h": chunk[0]["h"], "hours": len(chunk)}
        for key in ("t", "rh", "vpd", "dp", "tout", "rhout", "solar"):
            merged[key] = mean(key)
        out.append(merged)
    return out


def s7_observed(inputs: PackInputs, level: int) -> str:
    clock = Clock(inputs)
    step = (1, 2, 3, 4)[min(level, 3)]
    end = inputs.as_of.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=72)
    rows = []
    for r in _hourly_rows(inputs, start, end, step):
        span_end = r["h"] + timedelta(hours=r.get("hours", 1))
        band = band_at(inputs, r["h"] + timedelta(minutes=30 * r.get("hours", 1)))
        mins = {e: minutes_on(inputs.equipment.get(e, []), r["h"], span_end) for e in TABLE_EQUIPMENT}
        rows.append(
            [
                clock.hour(r["h"]),
                _n(r.get("t")),
                _n(temp_delta(r.get("t"), band)),
                _n(r.get("rh")),
                _c(r.get("vpd")),
                _c(vpd_delta(r.get("vpd"), band)),
                _n(r.get("dp")),
                _n(r.get("tout")),
                _n(r.get("rhout")),
                _n(mins["heat1"], blank_zero=True),
                _n(mins["heat2"], blank_zero=True),
                _n(mins["fog"], blank_zero=True),
                _n(mins["mister_any"], blank_zero=True),
                _n(mins["vent"], blank_zero=True),
                _n(max(mins["fan1"], mins["fan2"]), blank_zero=True),
            ]
        )
    header = ["time", "T", "dT", "RH", "VPDc", "dVc", "DP", "Tout", "RHout", "h1", "h2", "fog", "mist", "vent", "fan"]
    return f"Observed, {step}-hour rows, minutes on per row:\n" + table(header, rows)


def _nights(inputs: PackInputs, count: int) -> list[tuple[str, datetime, datetime]]:
    clock = Clock(inputs)
    sun = inputs.sun
    out = []
    for offset in range(count, 0, -1):
        day = inputs.as_of - timedelta(days=offset)
        start = clock.at_minutes(day, sun.get("sunset", 1020))
        end = clock.at_minutes(day + timedelta(days=1), sun.get("sunrise", 420))
        if end <= inputs.as_of:
            out.append((clock.local(start).strftime("%a %m-%d"), start, end))
    return out


def s8_night(inputs: PackInputs, level: int) -> str:
    rows = []
    for label, start, end in _nights(inputs, 7 if level == 0 else 4):
        hours = [r for r in inputs.hourly if start <= r["h"] < end]
        below = kpah = drier = 0.0
        for r in hours:
            band = band_at(inputs, r["h"] + timedelta(minutes=30))
            d = vpd_delta(r.get("vpd"), band)
            if d is not None and d < 0:
                below += 1
                kpah += -d
            out_dp = dew_point_f(r.get("tout"), r.get("rhout"))
            if out_dp is not None and r.get("dp") is not None and out_dp < r["dp"] - 2:
                drier += 1
        rows.append(
            [
                label,
                _n(len(hours)),
                _n(below, blank_zero=True),
                _n(kpah, 2, blank_zero=True),
                _n(minutes_on(inputs.equipment.get("heat1", []), start, end), blank_zero=True),
                _n(minutes_on(inputs.equipment.get("heat2", []), start, end), blank_zero=True),
                _n(drier, blank_zero=True),
            ]
        )
    return "\n".join(
        [
            "Nights (sunset to sunrise): hours, hours VPD below band, kPa-h below, heat minutes,"
            " hours outdoor dew point >2 °F below indoor (outdoor air could dry the house).",
            table(["night", "h", "below", "kPa-h", "h1", "h2", "drierOut"], rows),
            f"night_vpd_bias_kpa in force now: {_n(inputs.night_vpd_bias_now, 2) or 'unknown'}",
        ]
    )


def s9_recent(inputs: PackInputs, level: int) -> str:
    clock = Clock(inputs)
    fine = inputs.trigger.event_type in ("TRANSITION", "FORECAST_DEVIATION")
    minutes = ((5 if fine else 10), 15, 30, 60)[min(level, 3)]
    rows = []
    last = None
    for r in inputs.recent:
        slot = r["ts"].replace(second=0, microsecond=0)
        slot = slot - timedelta(minutes=slot.minute % minutes)
        if slot == last:
            continue
        last = slot
        band = band_at(inputs, r["ts"])
        rows.append(
            [
                clock.local(r["ts"]).strftime("%H:%M"),
                _n(r.get("t"), 1),
                _n(r.get("rh")),
                _c(r.get("vpd")),
                _c(vpd_delta(r.get("vpd"), band)),
                _n(r.get("dp"), 1),
                _n(r.get("tout")),
            ]
        )
    return f"Last 6 h, {minutes}-minute rows:\n" + table(["time", "T", "RH", "VPDc", "dVc", "DP", "Tout"], rows)


def _pairs(inputs: PackInputs) -> list[tuple[dict, dict, dict[str, float]]]:
    """Consecutive hourly rows with the minutes each equipment ran in the first hour."""
    rows = inputs.hourly
    out = []
    for a, b in zip(rows, rows[1:], strict=False):
        if (b["h"] - a["h"]) != timedelta(hours=1):
            continue
        mins = {e: minutes_on(inputs.equipment.get(e, []), a["h"], b["h"]) for e in TABLE_EQUIPMENT}
        out.append((a, b, mins))
    return out


def s10_priors(inputs: PackInputs, level: int) -> str:
    pairs = _pairs(inputs)
    bins = (("<25", -999, 25), ("25-40", 25, 40), ("40-55", 40, 55), (">=55", 55, 999))
    rows = []
    for label, lo, hi in bins:
        cells = [label]
        for _name, test in (
            ("h1", lambda m: m["heat1"] >= 30 and m["heat2"] < 5),
            ("h2", lambda m: m["heat2"] >= 30),
            ("none", lambda m: m["heat1"] < 5 and m["heat2"] < 5),
        ):
            deltas = [
                b["t"] - a["t"]
                for a, b, m in pairs
                if a.get("tout") is not None
                and lo <= a["tout"] < hi
                and a.get("t") is not None
                and b.get("t") is not None
                and test(m)
            ]
            cells.append(f"{_n(sum(deltas) / len(deltas), 1)} ({len(deltas)})" if deltas else "")
        rows.append(cells)
    out = [
        "Deterministic, last 14 days of hourly rows. Mean indoor dT per hour (n hours) by outdoor temp bin:",
        table(["Tout", "heat1 only", "heat2", "no heat"], rows),
    ]

    def per(e: str, key: str, scale: float) -> str:
        sel = [
            (b[key] - a[key], m[e])
            for a, b, m in pairs
            if m[e] > 0 and a.get(key) is not None and b.get(key) is not None
        ]
        if not sel:
            return "no hours"
        return f"{_n(sum(d for d, _ in sel) * scale / sum(m for _, m in sel) * 10, 1)} per 10 min ({len(sel)} h)"

    out.append(f"VPDc change: fog {per('fog', 'vpd', 100)}; mist {per('mister_any', 'vpd', 100)}")
    if level == 0:
        vent = [
            (b["t"] - a["t"], b["dp"] - a["dp"])
            for a, b, m in pairs
            if m["vent"] >= 30 and None not in (a.get("t"), b.get("t"), a.get("dp"), b.get("dp"))
        ]
        if vent:
            out.append(
                f"vent >= 30 min/h: mean dT {_n(sum(v[0] for v in vent) / len(vent), 1)}/h,"
                f" dDP {_n(sum(v[1] for v in vent) / len(vent), 1)}/h ({len(vent)} h)"
            )
        clock = Clock(inputs)
        night = [
            b["rh"] - a["rh"]
            for a, b, _ in pairs
            if not clock.is_day(a["h"]) and a.get("rh") is not None and b.get("rh") is not None
        ]
        if night:
            out.append(f"night RH drift: {_n(sum(night) / len(night), 1)} %/h ({len(night)} h)")
    return "\n".join(out)


def s11_scorecard(inputs: PackInputs, level: int) -> str:
    clock = Clock(inputs)
    days: dict[str, list[dict]] = {}
    for r in inputs.hourly:
        days.setdefault(clock.local(r["h"]).strftime("%a %m-%d"), []).append(r)
    labels = list(days)[-(14 if level == 0 else 7) :]
    rows = []
    for label in labels:
        hours = days[label]
        stats = {k: 0.0 for k in ("td", "tn", "vd", "vn", "nd", "nn", "below", "above", "kbelow", "cold", "hot")}
        for r in hours:
            band = band_at(inputs, r["h"] + timedelta(minutes=30))
            if not band or r.get("t") is None or r.get("vpd") is None:
                continue
            day = clock.is_day(r["h"])
            stats["nd" if day else "nn"] += 1
            if temp_delta(r["t"], band) is None:
                stats["td" if day else "tn"] += 1
            dv = vpd_delta(r["vpd"], band)
            if dv is None:
                stats["vd" if day else "vn"] += 1
            elif dv < 0:
                stats["below"] += 1
                stats["kbelow"] += -dv
            else:
                stats["above"] += 1
            dt = temp_delta(r["t"], band)
            if dt is not None:
                stats["cold" if dt < 0 else "hot"] += abs(dt)
        start = hours[0]["h"]
        end = hours[-1]["h"] + timedelta(hours=1)
        mins = {e: minutes_on(inputs.equipment.get(e, []), start, end) for e in ("heat1", "heat2", "fog", "mister_any")}
        water = [r.get("water") for r in hours if r.get("water") is not None]
        n = max(1.0, stats["nd"] + stats["nn"])

        def pct(a, b):
            return _n(100 * a / b) if b else ""

        rows.append(
            [
                label,
                pct(stats["td"], stats["nd"]),
                pct(stats["tn"], stats["nn"]),
                pct(stats["vd"], stats["nd"]),
                pct(stats["vn"], stats["nn"]),
                pct(stats["below"], n),
                pct(stats["above"], n),
                _n(stats["kbelow"], 1, blank_zero=True),
                _n(stats["cold"], blank_zero=True),
                _n(stats["hot"], blank_zero=True),
                _n(mins["heat1"], blank_zero=True),
                _n(mins["heat2"], blank_zero=True),
                _n(mins["fog"], blank_zero=True),
                _n(mins["mister_any"], blank_zero=True),
                _n(max(water) if water else None, blank_zero=True),
            ]
        )
    header = [
        "day",
        "Tin%d",
        "Tin%n",
        "Vin%d",
        "Vin%n",
        "Vlo%",
        "Vhi%",
        "kPa-h lo",
        "°F-h cold",
        "°F-h hot",
        "h1",
        "h2",
        "fog",
        "mist",
        "gal",
    ]
    return "Served-band scorecard by local day (d = day hours, n = night hours), equipment minutes:\n" + table(
        header, rows
    )


def _window_score(inputs: PackInputs, start: datetime, end: datetime) -> str:
    hours = [r for r in inputs.hourly if start <= r["h"] < end]
    t_in = v_in = n = 0
    for r in hours:
        band = band_at(inputs, r["h"] + timedelta(minutes=30))
        if not band or r.get("t") is None or r.get("vpd") is None:
            continue
        n += 1
        t_in += temp_delta(r["t"], band) is None
        v_in += vpd_delta(r["vpd"], band) is None
    return (
        f"T in band {_n(100 * t_in / n)}%, VPD in band {_n(100 * v_in / n)}% over {n} h"
        if n
        else "no realized hours yet"
    )


def s12_plans(inputs: PackInputs, level: int) -> str:
    clock = Clock(inputs)
    plans = inputs.plans[-(6 if level == 0 else 3) :]
    lines = []
    previous = None
    if len(inputs.plans) > len(plans):
        previous = inputs.plans[-len(plans) - 1]
    for plan in plans:
        intents = plan.get("climate_intents") or []
        first = (intents[0].get("climate_intent") or {}) if intents else {}
        prev_first = {}
        if previous and previous.get("climate_intents"):
            prev_first = previous["climate_intents"][0].get("climate_intent") or {}
        deltas = [
            f"{k} {_n(prev_first.get(k), 2)}->{_n(v, 2)}"
            for k, v in sorted(first.items())
            if k in INTENT_FIELDS and prev_first.get(k) is not None and abs(float(v) - float(prev_first[k])) > 1e-9
        ]
        created = plan.get("created_at")
        valid_from = plan.get("valid_from") or created
        expires = plan.get("expires_at") or (created + timedelta(hours=24) if created else None)
        score = _window_score(inputs, valid_from, min(expires, inputs.as_of)) if valid_from and expires else ""
        clamps = sum(1 for c in inputs.clamps if c.get("plan_id") == plan.get("plan_id"))
        lines.append(
            f"- {plan.get('plan_id')} {clock.hhmm(created) if created else ''} {plan.get('planner_instance') or ''}:"
            f" {len(intents)} waypoints; realized {score}; clamps 24 h {clamps}"
        )
        lines.append("  changed vs previous: " + ("; ".join(deltas[:8]) if deltas else "nothing"))
        hyp = plan.get("hypothesis_structured")
        if isinstance(hyp, dict):
            preds = hyp.get("predictions") or []
            if preds and level == 0:
                lines.append(
                    "  predicted: "
                    + "; ".join(
                        f"{p.get('metric')} {p.get('window', '')} {p.get('value', p.get('expected', ''))}"
                        for p in preds[:3]
                    )
                )
        previous = plan
    if not lines:
        return "No previous plans."
    return (
        "Recent plans, oldest first. Realized scores are deterministic served-band hours, not self-grades:\n"
        + "\n".join(lines)
    )


def s13_alerts(inputs: PackInputs, level: int) -> str:
    lines = []
    for a in inputs.alerts[:12]:
        age = (inputs.as_of - a["ts"]).total_seconds() / 3600 if a.get("ts") else None
        lines.append(
            f"- {a.get('severity')} {a.get('alert_type')} {a.get('sensor_id') or ''} {_n(age)} h:"
            f" {str(a.get('message') or '')[:90]}"
        )
    groups: dict[str, list[dict]] = {}
    for c in inputs.clamps:
        groups.setdefault(c.get("parameter") or "?", []).append(c)
    clamp_lines = [
        f"- {name}: {len(rows)}x, last {_n(rows[-1].get('requested'), 2)}->{_n(rows[-1].get('applied'), 2)} ({str(rows[-1].get('reason') or '')[:50]})"
        for name, rows in sorted(groups.items())
    ]
    return "\n".join(
        ["Open alerts:"] + (lines or ["none"]) + ["Dispatcher clamps, last 24 h:"] + (clamp_lines or ["none"])
    )


def s14_crops(inputs: PackInputs, level: int) -> str:
    if not inputs.crops:
        return "No active plantings."
    return "\n".join(
        f"- {c.get('name')} ({c.get('variety') or ''}) {c.get('zone') or ''}/{c.get('position') or ''},"
        f" stage {c.get('stage') or '?'}, count {c.get('count') or '?'}"
        for c in inputs.crops
    )


def s15_task(inputs: PackInputs, level: int) -> str:
    goal = EVENT_GOALS.get(inputs.trigger.event_type, EVENT_GOALS["MANUAL"])
    return "\n".join(
        [
            f"Event goal: {goal}",
            (STATIC / "task.md").read_text().strip(),
            "Anchors: " + ", ".join(anchors(inputs)),
            "Decision-surface fields: " + ", ".join(INTENT_FIELDS),
        ]
    )


BUILDERS: dict[str, tuple[Callable[[PackInputs, int], str], int]] = {
    # key: (builder, highest reduction level)
    "S0": (s0_system, 0),
    "S1": (s1_decision, 1),
    "S2": (s2_card, 0),
    "S3": (s3_playbook, 0),
    "S4": (s4_trigger, 0),
    "S5": (s5_now, 0),
    "S6": (s6_forecast, 3),
    "S7": (s7_observed, 3),
    "S8": (s8_night, 1),
    "S9": (s9_recent, 3),
    "S10": (s10_priors, 1),
    "S11": (s11_scorecard, 1),
    "S12": (s12_plans, 1),
    "S13": (s13_alerts, 0),
    "S14": (s14_crops, 0),
    "S15": (s15_task, 0),
}


# ── assembly ─────────────────────────────────────────────────────────────────


@dataclass
class Section:
    key: str
    title: str
    budget: int
    text: str
    tokens: int
    level: int = 0
    truncated: bool = False
    dropped: bool = False

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    def meta(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "budget": self.budget,
            "tokens": self.tokens,
            "sha256": self.sha256,
            "level": self.level,
            "truncated": self.truncated,
            "dropped": self.dropped,
        }


@dataclass
class Pack:
    version: str
    variant: str
    sections: list[Section]
    anchors: list[str]
    token_method: str
    system: str = ""
    user: str = ""
    extra: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self.system + "\n\n" + self.user

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    def sections_meta(self) -> list[dict]:
        meta = [s.meta() for s in self.sections]
        for extra in self.extra:
            meta.append(
                {
                    "key": "repair",
                    "title": "Repair",
                    "tokens": tokens.count(extra),
                    "sha256": hashlib.sha256(extra.encode()).hexdigest(),
                }
            )
        return meta

    @property
    def total_tokens(self) -> int:
        return sum(s.tokens for s in self.sections if not s.dropped) + sum(tokens.count(x) for x in self.extra)


def _truncate(text: str, budget: int) -> str:
    lines = text.split("\n")
    while len(lines) > 1 and tokens.count("\n".join(lines + ["[cut to budget]"])) > budget:
        lines.pop()
    return "\n".join(lines + ["[cut to budget]"])


def render_section(key: str, inputs: PackInputs) -> Section:
    spec = BY_KEY[key]
    builder, max_level = BUILDERS[key]
    level = 0
    while True:
        body = f"## {key} {spec.title}\n" + builder(inputs, level)
        n = tokens.count(body)
        if n <= spec.budget or level >= max_level:
            break
        level += 1
    section = Section(key, spec.title, spec.budget, body, n, level)
    if n > spec.budget:
        section.text = _truncate(body, spec.budget)
        section.tokens = tokens.count(section.text)
        section.truncated = True
    return section


def build_pack(inputs: PackInputs, variant: str = "v1", repair_errors: Sequence[str] | None = None) -> Pack:
    """Render the pack. variant "v1" is all 16 sections; "minimum" is S0-S6 + S15 (repair)."""
    if variant not in ("v1", "minimum"):
        raise ValueError(f"unknown pack variant {variant!r}")
    specs = [s for s in SECTIONS if variant == "v1" or s.minimum]
    sections = [render_section(spec.key, inputs) for spec in specs]
    by_key = {s.key: s for s in sections}
    for key in DROP_ORDER:
        if sum(s.tokens for s in sections if not s.dropped) <= PACK_HARD_CAP:
            break
        if key in by_key:
            by_key[key].dropped = True
    pack = Pack(PACK_VERSION, variant, sections, anchors(inputs), tokens.method())
    live = [s for s in sections if not s.dropped]
    pack.system = live[0].text
    body = [s.text for s in live[1:-1]]
    if repair_errors:
        repair = (
            "## Repair\nYour previous answer was rejected. Fix these errors and answer again with one JSON object:\n"
            + "\n".join(f"- {e}" for e in list(repair_errors)[:12])
        )
        pack.extra.append(repair)
        body.append(repair)
    body.append(live[-1].text)
    pack.user = "\n\n".join(body)
    return pack
