"""Bind dashboard rendering to ordinary-role live rollback query receipts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "grafana/dashboards/site-evidence-planning-quality.json"
RECEIPT = ROOT / "tests/fixtures/native-route-grafana-query-receipt.json"


def test_dashboard_exact_queries_have_historical_measurements_and_current_missingness_receipts():
    dashboard = json.loads(DASHBOARD.read_text())
    receipt = json.loads(RECEIPT.read_text())
    panels = {str(p["id"]): p for p in dashboard["panels"] if p["id"] in (21, 22)}
    assert {
        key: hashlib.sha256(p["targets"][0]["rawSql"].encode()).hexdigest() for key, p in panels.items()
    } == receipt["query_sha256"]
    migration = ROOT / "db/migrations/273-native-route-measurement-reader.sql"
    assert hashlib.sha256(migration.read_bytes()).hexdigest() == receipt["migration_sha256"]
    assert receipt["effective_role"] == "verdify_grafana_runtime_login"
    assert receipt["rollback_readback"] == {"ledger": 270, "new_function": None}
    samples = {(r["day"], r["panel"]): r["rows"] for r in receipt["samples"]}
    temp, vpd, joint = samples["2026-09-29", 21]
    assert temp["Eligible bins"] == temp["Expected bins"] == 72 and temp["In-band bins"] == 47
    assert temp["High misses"] == 25 and temp["Low misses"] == 0
    assert abs(temp["Mean outside distance"] - 0.5744447) < 1e-7
    assert temp["Distance unit"] == "°F" and temp["Worst measured route"] == "north"
    assert vpd["In band %"] == 100 and vpd["Distance unit"] == "kPa"
    assert joint["Eligible bins"] == 72 and joint["In-band bins"] == 47
    for row in samples["2026-10-05", 21]:
        assert row["In band %"] is None and row["Eligible bins"] is None
        assert row["Mean outside distance"] is None
        assert row["Unavailable bins / reason"] == "missing_or_ambiguous_prospective_target"
    for sample in ("2026-09-29", "2026-10-05"):
        assert all(r["Physical crop outcome"] == "unqualified" for r in samples[sample, 21])
        lineage = {r["Definition / lineage"]: r["Value"] for r in samples[sample, 22]}
        assert lineage["Experiment endpoint / causal proof"] == "false"
        assert lineage["Physical qualification"].startswith("UNQUALIFIED:")
    for panel in panels.values():
        assert panel["fieldConfig"]["defaults"]["noValue"] == "Unavailable"
        assert "physical" in panel["description"] and "nulls are never zero" in panel["description"]


def test_decision_panels_do_not_overlap_and_use_the_dedicated_product_datasource():
    panels = json.loads(DASHBOARD.read_text())["panels"]
    for i, a in enumerate(panels):
        assert a["datasource"]["uid"] == "verdify-tsdb"
        x = a["gridPos"]
        for b in panels[i + 1 :]:
            y = b["gridPos"]
            assert (
                x["x"] + x["w"] <= y["x"]
                or y["x"] + y["w"] <= x["x"]
                or x["y"] + x["h"] <= y["y"]
                or y["y"] + y["h"] <= x["y"]
            ), f"overlapping panels {a['id']}/{b['id']}"


def test_humidity_missing_projection_cannot_hide_measured_temperature_or_turn_missingness_into_zero_success():
    rows = json.loads(RECEIPT.read_text())["missing_humidity_fixture"]["rows"]
    temp, vpd, joint = rows
    assert temp["In-band bins"] == 47 and temp["Eligible bins"] == 72
    assert temp["Mean outside distance"] > 0
    for axis in (vpd, joint):
        assert axis["Eligible bins"] == 0
        assert axis["In band %"] is None and axis["Mean outside distance"] is None
        assert axis["Physical crop outcome"] == "unqualified"
    assert json.loads(vpd["Unavailable bins / reason"]) == {"fewer_than_12_fresh_six_field_minutes": 72}


def test_reader_access_is_confined_to_api_mcp_and_grafana_without_raw_ledger_access():
    permissions = json.loads(RECEIPT.read_text())["permissions"]
    assert {login for login, allowed in permissions["native_reader_capabilities"].items() if allowed} == {
        "verdify_api_runtime_login",
        "verdify_mcp_runtime_login",
        "verdify_grafana_runtime_login",
    }
    assert permissions["raw_native_table_granted"] is False
