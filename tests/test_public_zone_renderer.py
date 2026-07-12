from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from datetime import date
from pathlib import Path

from verdify_public import output_policy as policy


def load_renderer():
    if "asyncpg" not in sys.modules and importlib.util.find_spec("asyncpg") is None:
        asyncpg_stub = types.ModuleType("asyncpg")
        asyncpg_stub.Connection = object
        asyncpg_stub.connect = None
        sys.modules["asyncpg"] = asyncpg_stub
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "render-zone-pages.py"
    spec = importlib.util.spec_from_file_location("public_zone_renderer_under_test", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Connection:
    def __init__(self, excluded: str):
        self.excluded = excluded
        self.fetch_sql = ""

    async def fetchrow(self, _sql, _zone_slug):
        return {
            "zone_id": 1,
            "greenhouse_id": "vallery",
            "zone_name": "South",
            "zone_status": "active",
            "orientation": "south",
            "sensor_modbus_addr": None,
            "peak_temp_f": None,
            "shelves": [],
            "sensors": [],
            "equipment": [],
            "water_systems": [],
            "active_crops_fk_count": 3,
        }

    async def fetch(self, sql, _zone_id, excluded_slugs, excluded_name_pattern):
        self.fetch_sql = sql
        self.fetch_args = (_zone_id, excluded_slugs, excluded_name_pattern)
        base = {
            "position_label": "SOUTH-FLOOR",
            "crop_variety": None,
            "crop_stage": "vegetative",
            "crop_planted_date": date(2026, 6, 10),
            "crop_days_in_place": 31,
            "is_occupied": True,
        }
        return [
            {**base, "crop_name": "Canna lily", "crop_catalog_slug": "canna"},
            {**base, "crop_name": self.excluded.title(), "crop_catalog_slug": self.excluded},
            {**base, "crop_name": "unknown", "crop_catalog_slug": None},
        ]


def test_zone_count_and_planting_table_use_same_fail_closed_records():
    renderer = load_renderer()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    conn = Connection(excluded)

    _filename, content, blocks = asyncio.run(renderer.render_zone(conn, "south"))

    assert excluded not in content.casefold()
    assert "Canna lily" in blocks["current-plantings"]
    assert "<strong>1</strong><p>Public active crop records." in blocks["zone-profile"]
    assert "FROM crops c" in conn.fetch_sql
    assert "COALESCE(c.zone_id, sh.zone_id, legacy_zone.id) = $1" in conn.fetch_sql
    assert "LEFT JOIN positions p" in conn.fetch_sql
    assert "lower(btrim(legacy_zone.slug)) = lower(btrim(c.zone))" in conn.fetch_sql
    assert "v_position_current" not in conn.fetch_sql
    assert "America/Denver" in conn.fetch_sql
    assert "CURRENT_DATE" not in conn.fetch_sql
    assert conn.fetch_args == (1, sorted(policy.PUBLIC_CROP_EXCLUDE_SLUGS), policy.PUBLIC_CROP_SQL_NAME_PATTERN)
