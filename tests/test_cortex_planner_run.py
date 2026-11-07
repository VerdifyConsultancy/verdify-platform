"""Cortex planner failure policy (#1350): retry once, repair once, then write nothing and alert."""

import asyncio
import json

import pytest

from cortex_planner import run as runner
from cortex_planner.client import (
    DEADLINE_S,
    MIN_TOKENS_PER_S,
    MODEL,
    CallConfig,
    CallResult,
    KeyMissing,
    TransportError,
    build_request,
    read_key,
)
from cortex_planner.fixture import AS_OF, sample_inputs, sample_proposal
from cortex_planner.inputs import Trigger
from cortex_planner.pack import anchors
from cortex_planner.sections import REQUEST_TOKEN_CAP

TRIGGER = Trigger("00000000-0000-0000-0000-000000001350", "SUNRISE", "sunrise", AS_OF)


class FakeStore:
    def __init__(self, ran=False):
        self.ran = ran
        self.packs, self.attempts, self.raised, self.resolved = [], [], [], []
        self.closed = 0
        self.triggers = [TRIGGER]

    async def has_run(self, trigger_id):
        return self.ran

    async def load_inputs(self, trigger):
        return sample_inputs(trigger.event_type)

    async def save_pack(self, pack, inputs):
        self.packs.append(pack)

    async def save_attempt(self, attempt):
        self.attempts.append(attempt)

    async def raise_failed(self, trigger_id, message, details):
        self.raised.append((trigger_id, message, details))

    async def resolve_failed(self, resolution):
        self.resolved.append(resolution)

    async def pending_triggers(self, event_types):
        found, self.triggers = self.triggers, []
        return found

    async def close(self):
        self.closed += 1


def answer(content, finish="stop", status=200):
    return CallResult(status, 1234, {"choices": [{"message": {"content": content}, "finish_reason": finish}]})


ANCHORS = anchors(sample_inputs())
GOOD = json.dumps(sample_proposal(ANCHORS))


class FakeClient:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.bodies = []

    async def complete(self, body):
        self.bodies.append(body)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def go(store, client):
    return asyncio.run(runner.run_trigger(store, client, TRIGGER))


def test_request_is_one_deterministic_text_call():
    body = build_request("sys", "user", CallConfig())
    assert body["model"] == MODEL == "llm.primary.longctx"
    assert (body["temperature"], body["top_p"], body["top_k"], body["max_tokens"]) == (0.6, 0.95, 20, 24_000)
    assert body["chat_template_kwargs"] == {"reasoning_effort": "medium"} and isinstance(body["seed"], int)
    assert "tools" not in body and body["stream"] is False
    assert body["response_format"] == {"type": "json_object"}


def test_ok_first_time_stores_pack_and_attempt_and_resolves():
    assert sample_inputs().as_of == AS_OF and anchors(sample_inputs())
    store, client = FakeStore(), FakeClient(answer(GOOD))
    outcome = go(store, client)
    assert outcome.status == "ok" and outcome.proposal
    assert [a.kind for a in store.attempts] == ["primary"] and store.attempts[0].status == "ok"
    assert len(store.packs) == 1 and store.packs[0].variant == "v1"
    assert store.resolved and not store.raised


def test_transport_error_is_retried_once():
    store, client = FakeStore(), FakeClient(TransportError("HTTP 503"), answer(GOOD))
    outcome = go(store, client)
    assert outcome.status == "ok" and [a.kind for a in store.attempts] == ["primary", "retry"]
    assert client.bodies[0] == client.bodies[1]


def test_invalid_answer_gets_one_repair_with_the_minimum_pack():
    bad = json.dumps(sample_proposal(ANCHORS, confidence=2.0))
    store, client = FakeStore(), FakeClient(answer(bad), answer(GOOD))
    outcome = go(store, client)
    assert outcome.status == "ok" and [a.kind for a in store.attempts] == ["primary", "repair"]
    repair = store.attempts[1]
    assert repair.pack.variant == "minimum" and repair.config.reasoning_effort == "low"
    assert repair.config.enable_thinking is False
    assert "## Repair" in client.bodies[1]["messages"][1]["content"]
    assert "confidence" in client.bodies[1]["messages"][1]["content"]


def test_attempt_configs_cannot_end_inside_reasoning():
    """#1480: the primary has room past the measured reasoning, the repair does not think at all."""
    store, client = FakeStore(), FakeClient(answer("", finish="length"), answer(GOOD))
    assert go(store, client).status == "ok"
    primary, repair = client.bodies
    assert primary["chat_template_kwargs"] == {"reasoning_effort": "medium"}
    assert primary["max_tokens"] >= 2 * 12_000  # the 2026-11-07 SUNRISE cut
    assert repair["chat_template_kwargs"] == {"enable_thinking": False}
    assert (repair["temperature"], repair["top_p"], repair["top_k"]) == (0.7, 0.8, 20)
    pack_cap = max(p.total_tokens for p in store.packs)
    assert pack_cap + primary["max_tokens"] <= REQUEST_TOKEN_CAP
    assert DEADLINE_S >= primary["max_tokens"] / MIN_TOKENS_PER_S * 1.15
    assert CallConfig().identity_sha256() != store.attempts[1].config.identity_sha256()


def test_length_stop_goes_to_repair():
    store, client = FakeStore(), FakeClient(answer("", finish="length"), answer(GOOD))
    assert go(store, client).status == "ok"
    assert store.attempts[0].status == "invalid"


def test_everything_fails_writes_nothing_and_raises_planner_failed():
    store = FakeStore()
    client = FakeClient(TransportError("ConnectTimeout"), answer("not json"), answer("still not json"))
    outcome = go(store, client)
    assert outcome.status == "failed" and outcome.proposal is None
    assert [a.kind for a in store.attempts] == ["primary", "retry", "repair"]
    assert all(a.proposal is None for a in store.attempts)
    assert len(store.raised) == 1 and not store.resolved
    trigger_id, message, details = store.raised[0]
    assert trigger_id == TRIGGER.trigger_id and "plan in force is kept" in message
    assert details["mode"] == "shadow" and details["errors"]


def test_client_error_fails_at_once_without_retry_or_repair():
    store, client = FakeStore(), FakeClient(answer("", status=401))
    outcome = go(store, client)
    assert outcome.status == "failed" and len(store.attempts) == 1 and len(store.raised) == 1
    assert not client.replies


def test_a_trigger_that_already_ran_is_skipped():
    store, client = FakeStore(ran=True), FakeClient()
    assert go(store, client).status == "skipped"
    assert not store.packs and not store.attempts and not client.bodies


def test_only_shadow_mode_is_supported():
    with pytest.raises(ValueError, match="not supported"):
        asyncio.run(runner.run_trigger(FakeStore(), FakeClient(), TRIGGER, mode="live"))


def serve_once(monkeypatch, tmp_path, store, **env):
    monkeypatch.setattr(runner, "HEARTBEAT_FILE", tmp_path / "alive")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    async def factory():
        return store

    async def main():
        task = asyncio.create_task(runner.serve(factory, poll_s=0.01))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(main())


def test_missing_key_fails_closed_with_one_alert_and_keeps_running(monkeypatch, tmp_path, caplog):
    store = FakeStore()
    serve_once(
        monkeypatch, tmp_path, store, CORTEX_API_KEY_FILE=str(tmp_path / "absent"), VERDIFY_PLANNER_MODE="shadow"
    )
    assert len(store.raised) == 1 and "fails closed" in store.raised[0][1]
    assert store.closed > 1  # the loop kept polling instead of crashing
    assert not store.attempts and store.triggers == [TRIGGER]
    assert (tmp_path / "alive").exists()
    assert "FAIL CLOSED" in caplog.text


def test_unsupported_mode_idles_with_one_alert(monkeypatch, tmp_path):
    store = FakeStore()
    key = tmp_path / "key"
    key.write_text("k")
    serve_once(monkeypatch, tmp_path, store, CORTEX_API_KEY_FILE=str(key), VERDIFY_PLANNER_MODE="live")
    assert len(store.raised) == 1 and "refused mode" in store.raised[0][1]
    assert not store.attempts


def test_read_key(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.write_text("\n")
    with pytest.raises(KeyMissing):
        read_key(str(empty))
    monkeypatch.setenv("CORTEX_API_KEY_FILE", str(tmp_path / "nope"))
    with pytest.raises(KeyMissing):
        read_key()
