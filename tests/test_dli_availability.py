import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/195-dli-availability-provenance.sql"


def _dashboard_panels():
    for path in sorted((ROOT / "grafana").rglob("*.json")):
        data = json.loads(path.read_text())
        stack = list(data.get("panels", []))
        while stack:
            panel = stack.pop()
            stack.extend(panel.get("panels", []))
            yield path, panel


def test_migration_preserves_raw_proxy_and_creates_product_contract():
    sql = MIGRATION.read_text()
    assert "CREATE TABLE IF NOT EXISTS public.dli_validity_intervals" in sql
    assert "CREATE OR REPLACE VIEW public.v_dli_forensic_history" in sql
    assert "CREATE OR REPLACE VIEW public.v_dli_current" in sql
    assert "CREATE OR REPLACE VIEW public.v_dli_daily" in sql
    assert "forensic_proxy_dli_mol_m2_day" in sql
    assert "validity_interval_does_not_cover_full_local_day" in sql
    assert "COMMENT ON COLUMN public.climate.dli_today" in sql
    assert "COMMENT ON COLUMN public.daily_summary.dli_final" in sql
    assert "UPDATE public.climate" not in sql
    assert "UPDATE public.daily_summary" not in sql
    assert "DELETE FROM public.climate" not in sql
    assert "DELETE FROM public.daily_summary" not in sql


def test_product_views_do_not_promote_legacy_numeric_proxy():
    sql = MIGRATION.read_text()
    estimated = sql.split("CREATE OR REPLACE VIEW public.v_estimated_plant_dli", 1)[1].split(
        "CREATE OR REPLACE VIEW public.v_lighting_daily", 1
    )[0]
    assert "NULL::numeric AS sensor_dli" in estimated
    assert "NULL::numeric AS corrected_solar_dli" in estimated
    assert "NULL::numeric AS estimated_plant_dli" in estimated
    assert "'UNAVAILABLE'::text AS dli_status" in estimated

    daily_kpi = sql.split("CREATE OR REPLACE VIEW public.v_daily_kpi", 1)[1].split(
        "CREATE OR REPLACE VIEW public.v_iris_planning_context", 1
    )[0]
    assert "round(crop_dli_mol_m2_day::numeric, 1) AS dli" in daily_kpi
    assert "round(dli_final::numeric, 1) AS dli" not in daily_kpi
    for field in (
        "dli_availability",
        "dli_unavailable_reason",
        "dli_provenance",
        "dli_validity_revision",
        "dli_valid_from",
        "dli_valid_to",
    ):
        assert field in daily_kpi


def test_planner_context_contains_only_availability_bearing_dli():
    gather = (ROOT / "scripts/gather-plan-context.sh").read_text()
    assert "FROM v_dli_current" in gather
    assert "interior_light_sensor_broken" in gather
    assert "No correction factor or outdoor proxy" in gather
    for forbidden in (
        "max(dli_today)",
        "estimated_actual_dli",
        "estimated_total_plant_dli",
        "s*3.5",
        "Sensor DLI of 5-7",
    ):
        assert forbidden not in gather

    migration = MIGRATION.read_text()
    planner_view = migration.split("CREATE OR REPLACE VIEW public.v_iris_planning_context", 1)[1].split(
        "CREATE OR REPLACE VIEW public.v_water_efficiency", 1
    )[0]
    assert "'availability', 'unavailable'" in planner_view
    assert "outdoor_forecast_is_not_interior_crop_dli" in planner_view
    assert "max(c.dli_today)" not in planner_view
    assert "daily_summary.dli_final" not in planner_view

    planner = (ROOT / "ingestor/iris_planner.py").read_text()
    assert "Interior crop DLI is\n  explicitly unavailable" in planner
    assert "do not infer it" in planner
    assert "DLI-independent photoperiod/runtime lever" in planner


def test_mcp_and_api_use_typed_unavailable_contract():
    mcp = (ROOT / "mcp/server.py").read_text()
    outcome = mcp.split("async def outcome_kpi", 1)[1].split("async def equipment_state", 1)[0]
    assert "FROM v_dli_daily" in outcome
    assert 'dli="unavailable"' in outcome
    assert "dli=dli_evidence" in outcome
    assert 'dli={"sensor_mol_m2_d"' not in outcome
    assert "dli_final" not in outcome

    api = (ROOT / "api/main.py").read_text()
    endpoint = api.split("async def get_dli_evidence", 1)[1].split("async def daily_resource_accounting", 1)[0]
    assert "response_model=DliEvidence" in api
    assert "FROM v_dli_current" in endpoint
    assert "DliEvidence.model_validate" in endpoint
    assert "dli_today" not in endpoint


def test_firmware_publishes_unavailable_but_accumulates_real_elapsed_time():
    controls = (ROOT / "firmware/greenhouse/controls.yaml").read_text()
    dli_block = controls.split("/*** B5 — forensic DLI proxy accumulation", 1)[1].split("/*** B6", 1)[0]
    assert "dli_dt_s = dt_ms > 0 ? (float)dt_ms / 1000.0f" in dli_block
    assert "5.0f" not in dli_block

    sensors = (ROOT / "firmware/greenhouse/sensors.yaml").read_text()
    assert "interior_dli_evidence(id(dli_accumulator)).value_mol_m2_day" in sensors
    for entity in (
        "dli_availability",
        "dli_unavailable_reason",
        "dli_provenance",
        "dli_validity_revision",
        "dli_valid_from",
        "dli_valid_to",
    ):
        assert f"id: {entity}" in sensors

    assert (ROOT / "firmware/lib/greenhouse_logic.h").read_bytes() == (
        ROOT / "deploy/k8s/components/firmware-twin/src/greenhouse_logic.h"
    ).read_bytes()
    assert (ROOT / "firmware/lib/greenhouse_types.h").read_bytes() == (
        ROOT / "deploy/k8s/components/firmware-twin/src/greenhouse_types.h"
    ).read_bytes()


def test_all_dli_dashboard_queries_report_unavailable_without_proxy_laundering():
    found = 0
    forbidden = (
        "max(dli_today)",
        "dli_today as",
        "dli_final as",
        "estimated_plant_dli as",
        "sensor_dli as",
        "* 3.2",
        "* 3.5",
    )
    for path, panel in _dashboard_panels():
        title = str(panel.get("title", ""))
        if "dli" not in title.lower():
            continue
        found += 1
        assert "unavailable" in (title + " " + str(panel.get("description", ""))).lower(), path
        queries = "\n".join(
            str(target.get("rawSql", "")) for target in panel.get("targets", []) if isinstance(target, dict)
        ).lower()
        assert "v_dli_current" in queries or "v_dli_daily" in queries, (path, title)
        assert "unavailable_reason" in queries, (path, title)
        for token in forbidden:
            assert token not in queries, (path, title, token)
    assert found >= 10


def test_generated_site_and_public_exports_preserve_keys_without_numeric_dli():
    daily_plan = (ROOT / "scripts/generate-daily-plan.py").read_text()
    assert "d.crop_dli_mol_m2_day AS dli_final" in daily_plan
    assert '"dli_availability"' in daily_plan
    assert "### Interior Light Evidence" in daily_plan
    assert "FROM daily_summary ds\n            LEFT JOIN v_dli_daily d" in daily_plan

    vault = (ROOT / "scripts/vault-daily-writer.py").read_text()
    assert 'dli=_round(row.get("dli_product_value"))' in vault
    assert 'frontmatter["dli"] = None' in vault
    assert "Interior crop DLI:** unavailable" in vault
    assert "LEFT JOIN v_dli_daily d" in vault

    public_sample = (ROOT / "scripts/export-public-sample-dataset.sh").read_text()
    assert "NULL::numeric AS dli_today_mol_m2" in public_sample
    assert "interior_light_sensor_broken" in public_sample
    assert "physical interior" in public_sample
    assert "legacy_invalid_exterior_proxy_plus_fixture_estimate" in public_sample
    assert "round(max(dli_today)::numeric" not in public_sample

    lifecycle = (ROOT / "scripts/export-daily-lifecycle-artifact.py").read_text()
    assert "NULL::numeric AS dli_today_mol_m2" in lifecycle
    assert "Reason=interior_light_sensor_broken" in lifecycle
    assert "round(max(dli_today)::numeric" not in lifecycle
