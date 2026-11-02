"""verdify_services.settings and .alerts on a private TimescaleDB (VD-67, #1180), as the MCP login.

jvallery/agents#5994's campaign step starts a fresh server for each tests/test_dashboard_db_*.py file and sets
VERDIFY_ISOLATED_TEST_DSN; without it this file skips. The database is prepared as
tests/test_dashboard_db_services.py prepares it (db/schema.sql, the ingestor and MCP logins, migration 280,
then fn_mcp_armed_assignment_context() from 259's own text). The MCP login then gets exactly the 259 and 261
grants the services use, read from those migrations, so a statement production would refuse fails here too.

Covers the acceptance criteria that need PostgreSQL: writes and snapping, the manual rule, the crop band rows
(fn_zone_vpd_targets and band_anchors.crop_band_anchor_values see the move), the status reasons with their
since, the alert merge with its stable id and one count, ack and resolve, and the open set against
v_open_alerts.
"""

import asyncio
import importlib.util
import json
import re
import sys
import uuid

import pytest
from test_dashboard_db_280 import MCP, ROOT, connect, install, private_dsn
from test_dashboard_db_services import EXPERIMENT_COLUMNS, armed_context_sql, owner_execute, owner_fetch

from verdify_services import alerts, settings
from verdify_services.actor import Actor
from verdify_services.problems import ServiceError

PERSON = Actor.person("jason", "operator")
IRIS = Actor.iris()
AGENT = Actor.agent("db-test")
MIGRATIONS = (
    ROOT / "db/migrations/259-mcp-ordinary-runtime-boundary.sql",
    ROOT / "db/migrations/261-outcome-kpi-ordinary-acl.sql",
)
# What the services read and write as the MCP login; each must be a production grant (259 or 261).
MCP_PRIVILEGES = {
    "alert_log": {"SELECT", "UPDATE"},
    "climate": {"SELECT"},
    "crop_band_anchors": {"SELECT"},
    "equipment_state": {"SELECT"},
    "setpoint_changes": {"SELECT"},
    "setpoint_clamps": {"SELECT"},
    "setpoint_snapshot": {"SELECT"},
    "slack_alert_runbooks": {"SELECT"},
    "system_state": {"SELECT"},
    "v_active_plan": {"SELECT"},
}
MCP_FUNCTIONS = ("public.fn_current_season()", "public.fn_zone_vpd_targets(timestamptz)")
GRANT = re.compile(
    r"GRANT\s+([A-Z][A-Z, ]*?)\s+ON\s+(?:TABLE\s+)?(?!FUNCTION|SEQUENCE|SCHEMA)(.+?)\s+TO\s+verdify_mcp_runtime\s*;",
    re.S,
)


def production_grants() -> dict[str, set[str]]:
    """Relation → privileges migrations 259 and 261 grant verdify_mcp_runtime (tables and views)."""
    granted: dict[str, set[str]] = {}
    for path in MIGRATIONS:
        for match in GRANT.finditer(path.read_text()):
            privileges = {privilege.strip() for privilege in match.group(1).split(",")}
            for relation in match.group(2).split(","):
                granted.setdefault(relation.strip().removeprefix("public."), set()).update(privileges)
    return granted


def mcp_grants_sql() -> str:
    granted = production_grants()
    statements = []
    for relation, privileges in sorted(MCP_PRIVILEGES.items()):
        assert privileges <= granted.get(relation, set()), (relation, privileges, granted.get(relation))
        statements.append(f"GRANT {', '.join(sorted(privileges))} ON public.{relation} TO verdify_mcp_runtime;")
    executes = MIGRATIONS[1].read_text()
    assert all(function in executes for function in MCP_FUNCTIONS)  # 261's EXECUTE grants (PUBLIC has them here)
    return "\n".join(statements)


@pytest.fixture(scope="module")
def dsn():
    value = private_dsn()
    asyncio.run(install(value))
    asyncio.run(owner_execute(value, EXPERIMENT_COLUMNS))
    asyncio.run(owner_execute(value, armed_context_sql()))
    asyncio.run(owner_execute(value, mcp_grants_sql()))
    return value


def run(coroutine):
    return asyncio.run(coroutine)


async def as_mcp(dsn, check):
    mcp = await connect(dsn, MCP)
    try:
        return await check(mcp)
    finally:
        await mcp.close()


async def refused(call) -> object:
    with pytest.raises(ServiceError) as excinfo:
        await call
    return excinfo.value.problem


def band_anchors_module():
    """ingestor/tasks/band_anchors.py on its own, as tests/test_solar_band_anchors.py loads it."""
    for path in (str(ROOT), str(ROOT / "ingestor")):
        if path not in sys.path:
            sys.path.insert(0, path)
    spec = importlib.util.spec_from_file_location("band_anchors_db", ROOT / "ingestor/tasks/band_anchors.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def activity_detail(conn, desired_id: int) -> tuple[str, dict]:
    row = await conn.fetchrow(
        "SELECT action, detail::text AS detail FROM public.activity_log WHERE (detail->>'desired_id')::bigint = $1",
        desired_id,
    )
    return row["action"], json.loads(row["detail"])


# ── Settings ─────────────────────────────────────────────────────────────────


def test_writes_snap_store_the_flag_as_passed_and_refuse_with_problems(dsn):
    async def check(mcp):
        result = await settings.write(mcp, PERSON, "mister_engage_kpa", 1.53, True, "campaign", uuid.uuid4())
        desired = result.value
        assert (desired.value, desired.snapped_from, desired.manual, desired.manual_before, desired.state) == (
            1.55,
            1.53,
            True,
            False,
            "requested",
        )
        stored = await mcp.fetchrow(
            "SELECT value, manual, source, actor, reason FROM public.desired_state WHERE id = $1", desired.id
        )
        assert dict(stored) == {
            "value": 1.55,
            "manual": True,
            "source": "dashboard",
            "actor": "jason",
            "reason": "campaign",
        }
        action, detail = await activity_detail(mcp, desired.id)
        assert action == "setting.write"
        assert {key: detail[key] for key in ("desired_id", "value", "requested", "manual")} == {
            "desired_id": desired.id,
            "value": 1.55,
            "requested": 1.53,
            "manual": {"before": False, "after": True},
        }
        assert detail["ref"] == {"table": "desired_state", "id": desired.id}

        problem = await refused(settings.write(mcp, PERSON, "mister_engage_kpa", 2.6, True, None, None))
        assert (problem.code, problem.min, problem.max, problem.retryable) == ("out_of_range", 0.5, 2.5, False)
        for param in ("sw_wall_fertigation_commissioned", "sw_onchip_band_enabled"):
            problem = await refused(settings.write(mcp, PERSON, param, 1, True, None, None))
            assert problem.code == "read_only", param
        problem = await refused(settings.write(mcp, PERSON, "no_such_setting", 1, True, None, None))
        assert problem.code == "unknown_target"
        assert await mcp.fetchval("SELECT count(*) FROM public.desired_state") == 1  # refusals stored nothing

    run(as_mcp(dsn, check))


def test_iris_s_plan_value_taken_manual_and_the_manual_rule(dsn):
    async def check(mcp):
        await owner_execute(
            dsn,
            "INSERT INTO public.setpoint_plan (ts, parameter, value, plan_id, source, created_at) "
            "VALUES (now() - interval '1 hour', 'mister_engage_delay_s', 48.75, 'iris-oneshot-vd67', 'iris', "
            "now() - interval '1 hour')",
        )
        taken = (await settings.write(mcp, PERSON, "mister_engage_delay_s", 48.75, True, None, None)).value
        assert (taken.value, taken.snapped_from, taken.manual) == (48.75, None, True)
        status = await settings.status(mcp, "mister_engage_delay_s")
        assert (status.applied_from, status.desired.id) == ("manual", taken.id)

        problem = await refused(settings.write(mcp, IRIS, "mister_engage_delay_s", 60, False, None, None))
        assert (problem.code, problem.current) == ("manual", 48.75)
        problem = await refused(settings.release(mcp, IRIS, "mister_engage_delay_s", None, None))
        assert problem.code == "manual"

        cleared = (await settings.write(mcp, AGENT, "mister_engage_delay_s", 60, False, "agent", None)).value
        assert (cleared.manual, cleared.manual_before) == (False, True)
        assert await mcp.fetchval("SELECT manual FROM public.desired_state WHERE id = $1", cleared.id) is False
        _, detail = await activity_detail(mcp, cleared.id)
        assert detail["manual"] == {"before": True, "after": False}

        released = (await settings.release(mcp, PERSON, "mister_engage_delay_s", "back to Iris", None)).value
        assert (released.value, released.manual, released.state) == (None, False, "released")
        assert (await settings.status(mcp, "mister_engage_delay_s")).applied_from == "plan"

    run(as_mcp(dsn, check))


def test_band_writes_move_the_current_season_rows_and_name_them(dsn):
    async def check(mcp):
        rows = await owner_fetch(
            dsn,
            "INSERT INTO public.crop_band_anchors (crop_type, season, series, anchor, value, width_below, width_above) "
            "SELECT 'orchid', s.season, 'vpd_target', a.anchor, s.value, 0.20, 0.35 "
            "  FROM (VALUES ('all', 0.70), (public.fn_current_season(), 0.80)) AS s(season, value) "
            " CROSS JOIN unnest(ARRAY['sr', 'sm', 'ss', 'mid']) AS a(anchor) "
            "RETURNING id, season, anchor",
        )
        current = {row["anchor"]: row["id"] for row in rows if row["season"] != "all"}
        every = {row["anchor"]: row["id"] for row in rows if row["season"] == "all"}
        before = await mcp.fetchval("SELECT vpd_target_center FROM public.fn_zone_vpd_targets(now())")
        assert abs(before - 0.80) < 1e-9

        for anchor in ("sr", "sm", "ss", "mid"):
            written = (
                await settings.write(mcp, AGENT, f"zone_vpd_target_center_{anchor}", 1.23, False, None, None)
            ).value
            _, detail = await activity_detail(mcp, written.id)
            assert detail["anchor_rows"] == [current[anchor]]
        after = await mcp.fetchval("SELECT vpd_target_center FROM public.fn_zone_vpd_targets(now())")
        assert abs(after - 1.23) < 1e-9
        values = {
            row["id"]: row["value"]
            for row in await mcp.fetch("SELECT id, value FROM public.crop_band_anchors WHERE crop_type = 'orchid'")
        }
        assert {values[i] for i in current.values()} == {1.23} and {values[i] for i in every.values()} == {0.70}

        width = (await settings.write(mcp, PERSON, "zone_vpd_width_below_center", 0.25, True, None, None)).value
        _, detail = await activity_detail(mcp, width.id)
        assert detail["anchor_rows"] == sorted(current.values())
        widths = {
            row["id"]: row["width_below"]
            for row in await mcp.fetch(
                "SELECT id, width_below FROM public.crop_band_anchors WHERE crop_type = 'orchid'"
            )
        }
        assert {widths[i] for i in current.values()} == {0.25} and {widths[i] for i in every.values()} == {0.20}
        band_anchors = band_anchors_module()
        anchor_values, origin = await band_anchors.crop_band_anchor_values(mcp)
        assert origin == band_anchors.ANCHOR_ORIGIN_DB
        assert (anchor_values["zone_vpd_width_below_center"], anchor_values["zone_vpd_target_center_sm"]) == (
            0.25,
            1.23,
        )

    run(as_mcp(dsn, check))


def test_status_reasons_and_their_since(dsn):
    async def check(mcp):
        clamped = (await settings.write(mcp, PERSON, "site_pressure_hpa", 845, True, None, None)).value
        [clamp] = await owner_fetch(
            dsn,
            "INSERT INTO public.setpoint_clamps (ts, parameter, requested, applied, reason) "
            "VALUES (now(), 'site_pressure_hpa', 845, 840, 'campaign_guard') RETURNING ts",
        )
        status = await settings.status(mcp, "site_pressure_hpa")
        assert (status.state, status.desired.id) == ("held", clamped.id)
        assert status.state_reason.model_dump() == {
            "code": "clamped",
            "detail": "campaign_guard",
            "applied_value": 840.0,
            "since": clamp["ts"],
        }

        await settings.write(mcp, PERSON, "fog_rh_ceiling_pct", 90, True, None, None)
        [push] = await owner_fetch(
            dsn,
            "INSERT INTO public.setpoint_changes (ts, parameter, value, source, delivery_status) "
            "VALUES (now(), 'fog_rh_ceiling_pct', 90, 'manual', 'deferred_heap_pressure') RETURNING ts",
        )
        reason = (await settings.status(mcp, "fog_rh_ceiling_pct")).state_reason
        assert (reason.code, reason.since) == ("deferred", push["ts"])

        await owner_execute(
            dsn,
            "INSERT INTO public.system_state (ts, entity, value) "
            "VALUES (now() - interval '30 seconds', 'ingestor_heartbeat', 'alive')",
        )
        [climate] = await owner_fetch(
            dsn, "INSERT INTO public.climate (ts) VALUES (now() - interval '6 minutes') RETURNING ts"
        )
        await settings.write(mcp, PERSON, "irrig_wall_duration_min", 12, True, None, None)
        status = await settings.status(mcp, "irrig_wall_duration_min")
        assert (status.state, status.state_reason.code, status.state_reason.since) == (
            "requested",
            "controller_not_reporting",
            climate["ts"],
        )

    run(as_mcp(dsn, check))


def test_the_catalog_reads_as_the_mcp_login(dsn):
    async def check(mcp):
        rows = {row.param: row for row in await settings.catalog(mcp)}
        assert (rows["mister_engage_kpa"].label, rows["mister_engage_kpa"].unit) == ("VPD Mister Engage", "kPa")
        assert rows["zone_vpd_target_center_sm"].writable is True  # its rows exist (the band test)
        assert rows["zone_priority_center"].group == "read_only"

    run(as_mcp(dsn, check))


# ── Alerts ───────────────────────────────────────────────────────────────────


async def add_alert(dsn, alert_type, sensor_id, *, minutes_ago, disposition="open", resolved=False, severity="warning"):
    [row] = await owner_fetch(
        dsn,
        "INSERT INTO public.alert_log (ts, alert_type, severity, category, sensor_id, message, disposition, "
        "resolved_at) VALUES (now() - make_interval(mins => $1), $2, $3, 'system', $4, $5, $6, "
        "CASE WHEN $7 THEN now() - make_interval(mins => $1 - 1) END) RETURNING id",
        minutes_ago,
        alert_type,
        severity,
        sensor_id,
        f"{alert_type} {sensor_id} {minutes_ago}",
        disposition,
        resolved,
    )
    return row["id"]


def test_repeats_merge_with_a_stable_id_one_count_and_one_ack(dsn):
    async def check(mcp):
        await owner_execute(dsn, "DELETE FROM public.alert_log")
        repeats = [await add_alert(dsn, "relay_stuck", "equipment.mister_south", minutes_ago=m) for m in (180, 120, 60)]
        await add_alert(dsn, "sensor_offline", "climate.rh_south", minutes_ago=300, disposition="acknowledged")

        page = await alerts.list(mcp)
        merged = {row.alert_type: row for row in page.items}
        assert (merged["relay_stuck"].id, merged["relay_stuck"].count, merged["relay_stuck"].status) == (
            repeats[0],
            3,
            "open",
        )
        assert merged["sensor_offline"].status == "acknowledged"
        assert (await alerts.open_count(mcp)).open == 1

        await add_alert(dsn, "relay_stuck", "equipment.mister_south", minutes_ago=5)
        relay = next(row for row in (await alerts.list(mcp)).items if row.alert_type == "relay_stuck")
        assert (relay.id, relay.count) == (repeats[0], 4)

        acked = await alerts.ack(mcp, PERSON, relay.id, "checked", uuid.uuid4())
        assert (acked.value.id, acked.value.status) == (repeats[0], "acknowledged")
        dispositions = await mcp.fetch(
            "SELECT DISTINCT disposition, acknowledged_by, notes FROM public.alert_log WHERE alert_type = 'relay_stuck'"
        )
        assert [tuple(row) for row in dispositions] == [("acknowledged", "jason", "checked")]
        assert (await alerts.open_count(mcp)).open == 0
        logged = await mcp.fetchrow(
            "SELECT target, detail::text AS detail FROM public.activity_log WHERE action = 'alert.ack'"
        )
        assert logged["target"] == str(repeats[0]) and len(json.loads(logged["detail"])["ids"]) == 4

    run(as_mcp(dsn, check))


def test_the_open_set_from_alert_log_equals_v_open_alerts(dsn):
    async def check(mcp):
        await add_alert(dsn, "vpd_extreme", "climate.vpd_avg", minutes_ago=50, resolved=True, disposition="resolved")
        await add_alert(
            dsn, "soil_sensor_offline", "soil.west", minutes_ago=40, resolved=True, disposition="suppressed"
        )
        await add_alert(dsn, "soil_sensor_offline", "soil.south_2", minutes_ago=30, disposition="suppressed")
        await add_alert(dsn, "heap_pressure_warning", "equipment.heap", minutes_ago=20, disposition="false_positive")
        await add_alert(dsn, "esp32_push_failed", None, minutes_ago=10, severity="critical")
        with pytest.raises(Exception) as denied:  # the MCP login has no grant on the view
            await mcp.fetch("SELECT id FROM public.v_open_alerts")
        assert getattr(denied.value, "sqlstate", None) == "42501"
        view = {row["id"] for row in await owner_fetch(dsn, "SELECT id FROM public.v_open_alerts")}
        table = {row["id"] for row in await mcp.fetch(f"SELECT id FROM public.alert_log WHERE {alerts.OPEN_PREDICATE}")}
        merged = {alert_id for item in alerts.merge(await mcp.fetch(alerts._OPEN_SQL)) for alert_id in item.ids}
        assert view == table == merged and view

    run(as_mcp(dsn, check))


def test_resolving_planner_tool_dependency_failed_through_the_service_succeeds(dsn):
    async def check(mcp):
        first = await add_alert(
            dsn, "planner_tool_dependency_failed", "mcp.scorecard", minutes_ago=20, severity="critical"
        )
        await add_alert(dsn, "planner_tool_dependency_failed", "mcp.scorecard", minutes_ago=10, severity="critical")
        result = await alerts.resolve(mcp, IRIS, first, "retrieval succeeded", None)
        assert (result.value.status, result.value.resolved_by) == ("resolved", "iris")
        rows = await mcp.fetch(
            "SELECT disposition, resolved_by, resolution FROM public.alert_log "
            "WHERE alert_type = 'planner_tool_dependency_failed'"
        )
        assert {tuple(row) for row in rows} == {("resolved", "iris", "retrieval succeeded")} and len(rows) == 2
        problem = await refused(alerts.resolve(mcp, IRIS, 2_000_000_000, None, None))
        assert problem.code == "unknown_target"

    run(as_mcp(dsn, check))
