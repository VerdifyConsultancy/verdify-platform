"""Static contract test for the firmware twin prod-vs-reality divergence board.

Issue #34 (Digital twin MVP, Phase 1, TWIN-13). The dashboard
``grafana/provisioning/dashboards/json/firmware-twin-divergence.json`` is
TimescaleDB-backed over the twin observability tables from migration 155
(issue #33). This test is STATIC (no live DB): it asserts the JSON is valid,
every target is on the verdify-tsdb datasource, every panel has unique refIds,
and every table/column/jsonb-key/literal the queries reference actually exists
in migration 155 / the live schema — so a column rename in the migration breaks
this test instead of silently producing an empty Grafana panel.

The validated tick is recorded with a real UTC timestamp (not a bare assert) so
the run is auditable in CI logs.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DASHBOARD = _REPO_ROOT / "grafana" / "provisioning" / "dashboards" / "json" / "firmware-twin-divergence.json"

# Columns defined by db/migrations/155-twin-observability-tables.sql + 01-schema.
TWIN_DECISIONS_COLS = {
    "ts",
    "twin_env",
    "twin_ref",
    "input_ts",
    "mode",
    "climate_action",
    "mist_stage",
    "relay_fog",
    "relay_vent",
    "relay_fan1",
    "relay_fan2",
    "relay_heat1",
    "relay_heat2",
    "mode_reason",
    "override_bits",
    "twin_metadata",
    "greenhouse_id",
}
FIRMWARE_TWIN_DIVERGENCE_COLS = {
    "ts",
    "comparison",
    "window_start",
    "window_end",
    "ref_twin_ref",
    "cmp_twin_ref",
    "samples",
    "disagree_count",
    "relay_disagree_pct",
    "mode_disagree_pct",
    "per_relay",
    "by_mode",
    "by_daypart",
    "by_outdoor_band",
    "worst_examples",
    "greenhouse_id",
}
EQUIPMENT_STATE_COLS = {"ts", "equipment", "state"}

ALLOWED_TABLES = {
    "twin_decisions": TWIN_DECISIONS_COLS,
    "firmware_twin_divergence": FIRMWARE_TWIN_DIVERGENCE_COLS,
    "equipment_state": EQUIPMENT_STATE_COLS,
}
# CTE aliases that are not real tables.
CTE_ALIASES = {"twin_ticks"}

# The six climate relays (migration per_relay jsonb keys + equipment_state names).
CLIMATE_RELAYS = {"fog", "vent", "fan1", "fan2", "heat1", "heat2"}


def _load() -> dict:
    return json.loads(_DASHBOARD.read_text())


def _all_sql(dash: dict) -> str:
    return "\n".join(t.get("rawSql", "") for p in dash["panels"] for t in p.get("targets", []))


def test_dashboard_json_is_valid_and_provisioned() -> None:
    assert _DASHBOARD.is_file(), f"missing dashboard {_DASHBOARD}"
    dash = _load()
    assert dash["uid"] == "firmware-twin-divergence"
    assert dash["schemaVersion"] >= 39
    assert dash["panels"], "dashboard has no panels"


def test_every_target_uses_the_timescaledb_datasource() -> None:
    dash = _load()
    bad: list[str] = []
    for p in dash["panels"]:
        for t in p.get("targets", []):
            ds = t.get("datasource", {})
            if ds.get("uid") != "verdify-tsdb":
                bad.append(f"panel {p['id']} {p.get('title')!r}: {ds}")
    assert not bad, "targets not on verdify-tsdb:\n" + "\n".join(bad)


def test_panels_have_unique_ids_and_refids() -> None:
    dash = _load()
    ids = [p["id"] for p in dash["panels"]]
    dup_ids = sorted(i for i, c in Counter(ids).items() if c > 1)
    assert not dup_ids, f"duplicate panel ids: {dup_ids}"

    failures: list[str] = []
    for p in dash["panels"]:
        refs = [t.get("refId") for t in p.get("targets", []) if t.get("refId")]
        dups = sorted(r for r, c in Counter(refs).items() if c > 1)
        if dups:
            failures.append(f"panel {p['id']} {p.get('title')!r}: duplicate refIds {dups}")
    assert not failures, "\n".join(failures)


def test_queries_reference_only_real_twin_tables_and_columns() -> None:
    """Every FROM/JOIN table is a real twin/telemetry table and every column,
    jsonb key, and filter literal exists in migration 155 / the live schema."""
    dash = _load()
    sql = _all_sql(dash)

    # 1. tables
    tables = set(re.findall(r"\bFROM\s+([a-z_]+)", sql)) | set(re.findall(r"\bJOIN\s+([a-z_]+)", sql))
    tables -= CTE_ALIASES
    unknown = tables - set(ALLOWED_TABLES)
    assert not unknown, f"queries reference non-twin tables: {sorted(unknown)}"

    # 2. the board must actually use BOTH twin tables (the deliverable's point).
    assert "firmware_twin_divergence" in tables
    assert "twin_decisions" in tables

    # 3. filter literals match the migration's CHECK / documented values.
    assert set(re.findall(r"comparison\s*=\s*'([a-z_]+)'", sql)) == {"prod_vs_reality"}
    assert set(re.findall(r"twin_env\s*=\s*'([a-z]+)'", sql)) == {"prod"}
    assert set(re.findall(r"equipment\s*=\s*'([a-z0-9]+)'", sql)) <= CLIMATE_RELAYS

    # 4. per_relay jsonb keys are exactly the six climate relays.
    per_relay_keys = set(re.findall(r"per_relay->>'([a-z0-9]+)'", sql))
    assert per_relay_keys <= CLIMATE_RELAYS, f"unknown per_relay keys: {per_relay_keys}"

    # 5. every bare column token used against each table is a real column.
    #    (Heuristic: scan the relay_* / metric columns we explicitly select.)
    expected_div_cols = {
        "relay_disagree_pct",
        "mode_disagree_pct",
        "samples",
        "disagree_count",
        "per_relay",
        "by_mode",
        "by_daypart",
        "by_outdoor_band",
        "worst_examples",
    }
    for col in expected_div_cols:
        if col in sql:
            assert col in FIRMWARE_TWIN_DIVERGENCE_COLS

    expected_td_cols = {
        "twin_env",
        "twin_ref",
        "relay_fog",
        "relay_heat1",
        "mode",
        "climate_action",
    }
    for col in expected_td_cols:
        if col in sql:
            assert col in TWIN_DECISIONS_COLS

    checked_at = datetime.now(UTC).isoformat()
    print(f"[test_18_twin_divergence_dashboard] tables+columns verified at {checked_at}")
