"""DB solar helper contract tests.

These tests guard the SQL source for the band-phase helpers. They are intentionally
file-based: CI can prove the migration/schema no longer contain the old
hardcoded-noon implementation without needing a live database.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "db" / "migrations" / "186-noaa-solar-phase-parity.sql"
SCHEMA = REPO_ROOT / "db" / "schema.sql"


def _function_body(sql: str, fn_name: str) -> str:
    pattern = rf"CREATE (?:OR REPLACE )?FUNCTION public\.{fn_name}\([^)]*\).*?AS \$\$(.*?)\$\$;"
    match = re.search(pattern, sql, re.DOTALL)
    assert match, f"{fn_name} body not found"
    return match.group(1)


def test_migration_and_schema_use_noaa_altitude_without_hardcoded_noon():
    for path in (MIGRATION, SCHEMA):
        sql = path.read_text()
        body = _function_body(sql, "fn_solar_altitude")

        assert "local_hour - 13.0" not in body
        assert "true_solar_time" in body
        assert "eqtime" in body
        assert "0.040849" in body
        assert "4.0 * lon_deg" in body
        assert "America/Denver" in body


def test_migration_and_schema_compute_sunrise_sunset_directly_with_noaa_zenith():
    for path in (MIGRATION, SCHEMA):
        sql = path.read_text()
        for fn_name in ("fn_solar_sunrise_hour", "fn_solar_sunset_hour"):
            body = _function_body(sql, fn_name)

            assert "90.833" in body
            assert "utc_offset_min" in body
            assert "doy - 1.0 + 0.5" in body
            assert "720.0 - 4.0" in body
            assert "fn_solar_altitude" not in body
            assert "America/Denver" in body


def test_sql_replay_fixture_exists_for_equinox_and_solstice_acceptance():
    fixture = REPO_ROOT / "db" / "migrations" / "tests" / "test-186-noaa-solar-phase-parity.sql"
    sql = fixture.read_text()

    for date_token in ("2026-03-20", "2026-06-21", "2026-09-22", "2026-12-21"):
        assert date_token in sql
    assert "max_event_error_min > 5.0" in sql
    assert "fn_solar_sunrise_hour" in sql
    assert "fn_solar_sunset_hour" in sql
