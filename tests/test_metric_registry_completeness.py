"""Every numeric climate column has an API-03 registry key or a written reason (VD-56, US-GRF-07).

The column set is ``CREATE TABLE public.climate`` in ``db/schema.sql`` (a pg_dump last refreshed
2026-08-11) plus every uncommented ``ADD COLUMN`` inside an ``ALTER TABLE [public.]climate``
statement in ``db/migrations/*.sql``, so a migration that adds a numeric column without a key or
a reason in ``verdify_services.series.EXCLUDED_COLUMNS`` fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

from verdify_services import series

ROOT = Path(__file__).resolve().parents[1]
NUMERIC = re.compile(
    r"(double\s+precision|float[48]?|real|numeric|decimal|smallint|integer|bigint|int[248]?)\b", re.IGNORECASE
)
ALTER_CLIMATE = re.compile(
    r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?:public\.)?\"?climate\"?\s(.*?);",
    re.IGNORECASE | re.DOTALL,
)
ADD_COLUMN = re.compile(
    r"\bADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?\"?([a-z_][a-z0-9_]*)\"?\s+([a-z][a-z0-9 ]*)",
    re.IGNORECASE,
)
NOT_COLUMNS = {"constraint", "primary", "unique", "foreign", "check", "exclude"}


def strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL))


def schema_columns(schema_sql: str) -> dict[str, str]:
    body = schema_sql.split("CREATE TABLE public.climate (", 1)[1].split("\n);", 1)[0]
    columns = {}
    for line in body.splitlines():
        line = line.strip().rstrip(",")
        if line and not line.upper().startswith("CONSTRAINT"):
            name, column_type = line.split(None, 1)
            columns[name.strip('"')] = column_type
    return columns


def migration_columns(sql: str) -> dict[str, str]:
    columns = {}
    for statement in ALTER_CLIMATE.finditer(strip_comments(sql)):
        for name, column_type in ADD_COLUMN.findall(statement.group(1)):
            if name.lower() not in NOT_COLUMNS:
                columns[name.lower()] = column_type
    return columns


def climate_columns(extra_migrations: tuple[str, ...] = ()) -> dict[str, str]:
    columns = schema_columns((ROOT / "db/schema.sql").read_text())
    sources = [path.read_text() for path in sorted((ROOT / "db/migrations").glob("*.sql"))]
    for sql in (*sources, *extra_migrations):
        for name, column_type in migration_columns(sql).items():
            columns.setdefault(name, column_type)
    return columns


def numeric(columns: dict[str, str]) -> set[str]:
    return {name for name, column_type in columns.items() if NUMERIC.match(column_type.strip())}


def keyed_columns() -> set[str]:
    return {
        binding.raw.column
        for binding in series.METRICS.values()
        if binding.raw is not None and binding.raw.table == "climate"
    }


def uncovered(columns: dict[str, str]) -> list[str]:
    return sorted(numeric(columns) - keyed_columns() - set(series.EXCLUDED_COLUMNS))


def test_every_numeric_climate_column_has_a_key_or_a_reason():
    columns = climate_columns()
    assert len(numeric(columns)) >= 100  # 98 in the schema dump plus outdoor_ppfd and hydro_tds_ppt
    assert uncovered(columns) == []


def test_a_migration_adding_an_unkeyed_climate_column_fails():
    synthetic = (
        "-- 999-synthetic.sql\n"
        "ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS par_umol_m2_s double precision;\n"
        "ALTER TABLE climate ADD canopy_temp_f FLOAT, ADD COLUMN probe_note text;\n"
        "-- ALTER TABLE climate ADD COLUMN commented_out double precision;\n"
        "/* ALTER TABLE climate ADD COLUMN block_commented integer; */\n"
        "ALTER TABLE public.climate_archive ADD COLUMN other_table double precision;\n"
    )
    assert uncovered(climate_columns((synthetic,))) == ["canopy_temp_f", "par_umol_m2_s"]


def test_keys_and_reasons_name_real_columns_and_never_overlap():
    columns = climate_columns()
    assert keyed_columns() <= set(columns)
    assert set(series.EXCLUDED_COLUMNS) <= set(columns)
    assert not keyed_columns() & set(series.EXCLUDED_COLUMNS)
    assert all(reason.strip() for reason in series.EXCLUDED_COLUMNS.values())


def test_exclusions_are_exactly_the_written_reasons():
    assert set(series.EXCLUDED_COLUMNS) == {
        "lux",
        "dli_today",
        "ppfd",
        "dli_par_today",
        "outdoor_ppfd",
        "hydro_tds_ppt",
        "wind_speed_avg_mph",
        "wind_direction_avg_deg",
        "feels_like_f",
        "wet_bulb_temp_f",
        "vapor_pressure_inhg",
        "air_density_kg_m3",
        "precip_intensity_in_h",
    }
    assert "migration 027" in series.EXCLUDED_COLUMNS["outdoor_ppfd"]
    assert "migration 017" in series.EXCLUDED_COLUMNS["hydro_tds_ppt"]
    assert "#1184" in series.EXCLUDED_COLUMNS["ppfd"]
    assert "_TEMPEST_MAP" in series.EXCLUDED_COLUMNS["feels_like_f"]
