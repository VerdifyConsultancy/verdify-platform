"""Static Grafana contract tests for band-to-firmware traceability."""

import importlib.util
import json
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest

DASHBOARD_ROOTS = (
    Path("grafana/dashboards"),
    Path("grafana/provisioning/dashboards/json"),
)

LEGACY_BAND_LABELS = (
    "Firmware Actual/Forecast",
    "Band High (Actual/Forecast)",
    "Band Low (Actual/Forecast)",
    "Planned temp_low",
    "Planned temp_high",
    "Planned vpd_low",
    "Planned vpd_high",
)

TEMP_FIELDS = (
    'projected_temp_low::float AS "Reconstructed Low"',
    'projected_temp_high::float AS "Reconstructed High"',
)

VPD_FIELDS = (
    'projected_vpd_low::float AS "Reconstructed Low"',
    'projected_vpd_high::float AS "Reconstructed High"',
)

FORBIDDEN_OPERATOR_FIELDS = (
    "Planner Event",
    "Band/API Update",
    "Firmware Mode Change",
    "Now Divider",
    "Heat Relay",
    "Fan/Vent Relay",
    "Fog Relay",
    "Temp Out of Band",
    "Mist/Fog Relay",
    "VPD Out of Band",
    "Crop Target Low",
    "Crop Target High",
    "Heat Target",
    "Heat On Below",
    "Heat 2 On Below",
    "Heat 2 Clears Above",
    "Cool Hold Until",
    "Cool On Above",
    "Fan On Above",
    "Cool Stage 2 Above",
    "Dehum/Fan Below",
    "Dehum On Below",
    "Dehum Clears Above",
    "Humidify Clears Below",
    "Humidify On Above",
    "Fog On Above",
    "Vent Fog Above",
    "Sealed Fog Above",
)


def _dashboard_paths() -> list[Path]:
    return [path for root in DASHBOARD_ROOTS for path in sorted(root.glob("*.json"))]


def _iter_panels(node: object):
    if not isinstance(node, dict):
        return
    if "targets" in node:
        yield node
    for child in node.get("panels") or ():
        yield from _iter_panels(child)
    for child in node.get("rows") or ():
        yield from _iter_panels(child)


def _panel_sql(panel: dict) -> str:
    return "\n".join(target.get("rawSql", "") for target in panel.get("targets") or ())


def _by_name_override(panel: dict, field_name: str) -> dict | None:
    for override in panel.get("fieldConfig", {}).get("overrides") or ():
        matcher = override.get("matcher", {})
        if matcher.get("id") == "byName" and matcher.get("options") == field_name:
            return override
    return None


def _override_property(override: dict | None, property_id: str):
    if override is None:
        return None
    for prop in override.get("properties") or ():
        if prop.get("id") == property_id:
            return prop.get("value")
    return None


def test_grafana_dashboards_do_not_use_legacy_band_labels():
    dashboards = "\n".join(path.read_text() for path in _dashboard_paths())
    missing = [label for label in LEGACY_BAND_LABELS if label in dashboards]

    assert not missing, f"legacy min/max-only band labels still present: {missing}"


def test_grafana_panels_have_unique_target_ref_ids():
    failures: list[str] = []

    for path in _dashboard_paths():
        data = json.loads(path.read_text())
        for panel in _iter_panels(data):
            refs = [target.get("refId") for target in panel.get("targets") or () if target.get("refId")]
            duplicates = sorted(ref for ref, count in Counter(refs).items() if count > 1)
            if duplicates:
                failures.append(f"{path}:{panel.get('title', '<untitled>')}: duplicate refIds {duplicates}")

    assert not failures, "\n".join(failures)


def test_fn_band_timeline_panels_show_explicit_reconstruction():
    failures: list[str] = []
    checked = 0

    for path in _dashboard_paths():
        data = json.loads(path.read_text())
        for panel in _iter_panels(data):
            sql = _panel_sql(panel)
            if "fn_band_timeline" not in sql:
                continue
            checked += 1

            title = panel.get("title", "<untitled>")
            high = _by_name_override(panel, "Reconstructed High")
            if _override_property(high, "custom.fillBelowTo") != "Reconstructed Low":
                failures.append(f"{path}:{title}: reconstructed fill missing")
            for name in ("Reconstructed Low", "Reconstructed High"):
                hide_from = _override_property(_by_name_override(panel, name), "custom.hideFrom")
                if hide_from != {"legend": False, "tooltip": False, "viz": False}:
                    failures.append(f"{path}:{title}: {name} must remain visible in legend/tooltip")

            if "projected_temp_low" in sql:
                missing = [field for field in TEMP_FIELDS if field not in sql]
                if missing:
                    failures.append(f"{path}:{title}: missing rendered temp fields {missing}")

            if "projected_vpd_low" in sql:
                missing = [field for field in VPD_FIELDS if field not in sql]
                if missing:
                    failures.append(f"{path}:{title}: missing rendered VPD fields {missing}")

            for hidden in FORBIDDEN_OPERATOR_FIELDS:
                if f'AS "{hidden}"' in sql:
                    failures.append(f"{path}:{title}: {hidden} should not be rendered on operator graph")
                if _by_name_override(panel, hidden) is not None:
                    failures.append(f"{path}:{title}: stale override for hidden field {hidden}")

            failures.extend(_brand().check_compliance_dashboard_data(str(path), {"panels": [panel]}))

    assert checked >= 24, "timeline coverage unexpectedly disappeared"
    assert not failures, "\n".join(failures)


def _brand():
    spec = importlib.util.spec_from_file_location("band_brand", "scripts/brand-grafana-embeds.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _timeline_panel(axis="temp"):
    dashboard = json.loads(Path("grafana/dashboards/site-climate.json").read_text())
    return next(p for p in _iter_panels(dashboard) if f"projected_{axis}_low" in _panel_sql(p))


@pytest.mark.parametrize(
    "regression",
    [
        "old_label",
        "hidden_label",
        "missing_description",
        "missing_title",
        "disabled_legend",
        "disabled_tooltip",
        "cfg_fallback",
        "missing_override",
        "broken_fill",
        "mixed_axes",
    ],
)
def test_lineage_checker_rejects_misleading_regressions(regression):
    panel = _timeline_panel()
    target = next(t for t in panel["targets"] if "fn_band_timeline" in t.get("rawSql", ""))
    if regression == "old_label":
        target["rawSql"] = target["rawSql"].replace("Reconstructed", "Compliant")
    elif regression == "hidden_label":
        _by_name_override(panel, "Reconstructed Low")["properties"].append(
            {"id": "custom.hideFrom", "value": {"legend": True, "tooltip": True, "viz": False}}
        )
    elif regression == "missing_description":
        panel.pop("description")
    elif regression == "missing_title":
        panel["title"] = "Temperature Compliance Band"
    elif regression == "disabled_legend":
        panel["options"]["legend"]["showLegend"] = False
    elif regression == "disabled_tooltip":
        panel["options"]["tooltip"]["mode"] = "none"
    elif regression == "cfg_fallback":
        target["rawSql"] = target["rawSql"].replace("projected_", "firmware_")
    elif regression == "mixed_axes":
        target["rawSql"] = target["rawSql"].replace("projected_temp_high", "projected_vpd_high")
    elif regression == "missing_override":
        panel["fieldConfig"]["overrides"].remove(_by_name_override(panel, "Reconstructed High"))
    elif regression == "broken_fill":
        _by_name_override(panel, "Reconstructed High")["properties"].append(
            {"id": "custom.fillBelowTo", "value": "Compliant Low"}
        )
    assert _brand().check_compliance_dashboard_data("fixture", {"panels": [panel]})


@pytest.mark.parametrize("axis", ["temp", "vpd"])
def test_lineage_normalization_is_scoped_idempotent_and_value_preserving(axis):
    brand = _brand()
    panel = _timeline_panel(axis)
    old = json.loads(
        json.dumps(panel)
        .replace("Reconstructed Low", "Compliant Low")
        .replace("Reconstructed High", "Compliant High")
        .replace("Reconstructed House Band", "Compliance Band")
    )
    targets = deepcopy(panel["targets"])
    unrelated = {"id": 9876, "type": "timeseries", "title": "Unrelated", "targets": []}
    before_unrelated = deepcopy(unrelated)
    brand.strengthen_compliance_band(unrelated)
    assert unrelated == before_unrelated
    brand.strengthen_compliance_band(old)
    assert old["targets"] == targets  # no query math, route, datasource or refId changes
    assert not brand.check_compliance_dashboard_data("fixture", {"panels": [old]})
    once = deepcopy(old)
    brand.strengthen_compliance_band(old)
    assert old == once
    # The general branding path must retain the same lineage, not revive old aliases.
    brand.normalize_public_panel_schema(old)
    brand.brand_field_config(old, "site-climate")
    assert not brand.check_compliance_dashboard_data("fixture", {"panels": [old]})


def test_lineage_only_check_never_writes_source(monkeypatch):
    before = {path: path.read_bytes() for path in _dashboard_paths()}
    monkeypatch.setattr(sys, "argv", ["brand", "--band-lineage-only", "--check"])
    assert _brand().main() == 0
    assert before == {path: path.read_bytes() for path in _dashboard_paths()}
