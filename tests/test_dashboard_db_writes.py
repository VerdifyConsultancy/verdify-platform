"""API-10, API-11, API-15 and API-16 as the dashboard login on a private TimescaleDB (#1150).

jvallery/agents#5994's campaign step starts a fresh server for each tests/test_dashboard_db_*.py file and sets
VERDIFY_ISOLATED_TEST_DSN; without it this file skips. The database is prepared as tests/test_dashboard_db_282.py
prepares it (db/schema.sql, the runtime logins, migrations 280 and 282), with fn_mcp_armed_assignment_context() from
259's own text over the columns it reads. M2b (VD-74, #1233) has not merged, so ``M2B_GRANTS`` stands in for what these
four writes use of its grants, with fn_current_season() closed to PUBLIC as production has it.

Every request runs the real operator API (FastAPI's test client, with INT-01 making the fixture sessions) on a real
asyncpg pool of ``verdify_dashboard_runtime_login`` whose connections attest, so a statement production would refuse
under those grants fails here too.

Covers the acceptance criteria that need PostgreSQL: replay and request_id_reused, the manual default, a crop-band
write's anchor rows in its transaction, a viewer's 403, ack and resolve on every merged repeat, resolving
planner_tool_dependency_failed, the refusals with their bounds, and the insert trigger as the only wake.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest
from fastapi.testclient import TestClient
from test_dashboard_db_282 import DUTY, LOGIN, OWNER, connect, install, private_dsn
from test_dashboard_db_services import EXPERIMENT_COLUMNS, armed_context_sql, owner_execute, owner_fetch
from test_dashboard_db_settings_alerts import add_alert
from test_operator_api_auth import SETTINGS, assert_problem, with_bearer

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"  # operator_api and operator_routes import as top-level modules, as in the image
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import operator_api  # noqa: E402

from verdify_services import activity  # noqa: E402

KPA = "mister_engage_kpa"
SETTING = f"/v1/settings/{KPA}"
BAND = "band_vpd_target_sm"
SWITCH = "sw_economiser_enabled"  # read back through its equipment_state mirror

# The dump predates the experiment tables and 282's owner-only stand-ins have only an id: the columns 259's function
# reads replace those two. alert_log's foreign key needs the greenhouse row.
PREPARE = f"""
DROP TABLE public.control_experiments, public.control_assignments;
{EXPERIMENT_COLUMNS}
INSERT INTO public.greenhouses (id, name) VALUES ('vallery', 'Vallery') ON CONFLICT (id) DO NOTHING;
"""
# What the four writes use of M2b's grants, and no more: settings.write and .release, alerts.ack and .resolve.
M2B_GRANTS = f"""
REVOKE EXECUTE ON FUNCTION public.fn_current_season() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.fn_current_season(), public.fn_mcp_armed_assignment_context() TO {DUTY};
GRANT SELECT ON public.climate, public.system_state, public.setpoint_snapshot, public.equipment_state,
    public.setpoint_changes, public.setpoint_clamps, public.v_active_plan, public.v_desired_state_current,
    public.crop_band_anchors, public.alert_log, public.slack_alert_runbooks TO {DUTY};
GRANT SELECT, INSERT ON public.desired_state, public.activity_log TO {DUTY};
GRANT USAGE ON SEQUENCE public.desired_state_id_seq, public.activity_log_id_seq TO {DUTY};
GRANT UPDATE (disposition, acknowledged_at, acknowledged_by, notes, resolved_at, resolved_by, resolution)
    ON public.alert_log TO {DUTY};
GRANT UPDATE (value, width_below, width_above) ON public.crop_band_anchors TO {DUTY};
"""


def run(coroutine):
    return asyncio.run(coroutine)


def fetch(dsn: str, sql: str, *args) -> list:
    """As the owner, so a check sees every row whatever the login may read."""
    return run(owner_fetch(dsn, sql, *args))


def one(dsn: str, sql: str, *args):
    [row] = fetch(dsn, sql, *args)
    return row


def counts(dsn: str) -> tuple[int, int]:
    row = one(
        dsn,
        "SELECT (SELECT count(*) FROM public.desired_state) AS desired, "
        "(SELECT count(*) FROM public.activity_log) AS logged",
    )
    return row["desired"], row["logged"]


@pytest.fixture(scope="module")
def dsn():
    value = private_dsn()
    run(install(value))
    run(owner_execute(value, PREPARE))
    run(owner_execute(value, armed_context_sql()))
    run(owner_execute(value, M2B_GRANTS))
    return value


@pytest.fixture(scope="module")
def client(dsn):
    """The operator API on a pool of the dashboard login: create_pool's own init and setup, on the private server."""

    async def dashboard_pool(_settings):
        return await asyncpg.create_pool(
            dsn,
            user=LOGIN,
            min_size=0,
            max_size=operator_api.POOL_MAX_SIZE,
            init=operator_api.attest_connection,
            setup=operator_api.setup_connection,
        )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(operator_api, "create_pool", dashboard_pool)
        with TestClient(operator_api.create_app(settings=SETTINGS)) as client:
            yield client


def sign_in(client, username: str, role: str) -> dict[str, str]:
    created = client.post(
        "/internal/sessions", headers=with_bearer(), json={"username": username, "display_name": None, "role": role}
    )
    assert created.status_code == 201, created.text
    return {**with_bearer(), "X-Dashboard-Session": created.json()["token"]}


@pytest.fixture(scope="module")
def operator(client) -> dict[str, str]:
    return sign_in(client, "jason", "operator")


@pytest.fixture(scope="module")
def viewer(client) -> dict[str, str]:
    return sign_in(client, "caretaker", "viewer")


@pytest.fixture(autouse=True)
def empty_tables(dsn):
    run(
        owner_execute(
            dsn,
            "DELETE FROM public.desired_state; DELETE FROM public.activity_log; DELETE FROM public.alert_log; "
            "DELETE FROM public.crop_band_anchors; DELETE FROM public.setpoint_snapshot",
        )
    )


def keyed(headers: dict[str, str]) -> dict[str, str]:
    return {**headers, "Idempotency-Key": str(uuid.uuid4())}


def add_house_band(dsn: str) -> dict[str, int]:
    """The house VPD target's four rows, season all, as production holds them: anchor → id."""
    rows = fetch(
        dsn,
        "INSERT INTO public.crop_band_anchors (crop_type, season, series, anchor, value, width_below, width_above) "
        "SELECT 'house', 'all', 'vpd_target', anchor, 0.9, 0.2, 0.3 FROM unnest(ARRAY['sr', 'sm', 'ss', 'mid']) AS anchor "
        "RETURNING id, anchor",
    )
    return {row["anchor"]: row["id"] for row in rows}


def add_repeats(dsn: str, alert_type: str, sensor_id: str, *, count: int = 3) -> list[int]:
    """Repeats of one alert, oldest first, severity critical: its merged row's id is the first."""
    return [
        run(add_alert(dsn, alert_type, sensor_id, minutes_ago=10 * (count - index), severity="critical"))
        for index in range(count)
    ]


def alert_rows(dsn: str, alert_type: str) -> list:
    return fetch(
        dsn,
        "SELECT id, disposition, acknowledged_by, notes, resolved_by, resolution, resolved_at IS NOT NULL AS resolved "
        "FROM public.alert_log WHERE alert_type = $1 ORDER BY id",
        alert_type,
    )


# ── the login ───────────────────────────────────────────────────────────────


def test_the_pool_attests_as_the_dashboard_login_under_these_grants(client):
    ready = client.get("/readyz")
    assert ready.status_code == 200, ready.text


# ── settings ────────────────────────────────────────────────────────────────


def test_the_same_write_twice_creates_one_row_and_another_payload_is_request_id_reused(client, dsn, operator):
    key = str(uuid.uuid4())
    headers = {**operator, "Idempotency-Key": key}
    body = {"value": 1.53, "manual": True, "reason": "campaign"}
    first = client.put(SETTING, headers=headers, json=body)
    assert first.status_code == 200, first.text
    assert "idempotency-replayed" not in first.headers
    stored = first.json()
    assert (stored["value"], stored["snapped_from"], stored["manual"], stored["manual_before"], stored["state"]) == (
        1.55,
        1.53,
        True,
        False,
        "requested",
    )
    assert stored["actor"] == {"source": "dashboard", "name": "jason"}

    again = client.put(SETTING, headers=headers, json=body)
    assert again.status_code == 200, again.text
    assert again.headers["idempotency-replayed"] == "true" and again.json() == stored
    assert counts(dsn) == (1, 1)

    row = one(
        dsn,
        "SELECT value, manual, source, actor, reason FROM public.desired_state WHERE id = $1",
        stored["id"],
    )
    assert tuple(row) == (1.55, True, "dashboard", "jason", "campaign")
    logged = one(
        dsn,
        "SELECT source, actor, action, target, reason, request_id, request_sha256, experiment_id, detail::text AS detail "
        "FROM public.activity_log",
    )
    assert tuple(logged)[:6] == ("dashboard", "jason", "setting.write", KPA, "campaign", uuid.UUID(key))
    assert logged["request_sha256"] == activity.request_hash(
        "API-10", KPA, {"value": 1.53, "manual": True, "reason": "campaign"}
    )
    assert logged["experiment_id"] is None
    assert json.loads(logged["detail"])["manual"] == {"before": False, "after": True}

    for other in (
        client.put(SETTING, headers=headers, json={**body, "value": 1.6}),
        client.delete(SETTING, headers=headers),
        client.post("/v1/alerts/1/ack", headers=headers),
    ):
        refused = assert_problem(other, 422, "request_id_reused")
        assert (refused["retryable"], refused["request_id"]) == (False, key)
    assert counts(dsn) == (1, 1)


def test_two_concurrent_sends_of_one_write_store_one_row(client, dsn, operator):
    key = keyed(operator)

    async def both():
        sends = (asyncio.to_thread(client.put, SETTING, headers=key, json={"value": 1.5}) for _ in range(2))
        return await asyncio.gather(*sends)

    first, second = run(both())
    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
    assert first.json()["id"] == second.json()["id"]
    assert sorted(answer.headers.get("idempotency-replayed", "") for answer in (first, second)) == ["", "true"]
    assert counts(dsn) == (1, 1)


def test_without_a_key_each_write_runs_with_request_id_null(client, dsn, operator):
    for _ in range(2):
        assert client.put(SETTING, headers=operator, json={"value": 1.5}).status_code == 200
    assert counts(dsn) == (2, 2)
    assert {tuple(row) for row in fetch(dsn, "SELECT request_id, request_sha256 FROM public.activity_log")} == {
        (None, None)
    }


def test_a_rest_write_that_omits_manual_stores_true_and_a_release_clears_it(client, dsn, operator):
    written = client.put(SETTING, headers=operator, json={"value": 1.5})
    assert written.status_code == 200, written.text
    assert written.json()["manual"] is True
    assert one(dsn, "SELECT manual FROM public.desired_state WHERE id = $1", written.json()["id"])["manual"] is True

    kept = client.put(SETTING, headers=operator, json={"value": 1.5, "manual": False}).json()
    assert (kept["manual"], kept["manual_before"]) == (False, True)
    assert one(dsn, "SELECT manual FROM public.desired_state WHERE id = $1", kept["id"])["manual"] is False

    key = keyed(operator)
    released = client.delete(SETTING, headers=key, params={"reason": "back to Iris"})
    assert released.status_code == 200, released.text
    body = released.json()
    assert (body["value"], body["manual"], body["manual_before"], body["state"]) == (None, False, False, "released")
    assert tuple(
        one(dsn, "SELECT value, manual, source, actor, reason FROM public.desired_state WHERE id = $1", body["id"])
    ) == (None, False, "dashboard", "jason", "back to Iris")
    again = client.delete(SETTING, headers=key, params={"reason": "back to Iris"})
    assert again.headers["idempotency-replayed"] == "true" and again.json() == body
    assert counts(dsn) == (3, 3)
    assert (
        one(dsn, "SELECT action FROM public.activity_log WHERE (detail->>'desired_id')::bigint = $1", body["id"])[
            "action"
        ]
        == "setting.release"
    )


def test_a_crop_band_write_updates_its_anchor_rows_in_the_transaction_of_its_desired_row(client, dsn, operator):
    rows = add_house_band(dsn)
    key = keyed(operator)
    response = client.put(f"/v1/settings/{BAND}", headers=key, json={"value": 0.93})
    assert response.status_code == 200, response.text
    desired = response.json()
    assert (desired["value"], desired["manual"]) == (0.93, True)

    anchors = {
        row["anchor"]: row
        for row in fetch(dsn, "SELECT anchor, value, xmin::text AS xid FROM public.crop_band_anchors")
    }
    assert {anchor: row["value"] for anchor, row in anchors.items()} == {"sr": 0.9, "sm": 0.93, "ss": 0.9, "mid": 0.9}
    logged = one(dsn, "SELECT xmin::text AS xid, detail::text AS detail FROM public.activity_log")
    assert json.loads(logged["detail"])["anchor_rows"] == [rows["sm"]]
    stored = one(dsn, "SELECT xmin::text AS xid FROM public.desired_state WHERE id = $1", desired["id"])
    assert stored["xid"] == logged["xid"] == anchors["sm"]["xid"] != anchors["sr"]["xid"]

    # A replay moves nothing, even after the row has moved on.
    run(owner_execute(dsn, f"UPDATE public.crop_band_anchors SET value = 1.1 WHERE id = {rows['sm']}"))
    assert client.put(f"/v1/settings/{BAND}", headers=key, json={"value": 0.93}).status_code == 200
    assert one(dsn, "SELECT value FROM public.crop_band_anchors WHERE id = $1", rows["sm"])["value"] == 1.1
    assert counts(dsn) == (1, 1)


def test_2_6_is_out_of_range_with_the_bounds_and_the_current_value_and_stores_nothing(client, dsn, operator):
    assert client.put(SETTING, headers=operator, json={"value": 1.6}).status_code == 200
    key = str(uuid.uuid4())
    response = client.put(SETTING, headers={**operator, "Idempotency-Key": key}, json={"value": 2.6})
    assert assert_problem(response, 422, "out_of_range") == {
        "type": "about:blank",
        "title": "Out of range",
        "status": 422,
        "detail": "Must be 0.50-2.50 kPa",
        "code": "out_of_range",
        "retryable": False,
        "field": "value",
        "min": 0.5,
        "max": 2.5,
        "current": 1.6,
        "request_id": key,
    }
    assert counts(dsn) == (1, 1)


def test_an_unregistered_or_read_only_param_is_refused_and_stores_nothing(client, dsn, operator):
    for code, path in (
        ("unknown_target", "/v1/settings/no_such_setting"),
        ("read_only", "/v1/settings/sw_onchip_band_enabled"),
        # A crop-band setting while crop_band_anchors holds none of its rows.
        ("read_only", f"/v1/settings/{BAND}"),
    ):
        for response in (client.put(path, headers=operator, json={"value": 1}), client.delete(path, headers=operator)):
            assert assert_problem(response, 422, code)["retryable"] is False
    assert counts(dsn) == (0, 0)


def test_a_switch_write_takes_only_0_or_1(client, dsn, operator):
    written = client.put(f"/v1/settings/{SWITCH}", headers=operator, json={"value": 1})
    assert written.status_code == 200, written.text
    assert (written.json()["value"], written.json()["state"]) == (1.0, "requested")
    refused = assert_problem(
        client.put(f"/v1/settings/{SWITCH}", headers=operator, json={"value": 0.5}), 422, "out_of_range"
    )
    assert (refused["min"], refused["max"], refused["current"]) == (0, 1, 1.0)
    assert counts(dsn) == (1, 1)


def test_a_replay_reads_the_state_as_of_now_so_a_readback_turns_requested_into_confirmed(client, dsn, operator):
    key = keyed(operator)
    first = client.put(SETTING, headers=key, json={"value": 1.53})
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "requested"
    run(
        owner_execute(
            dsn,
            f"INSERT INTO public.setpoint_snapshot (ts, parameter, value) VALUES (clock_timestamp(), '{KPA}', 1.55)",
        )
    )
    again = client.put(SETTING, headers=key, json={"value": 1.53})
    assert again.status_code == 200, again.text
    body = again.json()
    assert (body["id"], body["state"], body["state_reason"]) == (first.json()["id"], "confirmed", None)
    assert body["confirmed_at"] is not None and again.headers["idempotency-replayed"] == "true"


def test_the_insert_trigger_is_the_only_wake_and_nothing_reaches_setpoint_changes(client, dsn, operator):
    async def check() -> list[str]:
        owner = await connect(dsn, OWNER)
        wakes: list[str] = []
        await owner.add_listener("desired_state_changed", lambda _conn, _pid, _channel, payload: wakes.append(payload))
        try:
            key = keyed(operator)
            for call in (
                lambda: client.put(SETTING, headers=key, json={"value": 1.5}),
                lambda: client.put(SETTING, headers=key, json={"value": 1.5}),  # a replay inserts nothing
                lambda: client.delete(SETTING, headers=operator),
            ):
                response = await asyncio.to_thread(call)
                assert response.status_code == 200, response.text
            for _ in range(50):
                if len(wakes) >= 2:
                    break
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.2)  # room for a third wake, which must not come
        finally:
            await owner.close()
        return wakes

    assert run(check()) == ["", ""]
    assert one(dsn, "SELECT count(*) AS changes FROM public.setpoint_changes")["changes"] == 0


# ── alerts ──────────────────────────────────────────────────────────────────


def test_ack_acts_on_every_repeat_and_returns_the_merged_row(client, dsn, operator):
    ids = add_repeats(dsn, "relay_stuck", "equipment.mister_south")
    key = keyed(operator)
    response = client.post(f"/v1/alerts/{ids[1]}/ack", headers=key, json={"note": "checked"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["count"], body["status"]) == (ids[0], 3, "acknowledged")
    assert (body["acknowledged_by"], body["notes"]) == ("jason", "checked")
    assert {(row["disposition"], row["acknowledged_by"], row["notes"]) for row in alert_rows(dsn, "relay_stuck")} == {
        ("acknowledged", "jason", "checked")
    }
    logged = one(
        dsn, "SELECT source, actor, action, target, detail::text AS detail, xmin::text AS xid FROM public.activity_log"
    )
    assert tuple(logged)[:4] == ("dashboard", "jason", "alert.ack", str(ids[1]))
    assert json.loads(logged["detail"])["ids"] == ids
    # One transaction: every repeat's update and the activity row.
    assert {row["xid"] for row in fetch(dsn, "SELECT xmin::text AS xid FROM public.alert_log")} == {logged["xid"]}

    again = client.post(f"/v1/alerts/{ids[1]}/ack", headers=key, json={"note": "checked"})
    assert again.status_code == 200 and again.headers["idempotency-replayed"] == "true"
    assert again.json() == body
    assert_problem(
        client.post(f"/v1/alerts/{ids[1]}/ack", headers=key, json={"note": "other"}), 422, "request_id_reused"
    )
    assert counts(dsn) == (0, 1)


@pytest.mark.parametrize("alert_type", ["planner_tool_dependency_failed", "relay_stuck"])
def test_resolve_is_never_refused_and_acts_on_every_repeat(client, dsn, operator, alert_type):
    ids = add_repeats(dsn, alert_type, "mcp.scorecard", count=2)
    response = client.post(f"/v1/alerts/{ids[0]}/resolve", headers=keyed(operator), json={"resolution": "by hand"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["count"], body["status"]) == (ids[0], 2, "resolved")
    assert (body["resolved_by"], body["resolution"]) == ("jason", "by hand")
    rows = alert_rows(dsn, alert_type)
    assert {(row["disposition"], row["resolved_by"], row["resolution"], row["resolved"]) for row in rows} == {
        ("resolved", "jason", "by hand", True)
    }
    logged = one(dsn, "SELECT action, detail::text AS detail FROM public.activity_log")
    assert logged["action"] == "alert.resolve" and json.loads(logged["detail"])["ids"] == ids
    # With no note and on a row already resolved it still answers, with the resolved merged row.
    answered = client.post(f"/v1/alerts/{ids[0]}/resolve", headers=operator)
    assert answered.status_code == 200, answered.text
    assert answered.json()["status"] == "resolved"


@pytest.mark.parametrize("action", ["ack", "resolve"])
def test_an_unknown_alert_id_is_422_unknown_target(client, dsn, operator, action):
    refused = assert_problem(client.post(f"/v1/alerts/2000000000/{action}", headers=operator), 422, "unknown_target")
    assert (refused["field"], refused["retryable"]) == ("id", False)
    assert counts(dsn) == (0, 0)


# ── viewers ─────────────────────────────────────────────────────────────────


def test_a_viewer_s_write_returns_403_and_changes_nothing(client, dsn, operator, viewer):
    add_house_band(dsn)
    ids = add_repeats(dsn, "relay_stuck", "equipment.mister_south", count=2)
    assert client.put(SETTING, headers=operator, json={"value": 1.5}).status_code == 200

    def state() -> tuple:
        return (
            counts(dsn),
            [tuple(row) for row in alert_rows(dsn, "relay_stuck")],
            [tuple(row) for row in fetch(dsn, "SELECT id, value FROM public.crop_band_anchors ORDER BY id")],
        )

    before = state()
    for method, path, body in (
        ("PUT", SETTING, {"value": 1.6}),
        ("PUT", f"/v1/settings/{BAND}", {"value": 0.93}),
        ("DELETE", SETTING, None),
        ("POST", f"/v1/alerts/{ids[0]}/ack", {"note": "viewer"}),
        ("POST", f"/v1/alerts/{ids[0]}/resolve", {"resolution": "viewer"}),
    ):
        assert_problem(client.request(method, path, headers=keyed(viewer), json=body), 403, "forbidden")
    assert state() == before
    assert client.get("/v1/me", headers=viewer).json()["role"] == "viewer"  # the same session reads
