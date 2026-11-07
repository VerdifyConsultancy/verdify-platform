"""VD-65 (#1178) on a private TimescaleDB: a fired forecast rule, run as the ingestor's runtime login.

jvallery/agents#5994's campaign step starts a fresh server for each tests/test_dashboard_db_*.py file and sets
VERDIFY_ISOLATED_TEST_DSN; without it this file skips, and a skip fails campaign qualification. The database is
prepared as test_dashboard_db_280 prepares it: db/schema.sql, then 280's statements. db/schema.sql predates
migration 217, so this file adds the plan-write facade and the engine's ingestor grants from 217's text. Then the
engine's main() connects with that login, as its subprocess does in production. db/schema.sql seeds no rule: each
test inserts the rule it fires. An alert rule writes alert_log only (#1352), under 217's ingestor alert_log grants.
"""

import asyncio
import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from test_dashboard_db_280 import INGESTOR, OWNER, connect, install, private_dsn, run

from verdify_schemas.experiment_config import ACTIVE_EXPERIMENT_ID_ENV, LEGACY_DIRECT_POLICY_WRITES_ENV

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "scripts/forecast-action-engine.py"
MIGRATION_217 = ROOT / "db/migrations/217-runtime-role-boundary.sql"

# Migration 063's seeded rules, with one forecast hour that fires both.
HEAT_MISTING = """
INSERT INTO public.forecast_action_rules (name, condition, metric, operator, threshold, param, adjustment_value)
VALUES ('heat_misting', 'Daytime high > 90F: extend mister pulse', 'temp_f', '>', 90, 'mister_pulse_on_s', 90)
RETURNING id
"""
HEAT_WAVE = """
INSERT INTO public.forecast_action_rules (name, condition, metric, operator, threshold, param, adjustment_value)
VALUES ('heat_wave', 'Daytime high > 90F: preemptive cooling', 'temp_f', '>', 90, 'temp_high', 80)
RETURNING id
"""
HOT_HOUR = "INSERT INTO public.weather_forecast (ts, fetched_at, temp_f) VALUES (now() + interval '3 hours', now(), 95)"
OLD_VALUE = """
INSERT INTO public.setpoint_changes (ts, parameter, value, source)
VALUES (now() - interval '1 day', 'mister_pulse_on_s', 60, 'plan')
"""
# R2's live check (#1144): every written forecast_action_log row has its activity_log row.
UNATTRIBUTED = """
SELECT count(*) FROM public.forecast_action_log f
 WHERE f.action_taken IN ('setpoint_written', 'precool_rerouted')
   AND NOT EXISTS (SELECT 1 FROM public.activity_log a
                    WHERE a.source = 'system' AND a.actor = 'forecast-engine' AND a.action = 'plan.write'
                      AND a.target = f.plan_id AND a.detail->'changes'->0->>'param' = f.param)
"""
# 217 grants these in its long ingestor lists; of those, the engine needs only these.
ENGINE_READS = """
GRANT SELECT ON public.climate, public.setpoint_changes, public.weather_forecast TO verdify_ingestor_runtime;
GRANT USAGE ON SEQUENCE public.forecast_action_log_id_seq TO verdify_ingestor_runtime;
GRANT SELECT ON public.alert_log TO verdify_ingestor_runtime;
GRANT USAGE ON SEQUENCE public.alert_log_id_seq TO verdify_ingestor_runtime;
"""
FROST = """
INSERT INTO public.forecast_action_rules (name, condition, metric, operator, threshold, action_type)
VALUES ('frost_warning', 'Overnight low < 35F', 'temp_f', '<', 35, 'alert')
RETURNING id
"""
COLD_HOUR = (
    "INSERT INTO public.weather_forecast (ts, fetched_at, temp_f) VALUES (now() + interval '5 hours', now(), 31)"
)


def plan_facade_and_engine_grants() -> str:
    """217's v_runtime_setpoint_plan_write and its ingestor grants on what the engine writes."""
    sql = MIGRATION_217.read_text()
    facades = sql[sql.index("DO $runtime_write_facades$") : sql.index("$runtime_write_facades$;")]
    projection = re.search(r"'v_runtime_setpoint_plan_write', ARRAY\[(.*?)\]", facades, re.DOTALL)
    columns = ", ".join(re.findall(r"'([a-z_]+)'", projection[1]))
    grants = re.findall(
        r"GRANT (?:SELECT|INSERT|UPDATE)\b[^;]*?\bON (?:TABLE )?public\."
        r"(?:forecast_action_rules|forecast_action_log|v_runtime_setpoint_plan_write)\s+"
        r"TO verdify_ingestor_runtime;",
        sql,
    )
    assert len(grants) == 5, grants
    # The ingestor's column grants on alert_log: what an alert rule writes and resolves.
    alert_grants = re.findall(
        r"GRANT (?:INSERT|UPDATE) \([^)]*\)\s+ON public\.alert_log TO verdify_ingestor_runtime;", sql
    )
    assert len(alert_grants) == 2, alert_grants
    view = (
        "CREATE VIEW public.v_runtime_setpoint_plan_write WITH (security_barrier=true, security_invoker=false) "
        f"AS SELECT {columns} FROM public.setpoint_plan;"
    )
    return "\n".join([view, *grants, *alert_grants, ENGINE_READS])


def login_url(dsn: str, user: str) -> str:
    """The test DSN with another login, which is what the engine's DATABASE_URL holds."""
    parts = urlsplit(dsn)
    userinfo, _, host = parts.netloc.rpartition("@")
    password = userinfo.partition(":")[2]
    return urlunsplit(parts._replace(netloc=f"{user}:{password}@{host}" if password else f"{user}@{host}"))


async def prepare(dsn: str) -> None:
    await install(dsn)
    owner = await connect(dsn, OWNER)
    try:
        await owner.execute("SET search_path = pg_catalog, public, pg_temp;\n" + plan_facade_and_engine_grants())
    finally:
        await owner.close()


@pytest.fixture(scope="module")
def dsn():
    value = private_dsn()
    run(prepare(value))
    return value


@pytest.fixture(scope="module")
def engine():
    spec = importlib.util.spec_from_file_location("forecast_action_engine_db_test", ENGINE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def clear(dsn: str) -> None:
    owner = await connect(dsn, OWNER)
    try:
        for table in (
            "alert_log",
            "activity_log",
            "setpoint_plan",
            "setpoint_changes",
            "forecast_action_log",
            "forecast_action_rules",
            "weather_forecast",
        ):
            await owner.execute(f"DELETE FROM public.{table}")
    finally:
        await owner.close()


@pytest.fixture
def db(dsn, engine, monkeypatch):
    """Empty engine tables, the engine as the ingestor login and no demotion."""
    run(clear(dsn))
    monkeypatch.setenv("DATABASE_URL", login_url(dsn, INGESTOR))
    monkeypatch.delenv(LEGACY_DIRECT_POLICY_WRITES_ENV, raising=False)
    monkeypatch.delenv(ACTIVE_EXPERIMENT_ID_ENV, raising=False)
    return dsn


async def listen(owner) -> list[str]:
    """The desired_state_changed payloads this connection hears, as setpoint_listener hears them."""
    wakes: list[str] = []
    await owner.add_listener("desired_state_changed", lambda _conn, _pid, _channel, payload: wakes.append(payload))
    return wakes


async def settled(wakes: list[str], expected: int) -> list[str]:
    """Wait for the wakes a committed run should have sent, and long enough for a surplus one to show."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    while len(wakes) < expected and loop.time() < deadline:
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.3)
    return wakes


async def rows(owner, table: str, columns: str) -> list[dict]:
    return [dict(row) for row in await owner.fetch(f"SELECT {columns} FROM public.{table}")]


def test_a_fired_setpoint_rule_writes_one_plan_row_one_activity_row_and_no_setpoint_change(db, engine):
    async def check():
        owner = await connect(db, OWNER)
        try:
            rule_id = await owner.fetchval(HEAT_MISTING)
            await owner.execute(HOT_HOUR)
            await owner.execute(OLD_VALUE)

            wakes = await listen(owner)
            await engine.main()

            plan = await rows(owner, "setpoint_plan", "parameter, value, plan_id, source, reason")
            assert len(plan) == 1
            assert (plan[0]["parameter"], plan[0]["value"], plan[0]["source"]) == (
                "mister_pulse_on_s",
                90.0,
                "preemptive",
            )
            plan_id, reason = plan[0]["plan_id"], plan[0]["reason"]
            assert plan_id.startswith("preemptive-") and "heat_misting" in reason

            activity = await rows(
                owner, "activity_log", "source, actor, action, target, detail, reason, request_id, experiment_id"
            )
            assert len(activity) == 1
            assert json.loads(activity[0].pop("detail")) == {
                "changes": [{"param": "mister_pulse_on_s", "before": 60.0, "value": 90.0}],
                "rule": "heat_misting",
                "rule_id": rule_id,
            }
            assert activity[0] == {
                "source": "system",
                "actor": "forecast-engine",
                "action": "plan.write",
                "target": plan_id,
                "reason": reason,
                "request_id": None,
                "experiment_id": None,
            }

            # Only the seeded old value: the engine wrote no setpoint change.
            assert await rows(owner, "setpoint_changes", "parameter, value, source") == [
                {"parameter": "mister_pulse_on_s", "value": 60.0, "source": "plan"}
            ]
            log = await rows(owner, "forecast_action_log", "action_taken, plan_id, param, old_value, new_value")
            assert log == [
                {
                    "action_taken": "setpoint_written",
                    "plan_id": plan_id,
                    "param": "mister_pulse_on_s",
                    "old_value": 60,
                    "new_value": 90,
                }
            ]
            assert await owner.fetchval(UNATTRIBUTED) == 0
            assert await settled(wakes, 1) == [""]  # one wake, with an empty payload
            # What the dispatcher reads: the prefix it admits a forecast-rule row by.
            assert await rows(owner, "v_active_plan", "parameter, value, plan_id") == [
                {"parameter": "mister_pulse_on_s", "value": 90.0, "plan_id": plan_id}
            ]
        finally:
            await owner.close()

    run(check())


def test_a_precool_rule_attributes_each_remapped_parameter(db, engine):
    async def check():
        owner = await connect(db, OWNER)
        try:
            rule_id = await owner.fetchval(HEAT_WAVE)
            await owner.execute(HOT_HOUR)

            wakes = await listen(owner)
            await engine.main()

            plan = await rows(owner, "setpoint_plan", "parameter, value, plan_id, source, reason")
            assert {(row["parameter"], row["value"], row["source"]) for row in plan} == {
                ("cool_stage2_over_high_f", 0.0, "preemptive"),
                ("sw_cool_all_fans_at_high_enabled", 1.0, "preemptive"),
            }
            activity = await rows(owner, "activity_log", "source, actor, action, target, detail, reason")
            assert len(activity) == 2
            by_param = {row["parameter"]: row for row in plan}
            for entry in activity:
                detail = json.loads(entry["detail"])
                assert detail["rule"] == "heat_wave" and detail["rule_id"] == rule_id
                assert len(detail["changes"]) == 1
                change = detail["changes"][0]
                written = by_param[change["param"]]
                assert change == {"param": written["parameter"], "before": None, "value": written["value"]}
                assert (entry["source"], entry["actor"], entry["action"]) == ("system", "forecast-engine", "plan.write")
                assert (entry["target"], entry["reason"]) == (written["plan_id"], written["reason"])

            assert await rows(owner, "setpoint_changes", "parameter") == []
            log = await rows(owner, "forecast_action_log", "action_taken")
            assert log == [{"action_taken": "precool_rerouted"}] * 2
            assert await owner.fetchval(UNATTRIBUTED) == 0
            assert await settled(wakes, 2) == ["", ""]  # one committed transaction per plan write
        finally:
            await owner.close()

    run(check())


def test_the_plan_row_and_its_wake_roll_back_with_a_failed_activity_row(db, engine):
    """A plan row is never written unattributed: the three steps commit together."""

    async def check():
        owner = await connect(db, OWNER)
        try:
            await owner.fetchval(HEAT_MISTING)
            await owner.execute(HOT_HOUR)
            wakes = await listen(owner)
            await owner.execute("REVOKE INSERT ON public.activity_log FROM verdify_ingestor_runtime")
            try:
                with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                    await engine.main()
            finally:
                await owner.execute("GRANT INSERT ON public.activity_log TO verdify_ingestor_runtime")
            assert await settled(wakes, 0) == []
            assert await rows(owner, "setpoint_plan", "parameter") == []
            assert await rows(owner, "activity_log", "id") == []
            assert await rows(owner, "forecast_action_log", "action_taken") == []
        finally:
            await owner.close()

    run(check())


@pytest.mark.parametrize("mode, action_taken", [("demoted", "proposal_recorded"), ("dry_run", "dry_run")])
def test_a_demoted_or_dry_run_engine_writes_no_plan_or_activity_row(db, engine, monkeypatch, mode, action_taken):
    """Demotion is an armed experiment or legacy writes off; legacy writes off needs no experiment tables."""
    if mode == "demoted":
        monkeypatch.setenv(LEGACY_DIRECT_POLICY_WRITES_ENV, "0")
    else:
        monkeypatch.setattr(engine, "DRY_RUN", True)

    async def check():
        owner = await connect(db, OWNER)
        try:
            await owner.fetchval(HEAT_MISTING)
            await owner.execute(HOT_HOUR)

            wakes = await listen(owner)
            await engine.main()

            assert await settled(wakes, 0) == []
            assert await rows(owner, "setpoint_plan", "parameter") == []
            assert await rows(owner, "activity_log", "id") == []
            assert await rows(owner, "setpoint_changes", "parameter") == []
            assert await rows(owner, "forecast_action_log", "action_taken") == [{"action_taken": action_taken}]
        finally:
            await owner.close()

    run(check())


def test_an_alert_rule_writes_one_dashboard_alert_that_refreshes_and_resolves(db, engine):
    """#1352: a fired alert rule is one forecast_action_alert row (warning), never a chat post."""

    async def check():
        owner = await connect(db, OWNER)
        try:
            rule_id = await owner.fetchval(FROST)
            await owner.execute(COLD_HOUR)
            await engine.main()

            alerts = await rows(
                owner, "alert_log", "id, alert_type, severity, category, sensor_id, source, details, resolved_at"
            )
            assert len(alerts) == 1
            alert = alerts[0]
            details = json.loads(alert.pop("details"))
            assert alert["resolved_at"] is None
            assert (alert["alert_type"], alert["severity"], alert["category"]) == (
                "forecast_action_alert",
                "warning",
                "climate",
            )
            assert (alert["sensor_id"], alert["source"]) == ("forecast.rule.frost_warning", "forecast_engine")
            assert details["rule_id"] == rule_id and details["forecast_value"] == 31.0
            assert await rows(owner, "forecast_action_log", "action_taken") == [{"action_taken": "alert_recorded"}]

            # Past its cooldown the rule fires again: the open row refreshes, no second row.
            await owner.execute("UPDATE public.forecast_action_log SET triggered_at = now() - interval '30 days'")
            await engine.main()
            assert [row["id"] for row in await rows(owner, "alert_log", "id")] == [alert["id"]]

            # Its forecast hour has passed: the next run resolves it.
            await owner.execute(
                "UPDATE public.alert_log SET details = jsonb_set(details, '{forecast_at}', to_jsonb(now() - interval '1 hour'))"
            )
            await owner.execute("DELETE FROM public.weather_forecast")
            await engine.main()
            [closed] = await rows(owner, "alert_log", "resolved_at, resolved_by, resolution")
            assert closed["resolved_at"] is not None and closed["resolved_by"] == "system"
            assert closed["resolution"] == "auto-resolved: forecast hour passed"
        finally:
            await owner.close()

    run(check())
