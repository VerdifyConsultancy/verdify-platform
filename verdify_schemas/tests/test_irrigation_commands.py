"""verdify_schemas.irrigation commands (VD-64, #1177): IrrigationCommandRow against migration 283 and Command
against the committed OpenAPI ``Command``."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest
import yaml
from pydantic import ValidationError

from verdify_schemas import irrigation as irr

ROOT = Path(__file__).resolve().parents[2]
M3 = ROOT / "db/migrations/283-irrigation-tables.sql"
OPENAPI = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
NOW = datetime(2026, 11, 2, 18, tzinfo=UTC)


def m3_columns(table: str) -> list[str]:
    body = M3.read_text().split(f"CREATE TABLE public.{table} (", 1)[1].split("\n);", 1)[0]
    words = (line.split()[0] for line in body.splitlines() if line.strip())
    return [word for word in words if word not in {"CHECK", "UNIQUE", "PRIMARY", "CONSTRAINT"}]


def stored(**overrides) -> dict:
    return {
        "command_id": uuid.uuid4(),
        "greenhouse_id": "vallery",
        "kind": "stop",
        "line_id": None,
        "args": '{"bridge": true}',
        "status": "pending",
        "fail_reason": None,
        "source": "dashboard",
        "actor": "jason",
        "reason": "hose burst",
        "created_at": NOW,
        "sent_at": None,
        **overrides,
    }


def test_the_row_model_is_the_irrigation_command_table():
    assert list(irr.IrrigationCommandRow.model_fields) == m3_columns("irrigation_command")
    body = M3.read_text().split("CREATE TABLE public.irrigation_command (", 1)[1].split("\n);", 1)[0]
    assert "kind          text NOT NULL CHECK (kind IN ('run', 'stop', 'clear_fault', 'override'))" in body
    assert "status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed'))" in body
    assert irr.COMMAND_KINDS == ("run", "stop", "clear_fault", "override")
    assert irr.COMMAND_STATUSES == ("pending", "sent", "failed")


def test_the_command_model_is_the_openapi_command():
    spec = OPENAPI["components"]["schemas"]["Command"]
    assert set(irr.Command.model_fields) == set(spec["properties"])
    required = {name for name, field in irr.Command.model_fields.items() if field.is_required()}
    assert required == set(spec["required"])
    assert list(get_args(irr.CommandKind)) == spec["properties"]["kind"]["enum"]
    assert list(get_args(irr.CommandStatus)) == spec["properties"]["status"]["enum"]
    actor = OPENAPI["components"]["schemas"]["Actor"]
    assert set(irr.CommandActor.model_fields) == set(actor["properties"]) == set(actor["required"])


def test_a_stored_row_reads_as_its_command():
    row = irr.IrrigationCommandRow.from_row(stored())
    assert row.args == {"bridge": True}
    command = irr.Command.from_row(row)
    assert command.model_dump(mode="json") == {
        "command_id": str(row.command_id),
        "kind": "stop",
        "line_id": None,
        "args": {"bridge": True},
        "status": "pending",
        "actor": {"source": "dashboard", "name": "jason"},
        "reason": "hose burst",
        "created_at": "2026-11-02T18:00:00Z",
        "sent_at": None,
    }
    assert irr.Command.from_row(stored()).kind == "stop"
    with pytest.raises(ValueError, match="stored row"):
        irr.Command.from_row(stored(command_id=None))


def test_status_and_sent_at_go_together():
    sent = irr.IrrigationCommandRow.from_row(stored(status="sent", sent_at=NOW, args={"relays_on": ["drip_wall"]}))
    assert sent.args == {"relays_on": ["drip_wall"]}
    with pytest.raises(ValidationError, match="sent_at"):
        irr.IrrigationCommandRow.from_row(stored(status="sent"))
    with pytest.raises(ValidationError, match="no sent_at"):
        irr.IrrigationCommandRow.from_row(stored(sent_at=NOW))
    failed = irr.IrrigationCommandRow.from_row(stored(status="failed", sent_at=NOW, fail_reason="too old"))
    assert failed.status == "failed"  # a sent press that never reached the transport keeps its sent_at


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "press"},
        {"status": "done"},
        {"line_id": "L12"},
        {"source": "agent:"},
        {"actor": ""},
        {"reason": "r" * 501},
        {"created_at": datetime(2026, 11, 2, 18)},
    ],
)
def test_a_row_outside_the_table_contract_is_refused(change):
    with pytest.raises(ValidationError):
        irr.IrrigationCommandRow.from_row(stored(**change))


def test_the_bridge_constants():
    assert irr.BRIDGE_RUN_LINE == "L01"
    assert irr.COMMAND_SEND_WINDOW_S == irr.STOP_RESEND_WINDOW_S == 120
    assert json.loads(json.dumps(irr.Command.model_json_schema()))["properties"]["line_id"]["anyOf"][0]["enum"] == list(
        irr.LINE_IDS
    )
