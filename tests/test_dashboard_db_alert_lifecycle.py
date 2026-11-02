"""VD-72 on a private PostgreSQL: the campaign step (jvallery/agents#5994).

Replays the 30-day soil-probe flap fixture, a long-offline probe and its
return, the per-column rows from before the change and the leak lifecycle
through the owning alert code (alert_lifecycle_harness) into a real alert_log,
then reads v_open_alerts. alert_log, its sequence, trg_alert_log_updated_at,
v_open_alerts, climate and equipment_state come from db/schema.sql's own DDL
in a private schema. The monitor runs as a role holding exactly the ingestor
login's migration-217 alert_log grants, so a write outside them fails here.
Runs only against VERDIFY_ISOLATED_TEST_DSN (a fresh server per file in the
campaign step); skipped elsewhere.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta

import pytest
from alert_lifecycle_harness import (
    DENVER,
    ROOT,
    ingestor_alert_log_grants,
    load_alert_lifecycle,
    local,
    soil_counts,
    stale_sensor,
)

asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.skipif(
    not os.environ.get("VERDIFY_ISOLATED_TEST_DSN"),
    reason="needs the campaign step's private PostgreSQL (VERDIFY_ISOLATED_TEST_DSN)",
)

SCHEMA = "vd72_alert_lifecycle"
ROLE = "vd72_alert_lifecycle_ingestor"
WEST = ("soil_moisture_west", "soil_temp_west")
SOUTH_2 = ("soil_moisture_south_2", "soil_temp_south_2")
SCHEMA_DDL = (
    ("CREATE FUNCTION public.update_updated_at_column() RETURNS trigger", "$$;\n"),
    ("CREATE TABLE public.alert_log (", ";\n"),
    ("CREATE SEQUENCE public.alert_log_id_seq", ";\n"),
    ("ALTER SEQUENCE public.alert_log_id_seq OWNED BY", ";\n"),
    ("ALTER TABLE ONLY public.alert_log ALTER COLUMN id SET DEFAULT", ";\n"),
    ("ALTER TABLE ONLY public.alert_log\n    ADD CONSTRAINT alert_log_pkey", ";\n"),
    ("CREATE TRIGGER trg_alert_log_updated_at", ";\n"),
    ("CREATE VIEW public.v_open_alerts AS", ";\n"),
    ("CREATE TABLE public.climate (", ";\n"),
    ("CREATE TABLE public.equipment_state (", ";\n"),
)
ONE_COUNT = """
    SELECT count(*) FROM (
        SELECT alert_type, zone FROM v_open_alerts GROUP BY alert_type, zone HAVING bool_or(disposition = 'open')
    ) groups
"""


def schema_statements() -> list[str]:
    text = (ROOT / "db/schema.sql").read_text()
    statements = []
    for start, end in SCHEMA_DDL:
        assert text.count(start) == 1, start
        begin = text.index(start)
        statements.append(text[begin : text.index(end, begin) + len(end)].replace("public.", f"{SCHEMA}."))
    return statements


async def install(conn) -> None:
    grants = ingestor_alert_log_grants()
    await conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA}")
    for statement in schema_statements():
        await conn.execute(statement)
    await conn.execute(
        f"""
        DO $$ BEGIN CREATE ROLE {ROLE} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        GRANT USAGE ON SCHEMA {SCHEMA} TO {ROLE};
        GRANT SELECT ON {SCHEMA}.alert_log, {SCHEMA}.climate, {SCHEMA}.equipment_state TO {ROLE};
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


async def soil_cycle(ns, conn, when, *, silent=(), zones=("south", "west"), extra=()):
    alerts, keep_open, unpotted = ns["_soil_probe_offline_scan"](
        soil_counts(ns, silent), set(zones), when.astimezone(DENVER).hour
    )
    return await as_ingestor(
        conn,
        ns["_sync_monitor_alerts"](
            conn,
            [*alerts, *extra],
            component_status_available=True,
            component_closure_resolutions={},
            keep_open_keys=keep_open,
            unpotted_keys=unpotted,
            now=when,
        ),
    )


async def leak_cycle(ns, conn):
    async def run():
        leak, refresh_only = await ns["_leak_cycle_alert"](conn)
        return await ns["_sync_monitor_alerts"](
            conn,
            [leak] if leak else [],
            component_status_available=True,
            component_closure_resolutions={},
            refresh_only_keys=refresh_only,
        )

    return await as_ingestor(conn, run())


async def rows(conn, sensor_id):
    found = await conn.fetch("SELECT * FROM alert_log WHERE sensor_id = $1 ORDER BY id", sensor_id)
    return [{**row, "details": json.loads(row["details"]) if row["details"] else None} for row in found]


def seen(row) -> datetime:
    return datetime.fromisoformat(row["details"]["last_seen_at"])


async def flap_replay(ns, conn) -> None:
    """A month of a flapping west probe: one row, count 30, one in the one count."""
    for day in range(30):
        await soil_cycle(ns, conn, local(day, 8), silent=WEST)
        await soil_cycle(ns, conn, local(day, 14), silent=WEST)
        await soil_cycle(ns, conn, local(day, 23), silent=WEST)
        if day < 29:
            await soil_cycle(ns, conn, local(day + 1, 3))
    [row] = await rows(conn, "soil.west")
    assert row["resolved_at"] is None and row["disposition"] == "open" and row["zone"] == "west"
    assert row["details"]["count"] == 30 and seen(row) == local(29, 8)
    assert row["updated_at"] > row["created_at"]  # trg_alert_log_updated_at on the re-opens
    open_rows = await conn.fetch("SELECT alert_type, sensor_id, zone, disposition FROM v_open_alerts")
    assert [(r["alert_type"], r["sensor_id"], r["disposition"]) for r in open_rows] == [
        ("soil_sensor_offline", "soil.west", "open")
    ]
    assert await conn.fetchval(ONE_COUNT) == 1


async def long_offline_and_return(ns, conn) -> None:
    await soil_cycle(ns, conn, local(0, 8), silent=SOUTH_2)
    for day in range(1, 8):
        await soil_cycle(ns, conn, local(day, 8, 5), silent=SOUTH_2)
    [row] = await rows(conn, "soil.south_2")
    assert row["resolution"] == "auto-resolved: probe offline longer than 7 days" and row["resolved_by"] == "system"
    assert row["details"]["long_offline"] is True and await conn.fetchval("SELECT count(*) FROM v_open_alerts") == 0
    await soil_cycle(ns, conn, local(9, 8), silent=SOUTH_2)
    assert await rows(conn, "soil.south_2") == [row]  # still offline: untouched
    await soil_cycle(ns, conn, local(10, 12))
    [returned] = await rows(conn, "soil.south_2")
    assert returned["details"]["long_offline"] is False and returned["resolved_at"] is not None
    await soil_cycle(ns, conn, local(11, 12), silent=SOUTH_2)
    [reopened] = await rows(conn, "soil.south_2")
    assert reopened["id"] == row["id"] and reopened["ts"] == row["ts"] and reopened["zone"] == "south"
    assert reopened["resolved_at"] is None and reopened["resolved_by"] is None and reopened["resolution"] is None
    assert reopened["details"]["count"] == 2 and seen(reopened) == local(11, 12)


async def deploy_cycle(ns, conn) -> None:
    now = local(0, 7)
    await conn.execute(
        """
        INSERT INTO alert_log (ts, alert_type, severity, category, sensor_id, message, details, source, disposition)
        VALUES ($1, 'soil_sensor_offline', 'warning', 'sensor', 'soil.west.soil_moisture_west',
                'Soil sensor `soil_moisture_west` has no data for 30 min',
                '{"column": "soil_moisture_west", "sensor": "soil.west", "occupancy": "occupied"}', 'system', 'open'),
               ($2, 'sensor_offline', 'warning', 'sensor', 'climate.rh_south', 'Sensor `climate.rh_south` offline',
                '{"type": "humidity", "staleness_ratio": null}', 'system', 'open')
        """,
        now - timedelta(hours=1),
        now - timedelta(days=90),
    )
    raised = [stale_sensor("climate.rh_south", "humidity")]
    await soil_cycle(ns, conn, now, silent=WEST, extra=raised)
    [column] = await rows(conn, "soil.west.soil_moisture_west")
    assert column["resolution"] == "auto-resolved: replaced by the per-probe alert"
    [old] = await rows(conn, "climate.rh_south")
    assert old["resolution"] == "auto-resolved: probe offline longer than 7 days" and old["details"]["long_offline"]
    await soil_cycle(ns, conn, now + timedelta(minutes=5), silent=WEST, extra=raised)
    assert len(await rows(conn, "climate.rh_south")) == 1
    open_rows = await conn.fetch("SELECT sensor_id FROM v_open_alerts")
    assert [r["sensor_id"] for r in open_rows] == ["soil.west"]


async def soil_read(ns, conn) -> None:
    await conn.execute(
        "INSERT INTO climate (ts, soil_moisture_south_1, soil_temp_south_1, soil_ec_south_1, soil_moisture_west) "
        "VALUES (now() - interval '5 minutes', 41.0, 68.0, 1.2, 38.0)"
    )
    alerts, keep_open, unpotted = await as_ingestor(conn, ns["_soil_probe_offline_cycle"](conn, {"south", "west"}, 12))
    assert {a["sensor_id"]: a["details"]["columns"] for a in alerts} == {
        "soil.south_2": list(SOUTH_2),
        "soil.west": ["soil_temp_west"],
    }
    assert not keep_open and not unpotted


async def leak_lifecycle(ns, conn) -> None:
    await conn.execute("INSERT INTO equipment_state (ts, equipment, state) VALUES (now(), 'leak_detected', true)")
    await leak_cycle(ns, conn)
    [row] = await rows(conn, "equipment.leak_detected")
    assert row["message"].startswith("Water flowing with every Verdify valve closed since ")
    raised_at = row["ts"]
    await conn.execute(
        """
        INSERT INTO equipment_state (ts, equipment, state) VALUES ($1, 'leak_detected', false);
        """.strip(),
        raised_at + timedelta(minutes=1),
    )
    await conn.executemany(
        "INSERT INTO climate (ts, flow_gpm) VALUES ($1, $2)",
        [(raised_at + timedelta(minutes=minute), gpm) for minute, gpm in ((1, 1.1), (2, 0.0), (3, 0.0))],
    )
    await leak_cycle(ns, conn)
    [row] = await rows(conn, "equipment.leak_detected")
    stopped = raised_at + timedelta(minutes=3)
    assert row["resolved_at"] is None and datetime.fromisoformat(row["details"]["stopped_at"]) == stopped
    assert row["message"] == f"Flow stopped at {stopped.astimezone(DENVER):%H:%M}"
    await conn.execute("INSERT INTO climate (ts, flow_gpm) VALUES ($1, 0.6)", raised_at + timedelta(minutes=4))
    await leak_cycle(ns, conn)  # flow starts again: the same row, flowing text, stopped_at cleared
    [row] = await rows(conn, "equipment.leak_detected")
    assert row["details"]["stopped_at"] is None and row["message"].startswith("Water flowing with every Verdify")
    await conn.executemany(
        "INSERT INTO climate (ts, flow_gpm) VALUES ($1, $2)",
        [(raised_at + timedelta(minutes=minute), gpm) for minute, gpm in ((5, 0.0), (6, None), (7, 0.05), (8, 0.0))],
    )
    await leak_cycle(ns, conn)  # the second quiet reading after the last flow, a null skipped
    [row] = await rows(conn, "equipment.leak_detected")
    stopped = raised_at + timedelta(minutes=7)
    assert datetime.fromisoformat(row["details"]["stopped_at"]) == stopped
    assert row["message"] == f"Flow stopped at {stopped.astimezone(DENVER):%H:%M}"
    await conn.execute(
        "UPDATE alert_log SET acknowledged_at = now(), acknowledged_by = 'jason', disposition = 'acknowledged' "
        "WHERE id = $1 AND resolved_at IS NULL",
        row["id"],
    )
    await leak_cycle(ns, conn)
    assert await conn.fetchval("SELECT disposition FROM v_open_alerts WHERE id = $1", row["id"]) == "acknowledged"
    await conn.execute(
        "UPDATE alert_log SET resolved_at = now(), resolved_by = 'jason', resolution = 'hose', "
        "disposition = 'resolved' WHERE id = $1",
        row["id"],
    )
    await leak_cycle(ns, conn)
    assert len(await rows(conn, "equipment.leak_detected")) == 1
    assert await conn.fetchval("SELECT count(*) FROM v_open_alerts WHERE alert_type = 'leak_detected'") == 0


def test_alert_lifecycle_on_postgres():
    from conftest import isolated_mutation_test_dsn

    dsn = isolated_mutation_test_dsn()
    ns = load_alert_lifecycle()

    async def scenario():
        conn = await asyncpg.connect(dsn)
        try:
            await install(conn)
            for step in (flap_replay, long_offline_and_return, deploy_cycle, soil_read, leak_lifecycle):
                await conn.execute("TRUNCATE alert_log, climate, equipment_state RESTART IDENTITY")
                await step(ns, conn)
        finally:
            await remove(conn)
            await conn.close()

    asyncio.run(scenario())
