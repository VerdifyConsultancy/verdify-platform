from __future__ import annotations

import importlib.util
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_tempest_module():
    spec = importlib.util.spec_from_file_location(
        "tempest_sync_script",
        REPO_ROOT / "scripts" / "tempest-sync.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeConn:
    def __init__(self, latest_esp32: datetime | None):
        self.latest_esp32 = latest_esp32
        self.fetch_queries: list[str] = []
        self.executes: list[tuple[str, tuple[object, ...]]] = []

    async def fetchval(self, query: str, *args: object) -> datetime | None:
        self.fetch_queries.append(query)
        return self.latest_esp32

    async def execute(self, query: str, *args: object) -> str:
        self.executes.append((query, args))
        if query.startswith("UPDATE"):
            return "UPDATE 1"
        return "INSERT 0 1"


@pytest.fixture
def tempest_module(monkeypatch: pytest.MonkeyPatch):
    module = _load_tempest_module()
    monkeypatch.setattr(module, "load_token", lambda: "token")
    monkeypatch.setattr(
        module,
        "fetch_ha_states",
        lambda token, entity_ids: {
            "sensor.panorama_temperature": {"state": "75.2"},
            "sensor.panorama_humidity": {"state": "41.5"},
            "sensor.panorama_illuminance": {"state": "52000"},
        },
    )
    return module


@pytest.mark.asyncio
async def test_tempest_skips_climate_insert_without_recent_indoor_row(tempest_module, caplog):
    conn = _FakeConn(latest_esp32=None)

    with caplog.at_level(logging.WARNING, logger=tempest_module.log.name):
        await tempest_module.sync_tempest(conn)

    queries = [query for query, _args in conn.executes]
    assert any("skipped climate overlay" in record.message for record in caplog.records)
    assert not any(query.startswith("INSERT INTO v_runtime_climate_write") for query in queries)
    assert not any(query.startswith("UPDATE v_runtime_climate_write") for query in queries)
    assert not any(query.startswith(("INSERT INTO climate", "UPDATE climate")) for query in queries)
    assert any(query.startswith("INSERT INTO weather_station") for query in queries)


@pytest.mark.asyncio
async def test_tempest_merges_into_recent_indoor_row(tempest_module):
    latest = datetime(2026, 5, 22, 20, 0, tzinfo=UTC)
    conn = _FakeConn(latest_esp32=latest)

    await tempest_module.sync_tempest(conn)

    queries = [query for query, _args in conn.executes]
    assert any(query.startswith("UPDATE v_runtime_climate_write SET") for query in queries)
    assert not any(query.startswith("INSERT INTO v_runtime_climate_write") for query in queries)
    assert not any(query.startswith(("INSERT INTO climate", "UPDATE climate")) for query in queries)
    assert any(query.startswith("INSERT INTO weather_station") for query in queries)
