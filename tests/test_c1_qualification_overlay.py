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


def guard_packet(packet, *, source=1.11, selected=1.05):
    sheet, preview, now = packet
    preview["base_values"]["mister_all_kpa"] = source
    preview["base_values"]["mister_engage_kpa"] = 0.9
    sheet["decisions"]["mister_all_kpa"] = {
        "from": source,
        "to": selected,
        "rationale": "Fixed safe threshold under continuously recomputed solar cap",
    }
    sheet["projection"] = project_c1_grid_state(preview["base_values"], sheet["decisions"])
    return sheet, json.loads(json.dumps(preview)), now


def test_admitted_explicit_safe_guard_variation_keeps_original_projection_and_expiry(packet):
    sheet, current, now = guard_packet(packet)
    original = json.dumps(sheet, sort_keys=True)
    for minute, cap in [(1, 1.09), (2, 1.08)]:
        current["base_values"]["mister_all_kpa"] = cap
        result = overlay.validate_worksheet(
            sheet,
            current,
            now=now + timedelta(minutes=minute),
            physics=dispatcher._validate_physics,
            guardrails={"mister_all_kpa": cap},
            admitted=True,
        )
        assert result == sheet["projection"]
        assert result["proposed_values"]["mister_all_kpa"] == 1.05
        assert json.dumps(sheet, sort_keys=True) == original
    with pytest.raises(ValueError, match="expired"):
        overlay.validate_worksheet(
            sheet,
            current,
            now=now + timedelta(minutes=6),
            physics=dispatcher._validate_physics,
            guardrails={"mister_all_kpa": 1.08},
            admitted=True,
        )


def test_guard_variation_cannot_admit_stale_from_value(packet):
    sheet, current, now = guard_packet(packet)
    current["base_values"]["mister_all_kpa"] = 1.09
    with pytest.raises(ValueError, match="base_values"):
        overlay.validate_worksheet(
            sheet, current, now=now, physics=dispatcher._validate_physics, guardrails={"mister_all_kpa": 1.09}
        )


def test_unsafe_fixed_110_cannot_survive_fresh_109_cap(packet):
    sheet, current, now = guard_packet(packet, selected=1.10)
    current["base_values"]["mister_all_kpa"] = 1.09
    with pytest.raises(ValueError, match="moisture guardrail"):
        overlay.validate_worksheet(
            sheet,
            current,
            now=now,
            physics=dispatcher._validate_physics,
            guardrails={"mister_all_kpa": 1.09},
            admitted=True,
        )


def test_untouched_on_grid_cap_change_still_invalidates_entire_worksheet(packet):
    sheet, preview, now = packet
    preview["base_values"]["mister_all_kpa"] = 1.10
    preview["base_values"]["mister_engage_kpa"] = 0.9
    sheet["projection"] = project_c1_grid_state(preview["base_values"], sheet["decisions"])
    current = json.loads(json.dumps(preview))
    current["base_values"]["mister_all_kpa"] = 1.09
    with pytest.raises(ValueError, match="base_values"):
        overlay.validate_worksheet(
            sheet,
            current,
            now=now,
            physics=dispatcher._validate_physics,
            guardrails={"mister_all_kpa": 1.09},
            admitted=True,
        )


@pytest.mark.parametrize("caps", [{}, {"mister_all_kpa": float("nan")}, {"mister_all_kpa": 1.08}])
def test_guard_variation_requires_finite_cap_and_consistent_current_source(packet, caps):
    sheet, current, now = guard_packet(packet)
    current["base_values"]["mister_all_kpa"] = 1.09
    with pytest.raises(ValueError, match="base_values"):
        overlay.validate_worksheet(
            sheet, current, now=now, physics=dispatcher._validate_physics, guardrails=caps, admitted=True
        )


@pytest.mark.parametrize("changed", ["identity", "iris", "unselected_value"])
def test_admitted_guard_variation_does_not_relax_other_policy_bindings(packet, changed):
    sheet, current, now = guard_packet(packet)
    current["base_values"]["mister_all_kpa"] = 1.09
    if changed == "identity":
        current["identity"]["connection_generation"] = 4
    elif changed == "iris":
        current["base_inputs"]["iris"][0]["plan_id"] = "different-source"
        current["base_inputs_sha256"] = bounded._digest(current["base_inputs"])
    else:
        current["base_values"]["cool_exit_hysteresis_f"] = 1.2
    with pytest.raises(ValueError, match="base policy or identity changed"):
        overlay.validate_worksheet(
            sheet,
            current,
            now=now,
            physics=dispatcher._validate_physics,
            guardrails={"mister_all_kpa": 1.09},
            admitted=True,
        )


def test_explicit_nonguard_field_cannot_vary_even_with_a_spurious_cap(packet):
    sheet, current, now = guard_packet(packet)
    original = sheet["preview"]
    original["base_values"]["cool_stage2_over_high_f"] = 1.835
    sheet["decisions"]["cool_stage2_over_high_f"] = {"from": 1.835, "to": 1.8, "rationale": "Earlier cooling"}
    sheet["projection"] = project_c1_grid_state(original["base_values"], sheet["decisions"])
    current["base_values"]["cool_stage2_over_high_f"] = 1.836
    with pytest.raises(ValueError, match="base_values"):
        overlay.validate_worksheet(
            sheet,
            current,
            now=now,
            physics=dispatcher._validate_physics,
            guardrails={"cool_stage2_over_high_f": 1.836},
            admitted=True,
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
async def test_confirmed_admitted_guard_variation_reaches_active_without_rebinding(runtime, tmp_path):
    sheet, preview, readbacks = runtime
    guard_packet((sheet, preview, datetime.now(UTC)))
    readbacks.update(preview["base_values"])
    args = dict(
        base_values=preview["base_values"],
        base_inputs=preview["base_inputs"],
        guardrails={"mister_all_kpa": 1.11, "mister_engage_delay_s": 45},
        physics=dispatcher._validate_physics,
        state_dir=tmp_path,
        generation=3,
    )
    await overlay.choose(None, [], **args)
    sheet["preview"] = bounded._read(tmp_path / overlay.PREVIEW_NAME)
    bounded._write(tmp_path / overlay.WORKSHEET_NAME, sheet)
    decision = await overlay.choose(None, [], **args)
    assert decision.phase == "send"
    ts = datetime.now(UTC)
    records = [{"parameter": k, "value": v, "requested_at": ts} for k, v in decision.changes]
    overlay.finish(tmp_path, decision, records, [])
    readbacks.update(dict(decision.changes))
    args["base_values"] = {**args["base_values"], "mister_all_kpa": 1.09}
    args["guardrails"] = {**args["guardrails"], "mister_all_kpa": 1.09}

    class ConfirmedConnection:
        async def fetchrow(self, query, requested_at, parameter):
            assert requested_at == ts and parameter in dict(decision.changes)
            return {"delivery_status": "confirmed", "confirmed_at": datetime.now(UTC)}

    active = await overlay.choose(ConfirmedConnection(), [], **args)
    assert active.phase == "active" and active.changes == ()
    state = bounded._read(tmp_path / overlay.STATE_NAME)
    assert state["worksheet"] == sheet
    assert state["stages"][0]["records"] == [{**r, "requested_at": ts.isoformat()} for r in records]
    assert state["qualification_claimed"] is False


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


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["attestation", "canonical_readbacks"])
async def test_unadmitted_c1_readiness_cannot_hold_ordinary_dispatch(tmp_path, monkeypatch, missing):
    from types import SimpleNamespace

    import shared
    from tasks import component_experiment

    monkeypatch.setattr(
        component_experiment,
        "component_entity_grid_attestation",
        lambda: None if missing == "attestation" else SimpleNamespace(),
    )
    monkeypatch.setattr(shared, "transport_readbacks_ready", lambda generation: True)
    monkeypatch.setattr(shared, "current_cfg_readbacks", lambda generation: {})
    result = await overlay.choose(
        None,
        [("mister_engage_delay_s", 45.0)],
        base_values={},
        base_inputs={},
        guardrails={},
        physics=dispatcher._validate_physics,
        state_dir=tmp_path,
        generation=3,
    )
    assert result.phase == "ordinary"
    assert not (tmp_path / overlay.STATE_NAME).exists()


@pytest.mark.asyncio
async def test_malformed_unadmitted_worksheet_has_no_ordinary_authority(tmp_path):
    (tmp_path / overlay.WORKSHEET_NAME).write_text("{invalid")
    result = await overlay.choose(
        None,
        [],
        base_values={},
        base_inputs={},
        guardrails={},
        physics=dispatcher._validate_physics,
        state_dir=tmp_path,
        generation=3,
    )
    assert result.phase == "ordinary"
    assert not (tmp_path / overlay.STATE_NAME).exists()


@pytest.mark.asyncio
async def test_unreadable_existing_outcomes_hold_without_replaying(tmp_path):
    (tmp_path / overlay.STATE_NAME).write_text("{invalid")
    result = await overlay.choose(
        None,
        [],
        base_values={},
        base_inputs={},
        guardrails={},
        physics=dispatcher._validate_physics,
        state_dir=tmp_path,
        generation=3,
    )
    assert result.phase == "hold"
    assert (tmp_path / overlay.STATE_NAME).read_text() == "{invalid"


def test_fractional_ordinary_restore_requires_native_confirmation_before_equivalence():
    name = "min_fog_on_s"
    target, observed = 63.75, 63.0
    assert dispatcher.readback_values_equivalent(name, observed, target)
    assert overlay._upsert([], {name: target}, {name: observed}) == ((name, target),)
    assert overlay._upsert([], {name: target}, {name: observed}, confirmed_ordinary={name: target}) == ()
    changed = 63.8
    assert overlay._upsert([], {name: changed}, {name: observed}, confirmed_ordinary={name: target}) == (
        (name, changed),
    )


def test_grid_projection_comparison_never_uses_ordinary_fractional_equivalence():
    name = "min_fog_on_s"
    assert overlay._upsert([], {name: 60.0}, {name: 59.0}) == ((name, 60.0),)


@pytest.fixture
def full_vector(runtime):
    sheet, preview, readbacks = runtime
    choices = {
        "cool_exit_hysteresis_f": (1.96, 2.0),
        "cool_stage2_over_high_f": (1.73, 1.7),
        "fog_escalation_kpa": (0.25, 0.2),
        "min_fog_off_s": (72.0, 60.0),
        "min_fog_on_s": (59.25, 60.0),
        "mister_all_delay_s": (93.0, 90.0),
        "mister_all_kpa": (1.154, 1.15),
        "mister_engage_delay_s": (46.5, 30.0),
        "mister_engage_kpa": (0.9540000000000001, 0.95),
        "mister_pulse_gap_s": (41.25, 40.0),
        "mister_vpd_weight": (1.65, 1.5),
        "temp_hysteresis": (1.96, 2.0),
        "vpd_hysteresis": (0.20750000000000002, 0.2),
        "vpd_watch_dwell_s": (67.5, 60.0),
    }
    sheet["schema"] = "verdify-c1-qualification-worksheet-v2"
    preview["base_values"].update({name: pair[0] for name, pair in choices.items()})
    readbacks.update(preview["base_values"])
    sheet["decisions"] = {
        name: {"from": pair[0], "to": pair[1], "rationale": "Explicit regression choice within the physical grid"}
        for name, pair in choices.items()
    }
    sheet["projection"] = project_c1_grid_state(preview["base_values"], sheet["decisions"])
    return runtime


def test_full_vector_v2_and_historical_v1_limits(full_vector):
    sheet, preview, _ = full_vector
    result = overlay.validate_worksheet(
        sheet, preview, now=datetime.now(UTC), physics=dispatcher._validate_physics, guardrails={}
    )
    assert result["field_count"] == 48 and result["explicit_selection_count"] == 14
    sheet["schema"] = "verdify-c1-qualification-worksheet-v1"
    with pytest.raises(ValueError, match="bundle exceeds"):
        overlay.validate_worksheet(
            sheet, preview, now=datetime.now(UTC), physics=dispatcher._validate_physics, guardrails={}
        )


async def admit_full_vector(runtime, tmp_path):
    sheet, preview, _ = runtime
    args = dict(
        base_values=preview["base_values"],
        base_inputs=preview["base_inputs"],
        guardrails={},
        physics=dispatcher._validate_physics,
        state_dir=tmp_path,
        generation=3,
    )
    await overlay.choose(None, [], **args)
    sheet["preview"] = bounded._read(tmp_path / overlay.PREVIEW_NAME)
    bounded._write(tmp_path / overlay.WORKSHEET_NAME, sheet)
    return await overlay.choose(None, [], **args), args


class StageConfirmation:
    def __init__(self):
        self.confirmed = False

    async def fetchrow(self, query, requested_at, parameter):
        return {
            "delivery_status": "confirmed" if self.confirmed else "sent",
            "confirmed_at": datetime.now(UTC) if self.confirmed else None,
        }


@pytest.mark.asyncio
async def test_full_vector_two_stages_require_confirmation_and_preserve_expiry(full_vector, tmp_path):
    first, args = await admit_full_vector(full_vector, tmp_path)
    sheet, _, readbacks = full_vector
    assert first.phase == "send" and len(first.changes) == 12
    conn = StageConfirmation()
    first_ts = datetime.now(UTC)
    overlay.finish(
        tmp_path, first, [{"parameter": k, "value": v, "requested_at": first_ts} for k, v in first.changes], []
    )
    readbacks.update(dict(first.changes))
    awaiting = await overlay.choose(conn, [], **args)
    assert awaiting.phase == "awaiting_confirmation" and not awaiting.changes
    assert bounded._read(tmp_path / overlay.STATE_NAME)["touched"] == sorted(k for k, _ in first.changes)
    conn.confirmed = True
    second = await overlay.choose(conn, [], **args)
    assert second.phase == "send" and len(second.changes) == 2
    assert not set(dict(first.changes)) & set(dict(second.changes))
    second_ts = datetime.now(UTC)
    overlay.finish(
        tmp_path, second, [{"parameter": k, "value": v, "requested_at": second_ts} for k, v in second.changes], []
    )
    readbacks.update(dict(second.changes))
    active = await overlay.choose(conn, [], **args)
    assert active.phase == "active" and not active.changes
    state = bounded._read(tmp_path / overlay.STATE_NAME)
    assert state["worksheet"] == sheet and state["worksheet"]["expires_at"] == sheet["expires_at"]
    assert len(state["stages"]) == 2 and len(state["touched"]) == 14
    assert state["qualification_claimed"] is False
    # Expiry never gains more authority; restoration uses fresh source in the same bounded stages.
    state["worksheet"]["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    bounded._write(tmp_path / overlay.STATE_NAME, state)
    bounded._write(tmp_path / overlay.WORKSHEET_NAME, state["worksheet"])
    restored = await overlay.choose(conn, [], **args)
    assert restored.phase == "yield" and len(restored.changes) == 12
    restore_ts = datetime.now(UTC)
    overlay.finish(
        tmp_path, restored, [{"parameter": k, "value": v, "requested_at": restore_ts} for k, v in restored.changes], []
    )
    readbacks.update(dict(restored.changes))
    remainder = await overlay.choose(conn, [], **args)
    assert remainder.phase == "yield" and len(remainder.changes) == 2
    overlay.finish(
        tmp_path,
        remainder,
        [{"parameter": k, "value": v, "requested_at": datetime.now(UTC)} for k, v in remainder.changes],
        [],
    )
    readbacks.update(dict(remainder.changes))
    yielded = await overlay.choose(conn, [], **args)
    assert yielded.phase == "yield" and not yielded.changes
    state = bounded._read(tmp_path / overlay.STATE_NAME)
    assert state["status"] == "yielded" and len(state["stages"]) == 4


@pytest.mark.asyncio
async def test_expiry_before_second_stage_restores_only_attempted_fields(full_vector, tmp_path):
    first, args = await admit_full_vector(full_vector, tmp_path)
    ts = datetime.now(UTC)
    overlay.finish(tmp_path, first, [{"parameter": k, "value": v, "requested_at": ts} for k, v in first.changes], [])
    full_vector[2].update(dict(first.changes))
    state = bounded._read(tmp_path / overlay.STATE_NAME)
    state["worksheet"]["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    bounded._write(tmp_path / overlay.STATE_NAME, state)
    bounded._write(tmp_path / overlay.WORKSHEET_NAME, state["worksheet"])
    restoration = await overlay.choose(None, [], **args)
    assert restoration.phase == "yield"
    assert set(dict(restoration.changes)) == set(dict(first.changes))
    assert not (set(full_vector[0]["decisions"]) - set(dict(first.changes))) & set(dict(restoration.changes))


def test_preparer_requires_complete_fresh_v2_projection(full_vector):
    import importlib.util

    spec = importlib.util.spec_from_file_location("c1_prepare", ROOT / "scripts/prepare-c1-qualification-worksheet.py")
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    sheet, preview, _ = full_vector
    preview["base_converged"] = True
    now = datetime.now(UTC)
    actual = prepare.prepare(preview, sheet["decisions"], now=now)
    assert actual["schema"] == "verdify-c1-qualification-worksheet-v2"
    assert actual["projection"]["explicit_selection_count"] == 14
    assert actual["projection"]["field_count"] == 48
    assert datetime.fromisoformat(actual["expires_at"]) == datetime.fromisoformat(preview["captured_at"]) + timedelta(
        minutes=6
    )
    incomplete = dict(sheet["decisions"])
    incomplete.pop("vpd_watch_dwell_s")
    with pytest.raises(ValueError, match="requires from, to, and rationale"):
        prepare.prepare(preview, incomplete, now=now)
    with pytest.raises(ValueError, match="current within"):
        prepare.prepare(preview, sheet["decisions"], now=now + timedelta(seconds=61))
    preview["base_converged"] = False
    with pytest.raises(ValueError, match="not converged"):
        prepare.prepare(preview, sheet["decisions"], now=now)


@pytest.mark.asyncio
async def test_full_vector_unknown_stage_outcome_is_retained_not_replayed(full_vector, tmp_path):
    first, args = await admit_full_vector(full_vector, tmp_path)
    original = (tmp_path / overlay.STATE_NAME).read_bytes()
    held = await overlay.choose(None, [], **args)
    assert first.phase == "send" and len(first.changes) == 12
    assert held.phase == "hold" and not held.changes
    assert (tmp_path / overlay.STATE_NAME).read_bytes() == original


@pytest.mark.asyncio
async def test_ordinary_changes_do_not_gain_c1_restoration_authority(runtime, tmp_path):
    first, args = await admit(runtime, tmp_path)
    ts = datetime.now(UTC)
    overlay.finish(tmp_path, first, [{"parameter": k, "value": v, "requested_at": ts} for k, v in first.changes], [])
    runtime[2].update(dict(first.changes))
    conn = StageConfirmation()
    conn.confirmed = True
    ordinary = await overlay.choose(conn, [("unrelated_ordinary_field", 1.0)], **args)
    assert ordinary.phase == "send" and ordinary.changes == (("unrelated_ordinary_field", 1.0),)
    state = bounded._read(tmp_path / overlay.STATE_NAME)
    assert state["touched"] == ["mister_engage_delay_s"]
