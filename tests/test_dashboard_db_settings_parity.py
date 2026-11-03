"""MCP set_setting and API-10 store the same rows on a private TimescaleDB (#1150, #1171).

jvallery/agents#5994's campaign step starts a fresh server for each tests/test_dashboard_db_*.py file and sets
VERDIFY_ISOLATED_TEST_DSN; without it this file skips. The database and the operator API are
tests/test_dashboard_db_writes.py's. The tool runs through mcp/server.py under the FastMCP stub, as in
tests/test_dashboard_db_mcp_settings.py, on an owner connection: those two files hold each surface to its production
grants, and this one compares what the surfaces store.

Each write starts from the same state: one runs, its rows are read, the tables go back, then the other runs. Otherwise
the second write's ``before`` and ``manual.before`` would differ. Every column is compared (``SELECT *``, so a new
column joins by itself) except row ids, the ones inside ``detail``, timestamps, ``source``, ``actor`` and
``request_id``. ``request_sha256`` matches because MCP hashes the REST twin's operation id (#1147).
"""

from __future__ import annotations

import json
import uuid

import pytest
from test_dashboard_db_282 import OWNER, connect
from test_dashboard_db_services import owner_execute
from test_dashboard_db_writes import (  # noqa: F401
    KPA,
    SETTING,
    client,
    dsn,
    empty_tables,
    fetch,
    keyed,
    operator,
    run,
)
from test_mcp_settings_tools import body, run_as, server, steward  # noqa: F401

REASON = "drier afternoon"
IGNORED_DESIRED = {"id", "created_at", "source", "actor"}
IGNORED_ACTIVITY = {"id", "ts", "source", "actor", "request_id"}
IGNORED_DETAIL = {"ref", "desired_id"}


def stored(dsn_value: str) -> tuple[dict, dict]:
    """The newest desired_state and activity_log rows, every column as the table holds it."""
    [desired] = fetch(dsn_value, "SELECT * FROM public.desired_state ORDER BY id DESC LIMIT 1")
    [logged] = fetch(dsn_value, "SELECT * FROM public.activity_log ORDER BY id DESC LIMIT 1")
    return dict(desired), {**dict(logged), "detail": json.loads(logged["detail"])}


def compared(row: dict, ignored: set[str]) -> dict:
    return {column: value for column, value in row.items() if column not in ignored}


@pytest.mark.parametrize("held", [None, 1.7], ids=["nothing_held", "a_person_holds_it"])
def test_set_setting_and_api_10_store_the_same_rows(client, dsn, operator, server, monkeypatch, held):  # noqa: F811
    async def db():
        return await connect(dsn, OWNER)

    monkeypatch.setattr(server, "_db", db)

    def same_start():
        run(owner_execute(dsn, "DELETE FROM public.desired_state; DELETE FROM public.activity_log"))
        if held is not None:
            seeded = client.put(
                SETTING, headers=keyed(operator), json={"value": held, "manual": True, "reason": "held"}
            )
            assert seeded.status_code == 200, seeded.text

    same_start()
    tool = run_as(
        server,
        steward(server),
        server.set_setting(KPA, 1.53, manual=True, reason=REASON, request_id=str(uuid.uuid4())),
    )
    assert tool.isError is False and body(tool)["value"] == 1.55
    mcp_desired, mcp_logged = stored(dsn)

    same_start()
    rest = client.put(SETTING, headers=keyed(operator), json={"value": 1.53, "manual": True, "reason": REASON})
    assert rest.status_code == 200, rest.text
    rest_desired, rest_logged = stored(dsn)

    # Two surfaces, with the differences the comparison ignores.
    assert (mcp_desired["source"], mcp_desired["actor"]) == ("agent:steward", "steward")
    assert (rest_desired["source"], rest_desired["actor"]) == ("dashboard", "jason")
    assert (mcp_logged["source"], mcp_logged["actor"]) == ("agent:steward", "steward")
    assert (rest_logged["source"], rest_logged["actor"]) == ("dashboard", "jason")
    assert mcp_logged["request_id"] != rest_logged["request_id"]
    assert mcp_logged["detail"]["desired_id"] == mcp_desired["id"]
    assert rest_logged["detail"]["desired_id"] == rest_desired["id"]

    # The state each started from reached the rows, so the comparison below holds real values.
    assert mcp_desired["value"] == 1.55 and mcp_desired["manual"] is True and mcp_desired["reason"] == REASON
    detail = mcp_logged["detail"]
    assert (detail["before"], detail["value"], detail["requested"]) == (held, 1.55, 1.53)
    assert detail["manual"] == {"before": held is not None, "after": True}
    assert mcp_logged["action"] == "setting.write" and mcp_logged["target"] == KPA
    assert len(mcp_logged["request_sha256"]) == 32

    assert compared(mcp_desired, IGNORED_DESIRED) == compared(rest_desired, IGNORED_DESIRED)
    mcp_row = compared(mcp_logged, IGNORED_ACTIVITY)
    rest_row = compared(rest_logged, IGNORED_ACTIVITY)
    assert compared(mcp_row.pop("detail"), IGNORED_DETAIL) == compared(rest_row.pop("detail"), IGNORED_DETAIL)
    assert mcp_row == rest_row
