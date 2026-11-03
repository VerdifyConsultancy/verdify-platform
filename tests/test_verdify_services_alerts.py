"""verdify_services.alerts (VD-67, #1180): the merge, the one open-alert count, the cursor, ack and resolve.

Runs without a database: FakeAlertDB answers each statement the module sends (matched against its own SQL
constants) over an in-memory alert_log, with v_open_alerts' predicate, and keeps a transaction's writes apart
until it commits. tests/test_dashboard_db_settings_alerts.py proves the same paths on PostgreSQL, the open set
against the view included. The models are checked against the committed OpenAPI file.
"""

import asyncio
import copy
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest
import yaml

from verdify_schemas.activity import REASON_MAX_LENGTH
from verdify_services import activity, alerts
from verdify_services.actor import Actor
from verdify_services.problems import ServiceError

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
NOW = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
PERSON = Actor.person("jason", "operator")
IRIS = Actor.iris()
AGENT = Actor.agent("steward")


def run(coroutine):
    return asyncio.run(coroutine)


def ago(**kwargs) -> datetime:
    return NOW - timedelta(**kwargs)


def openapi_errors(name: str, instance) -> list[str]:
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": OPENAPI["components"]}
    )
    return [error.message for error in validator.iter_errors(instance)]


class FakeTransaction:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        self.db.depth += 1
        self.saved = copy.deepcopy((self.db.rows, self.db.activity))
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.db.depth -= 1
        if exc_type is not None:
            self.db.rows, self.db.activity = self.saved
        return False


class FakeAlertDB:
    """alert_log, slack_alert_runbooks and activity_log in memory, answered as PostgreSQL would."""

    def __init__(self):
        self.rows: list[dict] = []
        self.runbooks: list[dict] = []
        self.activity: list[dict] = []
        self.sql: list[str] = []
        self.depth = 0
        self.next_id = 0

    def is_in_transaction(self):
        return self.depth > 0

    def transaction(self, **_kwargs):
        return FakeTransaction(self)

    def add(self, alert_type, *, sensor_id=None, ts=None, severity="warning", disposition="open", details=None,
            zone=None, resolved_at=None, message=None, category="system", acknowledged_at=None):  # fmt: skip
        self.next_id += 1
        row = {
            "id": self.next_id,
            "ts": ts or NOW,
            "alert_type": alert_type,
            "severity": severity,
            "category": category,
            "sensor_id": sensor_id,
            "zone": zone,
            "message": message or f"{alert_type} {self.next_id}",
            "details": details,
            "metric_value": None,
            "threshold_value": None,
            "disposition": disposition,
            "acknowledged_at": acknowledged_at,
            "acknowledged_by": None,
            "notes": None,
            "resolved_at": resolved_at,
            "resolved_by": None,
            "resolution": None,
        }
        self.rows.append(row)
        return row

    @staticmethod
    def is_open(row):  # v_open_alerts' predicate
        return row["resolved_at"] is None and row["disposition"] != "suppressed"

    def out(self, rows):
        return [{**row, "details": None if row["details"] is None else json.dumps(row["details"])} for row in rows]

    def key(self, row, alert_type, sensor_id):
        return row["alert_type"] == alert_type and row["sensor_id"] == sensor_id

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        if sql == alerts._OPEN_SQL:
            return self.out(r for r in self.rows if self.is_open(r))
        if sql == alerts._RECENT_SQL:
            (window,) = args
            return self.out(
                r
                for r in self.rows
                if self.is_open(r)
                or (
                    r["resolved_at"] is not None
                    and r["resolved_at"] >= NOW - window
                    and r["disposition"] != "suppressed"
                )
            )
        if sql == alerts._KEY_OPEN_SQL:
            alert_type, sensor_id = args
            return self.out(r for r in self.rows if self.key(r, alert_type, sensor_id) and self.is_open(r))
        if sql == alerts._KEY_RESOLVED_SQL:
            alert_type, sensor_id, row_id, window = args
            return self.out(
                r
                for r in self.rows
                if self.key(r, alert_type, sensor_id)
                and r["resolved_at"] is not None
                and ((r["resolved_at"] >= NOW - window and r["disposition"] != "suppressed") or r["id"] == row_id)
            )
        if sql in (alerts._ACK_SQL, alerts._RESOLVE_SQL):
            assert self.depth > 0
            alert_type, sensor_id, by, text = args
            updated = []
            for row in self.rows:
                if not self.key(row, alert_type, sensor_id) or row["resolved_at"] is not None:
                    continue
                if sql == alerts._ACK_SQL and row["disposition"] == "open":
                    row.update(disposition="acknowledged", acknowledged_at=NOW, acknowledged_by=by)
                    row["notes"] = text if text is not None else row["notes"]
                    updated.append({"id": row["id"]})
                elif sql == alerts._RESOLVE_SQL and row["disposition"] != "suppressed":
                    row.update(disposition="resolved", resolved_at=NOW, resolved_by=by, resolution=text)
                    updated.append({"id": row["id"]})
            return updated
        if sql == alerts._RUNBOOKS_SQL:
            _gh, types = args
            return [r for r in self.runbooks if r["alert_type"].lower() in types and r.get("is_active", True)]
        raise AssertionError(f"unexpected statement: {sql}")

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        if sql == alerts._BY_ID_SQL:
            (row_id,) = args
            found = [r for r in self.rows if r["id"] == row_id]
            return self.out(found)[0] if found else None
        if "FROM public.activity_log WHERE source = $1 AND request_id = $2" in sql:
            source, key = args
            found = [r for r in self.activity if r["source"] == source and r["request_id"] == key]
            return {**found[0], "detail": json.dumps(found[0]["detail"])} if found else None
        raise AssertionError(f"unexpected statement: {sql}")

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        if sql == activity.ARMED_EXPERIMENT_SQL:
            return None
        if sql.startswith("INSERT INTO public.activity_log"):
            gh, source, actor, action, target, detail, reason, request_id, digest, experiment_id = args
            self.activity.append(
                {
                    "id": len(self.activity) + 1,
                    "greenhouse_id": gh,
                    "ts": NOW,
                    "source": source,
                    "actor": actor,
                    "action": action,
                    "target": target,
                    "detail": json.loads(detail),
                    "reason": reason,
                    "request_id": request_id,
                    "request_sha256": digest,
                    "experiment_id": experiment_id,
                }
            )
            return len(self.activity)
        raise AssertionError(f"unexpected statement: {sql}")


def relay_stuck_fixture():
    """Three open repeats of one relay_stuck alert and one acknowledged alert (the acceptance fixture)."""
    db = FakeAlertDB()
    repeats = [
        db.add("relay_stuck", sensor_id="equipment.mister_south", ts=ago(hours=h), zone="south") for h in (3, 2, 1)
    ]
    acked = db.add("sensor_offline", sensor_id="climate.rh_south", ts=ago(hours=5), disposition="acknowledged")
    return db, repeats, acked


def items(db, status="open", **kwargs):
    return run(alerts.list(db, status, **kwargs)).items


# ── The merge ────────────────────────────────────────────────────────────────


def test_three_open_repeats_merge_into_one_open_row_and_the_count_is_1():
    db, repeats, acked = relay_stuck_fixture()
    rows = {row.alert_type: row for row in items(db)}
    merged = rows["relay_stuck"]
    assert (merged.id, merged.count, merged.status) == (repeats[0]["id"], 3, "open")
    assert (merged.first_seen_at, merged.last_seen_at, merged.message) == (
        ago(hours=3),
        ago(hours=1),
        repeats[-1]["message"],
    )
    assert (rows["sensor_offline"].status, rows["sensor_offline"].count) == ("acknowledged", 1)
    assert run(alerts.open_count(db)).model_dump() == {"open": 1, "critical": 0, "warning": 1, "info": 0}


def test_the_merged_id_stays_put_as_a_fourth_repeat_merges_and_one_ack_moves_the_count():
    db, repeats, _ = relay_stuck_fixture()
    first_id = next(row.id for row in items(db) if row.alert_type == "relay_stuck")
    db.add("relay_stuck", sensor_id="equipment.mister_south", ts=ago(minutes=5))
    merged = next(row for row in items(db) if row.alert_type == "relay_stuck")
    assert (merged.id, merged.count) == (first_id, 4)
    result = run(alerts.ack(db, PERSON, first_id, "checked the relay", None))
    assert (result.value.id, result.value.status, result.value.count) == (first_id, "acknowledged", 4)
    assert {row["disposition"] for row in db.rows if row["alert_type"] == "relay_stuck"} == {"acknowledged"}
    assert {row["acknowledged_by"] for row in db.rows if row["alert_type"] == "relay_stuck"} == {"jason"}
    assert {row["notes"] for row in db.rows if row["alert_type"] == "relay_stuck"} == {"checked the relay"}
    assert run(alerts.open_count(db)).open == 0
    [logged] = db.activity
    assert (logged["action"], logged["target"], logged["source"], logged["reason"]) == (
        "alert.ack",
        str(first_id),
        "dashboard",
        "checked the relay",
    )
    assert logged["detail"]["ids"] == sorted(row["id"] for row in db.rows if row["alert_type"] == "relay_stuck")
    assert logged["detail"]["ref"] == {"table": "alert_log", "id": first_id}


def test_repeats_merge_by_type_and_sensor_never_by_zone():
    db = FakeAlertDB()
    db.add("sensor_offline", sensor_id="climate.hydro_ph", ts=ago(hours=2))  # zone NULL, like production
    db.add("sensor_offline", sensor_id="climate.temp_south", ts=ago(hours=1))
    db.add("soil_sensor_offline", sensor_id="soil.south_1", zone="south", ts=ago(hours=2))
    db.add("soil_sensor_offline", sensor_id="soil.south_2", zone="south", ts=ago(hours=1))
    db.add("house_band_drift", sensor_id=None, zone="house", ts=ago(hours=3))
    db.add("house_band_drift", sensor_id=None, zone="house", ts=ago(hours=1))  # NULL groups with NULL
    merged = items(db)
    assert sorted((row.alert_type, row.count) for row in merged) == [
        ("house_band_drift", 2),
        ("sensor_offline", 1),
        ("sensor_offline", 1),
        ("soil_sensor_offline", 1),
        ("soil_sensor_offline", 1),
    ]
    assert run(alerts.open_count(db)).open == 5


def test_count_sums_details_count_and_last_seen_reads_details_last_seen_at():
    db = FakeAlertDB()
    db.add("soil_sensor_offline", sensor_id="soil.west", ts=ago(days=3),
           details={"count": 5, "last_seen_at": ago(minutes=10).isoformat()})  # fmt: skip
    db.add("soil_sensor_offline", sensor_id="soil.west", ts=ago(hours=2), details={"count": "x"})
    [merged] = items(db)
    assert (merged.count, merged.first_seen_at, merged.last_seen_at) == (6, ago(days=3), ago(minutes=10))


def test_a_merged_row_is_open_while_any_repeat_is_unacknowledged_and_takes_the_newest_severity():
    db = FakeAlertDB()
    db.add("vpd_extreme", sensor_id="climate.vpd_avg", ts=ago(hours=2), severity="warning", disposition="acknowledged")
    db.add("vpd_extreme", sensor_id="climate.vpd_avg", ts=ago(hours=1), severity="critical")
    [merged] = items(db)
    assert (merged.status, merged.severity) == ("open", "critical")
    assert run(alerts.open_count(db)).model_dump() == {"open": 1, "critical": 1, "warning": 0, "info": 0}


def test_recent_merges_resolved_rows_among_themselves_apart_from_open_ones():
    db = FakeAlertDB()
    old = db.add("forecast_deviation", sensor_id="forecast.deviation", ts=ago(hours=5), resolved_at=ago(hours=4),
                 disposition="resolved")  # fmt: skip
    db.add("forecast_deviation", sensor_id="forecast.deviation", ts=ago(hours=3), resolved_at=ago(hours=2),
           disposition="resolved")  # fmt: skip
    db.add("forecast_deviation", sensor_id="forecast.deviation", ts=ago(days=3), resolved_at=ago(days=2),
           disposition="resolved")  # fmt: skip  # outside 24 h
    db.add("soil_sensor_offline", sensor_id="soil.west", ts=ago(hours=6), resolved_at=ago(hours=5),
           disposition="suppressed")  # fmt: skip  # suppressed never shows
    live = db.add("forecast_deviation", sensor_id="forecast.deviation", ts=ago(hours=1))
    assert [(row.id, row.status) for row in items(db)] == [(live["id"], "open")]
    recent = items(db, "recent")
    assert sorted((row.status, row.id, row.count) for row in recent) == [
        ("open", live["id"], 1),
        ("resolved", old["id"], 2),
    ]


def test_rows_order_by_severity_then_last_seen_and_page_with_a_keyset_cursor():
    db = FakeAlertDB()
    info = db.add("tunable_zero_variance", sensor_id="setpoint.temp_low", severity="info", ts=ago(minutes=1))
    warn_old = db.add("sensor_offline", sensor_id="a", ts=ago(hours=5))
    warn_new = db.add("sensor_offline", sensor_id="b", ts=ago(hours=1))
    crit = db.add("leak_detected", sensor_id="equipment.leak_detected", severity="critical", ts=ago(days=1))
    assert [row.id for row in items(db)] == [crit["id"], warn_new["id"], warn_old["id"], info["id"]]
    pages, cursor = [], None
    while True:
        page = run(alerts.list(db, "open", 3, cursor))
        pages.append([row.id for row in page.items])
        cursor = page.next_cursor
        if cursor is None:
            break
    assert pages == [[crit["id"], warn_new["id"], warn_old["id"]], [info["id"]]]


@pytest.mark.parametrize(
    "kwargs, field",
    [({"status": "all"}, "status"), ({"limit": 0}, "limit"), ({"limit": 201}, "limit"), ({"cursor": "%%%"}, "cursor")],
)
def test_list_refuses_bad_arguments_with_invalid_request(kwargs, field):
    with pytest.raises(ServiceError) as excinfo:
        run(alerts.list(FakeAlertDB(), **kwargs))
    assert (excinfo.value.problem.code, excinfo.value.problem.field) == ("invalid_request", field)


def test_the_runbook_is_attached_as_markdown_for_the_type_preferring_its_severity():
    db = FakeAlertDB()
    db.add("leak_detected", sensor_id="equipment.leak_detected", severity="critical")
    db.runbooks += [
        {"id": 1, "alert_type": "leak_detected", "severity": None, "title": "Generic", "summary": "Any",
         "runbook_url": None, "steps": []},
        {"id": 6, "alert_type": "leak_detected", "severity": "critical", "title": "Leak detected",
         "summary": "Inspect the valves.", "runbook_url": "https://lab.verdify.ai/greenhouse/",
         "steps": ["Walk the lines", "Read the meter"]},
    ]  # fmt: skip
    [row] = items(db)
    assert row.runbook == (
        "**Leak detected**\n\nInspect the valves.\n\n1. Walk the lines\n2. Read the meter\n\n"
        "[Operator view](https://lab.verdify.ai/greenhouse/)"
    )
    db.runbooks.pop()
    assert items(db)[0].runbook == "**Generic**\n\nAny"


# ── Ack and resolve ──────────────────────────────────────────────────────────


def test_resolving_planner_tool_dependency_failed_through_the_service_succeeds():
    db = FakeAlertDB()
    first = db.add("planner_tool_dependency_failed", sensor_id="mcp.scorecard", ts=ago(hours=2), severity="critical")
    db.add("planner_tool_dependency_failed", sensor_id="mcp.scorecard", ts=ago(hours=1), severity="critical")
    result = run(alerts.resolve(db, IRIS, first["id"], "retrieval succeeded", None))
    assert (result.value.status, result.value.id, result.value.count) == ("resolved", first["id"], 2)
    assert {(r["disposition"], r["resolved_by"], r["resolution"]) for r in db.rows} == {
        ("resolved", "iris", "retrieval succeeded")
    }
    assert run(alerts.open_count(db)).open == 0
    assert db.activity[0]["action"] == "alert.resolve"


def test_acted_by_is_the_username_for_a_person_and_the_source_for_the_rest():
    assert [alerts.acted_by(actor) for actor in (PERSON, IRIS, AGENT, Actor.agent("admin"), Actor.system("x"))] == [
        "jason",
        "iris",
        "agent:steward",
        "agent:admin",
        "system",
    ]


def test_an_unknown_id_is_unknown_target_and_a_bad_one_invalid_request():
    db = FakeAlertDB()
    for bad in (99, 2**40, 0):
        with pytest.raises(ServiceError) as excinfo:
            run(alerts.ack(db, PERSON, bad, None, None))
        assert (excinfo.value.problem.code, excinfo.value.problem.field) == ("unknown_target", "id")
    with pytest.raises(ServiceError) as excinfo:
        run(alerts.resolve(db, PERSON, "12", None, None))
    assert excinfo.value.problem.code == "invalid_request"
    assert db.activity == []


def test_acknowledging_a_resolved_row_acknowledges_nothing_and_returns_its_resolved_row():
    db = FakeAlertDB()
    done = db.add("relay_stuck", sensor_id="equipment.fog", ts=ago(hours=3), resolved_at=ago(hours=2),
                  disposition="resolved")  # fmt: skip
    live = db.add("relay_stuck", sensor_id="equipment.fog", ts=ago(hours=1))
    result = run(alerts.ack(db, PERSON, done["id"], None, None))
    assert (result.value.status, result.value.id) == ("resolved", done["id"])
    assert live["disposition"] == "open" and db.activity[0]["detail"]["ids"] == []


def test_a_retried_ack_replays_and_a_long_resolution_is_never_refused():
    db, repeats, _ = relay_stuck_fixture()
    key = uuid.uuid4()
    first = run(alerts.ack(db, AGENT, repeats[0]["id"], None, key))
    again = run(alerts.ack(db, AGENT, repeats[0]["id"], None, key))
    assert (first.replayed, again.replayed, len(db.activity)) == (False, True, 1)
    assert again.value.id == first.value.id
    text = "x" * 1200
    run(alerts.resolve(db, AGENT, repeats[0]["id"], text, None))
    assert {row["resolution"] for row in db.rows if row["alert_type"] == "relay_stuck"} == {text}
    assert len(db.activity[-1]["reason"]) == REASON_MAX_LENGTH


@pytest.mark.parametrize(("call", "field"), [(alerts.ack, "note"), (alerts.resolve, "resolution")])
def test_text_with_a_nul_is_refused_by_its_field_past_the_activity_reason_cut(call, field):
    db, repeats, _ = relay_stuck_fixture()
    with pytest.raises(ServiceError) as excinfo:
        run(call(db, AGENT, repeats[0]["id"], "x" * 1200 + "\x00", None))
    assert (excinfo.value.problem.code, excinfo.value.problem.field) == ("invalid_request", field)
    assert db.activity == [] and db.sql == []


def test_a_viewer_cannot_ack():
    db, repeats, _ = relay_stuck_fixture()
    with pytest.raises(ServiceError) as excinfo:
        run(alerts.ack(db, Actor.person("caretaker", "viewer"), repeats[0]["id"], None, None))
    assert excinfo.value.problem.code == "forbidden"
    assert {row["disposition"] for row in db.rows if row["alert_type"] == "relay_stuck"} == {"open"}


# ── The contract ─────────────────────────────────────────────────────────────


def test_alert_rows_and_pages_match_the_openapi_schemas():
    db, _, _ = relay_stuck_fixture()
    db.add("leak_detected", sensor_id="equipment.leak_detected", severity="critical",
           details={"stopped_at": ago(minutes=5).isoformat()})  # fmt: skip
    page = run(alerts.list(db, "recent", 2))
    body = page.model_dump(mode="json")
    assert openapi_errors("AlertPage", body) == [] and body["next_cursor"]
    for row in body["items"]:
        assert openapi_errors("Alert", row) == []
    schemas = OPENAPI["components"]["schemas"]
    assert set(alerts.Alert.model_fields) == set(schemas["Alert"]["properties"])
    assert set(alerts.AlertCounts.model_fields) == set(schemas["Now"]["properties"]["alerts"]["properties"])
    assert openapi_errors("Now", {}) != []  # the schema really is checked


def test_open_count_always_equals_the_open_rows_list_returns():
    db = FakeAlertDB()
    for index in range(40):
        db.add(
            ("relay_stuck", "sensor_offline", "vpd_extreme")[index % 3],
            sensor_id=f"s{index % 7}",
            ts=ago(minutes=index * 13),
            severity=("critical", "warning", "info")[index % 5 % 3],
            disposition=("open", "acknowledged", "suppressed")[index % 4 % 3],
            resolved_at=ago(minutes=index) if index % 6 == 0 else None,
        )
    listed = items(db, limit=200)
    count = run(alerts.open_count(db))
    assert count.open == sum(row.status == "open" for row in listed)
    for severity in ("critical", "warning", "info"):
        assert getattr(count, severity) == sum(row.status == "open" and row.severity == severity for row in listed)
