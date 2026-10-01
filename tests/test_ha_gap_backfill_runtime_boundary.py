"""HA energy compatibility preserves the exact runtime role/projection boundary."""

import asyncio
import runpy
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest


def _ha_gap_backfill_globals():
    return runpy.run_path("deploy/k8s/components/ha-gap-backfill/backfill-ha-gaps.py")


_ENERGY_FIELDS = {
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
}


def _energy_projection_connection(
    columns, schema="verdify_ha_backfill_runtime", role="verdify_ha_backfill_runtime_login"
):
    from unittest.mock import AsyncMock

    async def fetch(query, *args):
        if "information_schema.columns" in query:
            assert "table_schema = current_schema()" in query
            assert args == ("energy",)
            return [{"column_name": column} for column in columns]
        return []

    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=fetch)
    conn.fetchrow = AsyncMock(return_value={"schema": schema, "role": role})
    conn.executemany = AsyncMock()
    return conn


@pytest.mark.parametrize("private", [True, False])
def test_ha_energy_projection_all_three_query_paths(private):
    from types import SimpleNamespace

    module = _ha_gap_backfill_globals()
    conn = _energy_projection_connection(_ENERGY_FIELDS if private else _ENERGY_FIELDS | {"greenhouse_id"})
    start = datetime(2026, 10, 1, tzinfo=UTC)

    async def exercise():
        await module["detect_sample_windows"](conn, "energy", start, start + timedelta(minutes=10), 60, 120)
        await module["existing_buckets"](conn, "energy", start, start, 60)
        mappings = module["MappingSet"]([], [], [], [module["ScalarMapping"]("power", "ch0_power_w")], {}, {})
        count = await module["backfill_energy"](
            conn,
            module["Window"](start, start, ("energy",)),
            {"power": module["HAHistory"]([(start, "12")])},
            mappings,
            SimpleNamespace(energy_sample_seconds=60, apply=True),
        )
        assert count == 1

    asyncio.run(exercise())
    buckets = [c for c in conn.fetch.call_args_list if "SELECT DISTINCT" in c.args[0]]
    assert len(buckets) == 3
    for c in buckets:
        assert ("greenhouse_id = $4" in c.args[0]) is not private
        assert len(c.args[1:]) == (3 if private else 4)
        assert ("FROM verdify_ha_backfill_runtime.energy" in c.args[0]) is private
    sql, values = conn.executemany.call_args.args
    assert ("greenhouse_id" in sql) is not private
    assert sql.startswith("INSERT INTO verdify_ha_backfill_runtime.energy" if private else "INSERT INTO energy")
    assert len(values[0]) == (6 if private else 7)


@pytest.mark.parametrize(
    "schema,role,columns",
    [
        ("public", "verdify_ha_backfill_runtime_login", _ENERGY_FIELDS),
        ("verdify_vision_runtime", "verdify_ha_backfill_runtime_login", _ENERGY_FIELDS),
        ("verdify_ha_backfill_runtime", "verdify", _ENERGY_FIELDS),
        ("verdify_ha_backfill_runtime", "verdify_ha_backfill_runtime_login", _ENERGY_FIELDS - {"ts"}),
        ("verdify_ha_backfill_runtime", "verdify_ha_backfill_runtime_login", _ENERGY_FIELDS - {"ch0_quality"}),
        ("verdify_ha_backfill_runtime", "verdify_ha_backfill_runtime_login", _ENERGY_FIELDS | {"unreviewed"}),
    ],
)
def test_ha_energy_projection_refuses_unrecognized_schema_role_or_fields(schema, role, columns):
    module = _ha_gap_backfill_globals()
    conn = _energy_projection_connection(columns, schema, role)
    with pytest.raises(RuntimeError, match="energy projection"):
        asyncio.run(module["energy_relation"](conn))
    conn.executemany.assert_not_called()


def test_ha_energy_projection_refuses_other_house_and_keeps_other_table_filter(monkeypatch):
    monkeypatch.setenv("GREENHOUSE_ID", "another-house")
    module = _ha_gap_backfill_globals()
    conn = _energy_projection_connection(_ENERGY_FIELDS)
    with pytest.raises(RuntimeError, match="unrecognized"):
        asyncio.run(module["energy_relation"](conn))
    start = datetime(2026, 10, 1, tzinfo=UTC)
    asyncio.run(module["existing_buckets"](conn, "climate", start, start, 60))
    query = conn.fetch.call_args.args
    assert "FROM climate" in query[0] and "greenhouse_id = $4" in query[0]
    assert query[-1] == "another-house"
