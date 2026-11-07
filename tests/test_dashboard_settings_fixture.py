"""The Settings screen's fixture, rendered at test time from the tunable registry (VD-12, #1132).

scripts/gen-dashboard-settings-fixture.py writes dashboard/e2e/fixtures/settings.json, the fixture API's base API-09
response. Nothing commits that file, so these tests render it in process and check it against REGISTRY itself:
one Setting per registry row, in the group its control_class gives, with TunableDef's label, unit and step. A
registry change in another issue changes the render, never turns a committed copy stale.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from verdify_schemas.setting_help import setting_help, setting_label
from verdify_schemas.tunable_registry import REGISTRY, planner_effective_bounds, tunable_control_class

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gen-dashboard-settings-fixture.py"
WRITABLE_GROUPS = {"planner_policy", "scheduled_policy", "controller_safety", "crop_band"}
TIME_FIELDS = {"observed_at", "created_at", "confirmed_at", "since", "written_at", "at"}


@pytest.fixture(scope="module")
def generator():
    spec = importlib.util.spec_from_file_location("gen_dashboard_settings_fixture", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def payload(generator):
    return generator.build_payload()


@pytest.fixture(scope="module")
def rows(payload):
    return {row["param"]: row for row in payload["items"]}


def registry_rows(payload):
    return [row for row in payload["items"] if row["group"] != "band_in_force"]


def test_one_setting_per_registry_row_in_its_class_group_with_tunabledef_label_unit_and_step(payload):
    registry = registry_rows(payload)
    assert [row["param"] for row in registry] == list(REGISTRY)
    for row in registry:
        name = row["param"]
        spec = REGISTRY[name]
        control_class = tunable_control_class(name, spec)
        assert row["control_class"] == control_class, name
        assert (row["label"], row["unit"], row["step"], row["kind"]) == (
            setting_label(name),
            spec.unit,
            spec.step,
            spec.kind,
        ), name
        assert row["help"] == setting_help(name).model_dump(mode="json"), name
        assert (row["read_only_kind"] is None) is row["writable"], name
        assert (row["min"], row["max"]) == planner_effective_bounds(name), name
        if not row["writable"]:
            expected = "read_only"
        elif control_class == "irrigation_site":
            expected = "site_water_caps"
        else:
            expected = control_class
        assert row["group"] == expected, name
        assert row["writable"] is (row["read_only_reason"] is None), name
        if row["writable"]:
            assert control_class in WRITABLE_GROUPS | {"irrigation_site"}, name
    groups = Counter(row["group"] for row in registry)
    assert set(groups) == WRITABLE_GROUPS | {"site_water_caps", "read_only"}


def test_mister_engage_kpa_reads_its_entity_name_unit_bounds_and_step(rows):
    row = rows["mister_engage_kpa"]
    assert (row["label"], row["unit"], row["min"], row["max"], row["step"], row["group"], row["writable"]) == (
        "VPD Mister Engage",
        "kPa",
        0.5,
        2.5,
        0.05,
        "planner_policy",
        True,
    )


def present(*names):
    """The names the registry still has: VD-47 (#1162) retired the weekly-feed rows, and a registry change in another
    issue must never turn this test red."""
    return [name for name in names if name in REGISTRY]


def test_read_only_rows_follow_architecture_5_3_step_1(rows, generator):
    def reason(name):
        row = rows[name]
        assert row["writable"] is False, name
        assert row["group"] == "read_only", name
        return row["read_only_reason"]

    # The legacy wide limits read "legacy limit, read-only" (screens SCR-05).
    for name in ("temp_low", "temp_high", "vpd_low", "vpd_high"):
        assert reason(name) == "legacy limit, read-only: not the band while the on-chip band runs"
    # The zone VPD targets are computed but still used by the controller, so they are not legacy limits.
    for name in ("vpd_target_center", "vpd_target_west"):
        assert reason(name).startswith("zone VPD target, read-only"), name
        assert rows[name]["read_only_kind"] == "zone_vpd_target", name
    # #1477: the activity mirror left the registry with its firmware entities.
    for name in ("activity_start_hour", "activity_start_minute", "activity_duration_min"):
        assert name not in rows, name
    assert "owns the band" in reason("sw_onchip_band_enabled")
    for name in present(*sorted(generator.WEEKLY_FEED)):
        assert "retiring with the irrigation engine" in reason(name)
    for name in ("zone_priority_center", "manual_override_timeout_min"):
        assert "crop band anchor" in reason(name)
    retired = [name for name, spec in REGISTRY.items() if tunable_control_class(name, spec) == "retired"]
    context = [name for name, spec in REGISTRY.items() if tunable_control_class(name, spec) == "readback_context"]
    assert retired and context
    for name in retired:
        assert reason(name).startswith("retired"), name
    for name in context:
        assert reason(name), name
    # Writable: the anchors and widths, and the lighting rows.
    for name in present(
        "band_temp_low_sr",
        "band_vpd_target_mid",
        "zone_vpd_target_east_sm",
        "zone_vpd_width_below_center",
        "gl_main_lux_threshold",
        "sw_gl_main_auto_mode",
        "fog_rh_ceiling_pct",
    ):
        assert rows[name]["writable"] is True, name
        assert rows[name]["group"] == rows[name]["control_class"], name


def test_site_caps_come_from_the_registry_with_their_own_label_and_unit(rows):
    caps = {
        name: (
            row["label"],
            row["unit"],
            row["min"],
            row["max"],
            row["step"],
            row["readback_via"],
            row["readback"]["value"],
        )
        for name, row in rows.items()
        if row["group"] == "site_water_caps"
    }
    assert caps == {
        "mister_daily_volume_max_gal": ("SAF-4 ceiling", "gal", 100.0, 600.0, 10.0, "program", 360.0),
        "sched_mist_rh_max_pct": ("Scheduled-mist RH ceiling", "%", 75.0, 98.0, 1.0, "program", 90.0),
        "climate_yield_min": ("Climate yield", "min", 5.0, 30.0, 1.0, "program", 15.0),
    }
    for name in caps:
        assert rows[name]["control_class"] == "irrigation_site"
        assert rows[name]["writable"] is True


def test_band_in_force_rows_as_api_09_serves_them(payload):
    band = [row for row in payload["items"] if row["group"] == "band_in_force"]
    zones = ("house", "center", "south", "west", "east")
    roles = ("temp_low", "temp_target", "temp_high", "vpd_low", "vpd_target", "vpd_high")
    assert [row["param"] for row in band] == [f"band_{zone}_{role}" for zone in zones for role in roles]
    for row in band:
        assert row["param"] not in REGISTRY
        assert row["readback_via"] == "none"
        assert row["state"] == "no_readback"
        assert row["writable"] is False
        assert row["read_only_reason"] == "derived by the dispatcher from the crop band anchors"
        assert (row["control_class"], row["applied_from"], row["desired"], row["plan"]) == (
            "crop_band",
            "band",
            None,
            None,
        )
        assert row["readback"]["value"] is not None
        assert row["readback"]["observed_at"]
        assert row["unit"] == ("°F" if "_temp_" in row["param"] else "kPa")
    labels = {row["param"]: row["label"] for row in band}
    assert labels["band_house_temp_low"] == "House temp low"
    assert labels["band_west_vpd_high"] == "West VPD high"
    # The house band equals API-02's band in the fixture world (62-70 °F, 0.45-0.80 kPa).
    values = {row["param"]: row["readback"]["value"] for row in band}
    assert (values["band_house_temp_low"], values["band_house_temp_high"]) == (62.0, 70.0)
    assert (values["band_house_vpd_low"], values["band_house_vpd_high"]) == (0.45, 0.8)


def test_scenario_rows_the_screens_and_named_fixtures_rely_on(rows):
    engage = rows["mister_engage_kpa"]
    assert engage["readback"]["value"] == 1.6
    assert engage["applied_from"] == "plan"
    assert engage["plan"]["source"] == "iris"
    assert engage["plan"]["next"] == {
        "at": "2026-11-02T20:00:00.000Z",
        "value": 1.7,
        "source": "iris",
        "effective": True,
    }
    # iris-unsnapped puts Iris's 48.75 on this plan.
    assert rows["mister_engage_delay_s"]["plan"]["source"] == "iris"
    agent = rows["fog_rh_ceiling_pct"]
    assert agent["applied_from"] == "desired"
    assert agent["desired"]["actor"] == {"source": "agent:steward", "name": "steward"}
    assert agent["desired"]["manual"] is False
    forecast = rows["sw_economiser_enabled"]
    assert (forecast["applied_from"], forecast["plan"]["source"]) == ("plan", "preemptive")
    assert forecast["plan"]["plan_id"].startswith("preemptive-")
    assert rows["temp_low"]["readback"]["value"] == 40.0


def test_a_scenario_row_the_registry_retired_is_skipped_never_fatal(generator, monkeypatch, capsys, tmp_path):
    # Another issue retiring a row the scenario names (as #1165's follow-up retired the L01 bridge's
    # irrig_wall_duration_min) must not break the render: the row is skipped with a note, and every other row renders
    # as before.
    monkeypatch.setitem(
        generator.SCENARIO, "retired_by_another_issue", {"readback": generator.reading(1.0, generator.SERVER_TIME)}
    )
    assert generator.unused_scenario() == ["retired_by_another_issue"]
    out = tmp_path / "settings.json"
    assert generator.render(out) == len(REGISTRY) + 30
    assert "retired_by_another_issue" in capsys.readouterr().err
    assert "retired_by_another_issue" not in {
        row["param"] for row in json.loads(out.read_text(encoding="utf-8"))["items"]
    }


def test_a_row_with_no_readback_path_has_no_readback(rows):
    # NO READBACK: the API has no readback for it, so the row reads "—" with that badge (US-SET-01).
    for row in rows.values():
        if row["readback_via"] == "none" and row["group"] != "band_in_force":
            assert (row["readback"], row["state"]) == (None, "no_readback"), row["param"]


def test_the_cli_writes_strict_json_with_every_time_relative_to_server_time(tmp_path):
    out = tmp_path / "settings.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "rows from the tunable registry" in result.stdout

    def refuse(constant):
        raise ValueError(f"not JSON: {constant}")

    body = json.loads(out.read_text(encoding="utf-8"), parse_constant=refuse)
    assert set(body) == {"items", "server_time"}
    assert body["server_time"] == "2026-11-02T17:42:00.000Z"
    assert len(body["items"]) == len(REGISTRY) + 30
    required = {"param", "label", "control_class", "group", "kind", "writable", "readback_via", "readback", "desired"}
    required |= {"plan", "applied_from", "state"}
    server = datetime.fromisoformat(body["server_time"])

    def times(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in TIME_FIELDS and isinstance(item, str):
                    yield item
                else:
                    yield from times(item)
        elif isinstance(value, list):
            for item in value:
                yield from times(item)

    for row in body["items"]:
        assert required <= set(row), row["param"]
        for moment in times(row):
            # Within a day of the screens' clock: the fixture API shifts them all by the same amount.
            assert abs((datetime.fromisoformat(moment) - server).total_seconds()) <= 86_400, (row["param"], moment)
    assert list(tmp_path.iterdir()) == [out]
