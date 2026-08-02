"""RELATIONSHIPS.md FK drift guard.

Walks the canonical FK table documented in verdify_schemas/RELATIONSHIPS.md
and asserts each "hard constraint" row corresponds to a real foreign-key
constraint in `information_schema.referential_constraints`. Prevents the
ERD doc from rotting.

Pairs with:
- test_drift_guards.py (schema ↔ DB table columns)
- test_firmware_drift.py (entity_map keys ↔ firmware YAML)
- test_tunables.py (schema enum ↔ entity_map.SETPOINT_MAP)

Four drift-guards now triangulate firmware ↔ ingestor ↔ schema ↔ DB ↔ docs.
"""

from __future__ import annotations

import os
import subprocess

import pytest

# Each tuple: (parent_table, parent_col, child_table, child_col).
# These mirror the "Hard constraints" table in RELATIONSHIPS.md. If a row here
# is NOT in information_schema.referential_constraints, either:
#   - the DB never created the FK (doc is aspirational — should be removed), OR
#   - the FK was dropped (doc is stale — should be removed), OR
#   - the DB did create it, but the table/column names here are wrong.
HARD_FKS = [
    ("crops", "id", "crop_events", "crop_id"),
    ("crops", "id", "observations", "crop_id"),
    ("crops", "id", "harvests", "crop_id"),
    ("crops", "id", "treatments", "crop_id"),
    ("crops", "id", "lab_results", "crop_id"),
    ("observations", "id", "treatments", "observation_id"),
    ("image_observations", "id", "observations", "image_observation_id"),
    ("planner_lessons", "id", "planner_lessons", "superseded_by"),
    ("forecast_action_rules", "id", "forecast_action_log", "rule_id"),
    ("irrigation_schedule", "id", "irrigation_log", "schedule_id"),
]


def _docker_available() -> bool:
    import shutil

    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "ps"], capture_output=True, text=True, check=False)
    return r.returncode == 0


def _ci_postgres_reachable() -> bool:
    return bool(os.environ.get("POSTGRES_HOST"))


def _disposable_db_probe_enabled() -> bool:
    return os.environ.get("VERDIFY_TEST_DISPOSABLE_DB") == "1"


pytestmark = [
    pytest.mark.live_db,
    pytest.mark.skipif(
        not (_disposable_db_probe_enabled() and (_ci_postgres_reachable() or _docker_available())),
        reason="set VERDIFY_TEST_DISPOSABLE_DB=1 with POSTGRES_HOST or local docker",
    ),
]


def _psql(sql: str) -> list[list[str]]:
    if _ci_postgres_reachable():
        env = os.environ.copy()
        env.setdefault("PGHOST", env.get("POSTGRES_HOST", "localhost"))
        env.setdefault("PGPORT", env.get("POSTGRES_PORT", "5432"))
        env.setdefault("PGUSER", env.get("POSTGRES_USER", "verdify"))
        env.setdefault("PGPASSWORD", env.get("POSTGRES_PASSWORD", "verdify"))
        env.setdefault("PGDATABASE", env.get("POSTGRES_DB", "verdify"))
        cmd = ["psql", "-t", "-A", "-F", "|", "-c", sql]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=True, env=env)
    else:
        r = subprocess.run(
            [
                "docker",
                "exec",
                "verdify-timescaledb",
                "psql",
                "-U",
                "verdify",
                "-d",
                "verdify",
                "-t",
                "-A",
                "-F",
                "|",
                "-c",
                sql,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    return [ln.split("|") for ln in r.stdout.splitlines() if ln.strip()]


def _actual_fks() -> set[tuple[str, str, str, str]]:
    """Query information_schema for every FK: (parent_tbl, parent_col, child_tbl, child_col)."""
    sql = """
        SELECT
          kcu_c.table_name,    -- child table
          kcu_c.column_name,   -- child column
          kcu_p.table_name,    -- parent table
          kcu_p.column_name    -- parent column
        FROM information_schema.referential_constraints rc
        JOIN information_schema.key_column_usage kcu_c
          ON kcu_c.constraint_name = rc.constraint_name
         AND kcu_c.constraint_schema = rc.constraint_schema
        JOIN information_schema.key_column_usage kcu_p
          ON kcu_p.constraint_name = rc.unique_constraint_name
         AND kcu_p.constraint_schema = rc.unique_constraint_schema
         AND kcu_p.ordinal_position = kcu_c.ordinal_position
        WHERE kcu_c.table_schema = 'public'
    """
    rows = _psql(sql)
    # Return as (parent_tbl, parent_col, child_tbl, child_col)
    return {(r[2], r[3], r[0], r[1]) for r in rows if len(r) >= 4}


@pytest.fixture(scope="module")
def actual_fks() -> set[tuple[str, str, str, str]]:
    return _actual_fks()


@pytest.mark.parametrize(
    "parent_tbl,parent_col,child_tbl,child_col",
    HARD_FKS,
    ids=[f"{p}.{pc}->{c}.{cc}" for (p, pc, c, cc) in HARD_FKS],
)
def test_documented_fk_exists_in_db(parent_tbl, parent_col, child_tbl, child_col, actual_fks):
    """Every (parent, child) tuple in RELATIONSHIPS.md's hard-FK table must exist
    as a real foreign-key constraint in the DB."""
    key = (parent_tbl, parent_col, child_tbl, child_col)
    if not actual_fks:
        pytest.skip("no referential_constraints rows returned (migrations not applied?)")
    assert key in actual_fks, (
        f"RELATIONSHIPS.md documents a hard FK {parent_tbl}.{parent_col} → "
        f"{child_tbl}.{child_col} but the DB has no such constraint. "
        "Either the doc is wrong (remove the row) or a migration is missing."
    )


def test_sanity_at_least_some_fks_exist(actual_fks):
    """If information_schema returned nothing, the test is meaningless."""
    assert len(actual_fks) > 0, "information_schema.referential_constraints is empty"
