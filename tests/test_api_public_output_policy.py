from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
import types
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

from verdify_public import output_policy as policy


def load_api():
    if "asyncpg" not in sys.modules and importlib.util.find_spec("asyncpg") is None:
        asyncpg_stub = types.ModuleType("asyncpg")
        asyncpg_stub.Connection = object
        asyncpg_stub.Pool = object
        asyncpg_stub.Record = dict
        asyncpg_stub.QueryCanceledError = RuntimeError
        asyncpg_stub.exceptions = types.SimpleNamespace(UniqueViolationError=RuntimeError)
        sys.modules["asyncpg"] = asyncpg_stub
    script_path = Path(__file__).resolve().parents[1] / "api" / "main.py"
    spec = importlib.util.spec_from_file_location("verdify_api_public_policy_under_test", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return Acquire(self.connection)


class Connection:
    def __init__(self, *, rows=None, row=None, value=None):
        self.rows = rows or []
        self.row = row
        self.value = value
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.rows

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self.row

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self.value


class ScriptedConnection(Connection):
    def __init__(self, *, fetch_results=None, fetchrow_results=None, fetchval_results=None):
        super().__init__()
        self.fetch_results = list(fetch_results or [])
        self.fetchrow_results = list(fetchrow_results or [])
        self.fetchval_results = list(fetchval_results or [])

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.fetch_results.pop(0)

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self.fetchrow_results.pop(0)

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self.fetchval_results.pop(0)


def public_identity():
    return {"crop_catalog_slug": "canna", "name": "Canna lily"}


def position_row(**overrides):
    row = {
        "position_id": 1,
        "greenhouse_id": "vallery",
        "position_label": "P1",
        "shelf_slug": "floor",
        "shelf_kind": "floor",
        "zone_id": 1,
        "zone_slug": "south",
        "zone_name": "South",
        "crop_id": None,
        "crop_name": None,
        "crop_variety": None,
        "crop_stage": None,
        "crop_planted_date": None,
        "crop_expected_harvest": None,
        "crop_catalog_slug": None,
        "crop_days_in_place": None,
        "is_occupied": False,
    }
    row.update(overrides)
    return row


def history_row(**overrides):
    row = {
        "position_id": 1,
        "greenhouse_id": "vallery",
        "position_label": "P1",
        "zone_slug": "south",
        "crop_id": 7,
        "crop_name": "Canna lily",
        "crop_variety": None,
        "final_stage": "vegetative",
        "planted_date": date(2026, 7, 1),
        "cleared_at": None,
        "is_active": True,
        "days_in_place": 10,
        "crop_catalog_slug": "canna",
        "crop_common_name": "Canna lily",
        "event_count": 1,
        "observation_count": 1,
        "harvest_count": 0,
    }
    row.update(overrides)
    return row


def test_greenhouse_list_and_detail_enforce_minimal_public_projection(monkeypatch):
    api = load_api()
    database_row = {
        "id": "vallery",
        "name": "Verdify Lab",
        "timezone": "America/Denver",
        "status": "active",
        "owner_email": "private@example.invalid",
        "esp32_host": "internal.invalid",
        "esp32_port": 6053,
        "esp32_api_key": "private",
        "mqtt_topic": "private/topic",
        "config": {"private": True},
    }

    list_conn = Connection(rows=[database_row])
    monkeypatch.setattr(api, "pool", Pool(list_conn))
    listed = asyncio.run(api.list_greenhouses())

    assert listed == [{"id": "vallery", "name": "Verdify Lab", "timezone": "America/Denver", "status": "active"}]
    list_sql = list_conn.calls[0][1]
    assert "SELECT *" not in list_sql
    for forbidden in ("owner_email", "esp32_host", "esp32_port", "esp32_api_key", "mqtt_topic", "config"):
        assert forbidden not in list_sql

    detail_conn = Connection(row=database_row, value=2)
    monkeypatch.setattr(api, "pool", Pool(detail_conn))
    detailed = asyncio.run(api.get_greenhouse("vallery"))

    assert detailed == {
        "id": "vallery",
        "name": "Verdify Lab",
        "timezone": "America/Denver",
        "status": "active",
        "active_crops": 2,
    }
    assert "SELECT *" not in detail_conn.calls[0][1]
    assert "c.name IS NOT NULL" in detail_conn.calls[1][1]


def test_greenhouse_detail_uses_generic_404(monkeypatch):
    api = load_api()
    conn = Connection(row=None)
    monkeypatch.setattr(api, "pool", Pool(conn))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.get_greenhouse("private-input"))

    assert exc.value.status_code == 404
    assert exc.value.detail == "Greenhouse not found"


def test_public_resource_errors_never_reflect_supplied_identifiers(monkeypatch):
    api = load_api()
    secret_zone = "private-zone-identifier"
    secret_greenhouse = "private-greenhouse-identifier"
    secret_position = 8675309
    secret_crop = 424242
    cases = [
        (Connection(value=False), lambda: api.get_zone(secret_zone), "Zone not found", (secret_zone,)),
        (
            Connection(row=None),
            lambda: api.get_topology(secret_greenhouse),
            "Greenhouse not found",
            (secret_greenhouse,),
        ),
        (
            Connection(row=None),
            lambda: api.get_zone_full(secret_zone, secret_greenhouse),
            "Zone not found",
            (secret_zone, secret_greenhouse),
        ),
        (
            Connection(row=None),
            lambda: api.get_position(secret_position, secret_greenhouse),
            "Position not found",
            (str(secret_position), secret_greenhouse),
        ),
        (
            ScriptedConnection(fetchrow_results=[public_identity(), None]),
            lambda: api.get_crop_lifecycle(secret_crop, secret_greenhouse),
            "Crop not found",
            (str(secret_crop), secret_greenhouse),
        ),
    ]

    for conn, call, expected_detail, supplied_identifiers in cases:
        monkeypatch.setattr(api, "pool", Pool(conn))
        with pytest.raises(HTTPException) as exc:
            asyncio.run(call())
        assert exc.value.status_code == 404
        assert exc.value.detail == expected_detail
        assert all(identifier not in exc.value.detail for identifier in supplied_identifiers)


def test_write_guard_error_does_not_reflect_request_path(monkeypatch):
    api = load_api()
    supplied_identifier = "private-resource-identifier"
    request = types.SimpleNamespace(url=types.SimpleNamespace(path=f"/api/v1/crops/{supplied_identifier}"))
    monkeypatch.delenv(api.WRITE_API_KEY_ENV, raising=False)
    monkeypatch.delenv(api.ALLOW_UNAUTHENTICATED_WRITES_ENV, raising=False)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.require_write_access(request))

    assert exc.value.status_code == 403
    assert exc.value.detail == "Write API disabled for unauthenticated request"
    assert supplied_identifier not in exc.value.detail


def test_crop_list_uses_catalog_filter_and_fails_closed_for_returned_rows(monkeypatch):
    api = load_api()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    conn = Connection(
        rows=[
            {"id": 1, "name": "Canna lily", "crop_catalog_slug": "canna", "notes": f"near {excluded}"},
            {"id": 2, "name": "missing", "crop_catalog_slug": None},
            {"id": 3, "name": excluded.title(), "crop_catalog_slug": excluded},
        ]
    )
    monkeypatch.setattr(api, "pool", Pool(conn))

    rows = asyncio.run(api.list_crops(active=True, greenhouse_id="vallery", limit=100, offset=0))

    assert [row["id"] for row in rows] == [1]
    assert excluded not in str(rows).casefold()
    _kind, sql, args = conn.calls[0]
    assert "JOIN crop_catalog" in sql
    assert "lower(btrim(cc.slug)) <> ALL($3::text[])" in sql
    assert "c.name IS NOT NULL" in sql
    assert "btrim(c.name) <> ''" in sql
    assert sql.index("c.name IS NOT NULL") < sql.index("LIMIT")
    assert args[2] == sorted(policy.PUBLIC_CROP_EXCLUDE_SLUGS)
    assert args[3] == policy.PUBLIC_CROP_SQL_NAME_PATTERN


def test_direct_catalog_identifier_is_404_before_database_access(monkeypatch):
    api = load_api()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))

    class BombPool:
        def acquire(self):
            raise AssertionError("database must not be queried")

    monkeypatch.setattr(api, "pool", BombPool())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.get_crop_catalog_entry(excluded.upper()))
    assert exc.value.status_code == 404


@pytest.mark.parametrize(
    "call",
    [
        lambda api: api.list_observations(42),
        lambda api: api.list_events(42),
        lambda api: api.crop_health(42),
        lambda api: api.get_crop_lifecycle(42, "vallery"),
    ],
)
def test_child_crop_surfaces_return_404_for_missing_or_nonpublic_identity(monkeypatch, call):
    api = load_api()
    conn = Connection(row=None)
    monkeypatch.setattr(api, "pool", Pool(conn))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(call(api))

    assert exc.value.status_code == 404
    assert len(conn.calls) == 1
    assert "JOIN crop_catalog" in conn.calls[0][1]


def test_child_crop_surfaces_reapply_display_name_policy(monkeypatch):
    api = load_api()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    conn = Connection(row={"crop_catalog_slug": "canna", "name": excluded.title()})
    monkeypatch.setattr(api, "pool", Pool(conn))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.list_observations(42))

    assert exc.value.status_code == 404
    assert len(conn.calls) == 1


@pytest.mark.parametrize(
    "call",
    [
        lambda api: api.list_observations(42),
        lambda api: api.list_events(42),
        lambda api: api.crop_health(42),
    ],
)
def test_child_crop_surfaces_redact_successful_free_text(monkeypatch, call):
    api = load_api()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    conn = ScriptedConnection(
        fetchrow_results=[public_identity()],
        fetch_results=[[{"id": 1, "notes": f"inspect {excluded}", "source": "fixture"}]],
    )
    monkeypatch.setattr(api, "pool", Pool(conn))

    result = asyncio.run(call(api))

    assert excluded not in str(result).casefold()
    identity_sql = conn.calls[0][1]
    assert "cc.slug IS NOT NULL" in identity_sql
    assert "c.name IS NOT NULL" in identity_sql
    assert "btrim(c.name) <> ''" in identity_sql


def test_lifecycle_success_reapplies_policy_and_redacts_nested_events(monkeypatch):
    api = load_api()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    now = datetime(2026, 7, 11, tzinfo=UTC)
    lifecycle = {
        "crop_id": 42,
        "greenhouse_id": "vallery",
        "crop_name": "Canna lily",
        "variety": None,
        "current_stage": "vegetative",
        "is_active": True,
        "planted_date": date(2026, 7, 1),
        "cleared_at": None,
        "days_alive": 10,
        "current_zone_slug": "south",
        "current_position_label": "P1",
        "crop_catalog_slug": "canna",
        "catalog_name": "Canna lily",
        "catalog_category": "ornamental",
        "events": [{"ts": now, "event_type": "note", "notes": f"inspect {excluded}"}],
        "total_weight_kg": 0,
        "total_units": 0,
        "total_revenue_usd": 0,
        "observation_count": 1,
        "avg_health_score": 0.9,
        "latest_observation_ts": now,
    }
    conn = ScriptedConnection(fetchrow_results=[public_identity(), lifecycle])
    monkeypatch.setattr(api, "pool", Pool(conn))

    result = asyncio.run(api.get_crop_lifecycle(42, "vallery"))

    assert excluded not in str(result.model_dump()).casefold()
    assert policy.PUBLIC_CROP_REDACTION in result.events[0].notes


def test_direct_crop_id_is_defensively_rejected_even_if_query_fixture_returns_excluded_row(monkeypatch):
    api = load_api()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    conn = Connection(row={"id": 42, "name": excluded.title(), "crop_catalog_slug": excluded})
    monkeypatch.setattr(api, "pool", Pool(conn))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.get_crop(42))

    assert exc.value.status_code == 404
    assert len(conn.calls) == 1


def test_crop_pagination_filters_null_identity_before_limit(monkeypatch):
    api = load_api()
    source_rows = [
        {"id": 1, "name": None, "crop_catalog_slug": "canna"},
        {"id": 2, "name": "Canna lily", "crop_catalog_slug": "canna"},
        {"id": 3, "name": "Basil", "crop_catalog_slug": "basil"},
    ]

    class PaginationConnection(Connection):
        async def fetch(self, sql, *args):
            self.calls.append(("fetch", sql, args))
            assert sql.index("c.name IS NOT NULL") < sql.index("LIMIT")
            public = [
                row
                for row in source_rows
                if policy.is_public_crop_record(row["crop_catalog_slug"], row["name"], occupied=True)
            ]
            limit, offset = args[-2:]
            return public[offset : offset + limit]

    conn = PaginationConnection()
    monkeypatch.setattr(api, "pool", Pool(conn))

    rows = asyncio.run(api.list_crops(limit=2, offset=0))

    assert [row["id"] for row in rows] == [2, 3]


def test_recent_observations_preserve_crop_less_rows_without_pagination_holes(monkeypatch):
    api = load_api()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    rows = [
        {"id": 1, "crop_id": None, "crop_name": None, "crop_catalog_slug": None, "notes": "house note"},
        {"id": 2, "crop_id": 7, "crop_name": "Canna lily", "crop_catalog_slug": "canna"},
        {"id": 3, "crop_id": 8, "crop_name": excluded.title(), "crop_catalog_slug": excluded},
        {"id": 4, "crop_id": 9, "crop_name": None, "crop_catalog_slug": "canna"},
    ]
    conn = Connection(rows=rows)
    monkeypatch.setattr(api, "pool", Pool(conn))

    result = asyncio.run(api.recent_observations(limit=2))

    assert [row["id"] for row in result] == [1, 2]
    sql = conn.calls[0][1]
    assert "LEFT JOIN crops" in sql
    assert "o.crop_id IS NULL" in sql
    assert sql.index("c.name IS NOT NULL") < sql.index("LIMIT")


def test_status_counts_use_public_predicate_and_preserve_crop_less_observations(monkeypatch):
    api = load_api()
    now = datetime(2026, 7, 11, tzinfo=UTC)
    conn = ScriptedConnection(fetchval_results=[2, 5, now])
    monkeypatch.setattr(api, "pool", Pool(conn))

    result = asyncio.run(api.status())

    assert result["active_crops"] == 2
    assert result["observations"] == 5
    active_sql = conn.calls[0][1]
    observation_sql = conn.calls[1][1]
    assert "crop_name IS NOT NULL" in active_sql
    assert "btrim(crop_name) <> ''" in active_sql
    assert "o.crop_id IS NULL" in observation_sql
    assert "c.name IS NOT NULL" in observation_sql


def test_position_policy_preserves_empty_rows_and_hides_unknown_occupancy():
    api = load_api()
    base = {
        "position_id": 1,
        "position_label": "P1",
        "is_occupied": False,
        "crop_id": None,
        "crop_name": None,
        "crop_catalog_slug": None,
        "crop_variety": "stale variety",
        "crop_future_field": "stale future value",
    }
    empty = api._sanitize_public_position(base)
    assert empty["is_occupied"] is False
    assert empty["crop_variety"] is None
    assert "crop_future_field" not in empty

    unknown = {**base, "is_occupied": True, "crop_id": 7, "crop_name": "unknown"}
    hidden = api._sanitize_public_position(unknown)
    assert hidden["is_occupied"] is False
    assert hidden["crop_id"] is None
    assert hidden["crop_name"] is None

    public = {**base, "is_occupied": True, "crop_id": 8, "crop_name": "Canna lily", "crop_catalog_slug": "canna"}
    assert api._sanitize_public_position(public)["is_occupied"] is True


def test_zone_list_keeps_empty_zones_while_counting_only_public_identity(monkeypatch):
    api = load_api()
    conn = Connection(rows=[{"zone": "south", "active_crops": 0, "current_temp": 75.0}])
    monkeypatch.setattr(api, "pool", Pool(conn))

    rows = asyncio.run(api.list_zones())

    assert rows == [{"zone": "south", "active_crops": 0, "current_temp": 75.0}]
    _kind, sql, args = conn.calls[0]
    assert "FROM zones z" in sql
    assert "JOIN crop_catalog" in sql
    assert "COUNT(c.id) AS active_crops" in sql
    assert "~* $2" in sql
    assert args == (
        sorted(policy.PUBLIC_CROP_EXCLUDE_SLUGS),
        policy.PUBLIC_CROP_SQL_NAME_PATTERN,
        api.DEFAULT_GREENHOUSE,
    )


def test_zone_count_excludes_null_name_before_counting_public_rows(monkeypatch):
    api = load_api()
    source_rows = [
        {"crop_catalog_slug": "canna", "name": None},
        {"crop_catalog_slug": "canna", "name": "Canna lily"},
        {"crop_catalog_slug": "basil", "name": "Basil"},
    ]
    public_count = sum(
        policy.is_public_crop_record(row["crop_catalog_slug"], row["name"], occupied=True) for row in source_rows
    )
    conn = Connection(rows=[{"zone": "south", "active_crops": public_count, "current_temp": 75.0}])
    monkeypatch.setattr(api, "pool", Pool(conn))

    rows = asyncio.run(api.list_zones())

    assert rows[0]["active_crops"] == 2
    sql = conn.calls[0][1]
    assert sql.index("c.name IS NOT NULL") < sql.index(") c ON")


def test_zone_counts_preserve_nullable_zone_fk_with_position_and_legacy_fallback(monkeypatch):
    api = load_api()
    conn = Connection(rows=[{"zone": "south", "active_crops": 1, "current_temp": 75.0}])
    monkeypatch.setattr(api, "pool", Pool(conn))

    rows = asyncio.run(api.list_zones())

    assert rows[0]["active_crops"] == 1
    sql = conn.calls[0][1]
    assert "LEFT JOIN LATERAL" in sql
    assert "COALESCE(c.zone_id, sh.zone_id, legacy_zone.id) = z.id" in sql
    assert "LEFT JOIN positions p" in sql
    assert "lower(btrim(legacy_zone.slug)) = lower(btrim(c.zone))" in sql
    assert "JOIN crop_catalog" in sql
    assert "c.name IS NOT NULL" in sql


def test_zone_detail_filters_nested_crops_and_observations(monkeypatch):
    api = load_api()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))

    class ZoneConnection(Connection):
        def __init__(self):
            super().__init__(value=True)
            self.fetch_results = [
                [
                    {"id": 1, "name": "Canna lily", "crop_catalog_slug": "canna"},
                    {"id": 2, "name": excluded.title(), "crop_catalog_slug": excluded},
                    {"id": 3, "name": "unknown", "crop_catalog_slug": None},
                ],
                [
                    {"id": 4, "crop_id": 1, "crop_name": "Canna lily", "crop_catalog_slug": "canna"},
                    {"id": 5, "crop_id": 2, "crop_name": excluded.title(), "crop_catalog_slug": excluded},
                    {"id": 6, "crop_id": 3, "crop_name": "unknown", "crop_catalog_slug": None},
                ],
            ]

        async def fetch(self, sql, *args):
            self.calls.append(("fetch", sql, args))
            return self.fetch_results.pop(0)

    conn = ZoneConnection()
    monkeypatch.setattr(api, "pool", Pool(conn))

    result = asyncio.run(api.get_zone("south"))

    assert [row["id"] for row in result["crops"]] == [1]
    assert [row["id"] for row in result["recent_observations"]] == [4]
    assert excluded not in str(result).casefold()
    crop_sql = conn.calls[1][1]
    observation_sql = conn.calls[2][1]
    for sql in (crop_sql, observation_sql):
        assert "COALESCE(c.zone_id, sh.zone_id, legacy_zone.id)" in sql
        assert "LEFT JOIN positions p" in sql
        assert "sh.zone_id IS NULL" in sql
        assert "cc.slug IS NOT NULL" in sql
        assert "c.name IS NOT NULL" in sql
    assert conn.calls[1][2] == (
        "south",
        sorted(policy.PUBLIC_CROP_EXCLUDE_SLUGS),
        policy.PUBLIC_CROP_SQL_NAME_PATTERN,
        api.DEFAULT_GREENHOUSE,
    )
    assert conn.calls[2][2] == conn.calls[1][2]


def test_zone_full_count_uses_same_nullable_linked_legacy_identity_and_public_filter(monkeypatch):
    api = load_api()
    zone_row = {
        "zone_id": 9,
        "greenhouse_id": api.DEFAULT_GREENHOUSE,
        "zone_slug": "south",
        "zone_name": "South",
        "shelves": [],
        "sensors": [],
        "equipment": [],
        "water_systems": [],
    }
    conn = Connection(row=zone_row, value=2)
    monkeypatch.setattr(api, "pool", Pool(conn))

    result = asyncio.run(api.get_zone_full("south"))

    assert result["active_crops_fk_count"] == 2
    count_sql = conn.calls[1][1]
    assert "COALESCE(c.zone_id, sh.zone_id, legacy_zone.id) = $1" in count_sql
    assert "LEFT JOIN positions p" in count_sql
    assert "sh.zone_id IS NULL" in count_sql
    assert "cc.slug IS NOT NULL" in count_sql
    assert "c.name IS NOT NULL" in count_sql
    assert conn.calls[1][2] == (
        9,
        api.DEFAULT_GREENHOUSE,
        sorted(policy.PUBLIC_CROP_EXCLUDE_SLUGS),
        policy.PUBLIC_CROP_SQL_NAME_PATTERN,
    )


def test_position_history_filters_in_sql_and_python_and_clears_stale_empty_fields(monkeypatch):
    api = load_api()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    current = position_row(crop_id=99, crop_name="stale", crop_catalog_slug="canna", crop_variety="stale")
    public = history_row()
    hidden = history_row(crop_id=8, crop_name=excluded.title(), crop_catalog_slug=excluded)
    missing = history_row(crop_id=9, crop_name=None, crop_catalog_slug="canna")
    conn = ScriptedConnection(fetchrow_results=[current], fetch_results=[[public, hidden, missing]])
    monkeypatch.setattr(api, "pool", Pool(conn))

    result = asyncio.run(api.get_position(1))

    assert result["current"]["is_occupied"] is False
    assert result["current"]["crop_id"] is None
    assert result["current"]["crop_variety"] is None
    assert [row["crop_id"] for row in result["history"]] == [7]
    history_sql = conn.calls[1][1]
    assert "crop_catalog_slug IS NOT NULL" in history_sql
    assert "crop_name IS NOT NULL" in history_sql
    assert history_sql.index("crop_name IS NOT NULL") < history_sql.index("ORDER BY")


def test_catalog_list_and_detail_apply_sql_and_recursive_success_redaction(monkeypatch):
    api = load_api()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    list_conn = Connection(
        rows=[
            {
                "slug": "canna",
                "common_name": "Canna lily",
                "description": f"near {excluded}",
                "stage_season_profiles": "[]",
            },
            {"slug": excluded, "common_name": excluded.title(), "stage_season_profiles": "[]"},
            {"slug": "basil", "common_name": None, "stage_season_profiles": "[]"},
        ]
    )
    monkeypatch.setattr(api, "pool", Pool(list_conn))

    listed = asyncio.run(api.list_crop_catalog())

    assert [row["slug"] for row in listed] == ["canna"]
    assert excluded not in str(listed).casefold()
    list_sql = list_conn.calls[0][1]
    assert "common_name IS NOT NULL" in list_sql
    assert "btrim(common_name) <> ''" in list_sql

    entry = {
        "slug": "canna",
        "common_name": "Canna lily",
        "description": f"inspect {excluded}",
        "stage_season_profiles": "[]",
    }
    detail_conn = ScriptedConnection(
        fetchrow_results=[entry],
        fetch_results=[[{"growth_stage": "seed", "notes": f"inspect {excluded}"}]],
    )
    monkeypatch.setattr(api, "pool", Pool(detail_conn))

    detailed = asyncio.run(api.get_crop_catalog_entry("canna"))

    assert excluded not in str(detailed).casefold()
    assert "common_name IS NOT NULL" in detail_conn.calls[0][1]


@pytest.mark.parametrize(
    ("call", "row", "expected_safe_value"),
    [
        (
            lambda api: api.list_equipment(),
            {
                "id": 1,
                "name": "Canna lily",
                "specs": {"telemetry_slug": "fan1", "future_private": "hidden"},
            },
            "Canna lily",
        ),
        (
            lambda api: api.list_switches(),
            {"equipment_name": "Canna lily", "future_private": "hidden"},
            "Canna lily",
        ),
        (
            lambda api: api.list_sensors(),
            {"id": 1, "slug": "safe_sensor", "future_private": "hidden"},
            "safe_sensor",
        ),
        (
            lambda api: api.pressure_group_status(),
            {
                "group_name": "Safe manifold",
                "systems": [
                    {
                        "water_system_slug": "safe_line",
                        "future_private": "hidden",
                    }
                ],
                "future_private": "hidden",
            },
            "safe_line",
        ),
    ],
)
def test_public_inventory_surfaces_use_nested_allowlists(monkeypatch, call, row, expected_safe_value):
    api = load_api()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    row["future_private"] = f"inspect {excluded}"
    conn = Connection(rows=[row])
    monkeypatch.setattr(api, "pool", Pool(conn))

    result = asyncio.run(call(api))

    assert excluded not in str(result).casefold()
    assert expected_safe_value in str(result)
    assert "future_private" not in str(result)


def test_topology_success_recursively_redacts_nested_free_text(monkeypatch):
    api = load_api()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    row = {
        "greenhouse_id": "vallery",
        "greenhouse_name": "Verdify Lab",
        "future_private": "hidden",
        "zones": (
            '[{"name":"Canna lily","notes":"inspect '
            + excluded
            + '","future_private":"hidden","shelves":[{"slug":"floor",'
            '"future_private":"hidden","positions":[{"label":"P1",'
            '"future_private":"hidden"}]}]}]'
        ),
    }
    conn = Connection(row=row)
    monkeypatch.setattr(api, "pool", Pool(conn))

    result = asyncio.run(api.get_topology())

    assert excluded not in str(result).casefold()
    assert result["zones"][0]["name"] == "Canna lily"
    assert result["zones"][0]["shelves"][0]["positions"][0]["label"] == "P1"
    assert "future_private" not in str(result)


def test_public_get_queries_do_not_use_wildcard_projection():
    source = (Path(__file__).resolve().parents[1] / "api" / "main.py").read_text()
    assert re.search(r"\bSELECT\s+(?:[A-Za-z_][A-Za-z0-9_]*\.)?\*", source, re.IGNORECASE) is None
