"""The MCP set_setting, settings, activity, alerts, set_plan and set_tunable tools on a private PostgreSQL (VD-58, #1171).

jvallery/agents#5994's campaign step starts a fresh server for each tests/test_dashboard_db_*.py file and sets
VERDIFY_ISOLATED_TEST_DSN; without it this file skips. As tests/test_dashboard_db_settings_reads.py does, it builds in
that fresh database db/schema.sql's own DDL for the tables, views, keys and triggers these tools touch and migration
280's desired_state and activity_log. The tools run through mcp/server.py under the FastMCP stub, on connections holding
only the MCP login's production grants on those relations (migrations 259, 261 and 280, checked here), so a statement
production would refuse fails here too.

Covers the acceptance that needs PostgreSQL: an agent's write and its Activity row, the manual rule, replay and
request_id_reused, one plan.write row for each of set_plan and set_tunable, the one-shot and its 6 h expiry on a manual
setting, the SUNRISE delivery set_setting leaves alone, and the merged alert list against the one count.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from test_dashboard_db_settings_reads import (
    EXPERIMENT_STAND_INS,
    MIGRATION_259,
    MIGRATION_261,
    MIGRATION_280,
    MIGRATION_DDL,
    alert,
    extract,
)
from test_dashboard_db_settings_reads import ROOT as REPO_ROOT
from test_dashboard_db_settings_reads import SCHEMA_DDL as READS_SCHEMA_DDL
from test_mcp_settings_tools import TUNABLE_KEYS, body, iris, run_as, server, steward, transitions  # noqa: F401

from verdify_schemas.tunable_registry import REGISTRY
from verdify_services import alerts as alerts_service
from verdify_services import reads, settings
from verdify_services.actor import Actor

asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.skipif(
    not os.environ.get("VERDIFY_ISOLATED_TEST_DSN"),
    reason="needs the campaign step's private PostgreSQL (VERDIFY_ISOLATED_TEST_DSN)",
)

MCP_ROLE = "vd58_mcp_tools"
INGESTOR_ROLE = "vd58_ingestor_context"
PARAM = "mister_engage_kpa"
PERSON = Actor.person("jason", "operator")
# set_plan, set_tunable and the delivery rows they fence, on top of what the settings and activity reads need.
SCHEMA_DDL = (
    *READS_SCHEMA_DDL,
    ("CREATE TABLE public.plan_delivery_log (", ");\n"),
    ("CREATE SEQUENCE public.plan_delivery_log_id_seq", ";\n"),
    ("ALTER SEQUENCE public.plan_delivery_log_id_seq OWNED BY", ";\n"),
    ("ALTER TABLE ONLY public.plan_delivery_log ALTER COLUMN id SET DEFAULT", ";\n"),
    ("CREATE SEQUENCE public.planner_trigger_ledger_id_seq", ";\n"),
    ("ALTER SEQUENCE public.planner_trigger_ledger_id_seq OWNED BY", ";\n"),
    ("ALTER TABLE ONLY public.planner_trigger_ledger ALTER COLUMN id SET DEFAULT", ";\n"),
    ("ALTER TABLE ONLY public.plan_delivery_log\n    ADD CONSTRAINT plan_delivery_log_pkey", ";\n"),
    ("ALTER TABLE ONLY public.plan_delivery_log\n    ADD CONSTRAINT plan_delivery_log_trigger_id_key", ";\n"),
    ("ALTER TABLE ONLY public.plan_journal\n    ADD CONSTRAINT plan_journal_pkey", ";\n"),
    ("ALTER TABLE ONLY public.planner_trigger_ledger\n    ADD CONSTRAINT planner_trigger_ledger_pkey", ";\n"),
    ("ALTER TABLE ONLY public.setpoint_plan\n    ADD CONSTRAINT setpoint_plan_pkey", ";\n"),
    ("CREATE UNIQUE INDEX plan_journal_one_effective_per_greenhouse", ";\n"),
    ("CREATE FUNCTION public.normalize_param_name(p text)", "$$;\n"),
    ("CREATE FUNCTION public.normalize_plan_param()", "$$;\n"),
    ("CREATE FUNCTION public.normalize_plan_delivery_terminal_state()", "$$;\n"),
    ("CREATE FUNCTION public.normalize_planner_trigger_terminal_state()", "$$;\n"),
    ("CREATE TRIGGER trg_normalize_plan_delivery_terminal_state", ";\n"),
    ("CREATE TRIGGER trg_normalize_plan_param BEFORE INSERT ON public.setpoint_plan", ";\n"),
    ("CREATE TRIGGER trg_normalize_planner_trigger_terminal_state", ";\n"),
)
# set_plan's context read plans fn_setpoint_at over the newest climate row. climate stays empty here, so it never runs,
# and db/schema.sql's body would pull in the whole band function chain.
FN_SETPOINT_AT = """
CREATE FUNCTION public.fn_setpoint_at(p_param text, p_ts timestamptz) RETURNS double precision
    LANGUAGE sql STABLE AS $$ SELECT NULL::double precision $$;
"""
# Every relation the tools read or write, and how; each must be a production grant (259, 261 or 280).
MCP_PRIVILEGES = {
    "activity_log": {"SELECT", "INSERT"},
    "alert_log": {"SELECT", "UPDATE"},
    "climate": {"SELECT"},
    "crop_band_anchors": {"SELECT"},
    "desired_state": {"SELECT", "INSERT"},
    "equipment_state": {"SELECT"},
    "plan_delivery_log": {"SELECT", "UPDATE"},
    "plan_journal": {"SELECT", "INSERT", "UPDATE"},
    "planner_trigger_ledger": {"SELECT", "UPDATE"},
    "setpoint_changes": {"SELECT"},
    "setpoint_clamps": {"SELECT"},
    "setpoint_plan": {"SELECT", "INSERT", "UPDATE"},
    "setpoint_snapshot": {"SELECT"},
    "slack_alert_runbooks": {"SELECT"},
    "system_state": {"SELECT"},
    "v_active_plan": {"SELECT"},
    "v_desired_state_current": {"SELECT"},
}
MCP_FUNCTIONS = (
    "public.fn_mcp_armed_assignment_context()",
    "public.fn_current_season()",
    "public.fn_setpoint_at(text,timestamptz)",
)
MCP_SEQUENCES = ("public.desired_state_id_seq", "public.activity_log_id_seq")
GRANT = re.compile(r"GRANT\s+([A-Z][A-Z, ]*?)\s+ON\s+(?:TABLE\s+)?(.+?)\s+TO\s+verdify_mcp_runtime", re.S)
TABLES = (
    "activity_log",
    "alert_log",
    "climate",
    "control_assignments",
    "control_experiments",
    "desired_state",
    "plan_delivery_log",
    "plan_journal",
    "planner_trigger_ledger",
    "setpoint_changes",
    "setpoint_plan",
    "setpoint_snapshot",
    "system_state",
)
SUNRISE_HYPOTHESIS = json.dumps(
    {
        "conditions": {
            "outdoor_temp_peak_f": 75.0,
            "outdoor_rh_min_pct": 25.0,
            "solar_peak_w_m2": 900,
            "cloud_cover_avg_pct": 30,
        },
        "rationale": [
            {
                "parameter": PARAM,
                "new_value": 1.6,
                "forecast_anchor": "RH under 25% at noon",
                "expected_effect": "VPD stays in band",
            }
        ],
    }
)


def ddl() -> list[str]:
    schema = (REPO_ROOT / "db/schema.sql").read_text()
    statements = [extract(schema, start, end) for start, end in SCHEMA_DDL]
    statements.append(FN_SETPOINT_AT)
    statements.append(EXPERIMENT_STAND_INS)
    statements.extend(extract((REPO_ROOT / path).read_text(), start, end) for path, start, end in MIGRATION_DDL)
    return statements


def production_grants() -> tuple[dict[str, set[str]], str]:
    """Relation → privileges, and the sequence and function grant text, that 259, 261 and 280 give the MCP duty."""
    relations: dict[str, set[str]] = {}
    others = []
    for path in (MIGRATION_259, MIGRATION_261, MIGRATION_280):
        for statement in (REPO_ROOT / path).read_text().split(";"):
            # One statement at a time: a grant to another role must not run on into the next MCP grant.
            match = GRANT.fullmatch(statement[statement.rfind("GRANT ") :].strip())
            if match is None:
                continue
            privileges, target = match.groups()
            if target.startswith(("FUNCTION", "SEQUENCE", "SCHEMA")):
                others.append(target)
                continue
            for relation in target.split(","):
                relations.setdefault(relation.strip().removeprefix("public."), set()).update(
                    privilege.strip() for privilege in privileges.split(",")
                )
    return relations, " ".join(others)


def grants_sql() -> str:
    granted, others = production_grants()
    statements = [f"DO $$ BEGIN CREATE ROLE {MCP_ROLE} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;"]
    statements.append(f"GRANT USAGE ON SCHEMA public TO {MCP_ROLE};")
    for relation, privileges in sorted(MCP_PRIVILEGES.items()):
        assert privileges <= granted.get(relation, set()), (relation, privileges, granted.get(relation))
        statements.append(f"GRANT {', '.join(sorted(privileges))} ON public.{relation} TO {MCP_ROLE};")
    for function in MCP_FUNCTIONS:
        assert function in others, function
        statements.append(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC;")
        statements.append(f"GRANT EXECUTE ON FUNCTION {function} TO {MCP_ROLE};")
    for sequence in MCP_SEQUENCES:
        assert sequence in others, sequence
        statements.append(f"GRANT USAGE ON SEQUENCE {sequence} TO {MCP_ROLE};")
    # The gather script's login reads the view (M1).
    assert (
        "GRANT SELECT ON public.desired_state, public.v_desired_state_current TO verdify_ingestor_runtime;"
        in (REPO_ROOT / MIGRATION_280).read_text()
    )
    statements.append(
        f"DO $$ BEGIN CREATE ROLE {INGESTOR_ROLE} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
    statements.append(f"GRANT USAGE ON SCHEMA public TO {INGESTOR_ROLE};")
    statements.append(f"GRANT SELECT ON public.v_desired_state_current TO {INGESTOR_ROLE};")
    return "\n".join(statements)


@pytest.fixture(scope="module")
def dsn():
    from conftest import isolated_mutation_test_dsn

    value = isolated_mutation_test_dsn()

    async def install():
        conn = await asyncpg.connect(value)
        try:
            if await conn.fetchval("SELECT to_regclass('public.desired_state') IS NOT NULL"):
                pytest.fail("test_dashboard_db_mcp_settings needs a fresh database: public.desired_state exists")
            for statement in ddl():
                await conn.execute(statement)
            await conn.execute(grants_sql())
        finally:
            await conn.close()

    asyncio.run(install())
    return value


async def _as_owner(dsn_value, work):
    conn = await asyncpg.connect(dsn_value)
    try:
        return await work(conn)
    finally:
        await conn.close()


def owner(dsn_value, work):
    """Fixture rows and readbacks, as the database owner."""
    return asyncio.run(_as_owner(dsn_value, work))


def rows(dsn_value, sql, *args) -> list[dict]:
    async def fetch(conn):
        return [dict(row) for row in await conn.fetch(sql, *args)]

    return owner(dsn_value, fetch)


@pytest.fixture(autouse=True)
def tools(server, dsn, monkeypatch):  # noqa: F811
    """Every tool connection holds only the MCP grants; each test starts on empty tables."""

    async def db():
        conn = await asyncpg.connect(dsn)
        await conn.execute(f"SET ROLE {MCP_ROLE}")
        return conn

    monkeypatch.setattr(server, "_db", db)
    monkeypatch.setattr(server, "SITE_PUBLISH_TRIGGER_PATH", "")
    for name in (
        "VERDIFY_POLICY_VECTOR_MODE",
        "VERDIFY_COMPONENT_EXPERIMENT_ENABLED",
        "VERDIFY_ACTIVE_EXPERIMENT_ID",
        "VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    async def empty(conn):
        await conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY")

    owner(dsn, empty)


def pending_delivery(dsn_value, event_type: str, expected_action: str) -> str:
    """A delivered planning trigger: a pending plan_delivery_log row and its delivered ledger row."""
    trigger_id = str(uuid.uuid4())

    async def insert(conn):
        delivery_id = await conn.fetchval(
            "INSERT INTO plan_delivery_log (event_type, event_label, status, instance, trigger_id) "
            "VALUES ($1, 'fixture', 'pending', 'local', $2::uuid) RETURNING id",
            event_type,
            trigger_id,
        )
        await conn.execute(
            "INSERT INTO planner_trigger_ledger (event_type, instance, expected_at, due_at, delivered_at, status, "
            "expected_action, plan_delivery_log_id, trigger_id) "
            "VALUES ($1, 'local', now(), now() + interval '1 hour', now(), 'delivered', $2, $3, $4::uuid)",
            event_type,
            expected_action,
            delivery_id,
            trigger_id,
        )

    owner(dsn_value, insert)
    return trigger_id


def person_holds_manual(dsn_value, value: float = 1.7) -> None:
    async def write(conn):
        await settings.write(conn, PERSON, PARAM, value, True, "holding for the vanda", None)

    owner(dsn_value, write)


def desired_rows(dsn_value) -> list[dict]:
    return rows(dsn_value, "SELECT value, manual, source, actor FROM desired_state ORDER BY id")


def activity_rows(dsn_value) -> list[dict]:
    fetched = rows(
        dsn_value,
        "SELECT source, actor, action, target, detail::text AS detail, reason, experiment_id "
        "FROM activity_log ORDER BY id",
    )
    return [{**row, "detail": json.loads(row["detail"])} for row in fetched]


def set_tunable(server, actor, trigger_id, value=1.5, parameter=PARAM):  # noqa: F811
    return json.loads(
        run_as(
            server,
            actor,
            server.set_tunable(
                parameter=parameter,
                value=value,
                reason="engage earlier",
                trigger_id=trigger_id,
                planner_instance="local",
            ),
        )
    )


def set_plan(server, actor, trigger_id, plan_id="iris-20261102-0600", hypothesis=SUNRISE_HYPOTHESIS):  # noqa: F811
    return json.loads(
        run_as(
            server,
            actor,
            server.set_plan(
                plan_id=plan_id,
                hypothesis=hypothesis,
                transitions=transitions(server),
                trigger_id=trigger_id,
                planner_instance="local",
            ),
        )
    )


# ── An agent's write ────────────────────────────────────────────────────────


def test_an_agents_write_snaps_is_attributed_and_shows_in_activity(server, dsn):  # noqa: F811
    result = run_as(server, steward(server), server.set_setting(PARAM, 1.53, reason="drier afternoon"))
    assert result.isError is False
    stored = body(result)
    assert (stored["value"], stored["snapped_from"], stored["manual"], stored["state"]) == (
        1.55,
        1.53,
        False,
        "requested",
    )
    assert stored["actor"] == {"source": "agent:steward", "name": "steward"}
    assert "replayed" not in stored
    assert desired_rows(dsn) == [{"value": 1.55, "manual": False, "source": "agent:steward", "actor": "steward"}]
    # Desired state only: never the fast path, and never Iris's plan.
    assert rows(dsn, "SELECT 1 FROM setpoint_changes UNION ALL SELECT 1 FROM setpoint_plan") == []

    page = body(asyncio.run(server.activity(source="agent", target=PARAM)))
    ((entry),) = page["items"]
    assert (entry["action"], entry["actor"]["source"], entry["state"]) == (
        "setting.write",
        "agent:steward",
        "requested",
    )
    assert entry["detail"]["manual"] == {"before": False, "after": False}
    assert entry["reason"] == "drier afternoon"

    # API-17 is the same function over the same row.
    async def api_17(conn):
        return await reads.activity_page(conn, source="agent:steward")

    assert owner(dsn, api_17).model_dump(mode="json")["items"] == page["items"]


def test_settings_reads_the_catalog_and_history_as_the_mcp_login(server, dsn):  # noqa: F811
    run_as(server, steward(server), server.set_setting(PARAM, 1.5, manual=True))
    answer = body(asyncio.run(server.settings(param=PARAM)))
    ((row),) = answer["items"]
    assert (row["applied_from"], row["min"], row["max"], row["step"]) == ("manual", 0.5, 2.5, 0.05)
    assert row["desired"]["manual"] is True
    assert [item["param"] for item in body(asyncio.run(server.settings(manual_only=True)))["items"]] == [PARAM]
    in_band = body(asyncio.run(server.settings(group="band_in_force")))["items"]
    assert in_band and {item["group"] for item in in_band} == {"band_in_force"}

    start = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    end = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    history = body(asyncio.run(server.settings(param=PARAM, since=start, until=end)))
    assert [item["value"] for item in history["desired"]] == [1.5]
    assert {"from", "to", "readback", "plan"} <= set(history)

    bad = asyncio.run(server.settings(param="not_a_setting", since=start, until=end))
    assert bad.isError is True and body(bad)["code"] == "not_found"


def test_every_irrigation_site_value_is_writable_with_no_trigger(server, dsn):  # noqa: F811
    site_rows = [spec for spec in REGISTRY.values() if spec.control_class == "irrigation_site"]
    assert site_rows
    for spec in site_rows:
        result = run_as(server, steward(server), server.set_setting(spec.name, float(spec.default)))
        assert result.isError is False, body(result)
        assert (body(result)["param"], body(result)["state"]) == (spec.name, "requested")


@pytest.mark.parametrize(
    ("param", "value", "code"),
    [("not_a_setting", 1.0, "unknown_target"), ("temp_low", 60.0, "read_only"), (PARAM, 9.0, "out_of_range")],
)
def test_a_refusal_stores_nothing(server, dsn, param, value, code):  # noqa: F811
    result = run_as(server, steward(server), server.set_setting(param, value))
    assert result.isError is True and body(result)["code"] == code
    assert desired_rows(dsn) == [] and activity_rows(dsn) == []


def test_a_reason_postgresql_cannot_store_is_refused_and_stores_nothing(server, dsn):  # noqa: F811
    result = run_as(server, steward(server), server.set_setting(PARAM, 1.5, reason="a\x00b"))
    assert result.isError is True
    assert (body(result)["code"], body(result)["field"]) == ("invalid_request", "reason")
    assert desired_rows(dsn) == [] and activity_rows(dsn) == []


def test_out_of_range_says_min_max_and_current(server, dsn):  # noqa: F811
    person_holds_manual(dsn, 1.7)
    answer = body(run_as(server, steward(server), server.set_setting(PARAM, 9.0)))
    assert (answer["min"], answer["max"], answer["current"], answer["retryable"]) == (0.5, 2.5, 1.7, False)


# ── The manual rule ─────────────────────────────────────────────────────────


def test_an_agents_write_clears_a_persons_manual_and_activity_says_so(server, dsn):  # noqa: F811
    person_holds_manual(dsn)
    stored = body(run_as(server, steward(server), server.set_setting(PARAM, 1.5)))
    assert (stored["manual"], stored["manual_before"]) == (False, True)
    assert activity_rows(dsn)[-1]["detail"]["manual"] == {"before": True, "after": False}


@pytest.mark.parametrize("arguments", [{"value": 1.5}, {"release": True}], ids=["write", "release"])
def test_iris_set_setting_on_a_manual_setting_is_refused_and_stores_nothing(server, dsn, arguments):  # noqa: F811
    person_holds_manual(dsn)
    before = (desired_rows(dsn), activity_rows(dsn))
    result = run_as(server, iris(server), server.set_setting(PARAM, **arguments))
    assert result.isError is True
    answer = body(result)
    assert (answer["code"], answer["status"], answer["retryable"]) == ("manual", 422, False)
    assert (desired_rows(dsn), activity_rows(dsn)) == before


def test_iris_writes_a_setting_nobody_holds_and_an_agent_releases_a_manual_one(server, dsn):  # noqa: F811
    stored = body(run_as(server, iris(server), server.set_setting(PARAM, 1.5)))
    assert (stored["actor"], stored["manual"]) == ({"source": "iris", "name": "iris"}, False)
    person_holds_manual(dsn)
    released = body(run_as(server, steward(server), server.set_setting(PARAM, release=True, reason="thanks")))
    assert (released["value"], released["state"], released["manual"], released["manual_before"]) == (
        None,
        "released",
        False,
        True,
    )
    row = activity_rows(dsn)[-1]
    assert (row["action"], row["source"], row["detail"]["manual"]) == (
        "setting.release",
        "agent:steward",
        {"before": True, "after": False},
    )


# ── Replays ─────────────────────────────────────────────────────────────────


def test_a_repeat_returns_the_stored_json_and_appends_nothing(server, dsn):  # noqa: F811
    request_id = str(uuid.uuid4())
    first = body(run_as(server, steward(server), server.set_setting(PARAM, 1.53, request_id=request_id)))
    again = run_as(server, steward(server), server.set_setting(PARAM, 1.53, request_id=request_id))
    assert again.isError is False
    answer = body(again)
    assert answer.pop("replayed") is True
    assert answer == first
    assert len(desired_rows(dsn)) == len(activity_rows(dsn)) == 1

    other = run_as(server, steward(server), server.set_setting(PARAM, 1.6, request_id=request_id))
    assert other.isError is True and body(other)["code"] == "request_id_reused"
    assert len(desired_rows(dsn)) == len(activity_rows(dsn)) == 1


# ── Iris's planner path ─────────────────────────────────────────────────────


def test_set_tunable_on_a_manual_setting_keeps_its_one_shot_and_its_reply(server, dsn):  # noqa: F811
    person_holds_manual(dsn)
    trigger_id = pending_delivery(dsn, "TRANSITION", "any")
    reply = set_tunable(server, iris(server), trigger_id)
    assert set(reply) == TUNABLE_KEYS
    assert (reply["ok"], reply["delivery_status"], reply["terminal_action"]) == (
        True,
        "action_completed",
        "set_tunable",
    )

    ((plan_row),) = rows(
        dsn,
        "SELECT plan_id, parameter, value, source, is_active, expires_at - ts AS lifetime, trigger_id::text AS trigger "
        "FROM setpoint_plan",
    )
    assert plan_row["plan_id"] == reply["plan_id"] and plan_row["plan_id"].startswith("iris-oneshot-")
    assert (plan_row["parameter"], plan_row["value"], plan_row["source"], plan_row["is_active"]) == (
        PARAM,
        1.5,
        "iris",
        True,
    )
    assert plan_row["lifetime"] == timedelta(hours=6)
    assert plan_row["trigger"] == trigger_id

    logged = [row for row in activity_rows(dsn) if row["action"] == "plan.write"]
    ((row),) = logged
    assert (row["source"], row["actor"], row["target"], row["reason"]) == (
        "iris",
        "iris",
        reply["plan_id"],
        "engage earlier",
    )
    ((change),) = row["detail"]["changes"]
    assert (change["param"], change["value"]) == (PARAM, 1.5)
    assert (row["detail"]["trigger_id"], row["detail"]["planner_instance"]) == (trigger_id, "local")


def test_set_plan_writes_one_plan_write_row_listing_every_row_it_wrote(server, dsn):  # noqa: F811
    trigger_id = pending_delivery(dsn, "SUNRISE", "set_plan")
    reply = set_plan(server, iris(server), trigger_id)
    assert reply.get("ok") is True, reply
    assert "skipped_manual" not in reply
    assert reply["delivery_status"] == "plan_written"

    ((row),) = activity_rows(dsn)
    assert (row["action"], row["source"], row["actor"], row["target"]) == (
        "plan.write",
        "iris",
        "iris",
        reply["plan_id"],
    )
    written = rows(dsn, "SELECT ts, parameter, value FROM setpoint_plan WHERE plan_id = $1", reply["plan_id"])
    assert reply["rows_written"] == len(written) == len(row["detail"]["changes"]) > 0
    assert sorted(
        (datetime.fromisoformat(c["ts"]), c["param"], c["value"]) for c in row["detail"]["changes"]
    ) == sorted((item["ts"], item["parameter"], item["value"]) for item in written)
    assert (row["detail"]["trigger_id"], row["detail"]["planner_instance"]) == (trigger_id, "local")
    assert rows(dsn, "SELECT status FROM plan_delivery_log") == [{"status": "plan_written"}]


def test_a_full_plan_on_a_manual_setting_is_not_refused(server, dsn):  # noqa: F811
    person_holds_manual(dsn)
    reply = set_plan(server, iris(server), pending_delivery(dsn, "SUNRISE", "set_plan"))
    assert reply.get("ok") is True, reply
    assert "skipped_manual" not in reply
    assert rows(dsn, "SELECT DISTINCT parameter FROM setpoint_plan WHERE parameter = $1", PARAM) == [
        {"parameter": PARAM}
    ]
    assert desired_rows(dsn) == [{"value": 1.7, "manual": True, "source": "dashboard", "actor": "jason"}]


@pytest.mark.parametrize(
    ("actor", "source", "name"),
    [(None, "iris", "iris"), (("agent:admin", "admin"), "agent:admin", "admin")],
    ids=["no-actor", "admin-token"],
)
def test_the_plan_row_records_the_request_actor_and_never_refuses_without_one(server, dsn, actor, source, name):  # noqa: F811
    mcp_actor = None if actor is None else server.McpActor(source=actor[0], name=actor[1])
    reply = set_tunable(server, mcp_actor, pending_delivery(dsn, "TRANSITION", "any"))
    assert reply["ok"] is True
    # Its setpoint_plan row stays iris's, as today.
    assert rows(dsn, "SELECT source FROM setpoint_plan") == [{"source": "iris"}]
    ((row),) = activity_rows(dsn)
    assert (row["source"], row["actor"]) == (source, name)


def test_a_plan_written_while_an_experiment_is_armed_carries_its_id(server, dsn):  # noqa: F811
    experiment_id = uuid.uuid4()

    async def arm(conn):
        await conn.execute(
            "INSERT INTO control_experiments (experiment_id, status, armed_at) VALUES ($1, 'armed', now())",
            experiment_id,
        )

    owner(dsn, arm)
    reply = set_tunable(server, iris(server), pending_delivery(dsn, "TRANSITION", "any"))
    assert reply["ok"] is True
    ((row),) = activity_rows(dsn)
    assert row["experiment_id"] == experiment_id


def test_a_refused_planner_call_appends_nothing(server, dsn):  # noqa: F811
    required = pending_delivery(dsn, "SUNRISE", "set_plan")
    wrong_action = set_tunable(server, iris(server), required)
    assert wrong_action["status"] == "wrong_action"
    bounds = set_tunable(server, iris(server), pending_delivery(dsn, "TRANSITION", "any"), value=9.0)
    assert bounds["error"] == "Tunable value outside registry bounds"
    unknown_trigger = set_plan(server, iris(server), str(uuid.uuid4()))
    assert unknown_trigger["error"] == "trigger_id not found in plan_delivery_log"
    assert activity_rows(dsn) == []
    assert rows(dsn, "SELECT 1 FROM setpoint_plan") == []


# ── Triggers: set_setting never touches one ─────────────────────────────────


def test_set_setting_with_a_trigger_leaves_a_pending_sunrise_delivery_alone(server, dsn):  # noqa: F811
    trigger_id = pending_delivery(dsn, "SUNRISE", "set_plan")
    result = run_as(
        server, iris(server), server.set_setting(PARAM, 1.5, trigger_id=trigger_id, planner_instance="local")
    )
    assert result.isError is False

    assert rows(dsn, "SELECT status, terminal_action, resulting_plan_id FROM plan_delivery_log") == [
        {"status": "pending", "terminal_action": None, "resulting_plan_id": None}
    ]
    assert rows(dsn, "SELECT status, terminal_action, resulting_plan_id FROM planner_trigger_ledger") == [
        {"status": "delivered", "terminal_action": None, "resulting_plan_id": None}
    ]
    ((row),) = activity_rows(dsn)
    assert (row["detail"]["trigger_id"], row["detail"]["planner_instance"]) == (trigger_id, "local")

    # The trigger is still Iris's to end with its plan.
    reply = set_plan(server, iris(server), trigger_id)
    assert reply.get("ok") is True, reply
    assert rows(dsn, "SELECT status FROM plan_delivery_log") == [{"status": "plan_written"}]


# ── MANUAL SETTINGS: the context block's own query ──────────────────────────


def test_the_context_query_lists_only_values_a_person_holds_as_the_ingestor_login_reads_them(server, dsn):  # noqa: F811
    script = (REPO_ROOT / "scripts/gather-plan-context.sh").read_text()
    block = script[script.index("# ── 1b. MANUAL SETTINGS") :]
    sql = block[block.index('-c "') + len('-c "') : block.index('" 2>/dev/null')]
    sql = sql.replace("${GREENHOUSE_ID}", "vallery")

    async def context_rows():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(f"SET ROLE {INGESTOR_ROLE}")
            return [row[0] for row in await conn.fetch(sql)]
        finally:
            await conn.close()

    assert asyncio.run(context_rows()) == []

    person_holds_manual(dsn, 1.7)
    # Not held: an agent's plain write, and a manual value since released.
    run_as(server, steward(server), server.set_setting("mister_all_kpa", 1.9))
    run_as(server, steward(server), server.set_setting("vpd_hysteresis", 0.3, manual=True))
    run_as(server, steward(server), server.set_setting("vpd_hysteresis", release=True))

    ((held),) = rows(
        dsn,
        "SELECT to_char(created_at AT TIME ZONE 'America/Denver', 'YYYY-MM-DD HH24:MI') AS since "
        "FROM desired_state WHERE param = $1",
        PARAM,
    )
    assert asyncio.run(context_rows()) == [f"{PARAM}|1.7|jason (dashboard)|{held['since']}"]


# ── alerts: one merged list and one count ───────────────────────────────────


def test_alerts_merges_acts_on_every_repeat_and_matches_the_one_count(server, dsn):  # noqa: F811
    now = datetime.now(UTC)
    ids = {}

    async def fixture(conn):
        ids["first"] = await alert(conn, now - timedelta(hours=3), "relay_stuck", "mister_south")
        ids["second"] = await alert(conn, now - timedelta(hours=1), "relay_stuck", "mister_south")
        ids["other"] = await alert(conn, now - timedelta(hours=2), "sensor_offline", "rh_south")
        ids["done"] = await alert(conn, now - timedelta(hours=2), "vpd_high", "house")
        await conn.execute(
            "UPDATE alert_log SET resolved_at = now(), resolved_by = 'jason', disposition = 'resolved' WHERE id = $1",
            ids["done"],
        )

    owner(dsn, fixture)

    async def open_count(conn):
        return await alerts_service.open_count(conn)

    listed = json.loads(asyncio.run(server.alerts("list")))
    merged = {row["alert_type"]: row for row in listed}
    assert (merged["relay_stuck"]["id"], merged["relay_stuck"]["count"]) == (ids["first"], 2)
    assert merged["vpd_high"]["resolved"] is True
    unacknowledged = [row for row in listed if not row["acknowledged"]]
    assert len(unacknowledged) == owner(dsn, open_count).open == 2

    reply = json.loads(run_as(server, steward(server), server.alerts("acknowledge", ids["first"])))
    assert reply == {"ok": True, "alert_id": ids["first"], "action": "acknowledged"}
    assert rows(
        dsn,
        "SELECT DISTINCT disposition, acknowledged_by FROM alert_log WHERE alert_type = 'relay_stuck'",
    ) == [{"disposition": "acknowledged", "acknowledged_by": "agent:steward"}]
    assert owner(dsn, open_count).open == 1

    already = json.loads(run_as(server, steward(server), server.alerts("acknowledge", ids["done"])))
    assert already == {"ok": True, "alert_id": ids["done"], "action": "already_resolved"}
    missing = json.loads(run_as(server, steward(server), server.alerts("acknowledge", 999_999)))
    assert missing == {"error": "alert_id 999999 not found"}

    resolved = json.loads(run_as(server, steward(server), server.alerts("resolve", ids["other"], "new probe fitted")))
    assert resolved == {"ok": True, "alert_id": ids["other"], "action": "resolved"}
    assert rows(dsn, "SELECT resolved_by, resolution FROM alert_log WHERE id = $1", ids["other"]) == [
        {"resolved_by": "agent:steward", "resolution": "new probe fitted"}
    ]
    assert [row["action"] for row in activity_rows(dsn)] == ["alert.ack", "alert.ack", "alert.resolve"]
