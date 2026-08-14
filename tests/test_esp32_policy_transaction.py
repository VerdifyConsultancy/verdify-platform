"""esp32_push whole-vector policy transactions (#584 Lane C).

Proves at the physical chokepoint, with a mocked API client:
- a policy transaction is NON-INTERLEAVABLE: an ordinary per-parameter push
  queued while it runs is delivered only after every transaction call;
- the transaction preserves default-deny (#79), the writer fences, and the
  service-availability guard;
- the experiment policy hold rejects individual legacy setter pushes for
  experiment-owned parameters while armed, and fails open when released.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

import esp32_push  # noqa: E402
import shared  # noqa: E402
from esp32_push import PolicyServiceCall, push_policy_transaction  # noqa: E402

POLICY_SERVICES = {
    name: f"svc-{name}" for name in ("policy_begin", "policy_chunk", "policy_validate", "policy_commit", "policy_abort")
}


@pytest.fixture(autouse=True)
def _writer_env(monkeypatch):
    """Fast pacing, clean gate/hold state, restored shared.esp32."""
    saved = dict(shared.esp32)
    monkeypatch.setattr(esp32_push, "_MIN_COMMAND_INTERVAL_S", 0.0)
    monkeypatch.setattr(esp32_push, "_BATCH_PAUSE_S", 0.0)
    monkeypatch.delenv("VERDIFY_DEVICE_WRITE_ENABLED", raising=False)
    esp32_push.set_experiment_policy_hold(False)
    esp32_push._DEVICE_WRITE_DISABLED_LOGGED = False
    yield
    shared.esp32.clear()
    shared.esp32.update(saved)
    esp32_push.set_experiment_policy_hold(False)


def _install_client(order: list) -> MagicMock:
    client = MagicMock()

    async def execute_service(service, payload):
        order.append(("service", service, dict(payload)))
        await asyncio.sleep(0.02)

    def number_command(key, value):
        order.append(("number", key, value))

    client.execute_service = execute_service
    client.number_command = number_command
    client.switch_command = MagicMock(return_value=None)
    shared.esp32["client"] = client
    shared.esp32["keys"] = {"mister_engage_kpa": 11, "fog_escalation_kpa": 12}
    shared.esp32["services"] = dict(POLICY_SERVICES)
    return client


# ── Non-interleaving ────────────────────────────────────────────────────────


def test_ordinary_push_queues_behind_an_active_policy_transaction(monkeypatch):
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    order: list = []

    async def scenario():
        _install_client(order)
        calls = [
            PolicyServiceCall("policy_begin", {"generation": 1}),
            PolicyServiceCall("policy_chunk", {"seq": 0}),
            PolicyServiceCall("policy_validate", {}),
            PolicyServiceCall("policy_commit", {"generation": 1}),
        ]
        txn = asyncio.create_task(push_policy_transaction(calls))
        while not esp32_push.policy_transaction_active():
            await asyncio.sleep(0.001)
        # A simulated concurrent ordinary request: it must queue, not splice.
        ordinary = asyncio.create_task(esp32_push.push_to_esp32([("mister_engage_kpa", 1.3, "number")]))
        return await txn, await ordinary

    result, pushed = asyncio.run(scenario())
    assert result.ok
    assert pushed == 1
    kinds = [entry[0] for entry in order]
    assert kinds == ["service", "service", "service", "service", "number"], (
        f"ordinary command interleaved into the policy transaction: {kinds}"
    )
    assert [entry[1] for entry in order[:4]] == [
        "svc-policy_begin",
        "svc-policy_chunk",
        "svc-policy_validate",
        "svc-policy_commit",
    ]


# ── Writer guarantees preserved ─────────────────────────────────────────────


def test_transaction_respects_default_deny_device_write_gate():
    order: list = []

    async def scenario():
        _install_client(order)
        return await push_policy_transaction([PolicyServiceCall("policy_begin", {})])

    result = asyncio.run(scenario())
    assert not result.ok
    assert result.failure.reason == "device_writes_disabled"
    assert order == []


def test_transaction_fails_when_writer_lease_not_held(monkeypatch):
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    monkeypatch.setattr(shared, "writer_lease_held", lambda: False)
    order: list = []

    async def scenario():
        _install_client(order)
        return await push_policy_transaction([PolicyServiceCall("policy_begin", {})])

    result = asyncio.run(scenario())
    assert not result.ok
    assert result.failure.reason == "writer_lease_not_held"
    assert order == []


def test_transaction_fails_closed_on_missing_service(monkeypatch):
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    order: list = []

    async def scenario():
        _install_client(order)
        del shared.esp32["services"]["policy_commit"]
        return await push_policy_transaction(
            [PolicyServiceCall("policy_begin", {}), PolicyServiceCall("policy_commit", {})]
        )

    result = asyncio.run(scenario())
    assert not result.ok
    assert result.failure.reason == "policy_service_unavailable"
    assert result.failure.index == 1
    # The begin call went out; nothing after the failure did.
    assert [entry[1] for entry in order] == ["svc-policy_begin"]


def test_transaction_fences_reconnect_mid_sequence(monkeypatch):
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    order: list = []

    async def scenario():
        client = _install_client(order)

        async def execute_service(service, payload):
            order.append(("service", service, dict(payload)))
            shared.transport_generation += 1  # reconnect happens mid-transaction

        client.execute_service = execute_service
        return await push_policy_transaction(
            [PolicyServiceCall("policy_begin", {}), PolicyServiceCall("policy_commit", {})]
        )

    generation_before = shared.transport_generation
    try:
        result = asyncio.run(scenario())
    finally:
        shared.transport_generation = generation_before
    assert not result.ok
    assert result.failure.reason == "transport_generation_changed"
    assert len(order) == 1


# ── Experiment policy hold on legacy setter pushes ──────────────────────────


def test_hold_rejects_legacy_setter_for_experiment_owned_param(monkeypatch):
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    order: list = []

    async def scenario():
        _install_client(order)
        esp32_push.set_experiment_policy_hold(True, {"fog_escalation_kpa"})
        held = await esp32_push.push_to_esp32_detailed([("fog_escalation_kpa", 0.3, "number")])
        free = await esp32_push.push_to_esp32_detailed([("mister_engage_kpa", 1.3, "number")])
        return held, free

    held, free = asyncio.run(scenario())
    assert held.outcomes[0].status == "failed"
    assert held.outcomes[0].reason == "experiment_policy_hold"
    assert free.sent_count == 1
    assert [entry[0] for entry in order] == ["number"], "only the un-held parameter reached the device"
    # Held rejections are terminal — never retried into a duplicate push.
    assert not esp32_push.delivery_failure_retryable(held.outcomes[0])


def test_hold_released_fails_open_to_current_behavior(monkeypatch):
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    order: list = []

    async def scenario():
        _install_client(order)
        esp32_push.set_experiment_policy_hold(True, {"fog_escalation_kpa"})
        esp32_push.set_experiment_policy_hold(False)
        return await esp32_push.push_to_esp32([("fog_escalation_kpa", 0.3, "number")])

    pushed = asyncio.run(scenario())
    assert pushed == 1
    assert order and order[0][0] == "number"


def test_hold_without_params_never_arms():
    esp32_push.set_experiment_policy_hold(True, ())
    assert esp32_push.experiment_policy_hold() == (False, frozenset())
