from __future__ import annotations

import hashlib
import json
import sys
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(MODULE_DIR))

from switchback import v2_selector as selector

NAMESPACE = uuid.UUID("c86df2e8-6ad8-4f67-8203-cf3e88643589")
BOUNDARY = datetime(2026, 9, 2, tzinfo=UTC)
CUTOFF = BOUNDARY - timedelta(hours=1)


def _identity() -> selector.SelectorIdentity:
    digest = hashlib.sha256(b"frozen").hexdigest()
    return selector.SelectorIdentity(
        provider="frozen-provider",
        model_identifier="immutable-model-2026-08-23",
        model_revision="model-revision-1",
        expected_system_fingerprint="fingerprint-1",
        prompt_sha256=digest,
        system_message_sha256=digest,
        messages_sha256=digest,
        decoding_parameters_sha256=digest,
        tool_contract_revision="tools-v1",
        response_schema_revision="selector-response-v2",
        context_schema_revision="selector-context-v2",
        lesson_snapshot_sha256=digest,
        runtime_environment_digest="sha256:runtime-frozen",
        timeout_milliseconds=10_000,
        max_attempts=2,
    )


def _context() -> selector.FrozenContext:
    return selector.freeze_context(
        [
            selector.ContextRecord(CUTOFF - timedelta(minutes=1), "climate_history", {"temp_f": 72.0}),
            selector.ContextRecord(CUTOFF + timedelta(seconds=1), "climate_history", {"temp_f": 99.0}),
            selector.ContextRecord(CUTOFF - timedelta(minutes=2), "comparative_outcome", {"score": 1}),
        ],
        cutoff_at=CUTOFF,
        boundary_at=BOUNDARY,
    )


class Provider:
    def __init__(self, body: bytes = b'{"profile":"moderate"}', **overrides: object) -> None:
        self.body = body
        self.overrides = overrides
        self.calls: list[tuple[bytes, str, int]] = []

    def infer(self, request: bytes, *, idempotency_key: str, timeout_milliseconds: int) -> selector.ProviderResponse:
        self.calls.append((request, idempotency_key, timeout_milliseconds))
        values = {
            "raw_response": self.body,
            "provider": "frozen-provider",
            "model_identifier": "immutable-model-2026-08-23",
            "model_revision": "model-revision-1",
            "system_fingerprint": "fingerprint-1",
            "completed_at": BOUNDARY - timedelta(minutes=5),
        }
        values.update(self.overrides)
        return selector.ProviderResponse(**values)


class TimeoutProvider(Provider):
    def infer(self, request: bytes, *, idempotency_key: str, timeout_milliseconds: int) -> selector.ProviderResponse:
        self.calls.append((request, idempotency_key, timeout_milliseconds))
        raise TimeoutError


class UnavailableProvider(Provider):
    def infer(self, request: bytes, *, idempotency_key: str, timeout_milliseconds: int) -> selector.ProviderResponse:
        self.calls.append((request, idempotency_key, timeout_milliseconds))
        raise selector.ProviderUnavailableError("synthetic outage")


def _select(provider: Provider, ledger: selector.ChoiceLedger | None = None) -> selector.SelectorChoice:
    return selector.select_once(
        ledger=ledger or selector.TestingChoiceLedger(),
        provider=provider,
        namespace_uuid=NAMESPACE,
        study_id="study-v2",
        local_date="2026-09-01",
        context=_context(),
        identity=_identity(),
        now=BOUNDARY - timedelta(minutes=10),
    )


def test_context_cutoff_positive_allowlist_and_arm_outcome_leakage() -> None:
    frozen = _context()
    assert len(frozen.records) == 1
    assert frozen.records[0]["payload"] == {"temp_f": 72.0}
    with pytest.raises(ValueError, match="forbidden selector-context key"):
        selector.freeze_context(
            [selector.ContextRecord(CUTOFF, "climate_history", {"physical_arm": "B"})],
            cutoff_at=CUTOFF,
            boundary_at=BOUNDARY,
        )
    with pytest.raises(ValueError, match="strictly before"):
        selector.freeze_context([], cutoff_at=BOUNDARY, boundary_at=BOUNDARY)


def test_identical_virtual_inference_and_boundary_only_physical_resolution() -> None:
    provider_a, provider_b = Provider(), Provider()
    choice_a, choice_b = _select(provider_a), _select(provider_b)
    assert provider_a.calls[0][0] == provider_b.calls[0][0]
    request = json.loads(provider_a.calls[0][0])
    serialized = json.dumps(request, sort_keys=True)
    assert "physical_arm" not in serialized and "blinded_label" not in serialized
    assert choice_a.raw_request_sha256 == choice_b.raw_request_sha256
    assert (
        selector.resolve_boundary_profile(
            choice_a,
            physical_arm="A",
            assignment_local_date="2026-09-01",
            boundary_at=BOUNDARY,
            resolved_at=BOUNDARY,
        )
        == "baseline"
    )
    assert (
        selector.resolve_boundary_profile(
            choice_b,
            physical_arm="B",
            assignment_local_date="2026-09-01",
            boundary_at=BOUNDARY,
            resolved_at=BOUNDARY,
        )
        == "moderate"
    )
    with pytest.raises(ValueError, match="intraday"):
        selector.resolve_boundary_profile(
            choice_b,
            physical_arm="B",
            assignment_local_date="2026-09-01",
            boundary_at=BOUNDARY,
            resolved_at=BOUNDARY + timedelta(minutes=1),
        )


def test_exactly_once_restart_retry_returns_existing_without_second_call() -> None:
    ledger = selector.TestingChoiceLedger()
    first_provider, retry_provider = Provider(), Provider(b'{"profile":"aggressive"}')
    first = _select(first_provider, ledger)
    retry = _select(retry_provider, ledger)
    assert first is retry
    assert first.profile == "moderate"
    assert len(first_provider.calls) == 1
    assert retry_provider.calls == []


def test_same_day_retry_under_changed_frozen_context_or_identity_aborts() -> None:
    ledger = selector.TestingChoiceLedger()
    _select(Provider(), ledger)
    changed_context = selector.freeze_context(
        [selector.ContextRecord(CUTOFF, "climate_history", {"temp_f": 71.0})],
        cutoff_at=CUTOFF,
        boundary_at=BOUNDARY,
    )
    with pytest.raises(ValueError, match="changed frozen"):
        selector.select_once(
            ledger=ledger,
            provider=Provider(),
            namespace_uuid=NAMESPACE,
            study_id="study-v2",
            local_date="2026-09-01",
            context=changed_context,
            identity=_identity(),
            now=BOUNDARY - timedelta(minutes=10),
        )
    with pytest.raises(ValueError, match="changed frozen"):
        selector.select_once(
            ledger=ledger,
            provider=Provider(),
            namespace_uuid=NAMESPACE,
            study_id="study-v2",
            local_date="2026-09-01",
            context=_context(),
            identity=replace(_identity(), model_revision="model-revision-2"),
            now=BOUNDARY - timedelta(minutes=10),
        )


@pytest.mark.parametrize(
    ("provider", "reason"),
    [
        (Provider(b"not-json"), "malformed"),
        (Provider("not-bytes"), "malformed"),  # type: ignore[arg-type]
        (Provider(b'{"profile":"other"}'), "invalid_output"),
        (Provider(completed_at=BOUNDARY), "late"),
        (Provider(model_revision="floating-alias"), "revision_mismatch"),
        (Provider(system_fingerprint="different"), "revision_mismatch"),
    ],
)
def test_invalid_late_malformed_and_revision_mismatch_fall_back(provider: Provider, reason: str) -> None:
    choice = _select(provider)
    assert choice.profile == "baseline"
    assert choice.fallback_reason == reason
    assert choice.raw_request_sha256
    assert choice.raw_response_sha256 or not isinstance(provider.body, bytes)


def test_timeout_reuses_one_idempotency_key_then_falls_back_without_fake_raw_response() -> None:
    provider = TimeoutProvider()
    choice = _select(provider)
    assert choice.profile == "baseline" and choice.fallback_reason == "timeout"
    assert len(provider.calls) == _identity().max_attempts
    assert len({call[1] for call in provider.calls}) == 1
    assert choice.raw_response_sha256 is None
    assert len(choice.attempt_receipt_sha256) == 2


def test_routine_provider_unavailability_falls_back_but_system_exceptions_propagate() -> None:
    choice = _select(UnavailableProvider())
    assert choice.profile == "baseline" and choice.fallback_reason == "provider_unavailable"

    class CancelledProvider(Provider):
        def infer(
            self, request: bytes, *, idempotency_key: str, timeout_milliseconds: int
        ) -> selector.ProviderResponse:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _select(CancelledProvider())


def test_missed_boundary_aborts_without_calling_provider() -> None:
    provider = Provider()
    with pytest.raises(ValueError, match="missed selector start"):
        selector.select_once(
            ledger=selector.TestingChoiceLedger(),
            provider=provider,
            namespace_uuid=NAMESPACE,
            study_id="study-v2",
            local_date="2026-09-01",
            context=_context(),
            identity=_identity(),
            now=BOUNDARY,
        )
    assert provider.calls == []


def test_selector_choice_schema_exactly_matches_persisted_record_fields() -> None:
    schema = json.loads((MODULE_DIR / "protocols/selector-choice-v2.schema.json").read_text())
    choice = _select(Provider())
    assert set(schema["required"]) == set(asdict(choice))
    assert schema["additionalProperties"] is False
