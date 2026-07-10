from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _vault_root() -> Path:
    candidates = [
        os.environ.get("VERDIFY_SITE_VAULT"),
        "/mnt/iris/verdify-vault/website",
        str(Path.home() / "Iris/verdify-vault/website"),
        "/Users/jason/Iris/verdify-vault/website",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return Path("/mnt/iris/verdify-vault/website")


VAULT_ROOT = _vault_root()


def _dashboard(path: str) -> dict:
    return json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))


def _site_dashboard_paths() -> list[Path]:
    return sorted((REPO_ROOT / "grafana" / "dashboards").glob("site-*.json"))


def _panel(dashboard: dict, panel_id: int) -> dict:
    for panel in dashboard["panels"]:
        if panel.get("id") == panel_id:
            return panel
    raise AssertionError(f"panel {panel_id} not found")


def _override_props(panel: dict, label: str) -> dict:
    for override in panel.get("fieldConfig", {}).get("overrides", []):
        matcher = override.get("matcher", {})
        if matcher.get("id") == "byName" and matcher.get("options") == label:
            return {prop.get("id"): prop.get("value") for prop in override.get("properties", [])}
    return {}


def _mapped_color(panel: dict, label: str, value: str) -> str | None:
    mappings = _override_props(panel, label).get("mappings")
    if not isinstance(mappings, list):
        return None
    for mapping in mappings:
        options = mapping.get("options") if isinstance(mapping, dict) else None
        if not isinstance(options, dict):
            continue
        option = options.get(value)
        if isinstance(option, dict):
            return option.get("color")
    return None


def test_overview_nav_promotes_greenhouse_evidence_pages():
    nav = (REPO_ROOT / "site/quartz/components/SiteNav.tsx").read_text(encoding="utf-8")
    overview_start = nav.index('title: "Overview"')
    live_start = nav.index('title: "Live Evidence"')
    overview = nav[overview_start:live_start]
    greenhouse_start = nav.index('title: "Greenhouse"')
    reference_start = nav.index('title: "Reference"')
    greenhouse = nav[greenhouse_start:reference_start]

    assert 'pageLink("Lighting", "greenhouse/lighting")' in overview
    assert 'pageLink("Hydroponics", "greenhouse/hydroponics")' in overview
    assert 'pageLink("Soil Sensors", "greenhouse/soil")' in overview
    assert 'pageLink("Lighting", "greenhouse/lighting")' not in greenhouse
    assert 'pageLink("Hydroponics", "greenhouse/hydroponics")' not in greenhouse
    assert 'pageLink("Soil Sensors", "greenhouse/soil")' not in greenhouse


def test_homepage_core_graphs_share_window_and_embed_scale():
    homepage = (VAULT_ROOT / "index.md").read_text(encoding="utf-8")

    for panel_id in (30, 31, 36):
        match = re.search(
            rf'<iframe[^>]+panelId={panel_id}[^>]+from=now-72h&to=now%2B72h[^>]+width="100%"[^>]+height="620"',
            homepage,
        )
        assert match, f"homepage panel {panel_id} does not use the shared 72h/620px embed scale"


def test_homepage_resource_graphs_follow_lighting_before_cameras():
    homepage = (VAULT_ROOT / "index.md").read_text(encoding="utf-8")

    lighting_index = homepage.index("panelId=36&theme=light&from=now-72h&to=now%2B72h")
    electric_index = homepage.index("panelId=310&theme=light&from=now-72h&to=now")
    gas_index = homepage.index("panelId=127&theme=light&from=now-72h&to=now")
    water_index = homepage.index("panelId=128&theme=light&from=now-72h&to=now")
    cost_index = homepage.index("panelId=312&theme=light&from=now-30d&to=now")
    cameras_index = homepage.index("## Live Greenhouse Cameras")

    assert lighting_index < electric_index < gas_index < water_index < cost_index < cameras_index
    assert '<div class="media-grid media-grid-3 home-resource-panel-row">' in homepage
    assert "site-evidence-economics/?orgId=1&panelId=310" in homepage
    assert "site-home/?orgId=1&panelId=127" in homepage
    assert "site-home/?orgId=1&panelId=128" in homepage
    assert "site-evidence-economics/?orgId=1&panelId=312" in homepage
    assert "diurnal pressure" in homepage
    assert "mitigating that sun-driven pressure" in homepage
    assert homepage.count('style="--home-panel-height: 300px;"') == 3
    assert 'style="--home-panel-height: 320px;"' in homepage


def test_homepage_lighting_state_is_on_only_policy_placed_fill():
    home = _dashboard("grafana/dashboards/site-home.json")
    lighting = _panel(home, 36)
    solar_sql = next(target["rawSql"] for target in lighting["targets"] if target.get("refId") == "A")
    state_sql = next(target["rawSql"] for target in lighting["targets"] if target.get("refId") == "B")

    assert lighting["title"] == "Lighting: Lux, Thresholds & Switch State"
    assert "Solar Forecast" in solar_sql
    assert "Threshold" not in solar_sql
    assert "fn_lighting_circuit_policy" not in solar_sql
    assert "setpoint_snapshot" in state_sql
    assert "setpoint_changes" in state_sql
    assert "gl_main_lux_threshold" in state_sql
    assert "gl_grow_lux_threshold" in state_sql
    assert ") THEN m.value_when_on ELSE NULL::double precision END AS value" in state_sql
    assert "Base" not in state_sql
    assert "fillBelowTo" not in _override_props(lighting, "Main Light On")
    assert "fillBelowTo" not in _override_props(lighting, "Grow Light On")
    for label in ("Main Light Threshold Base", "Grow Light Threshold Base"):
        assert not _override_props(lighting, label)
    for label in ("Main Light On", "Grow Light On"):
        props = _override_props(lighting, label)
        assert props["custom.lineWidth"] == 0
        assert props["custom.fillOpacity"] == 90
        assert props["custom.gradientMode"] == "opacity"
        assert props["custom.spanNulls"] is False


def test_resource_use_restores_individual_solar_alignment_panels():
    page = (VAULT_ROOT / "start/resource-use.md").read_text(encoding="utf-8")

    assert "panelId=310" in page, "electric-vs-solar panel missing"
    assert "panelId=127" in page, "gas-vs-solar panel missing"
    assert "panelId=128" in page, "water-vs-solar panel missing"
    assert "panelId=310&theme=light&from=now-7d&to=now" in page
    assert "panelId=127&theme=light&from=now-7d&to=now" in page
    assert "panelId=128&theme=light&from=now-7d&to=now" in page
    assert "Solar vs Resource Use" not in page


def test_resource_use_cost_panels_use_canonical_runtime_cost_fields():
    economics = _dashboard("grafana/dashboards/site-evidence-economics.json")
    baseline_page = (VAULT_ROOT / "data/baseline-vs-iris.md").read_text(encoding="utf-8")
    daily_cost = _panel(economics, 312)
    monthly_cost = _panel(economics, 10)
    solar_load = _panel(economics, 310)
    daily_sql = daily_cost["targets"][0]["rawSql"]

    assert "Runtime Electric ($)" in daily_sql
    assert "cost_electric::numeric" in daily_sql
    assert "kwh_estimated * 0.111" not in daily_sql
    assert "fn_runtime_power_30m" in solar_load["targets"][0]["rawSql"]
    assert "fn_equip_at" not in solar_load["targets"][0]["rawSql"]
    assert "Runtime Load (W)" in solar_load["targets"][0]["rawSql"]
    assert "energy e" not in solar_load["targets"][0]["rawSql"]
    assert monthly_cost["options"]["showValue"] == "never"
    assert "Runtime-modeled electric energy/day" in baseline_page
    assert "Metered electric energy/day" not in baseline_page


def test_lighting_dashboard_visual_contract():
    lighting = _dashboard("grafana/provisioning/dashboards/json/lighting.json")
    lux_panel = _panel(lighting, 9)
    altitude_panel = _panel(lighting, 14)
    decision_panel = _panel(lighting, 16)
    lighting_page = (VAULT_ROOT / "greenhouse/lighting.md").read_text(encoding="utf-8")

    assert _override_props(lux_panel, "Indoor Lux")["custom.fillOpacity"] == 85
    assert _override_props(lux_panel, "Outdoor Lux")["custom.fillOpacity"] == 55
    altitude = _override_props(lux_panel, "Sun Altitude")
    assert altitude["custom.fillOpacity"] == 0
    assert altitude["custom.lineWidth"] == 2
    assert altitude["custom.lineStyle"] == {"fill": "dash", "dash": [6, 4]}
    assert _override_props(altitude_panel, "Sun Altitude")["custom.fillOpacity"] == 0
    assert _mapped_color(decision_panel, "Occupancy", "occupied") == "#2196F3"
    assert _mapped_color(decision_panel, "Occupancy", "empty") == "rgba(253,216,53,0.30)"
    assert _mapped_color(decision_panel, "Sun", "Day") == "#FDD835"
    assert _mapped_color(decision_panel, "Sun", "Night") == "#112231"
    assert "panelId=10&theme=light&from=now-30d&to=now" in lighting_page


def test_site_grafana_panels_use_transparent_chrome():
    for path in _site_dashboard_paths():
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        for panel in dashboard.get("panels", []):
            if panel.get("type") != "row":
                assert panel.get("transparent") is True, f"{path.name} panel {panel.get('id')} is not transparent"


def test_site_grafana_stat_panels_do_not_use_background_color_mode():
    for path in _site_dashboard_paths():
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        for panel in dashboard.get("panels", []):
            if panel.get("type") == "stat":
                options = panel.get("options", {})
                assert options.get("colorMode") == "value", f"{path.name} panel {panel.get('id')} uses {options}"
                assert options.get("graphMode") in {None, "none"}, f"{path.name} panel {panel.get('id')} uses {options}"


def test_site_grafana_k3s_configmaps_match_source_dashboards():
    source_roots = [
        REPO_ROOT / "grafana" / "dashboards",
        REPO_ROOT / "grafana" / "provisioning" / "dashboards" / "json",
    ]
    generated = sorted(
        (REPO_ROOT / "deploy" / "k8s" / "components" / "grafana" / "generated").glob("dashboards-cm-*.yaml")
    )
    assert generated

    for cm_path in generated:
        doc = yaml.safe_load(cm_path.read_text(encoding="utf-8"))
        for name, raw in (doc.get("data") or {}).items():
            if not name.endswith(".json"):
                continue
            source = next((root / name for root in source_roots if (root / name).exists()), None)
            assert source is not None, f"{cm_path.name} data key {name} has no source dashboard"
            assert json.loads(raw) == json.loads(source.read_text(encoding="utf-8"))


def test_resource_dashboards_label_scope_quality_and_no_legacy_catalog():
    climate = _dashboard("grafana/dashboards/site-climate.json")
    water = _dashboard("grafana/dashboards/site-climate-water.json")
    equipment = _dashboard("grafana/dashboards/site-greenhouse-equipment.json")

    climate_text = json.dumps(climate)
    water_text = json.dumps(water)
    equipment_text = json.dumps(equipment)

    assert "equipment_assets" not in climate_text
    assert "Runtime-Modeled" in climate_text
    assert "coefficient_revision" in climate_text
    assert "Modeled kWh low" in climate_text
    assert "partial Shelly" in climate_text
    assert "v_daily_kpi" in climate_text
    assert "resource_terms_available" in climate_text
    assert "AVG(cost_total)::numeric, 2) AS v FROM daily_summary" not in climate_text
    assert "AVG(cost_total)::numeric, 2) AS v FROM v_daily_kpi" in climate_text

    assert "v_water_attribution_daily" in water_text
    assert "Meter-Conserving Water Attribution" in water_text
    assert "Ambiguous overlap" in water_text
    assert "Manual / unattributed" in water_text
    assert "command-only relay runs are never gallons" in water_text
    assert "COALESCE(SUM(quality_filtered_meter_gal), 0)" not in water_text
    assert water_text.count("available_for_scoring") >= 4

    assert "Wet-relay runtime is never delivered gallons" in equipment_text
    assert "partial Shelly measurement" in equipment_text


def test_quartz_dark_mode_contract_is_user_theme_driven():
    config = (REPO_ROOT / "site/quartz.config.ts").read_text(encoding="utf-8")
    head = (REPO_ROOT / "site/quartz/components/Head.tsx").read_text(encoding="utf-8")
    darkmode = (REPO_ROOT / "site/quartz/components/scripts/darkmode.inline.ts").read_text(encoding="utf-8")
    embeds = (REPO_ROOT / "site/quartz/components/GrafanaEmbeds.tsx").read_text(encoding="utf-8")

    assert 'light: "#071512"' in config
    assert "__verdifyLightThemeOnly" not in head
    assert 'localStorage.getItem("theme")' in darkmode
    assert "prefers-color-scheme: dark" in darkmode
    assert 'new CustomEvent("themechange"' in darkmode
    assert "function themedUrl" in embeds
    assert "themechange" in embeds


def test_architecture_page_removes_stale_sections_and_svg_return_path_is_behind_ingestor():
    architecture = (VAULT_ROOT / "reference/architecture.md").read_text(encoding="utf-8")
    svg = (VAULT_ROOT / "static/verdify-architecture.svg").read_text(encoding="utf-8")

    for stale in ("Homelab Compute", "Agent Fleet", "MQTT", "Not Production Safe"):
        assert stale not in architecture

    return_path_index = svg.index('d="M990 500 C850 585 650 535 280 235"')
    ingestor_label_index = svg.index(">Ingestor<")
    assert return_path_index < ingestor_label_index
