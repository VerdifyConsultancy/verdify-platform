"""Migration 289's alert silences on a private PostgreSQL: the campaign step (jvallery/agents#5994).

alert_silence, its seeds and its one grant come from 289's own statements between its seal
blocks; alert_log, its sequence, trg_alert_log_updated_at, v_open_alerts, climate and
equipment_state from db/schema.sql's own DDL, all in a private schema. The monitor runs as a
role holding exactly 289's SELECT on alert_silence and the ingestor login's migration-217
alert_log grants, so a read or write outside them fails here. Covers the seeds, the CHECK that
refuses a leak_detected silence, the read-only grant, the monitor's read (unexpired rows only),
_like against PostgreSQL's LIKE, and a silenced soil_dryout that closes as suppressed, raises
nothing while held and raises again once its silence has ended. Runs only against
VERDIFY_ISOLATED_TEST_DSN (a fresh server per file in the campaign step); skipped elsewhere.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime, timedelta

import pytest
from alert_lifecycle_harness import ROOT, ingestor_alert_log_grants, load_alert_lifecycle
from test_alert_silences import DRYOUT, dryout_alert
from test_dashboard_db_alert_lifecycle import SCHEMA_DDL

asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.skipif(
    not os.environ.get("VERDIFY_ISOLATED_TEST_DSN"),
    reason="needs the campaign step's private PostgreSQL (VERDIFY_ISOLATED_TEST_DSN)",
)

SCHEMA = "m289_alert_silences"
ROLE = "m289_alert_silences_ingestor"
MIGRATION = ROOT / "db/migrations/289-alert-silences.sql"
REASON = 'Jason, 2026-11-05 ~02:10Z: "Yes, silence for a week."'
LIKE_CASES = (
    ("soil.%", "soil.west"),
    ("soil.%", "soil"),
    ("%", ""),
    ("", "x"),
    ("soil._est", "soil.west"),
    ("soil._est", "soil.wwest"),
    ("soil.south\\_1", "soil.south_1"),
    ("soil.south\\_1", "soil.southX1"),
    ("100\\%", "100%"),
    ("100\\%", "1000"),
    ("Soil.%", "soil.west"),
    ("a%b%c", "aXXbYYc"),
    ("a%b%c", "aXXcYYb"),
    ("%south_1%", "soil.south_1.soil_moisture_south_1"),
)


def schema_statements() -> list[str]:
    text = (ROOT / "db/schema.sql").read_text()
    statements = []
    for start, end in SCHEMA_DDL:
        assert text.count(start) == 1, start
        begin = text.index(start)
        statements.append(text[begin : text.index(end, begin) + len(end)].replace("public.", f"{SCHEMA}."))
    return statements


def migration_body() -> str:
    """289's statements between its seal blocks, in the private schema, its grant to the test role."""
    sql = MIGRATION.read_text()
    body = sql.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0]
    body = re.sub(r"--[^\n]*", "", body)
    assert body.count("TO verdify_ingestor_runtime;") == 1
    return body.replace("public.", f"{SCHEMA}.").replace("TO verdify_ingestor_runtime;", f"TO {ROLE};")


async def install(conn) -> None:
    grants = ingestor_alert_log_grants()
    await conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA}")
    for statement in schema_statements():
        await conn.execute(statement)
    await conn.execute(f"DO $$ BEGIN CREATE ROLE {ROLE} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    await conn.execute(migration_body())
    await conn.execute(
        f"""
        GRANT USAGE ON SCHEMA {SCHEMA} TO {ROLE};
        GRANT SELECT ON {SCHEMA}.alert_log TO {ROLE};
        GRANT INSERT ({", ".join(sorted(grants["INSERT"]))}) ON {SCHEMA}.alert_log TO {ROLE};
        GRANT UPDATE ({", ".join(sorted(grants["UPDATE"]))}) ON {SCHEMA}.alert_log TO {ROLE};
        GRANT USAGE ON SEQUENCE {SCHEMA}.alert_log_id_seq TO {ROLE};
        SET search_path = {SCHEMA};
        """
    )


async def remove(conn) -> None:
    await conn.execute(f"RESET ROLE; DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    await conn.execute(
        f"DO $$ BEGIN DROP OWNED BY {ROLE}; DROP ROLE {ROLE}; EXCEPTION WHEN undefined_object THEN NULL; END $$"
    )


async def as_ingestor(conn, call):
    await conn.execute(f"SET ROLE {ROLE}")
    try:
        return await call
    finally:
        await conn.execute("RESET ROLE")


async def seeds_and_constraints(ns, conn) -> None:
    rows = await conn.fetch("SELECT alert_type, sensor_pattern, until, reason, actor FROM alert_silence ORDER BY id")
    assert [(r["alert_type"], r["sensor_pattern"], r["until"], r["actor"]) for r in rows] == [
        ("soil_dryout", None, datetime(2026, 11, 12, 3, tzinfo=UTC), "jason"),
        ("soil_sensor_offline", None, datetime(2026, 11, 12, 3, tzinfo=UTC), "jason"),
    ]
    assert all(r["reason"].startswith(REASON) and "out of the soil" in r["reason"] for r in rows)
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "INSERT INTO alert_silence (alert_type, until, reason, actor) VALUES ('leak_detected', now() + interval "
            "'1 day', 'never', 'test')"
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "INSERT INTO alert_silence (alert_type, until, reason, actor) VALUES ('soil_dryout', now(), '', 'test')"
        )
    for write in (
        "INSERT INTO alert_silence (alert_type, until, reason, actor) VALUES ('x', now(), 'r', 'a')",
        "UPDATE alert_silence SET until = now()",
        "DELETE FROM alert_silence",
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await as_ingestor(conn, conn.execute(write))


async def read_and_like(ns, conn) -> None:
    await conn.execute(
        "INSERT INTO alert_silence (alert_type, sensor_pattern, until, reason, actor) VALUES "
        "('sensor_offline', NULL, now() + interval '1 day', 'shorter', 'test'), "
        "('sensor_offline', 'climate.%', now() + interval '2 days', 'longest', 'test'), "
        "('sensor_offline', NULL, now() - interval '1 second', 'ended', 'test')"
    )
    silences = await as_ingestor(conn, ns["_active_alert_silences"](conn))
    assert [s["reason"] for s in silences] == ["longest", "shorter"]  # unexpired only, the longest first
    assert set(silences[0].keys()) == {"id", "alert_type", "sensor_pattern", "until", "reason"}
    for pattern, value in LIKE_CASES:
        assert ns["_like"](pattern, value) is await conn.fetchval("SELECT $2::text LIKE $1::text", pattern, value)


async def silenced_dryout(ns, conn) -> None:
    await conn.execute(
        "INSERT INTO alert_silence (alert_type, sensor_pattern, until, reason, actor) "
        "VALUES ('soil_dryout', 'soil.south\\_1.%', now() + interval '1 hour', 'reorganizing', 'jason')"
    )
    [row] = await conn.fetch(
        "INSERT INTO alert_log (ts, alert_type, severity, category, sensor_id, zone, message, source, disposition) "
        "VALUES (now() - interval '1 hour', $1, 'critical', 'sensor', $2, 'south', 'SOIL DRYOUT', 'system', 'open') "
        "RETURNING id",
        *DRYOUT,
    )

    async def cycle(when):
        silences = await ns["_active_alert_silences"](conn)
        return await ns["_sync_monitor_alerts"](
            conn,
            [dryout_alert()],
            component_status_available=True,
            component_closure_resolutions={},
            silences=silences,
            now=when,
        )

    now = datetime.now(UTC)
    assert await as_ingestor(conn, cycle(now)) == {"new": 0, "escalated": 0, "resolved": 0, "suppressed": 1}
    closed = await conn.fetchrow("SELECT * FROM alert_log WHERE id = $1", row["id"])
    silence_id = await conn.fetchval("SELECT id FROM alert_silence WHERE reason = 'reorganizing'")
    assert closed["disposition"] == "suppressed" and closed["resolved_by"] == "system"
    assert closed["resolved_at"] is not None and closed["resolution"].startswith(
        f"suppressed: alert_silence {silence_id} until "
    )
    assert closed["resolution"].endswith(" UTC (reorganizing)")
    assert await conn.fetchval("SELECT count(*) FROM v_open_alerts") == 0
    assert await as_ingestor(conn, cycle(now)) == {"new": 0, "escalated": 0, "resolved": 0, "suppressed": 0}
    assert await conn.fetchval("SELECT count(*) FROM alert_log") == 1
    # The silence ends: the rule applies again with no further action.
    await conn.execute("UPDATE alert_silence SET until = now() - interval '1 second' WHERE id = $1", silence_id)
    assert (await as_ingestor(conn, cycle(now + timedelta(seconds=1))))["new"] == 1
    assert await conn.fetchval("SELECT count(*) FROM v_open_alerts WHERE alert_type = 'soil_dryout'") == 1


def test_alert_silences_on_postgres():
    from conftest import isolated_mutation_test_dsn

    dsn = isolated_mutation_test_dsn()
    ns = load_alert_lifecycle()

    async def scenario():
        conn = await asyncpg.connect(dsn)
        try:
            await install(conn)
            for step in (seeds_and_constraints, read_and_like, silenced_dryout):
                await step(ns, conn)
                # Each later step starts without the seeds, which hold until 2026-11-12 03:00Z.
                await conn.execute("TRUNCATE alert_log, alert_silence RESTART IDENTITY")
        finally:
            await remove(conn)
            await conn.close()

    asyncio.run(scenario())
