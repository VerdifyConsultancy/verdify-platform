#!/usr/bin/env python3
"""Render the Settings screen's fixture, the base API-09 response, from the tunable registry (VD-12, #1132).

``dashboard/e2e/fixtures/settings.json`` is test data. It is rendered at test time and never committed (it is in
``dashboard/.gitignore``): ``pnpm fixtures:settings`` runs this script before vitest, and
``tests/test_dashboard_settings_fixture.py`` renders it in process. The fixture API (``dashboard/e2e/fixtures/world.mjs``)
reads it as the base ``GET /v1/settings`` body, and its named fixtures patch it.

It holds one ``Setting`` per ``REGISTRY`` row: its label, unit and step from ``TunableDef`` (VD-29) with no overlay, its
bounds from ``planner_effective_bounds`` and its group from ``tunable_control_class``; the read-only rows of
architecture §5.3 step 1 with their reasons; the "Band in force" rows as API-09 serves them (VD-55); and the
scenario values the screens draw (readbacks, desired and plan rows). Every time is relative to ``server_time``,
10:42 America/Denver on 2026-11-02 (the screens' clock); the fixture API moves them to each request's own time.

The writability rule below is test data only. In production the operator API's settings service (VD-67) decides it;
the dashboard never does, and shows ``writable`` and ``read_only_reason`` as API-09 serves them.

It needs only pydantic and pyyaml, because the dashboard CI step has no repository environment::

    uv run --no-project --with 'pydantic>=2.8,<3' --with 'pyyaml>=6,<7' python scripts/gen-dashboard-settings-fixture.py
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verdify_schemas.setting_help import (
    band_in_force_help,
    band_in_force_sources,
    setting_help,
    setting_label,
    source_params,
)
from verdify_schemas.tunable_registry import REGISTRY, TunableDef, planner_effective_bounds, tunable_control_class

OUT = ROOT / "dashboard" / "e2e" / "fixtures" / "settings.json"
# 10:42 America/Denver on 2026-11-02 (MST, UTC-7): the screens' clock, as in dashboard/e2e/fixtures/world.mjs.
SERVER_TIME = datetime(2026, 11, 2, 17, 42, tzinfo=UTC)
CLOCK_MINUTES = 10 * 60 + 42

# ---------------------------------------------------------------------------------------------------- groups
WRITABLE_CLASSES = ("planner_policy", "scheduled_policy", "controller_safety", "crop_band")
GROUP_OF_CLASS = {**{name: name for name in WRITABLE_CLASSES}, "irrigation_site": "site_water_caps"}

# Architecture §5.3 step 1: the rows a desired-state write may not target, each with its reason.
LEGACY_WIDE_LIMITS = frozenset({"temp_low", "temp_high", "vpd_low", "vpd_high"})
WEEKLY_FEED = frozenset(
    {
        "sw_wall_fertigation_commissioned",
        "wall_commissioning_revision",
        "wall_commissioning_evidence_mask",
        "wall_commissioning_valid_until_epoch_s",
        "wall_flow_lpm",
        "wall_flow_valid_until_epoch_s",
        "wall_prewet_liters",
        "wall_feed_liters",
        "wall_flush_liters",
    }
)
BAND_OWNER = "sw_onchip_band_enabled"
# The crop_band rows a crop_band_anchors row holds: the house anchors, the zone VPD targets and their widths.
ANCHOR_ROW = re.compile(
    r"^(band_(temp|vpd)_(low|target|high)|zone_vpd_target_[a-z]+)_(sr|sm|ss|mid)$|^zone_vpd_width_(below|above)_[a-z]+$"
)
REASONS = {
    "retired": "retired, read-only: the controller no longer uses it",
    "readback_context": "readback only, read-only: the controller reports it and nothing sends it",
    "legacy": "legacy limit, read-only: not the band while the on-chip band runs",
    "zone_target": "zone VPD target, read-only: computed from the crop curves; the controller uses it for zone misting",
    "band_owner": "read-only: it decides whether the controller or the dispatcher owns the band",
    "weekly_feed": "legacy feed commissioning, read-only: retiring with the irrigation engine",
    "no_anchor": "read-only: no crop band anchor row holds it",
    "no_entity": "read-only: no controller entity takes it",
}
BAND_REASON = "derived by the dispatcher from the crop band anchors"
# Each reason's Setting.read_only_kind (#1333).
KINDS = {
    "retired": "retired",
    "readback_context": "readback_context",
    "legacy": "legacy_wide_limit",
    "zone_target": "zone_vpd_target",
    "band_owner": "band_owner",
    "weekly_feed": "no_entity",
    "no_anchor": "no_anchor_row",
    "no_entity": "no_entity",
}


def read_only_key(name: str, spec: TunableDef) -> str | None:
    """The ``REASONS`` key that refuses a desired-state write to the row now, or None (architecture §5.3 step 1)."""
    control_class = tunable_control_class(name, spec)
    if control_class == "irrigation_site":
        return None
    if name == BAND_OWNER:
        return "band_owner"
    if control_class in ("retired", "readback_context"):
        return control_class
    if name in LEGACY_WIDE_LIMITS:
        return "legacy"
    if name.startswith("vpd_target_"):
        return "zone_target"
    if name in WEEKLY_FEED:
        return "weekly_feed"
    if control_class == "crop_band" and not ANCHOR_ROW.match(name):
        return "no_anchor"
    if control_class in WRITABLE_CLASSES and spec.esp_object_id:
        return None
    return "no_entity"


def write_rule(name: str, spec: TunableDef) -> tuple[bool, str | None]:
    """Whether a desired-state write may target the row now, and why not (architecture §5.3 step 1)."""
    key = read_only_key(name, spec)
    return (True, None) if key is None else (False, REASONS[key])


def group_of(control_class: str, writable: bool) -> str:
    """The Settings group: the class for a writable row (Site water caps for irrigation_site), else Read-only."""
    return GROUP_OF_CLASS[control_class] if writable else "read_only"


def readback_via(spec: TunableDef, control_class: str) -> str:
    """cfg_* sensor, native entity, the irrigation program (irrigation_site) or none."""
    if control_class == "irrigation_site":
        return "program"
    if spec.cfg_readback_object_id:
        return "cfg"
    if spec.esp_object_id:
        return "entity"
    return "none"


# ---------------------------------------------------------------------------------------------------- time
def at(clock: str, days: int = 0) -> datetime:
    """The screens' clock: ``at("07:02")`` is 3 h 40 min before SERVER_TIME (``clock()`` in world.mjs)."""
    hours, minutes = (int(part) for part in clock.split(":"))
    return SERVER_TIME + timedelta(minutes=hours * 60 + minutes - CLOCK_MINUTES + days * 1440)


def ago(seconds: float) -> datetime:
    return SERVER_TIME - timedelta(seconds=seconds)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---------------------------------------------------------------------------------------------------- scenario
def reading(value: float | None, observed: datetime | None, reason: str | None = None) -> dict[str, Any]:
    return {"value": value, "observed_at": None if observed is None else iso(observed), "reason": reason}


IRIS_PLAN_ID = "iris-20261102-0702"


def iris_plan(value: float, next_at: str | None = None, next_value: float | None = None) -> dict[str, Any]:
    """Iris's plan written at 07:02 (API-07's plan in world.mjs), with its next waypoint when it has one."""
    upcoming = (
        None if next_at is None else {"at": iso(at(next_at)), "value": next_value, "source": "iris", "effective": True}
    )
    return {"value": value, "plan_id": IRIS_PLAN_ID, "source": "iris", "written_at": iso(at("07:02")), "next": upcoming}


def desired(
    row_id: int,
    param: str,
    value: float,
    *,
    source: str,
    name: str,
    manual: bool,
    created: datetime,
    confirmed: datetime,
    reason: str | None,
) -> dict[str, Any]:
    """A confirmed desired_state row (DesiredSetting)."""
    return {
        "id": row_id,
        "param": param,
        "value": value,
        "snapped_from": None,
        "manual": manual,
        "manual_before": None,
        "state": "confirmed",
        "state_reason": None,
        "confirmed_at": iso(confirmed),
        "actor": {"source": source, "name": name},
        "reason": reason,
        "created_at": iso(created),
    }


# The screens' values (SCR-01, SCR-03, SCR-05): Iris's 07:02 plan, an agent's value and a forecast rule's, the L01
# bridge's duration and the site caps in program rev 42. Every other row reads its registry default, confirmed.
SCENARIO: dict[str, dict[str, Any]] = {
    "mister_engage_kpa": {
        "readback": reading(1.6, ago(120)),
        "plan": iris_plan(1.6, "13:00", 1.7),
        "applied_from": "plan",
    },
    "mister_engage_delay_s": {"readback": reading(60.0, ago(180)), "plan": iris_plan(60.0), "applied_from": "plan"},
    "mister_pulse_gap_s": {
        "readback": reading(45.0, ago(60)),
        "plan": iris_plan(45.0, "16:30", 55.0),
        "applied_from": "plan",
    },
    "mister_pulse_on_s": {"readback": reading(60.0, ago(60))},
    "fog_rh_ceiling_pct": {
        "readback": reading(88.0, at("06:48")),
        "desired": desired(
            41,
            "fog_rh_ceiling_pct",
            88.0,
            source="agent:steward",
            name="steward",
            manual=False,
            created=at("06:47"),
            confirmed=at("06:48"),
            reason="Dry afternoon ahead",
        ),
        "applied_from": "desired",
    },
    "sw_economiser_enabled": {
        "readback": reading(0.0, at("06:31")),
        "plan": {
            "value": 0.0,
            "plan_id": "preemptive-high-wind",
            "source": "preemptive",
            "written_at": iso(at("06:30")),
            "next": None,
        },
        "applied_from": "plan",
    },
    "temp_low": {"readback": reading(40.0, ago(240))},
    "outdoor_temp_f": {"readback": reading(41.2, ago(120))},
    "outdoor_dewpoint_f": {"readback": reading(28.4, ago(120))},
    "mister_daily_volume_max_gal": {"readback": reading(360.0, ago(120))},
    "sched_mist_rh_max_pct": {"readback": reading(90.0, ago(120))},
    "climate_yield_min": {"readback": reading(15.0, ago(120))},
}
DEFAULT_AGE_S = 240

# The Band in force rows (VD-55): zone house and each zone, six roles each, the temperature roles the house curve's.
BAND_ZONES = ("house", "center", "south", "west", "east")
BAND_ROLES = ("temp_low", "temp_target", "temp_high", "vpd_low", "vpd_target", "vpd_high")
HOUSE_TEMP = {"temp_low": 62.0, "temp_target": 66.0, "temp_high": 70.0}
BAND_VPD = {
    "house": (0.45, 0.62, 0.8),
    "center": (0.5, 0.65, 0.85),
    "south": (0.45, 0.6, 0.8),
    "west": (0.45, 0.6, 0.8),
    "east": (0.5, 0.7, 0.9),
}
BAND_AGE_S = 240


def finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


# ---------------------------------------------------------------------------------------------------- rows
def registry_row(name: str, spec: TunableDef) -> dict[str, Any]:
    """One registry row as API-09 serves it."""
    control_class = tunable_control_class(name, spec)
    writable, reason = write_rule(name, spec)
    low, high = planner_effective_bounds(name)
    via = readback_via(spec, control_class)
    default = finite(spec.default)
    row: dict[str, Any] = {
        "param": name,
        "label": setting_label(name),
        "control_class": control_class,
        "group": group_of(control_class, writable),
        "kind": spec.kind,
        "unit": spec.unit,
        "min": low,
        "max": high,
        "step": spec.step,
    }
    if spec.kind == "enum":
        row["options"] = [
            {"value": value, "label": label}
            for label, value in sorted((spec.enum_values or {}).items(), key=lambda item: item[1])
        ]
    # A row with no readback path has no readback (it reads "—" with NO READBACK); every other row reads its registry
    # default, confirmed.
    readback = (
        None
        if via == "none"
        else reading(default, ago(DEFAULT_AGE_S), None if default is not None else "not reported yet")
    )
    row |= {
        "writable": writable,
        "read_only_reason": reason,
        "read_only_kind": None if writable else KINDS[read_only_key(name, spec) or "no_entity"],
        "source_params": list(source_params(name)),
        "help": help_json(setting_help(name)),
        "readback_via": via,
        "readback": readback,
        "desired": None,
        "plan": None,
        "applied_from": "band" if spec.push_owner in ("band", "schedule") else "default",
        "state": "no_readback" if via == "none" else "confirmed",
    }
    row |= SCENARIO.get(name, {})
    return row


def help_json(help_text: Any) -> dict[str, Any] | None:
    """A ``SettingHelp`` as API-09 serves it (``raise``, not ``raise_``)."""
    return None if help_text is None else help_text.model_dump(mode="json")


def band_rows() -> Iterator[dict[str, Any]]:
    """The Band in force rows as API-09 serves them: DERIVED, read-only, no readback path."""
    for zone in BAND_ZONES:
        vpd = dict(zip(("vpd_low", "vpd_target", "vpd_high"), BAND_VPD[zone], strict=True))
        for role in BAND_ROLES:
            series, edge = role.split("_")
            yield {
                "param": f"band_{zone}_{role}",
                "label": f"{zone.capitalize()} {'temp' if series == 'temp' else 'VPD'} {edge}",
                "control_class": "crop_band",
                "group": "band_in_force",
                "kind": "numeric",
                "unit": "°F" if series == "temp" else "kPa",
                "min": None,
                "max": None,
                "step": None,
                "writable": False,
                "read_only_reason": BAND_REASON,
                "read_only_kind": "band_in_force",
                "source_params": list(band_in_force_sources(zone, role)),
                "help": help_json(band_in_force_help(zone, role)),
                "readback_via": "none",
                "readback": reading(HOUSE_TEMP[role] if series == "temp" else vpd[role], ago(BAND_AGE_S)),
                "desired": None,
                "plan": None,
                "applied_from": "band",
                "state": "no_readback",
            }


def build_payload() -> dict[str, Any]:
    """The base API-09 body: every registry row in registry order, then the Band in force rows."""
    items = [registry_row(name, spec) for name, spec in REGISTRY.items()]
    return {"items": [*items, *band_rows()], "server_time": iso(SERVER_TIME)}


def unused_scenario() -> list[str]:
    """Scenario rows the registry no longer has. They are skipped, so another issue retiring a row (as #1165's
    follow-up retired the L01 bridge's rows) never breaks the render."""
    return sorted(set(SCENARIO) - set(REGISTRY))


def render(out: Path) -> int:
    """Writes the payload to `out` (replacing it whole) and returns the number of rows."""
    unused = unused_scenario()
    if unused:
        print(f"settings fixture: scenario rows with no registry row, skipped: {', '.join(unused)}", file=sys.stderr)
    payload = build_payload()
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=1) + "\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".settings-", suffix=".json", dir=out.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temporary, 0o644)
        os.replace(temporary, out)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return len(payload["items"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUT, help="where to write the fixture (default: %(default)s)")
    args = parser.parse_args(argv)
    count = render(args.out)
    print(f"settings fixture: {count} rows from the tunable registry -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
