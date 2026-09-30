"""Explicit temporary C1 authority cannot weaken guards or replay stale work."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingestor"))
from tasks import bounded_reconcile as bounded
from tasks import c1_overlay as overlay
from tasks import dispatcher

from verdify_schemas.c1_grid_projection import project_c1_grid_state
from verdify_schemas.component_executor import GRID_REVISION


@pytest.fixture
def packet():
    values = json.loads((ROOT / "research/planner-efficacy/baseline/planner-switchback-v2-profiles.json").read_text())[
        "profiles"
    ]["baseline"]["values"]
    values["mister_engage_delay_s"] = 45.0
    now = datetime.now(UTC)
    inputs = {"iris": [{"parameter": "mister_engage_delay_s", "plan_id": "real-source", "value": 45}]}
    preview = {
        "captured_at": now.isoformat(),
        "identity": {"grid_revision": GRID_REVISION, "connection_generation": 3},
        "base_values": values,
        "base_inputs": inputs,
        "base_inputs_sha256": bounded._digest(inputs),
    }
    decisions = {
        "mister_engage_delay_s": {
            "from": 45.0,
            "to": 30.0,
            "rationale": "Preserve current moisture relief upper bound45seconds",
        }
    }
    sheet = {
        "schema": "verdify-c1-qualification-worksheet-v1",
        "worksheet_id": str(uuid4()),
        "expires_at": (now + timedelta(minutes=6)).isoformat(),
        "preview": preview,
        "decisions": decisions,
        "projection": project_c1_grid_state(values, decisions),
    }
    return sheet, preview, now


def test_explicit_projection_rechecks_actual_guard_after_choice(packet):
    sheet, preview, now = packet
    result = overlay.validate_worksheet(
        sheet, preview, now=now, physics=dispatcher._validate_physics, guardrails={"mister_engage_delay_s": 45}
    )
    assert result["proposed_values"]["mister_engage_delay_s"] == 30
    sheet["decisions"]["mister_engage_delay_s"]["to"] = 60
    sheet["projection"] = project_c1_grid_state(preview["base_values"], sheet["decisions"])
    with pytest.raises(ValueError, match="moisture guardrail"):
        overlay.validate_worksheet(
            sheet, preview, now=now, physics=dispatcher._validate_physics, guardrails={"mister_engage_delay_s": 45}
        )


@pytest.mark.parametrize("changed", ["identity", "base_values", "base_inputs_sha256"])
def test_any_bound_identity_or_policy_change_removes_authority(packet, changed):
    sheet, preview, now = packet
    current = json.loads(json.dumps(preview))
    if changed == "identity":
        current[changed]["connection_generation"] = 4
    elif changed == "base_values":
        current[changed]["cool_exit_hysteresis_f"] = 1.2
    else:
        current[changed] = "new-policy-hash"
    with pytest.raises(ValueError, match="base policy or identity changed"):
        overlay.validate_worksheet(sheet, current, now=now, physics=dispatcher._validate_physics, guardrails={})


def test_short_expiry_does_not_extend_for_capture(packet):
    sheet, preview, now = packet
    with pytest.raises(ValueError, match="expired"):
        overlay.validate_worksheet(
            sheet, preview, now=now + timedelta(minutes=6), physics=dispatcher._validate_physics, guardrails={}
        )


def test_restoration_uses_fresh_source_and_prioritizes_touched_fields_under_cap():
    fresh = {"mister_engage_delay_s": 45.0, "cool_exit_hysteresis_f": 1.93}
    readbacks = {"mister_engage_delay_s": 30.0, "cool_exit_hysteresis_f": 2.0}
    unrelated = [("other" + str(i), float(i)) for i in range(30)]
    stage = overlay._upsert(unrelated, fresh, readbacks)
    assert len(stage) == 12
    assert stage[:2] == tuple(fresh.items())
    assert not any(v == 30 for p, v in stage if p == "mister_engage_delay_s")


@pytest.mark.asyncio
async def test_native_failed_records_preserved_without_replaying(tmp_path):
    wid = str(uuid4())
    ts = datetime.now(UTC)
    bounded._write(
        tmp_path / overlay.STATE_NAME,
        {
            "worksheet_id": wid,
            "status": "inflight",
            "stage_started_at": ts.isoformat(),
            "touched": ["mister_engage_delay_s"],
        },
    )
    decision = overlay.Selection("send", (("mister_engage_delay_s", 30.0),), wid)
    record = {"parameter": "mister_engage_delay_s", "value": 30.0, "requested_at": ts}
    overlay.finish(tmp_path, decision, [record], [("mister_engage_delay_s", "transport_disconnected")])
    state = bounded._read(tmp_path / overlay.STATE_NAME)
    assert state["status"] == "halted"
    assert state["stages"][0]["failures"] == [["mister_engage_delay_s", "transport_disconnected"]]
    assert state["records"][0]["requested_at"] == ts.isoformat()
    assert state["qualification_claimed"] is False


@pytest.fixture
def runtime(packet, monkeypatch):
    from types import SimpleNamespace

    from tasks import component_experiment

    sheet, preview, now = packet
    monkeypatch.setenv("VERDIFY_GIT_SHA", "a" * 40)
    monkeypatch.setenv("HOSTNAME", "sole-writer-fixture")
    readbacks = dict(preview["base_values"])
    monkeypatch.setattr(
        component_experiment,
        "component_entity_grid_attestation",
        lambda: SimpleNamespace(firmware_revision="real-fixture-fw", grid_revision=GRID_REVISION),
    )
    monkeypatch.setattr(overlay.shared, "transport_readbacks_ready", lambda generation: True)
    monkeypatch.setattr(overlay.shared, "current_cfg_readbacks", lambda generation: readbacks)
    monkeypatch.setattr(overlay.shared, "writer_lease_strictly_held", lambda **kw: True)
    return sheet, preview, readbacks


async def admit(runtime, tmp_path):
    sheet, preview, readbacks = runtime
    args = dict(
        base_values=preview["base_values"],
        base_inputs=preview["base_inputs"],
        guardrails={"mister_engage_delay_s": 45},
        physics=dispatcher._validate_physics,
        state_dir=tmp_path,
        generation=3,
    )
    await overlay.choose(None, [], **args)
    sheet["preview"] = bounded._read(tmp_path / overlay.PREVIEW_NAME)
    bounded._write(tmp_path / overlay.WORKSHEET_NAME, sheet)
    return await overlay.choose(None, [], **args), args


@pytest.mark.asyncio
async def test_expiry_yields_fresh_policy_and_retains_native_failure(runtime, tmp_path):
    decision, args = await admit(runtime, tmp_path)
    assert decision.phase == "send"
    ts = datetime.now(UTC)
    overlay.finish(
        tmp_path,
        decision,
        [{"parameter": "mister_engage_delay_s", "value": 30.0, "requested_at": ts}],
        [("mister_engage_delay_s", "transport_disconnected")],
    )
    runtime[2]["mister_engage_delay_s"] = 30.0
    args["base_values"] = {**args["base_values"], "mister_engage_delay_s": 42.5}
    recovery = await overlay.choose(None, [], **args)
    assert recovery.phase == "yield"
    assert recovery.changes == (("mister_engage_delay_s", 42.5),)
    state = bounded._read(tmp_path / overlay.STATE_NAME)
    assert state["stages"][0]["failures"] == [["mister_engage_delay_s", "transport_disconnected"]]
    assert state["status"] == "restore_inflight"


@pytest.mark.asyncio
async def test_unknown_native_outcome_never_replayed_or_replaced(runtime, tmp_path):
    decision, args = await admit(runtime, tmp_path)
    original = (tmp_path / overlay.STATE_NAME).read_bytes()
    assert decision.phase == "send"
    again = await overlay.choose(None, [], **args)
    assert again.phase == "hold"
    assert (tmp_path / overlay.STATE_NAME).read_bytes() == original
    assert again.changes == ()


@pytest.mark.asyncio
async def test_missing_current_cfg_route_is_not_restore_authority(runtime, tmp_path):
    decision, args = await admit(runtime, tmp_path)
    del runtime[2]["mister_engage_delay_s"]
    again = await overlay.choose(None, [], **args)
    assert again.phase == "hold"
    assert again.changes == ()


@pytest.mark.asyncio
async def test_expired_queue_authority_fails_at_physical_chokepoint(runtime, tmp_path, monkeypatch):
    import asyncio

    import esp32_push as push

    decision, args = await admit(runtime, tmp_path)
    state = bounded._read(tmp_path / overlay.STATE_NAME)
    expired = datetime.now(UTC) - timedelta(seconds=1)
    state["worksheet"]["expires_at"] = expired.isoformat()
    bounded._write(tmp_path / overlay.STATE_NAME, state)
    bounded._write(tmp_path / overlay.WORKSHEET_NAME, state["worksheet"])
    client = object()
    monkeypatch.setattr(push, "_preflight_failure", lambda: None)
    monkeypatch.setattr(push.shared, "transport_generation", 3)
    monkeypatch.setitem(push.shared.esp32, "client", client)
    request = push._WriteRequest(
        changes=(("mister_engage_delay_s", 30.0, "number"),),
        future=asyncio.get_running_loop().create_future(),
        ready=asyncio.Event(),
        on_state=None,
        attempt=1,
        accepted_generation=3,
        accepted_client=client,
        command_versions=(1.0,),
        source_fence=lambda: overlay.physical_fence(tmp_path, decision),
    )
    result = await push._execute_one(request, 0)
    assert result.status == "failed"
    assert result.reason == "c1_worksheet_expired"


@pytest.mark.asyncio
async def test_replacement_worksheet_cannot_change_current_decisions(runtime, tmp_path):
    decision, args = await admit(runtime, tmp_path)
    overlay.finish(
        tmp_path,
        decision,
        [{"parameter": "mister_engage_delay_s", "value": 30.0, "requested_at": datetime.now(UTC)}],
        [],
    )
    runtime[2]["mister_engage_delay_s"] = 30.0
    worksheet = bounded._read(tmp_path / overlay.WORKSHEET_NAME)
    worksheet["decisions"]["mister_engage_delay_s"]["to"] = 60.0
    worksheet["projection"] = project_c1_grid_state(worksheet["preview"]["base_values"], worksheet["decisions"])
    bounded._write(tmp_path / overlay.WORKSHEET_NAME, worksheet)
    selected = await overlay.choose(None, [], **args)
    assert selected.phase == "yield"
    assert selected.reason == "immutable worksheet changed"
    assert selected.changes == (("mister_engage_delay_s", 45.0),)


@pytest.mark.asyncio
async def test_async_source_guard_runs_after_pacing_before_any_setter(monkeypatch):
    import asyncio

    import esp32_push as push

    events = []

    class Client:
        async def number_command(self, **kwargs):
            events.append("setter")

    client = Client()
    monkeypatch.setattr(push, "_preflight_failure", lambda: None)
    monkeypatch.setattr(push.shared, "transport_generation", 3)
    monkeypatch.setitem(push.shared.esp32, "client", client)
    monkeypatch.setitem(push.shared.esp32, "keys", {})

    async def pace():
        events.append("paced")

    async def guard():
        assert events == ["paced"]
        events.append("fresh-db-policy-changed")
        return "c1_source_policy_or_guardrail_changed"

    monkeypatch.setattr(push, "_pace_command", pace)
    request = push._WriteRequest(
        changes=(("mister_engage_delay_s", 30.0, "number"),),
        future=asyncio.get_running_loop().create_future(),
        ready=asyncio.Event(),
        on_state=None,
        attempt=1,
        accepted_generation=3,
        accepted_client=client,
        command_versions=(1.0,),
        source_guard=guard,
    )
    result = await push._execute_one(request, 0)
    assert result.status == "failed"
    assert result.reason == "c1_source_policy_or_guardrail_changed"
    assert events == ["paced", "fresh-db-policy-changed"]
