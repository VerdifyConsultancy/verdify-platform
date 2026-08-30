"""Dormant M8 design/preflight/activation source contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from experiment_orchestrator.contracts import (
    OPENAI_SELECTOR_IDENTITY_SCHEMA,
    OPENAI_SELECTOR_RESPONSE_FORMAT,
    OPENAI_SELECTOR_RESPONSE_SCHEMA,
    SelectorIdentity,
    canonical_json_bytes,
    openai_messages_template_bytes,
)
from experiment_orchestrator.launch_artifacts import (
    MODELED_JOINT_ADVANCE_POWER,
    build_direct_launch_design,
    earliest_offset_stable_start,
    parse_direct_launch_design,
)
from experiment_orchestrator.launch_control import execute_control
from experiment_orchestrator.preflight import run_preflight
from experiment_orchestrator.provider import SelectorAttemptResult

ROOT = Path(__file__).resolve().parents[1]
DESIGN_COMPONENT = ROOT / "deploy/k8s/components/experiment-v2-design-lock"
AUTHORIZATION_COMPONENT = ROOT / "deploy/k8s/components/experiment-v2-randomized-day1-authorization"
ACTIVATION_COMPONENT = ROOT / "deploy/k8s/components/experiment-v2-randomized-day1-activation"
ROLLBACK_COMPONENT = ROOT / "deploy/k8s/components/experiment-v2-randomized-day1-rollback"
PROD = ROOT / "deploy/k8s/overlays/prod/kustomization.yaml"


def _design():
    return build_direct_launch_design(
        analyzer_environment_sha256="1" * 64,
        context_schema_sha256="2" * 64,
        endpoint_artifact_sha256="3" * 64,
        outcome_schema_sha256="4" * 64,
        power_artifact_sha256="4d751a76465d03dc2e75034dcb398d25dc39b375d9976671bd8fffb018d237a2",
        rollback_artifact_sha256="6" * 64,
        schedule_schema_sha256="fc73d212f58db91bd55bb70e3faa1431172b4339ae3b22a11d404ba95147b794",
        selector_artifact_sha256="8" * 64,
        selector_identity_sha256="9" * 64,
        source_git_sha="a" * 40,
        study_start_local_date="2026-11-02",
    )


def _identity() -> SelectorIdentity:
    system = "Choose one approved profile. Return only the strict schema object."
    prompt = "Use only this preflight context and select the safest approved profile."
    decoding = {
        "max_completion_tokens": 512,
        "reasoning_effort": "medium",
        "response_format": OPENAI_SELECTOR_RESPONSE_FORMAT,
        "stream": False,
    }
    payload = {
        "schema": OPENAI_SELECTOR_IDENTITY_SCHEMA,
        "provider": "openai",
        "model_identifier": "gpt-5.6-sol",
        "model_revision": "gpt-5.6-sol",
        "expected_system_fingerprint": "openai-managed",
        "prompt": prompt,
        "system_message": system,
        "decoding_parameters": decoding,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "system_message_sha256": hashlib.sha256(system.encode()).hexdigest(),
        "messages_sha256": hashlib.sha256(openai_messages_template_bytes(system, prompt)).hexdigest(),
        "decoding_parameters_sha256": hashlib.sha256(canonical_json_bytes(decoding)).hexdigest(),
        "tool_contract_revision": "none-v1",
        "response_schema_revision": OPENAI_SELECTOR_RESPONSE_SCHEMA,
        "context_schema_sha256": "b" * 64,
        "lesson_snapshot_sha256": "c" * 64,
        "runtime_environment_sha256": "d" * 64,
        "timeout_milliseconds": 60_000,
        "max_attempts": 1,
    }
    raw = canonical_json_bytes(payload)
    return SelectorIdentity.parse(raw, hashlib.sha256(raw).hexdigest())


def test_future_design_hash_is_immutable_complete_and_offset_stable() -> None:
    design = _design()
    replay = parse_direct_launch_design(design.canonical_bytes, now_local_date=date(2026, 8, 29))
    assert replay == design
    assert design.payload["modeled_joint_advance_power"] == MODELED_JOINT_ADVANCE_POWER
    assert design.payload["accepted_underpowered_design"] is True
    assert design.payload["generalized_vector_mode"] == "off"
    assert design.payload["study_start_local_date"] == "2026-11-02"
    assert earliest_offset_stable_start(not_before=date(2026, 8, 30)) == date(2026, 8, 30)
    changed = json.loads(design.canonical_bytes)
    changed["randomized_pair_count"] = 31
    with pytest.raises(ValueError):
        parse_direct_launch_design(
            canonical_json_bytes(changed, reject_forbidden_fields=False), now_local_date=date.min
        )
    with pytest.raises(ValueError, match="missed starts"):
        parse_direct_launch_design(design.canonical_bytes, now_local_date=date(2026, 11, 2))


@pytest.mark.asyncio
async def test_preflight_persists_only_strict_profile_contract_metadata() -> None:
    class Provider:
        async def select(self, **kwargs):
            assert kwargs["identity"].decoding_parameters["response_format"] == OPENAI_SELECTOR_RESPONSE_FORMAT
            return SelectorAttemptResult("aggressive", None, "e" * 64, "f" * 64, ("1" * 64,))

    receipt = await run_preflight(
        identity=_identity(),
        provider=Provider(),
        clock=lambda: datetime(2026, 8, 29, 12, tzinfo=UTC),
    )
    raw = receipt.canonical_bytes()
    assert receipt.status == "pass"
    assert receipt.strict_profile_only is True and receipt.tools_allowed is False
    assert not receipt.database_authority and not receipt.device_authority and not receipt.kubernetes_authority
    assert b"aggressive" not in raw
    for forbidden in (b"api_key", b"authorization", b"mapping", b"physical_arm", b"secret"):
        assert forbidden not in raw.lower()


@pytest.mark.asyncio
async def test_lock_and_day1_approval_are_distinct_audited_api_calls() -> None:
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "admission_state": "closed",
                    "approvals": {"randomized_day_1": False},
                    "db_component_enabled": False,
                    "execution_phase": "shadow",
                    "lease_generation": 7,
                    "lifecycle_status": "draft",
                    "open_exposures": 0,
                    "revision_bundle_sha256": "2" * 64,
                },
            )
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "state": {
                    "admission_state": "closed",
                    "execution_phase": "randomized",
                    "lifecycle_status": "locked",
                }
            },
        )

    receipt = await execute_control(
        action="lock-design",
        api_root="http://verdify-api:8000",
        token="bounded-test-token",  # noqa: S106 - inert MockTransport credential
        audit_ref="issue-642-design-lock",
        design=_design(),
        transport=httpx.MockTransport(handler),
    )
    assert requests[0]["action"] == "direct_launch_commit"
    assert requests[0]["expected_component_enabled"] is False
    assert requests[0]["audit_ref"] == "issue-642-design-lock"
    assert b"secret" not in receipt.lower() and b"mapping" not in receipt.lower()

    requests.clear()

    async def approval_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "admission_state": "closed",
                    "approvals": {"randomized_day_1": False},
                    "db_component_enabled": True,
                    "execution_phase": "randomized",
                    "lease_generation": 8,
                    "lifecycle_status": "armed",
                    "open_exposures": 0,
                    "revision_bundle_sha256": "2" * 64,
                },
            )
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "state": {
                    "admission_state": "closed",
                    "execution_phase": "randomized",
                    "lifecycle_status": "armed",
                }
            },
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", _design().experiment_id)
    try:
        receipt = await execute_control(
            action="approve-day1",
            api_root="http://verdify-api:8000",
            token="bounded-test-token",  # noqa: S106 - inert MockTransport credential
            audit_ref="issue-642-randomized-day1-approval",
            transport=httpx.MockTransport(approval_handler),
        )
    finally:
        monkeypatch.undo()
    assert requests == [
        {
            "action": "direct_launch_approve_day1",
            "audit_ref": "issue-642-randomized-day1-approval",
            "expected_admission_state": "closed",
            "expected_component_enabled": True,
            "expected_execution_phase": "randomized",
            "expected_lease_generation": 8,
            "expected_lifecycle_status": "armed",
            "expected_revision_bundle_sha256": "2" * 64,
        }
    ]
    assert b"secret" not in receipt.lower() and b"mapping" not in receipt.lower()


def _documents(path: Path) -> list[dict]:
    documents: list[dict] = []
    for source in path.glob("*.yaml"):
        documents.extend(item for item in yaml.safe_load_all(source.read_text()) if item)
    return documents


def test_activation_and_rollback_manifests_are_dormant_hardened_and_vector_off() -> None:
    prod = PROD.read_text()
    for component in (
        DESIGN_COMPONENT,
        AUTHORIZATION_COMPONENT,
        ACTIVATION_COMPONENT,
        ROLLBACK_COMPONENT,
    ):
        assert component.name not in prod

    design_jobs = [item for item in _documents(DESIGN_COMPONENT) if item["kind"] == "Job"]
    assert {item["metadata"]["name"] for item in design_jobs} == {
        "verdify-experiment-v2-openai-preflight",
        "verdify-experiment-v2-design-lock",
    }
    for job in design_jobs:
        pod = job["spec"]["template"]["spec"]
        container = pod["containers"][0]
        assert pod["automountServiceAccountToken"] is False
        assert container["securityContext"]["readOnlyRootFilesystem"] is True
        assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
        env_names = {item["name"] for item in container.get("env", [])}
        assert not any(name.endswith("DB_PASSWORD") for name in env_names)

    authorization_jobs = [item for item in _documents(AUTHORIZATION_COMPONENT) if item["kind"] == "Job"]
    assert [item["metadata"]["name"] for item in authorization_jobs] == ["verdify-experiment-v2-day1-approval"]
    assert not any(item["kind"] == "Job" for item in _documents(ACTIVATION_COMPONENT))
    activation_kustomization = (ACTIVATION_COMPONENT / "kustomization.yaml").read_text()
    assert "day1-approval" not in activation_kustomization

    activation = (ACTIVATION_COMPONENT / "workload-activation.patch.yaml").read_text()
    assert activation.count('name: VERDIFY_POLICY_VECTOR_MODE\n              value: "off"') == 5
    assert activation.count('name: VERDIFY_COMPONENT_EXPERIMENT_ENABLED\n              value: "enabled"') == 5
    rollback = (ROLLBACK_COMPONENT / "workload-disable.patch.yaml").read_text()
    assert rollback.count('name: VERDIFY_POLICY_VECTOR_MODE\n              value: "off"') == 5
    assert rollback.count('name: VERDIFY_COMPONENT_EXPERIMENT_ENABLED\n              value: "off"') == 5
    assert rollback.count('name: VERDIFY_ACTIVE_EXPERIMENT_ID\n              value: ""') == 5
    rollback_job = next(item for item in _documents(ROLLBACK_COMPONENT) if item["kind"] == "Job")
    command = rollback_job["spec"]["template"]["spec"]["containers"][0]["command"]
    assert "emergency-hold" in command
