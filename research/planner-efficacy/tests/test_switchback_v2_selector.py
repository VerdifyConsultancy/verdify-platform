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


@pytest.fixture(autouse=True)
def _fixed_trusted_completion_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(selector, "_trusted_utc_now", lambda: BOUNDARY - timedelta(minutes=4))


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


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("timeout_milliseconds", True),
        ("timeout_milliseconds", 1.0),
        ("timeout_milliseconds", 0),
        ("timeout_milliseconds", selector.MAX_SELECTOR_TIMEOUT_MILLISECONDS + 1),
        ("max_attempts", True),
        ("max_attempts", 1.0),
        ("max_attempts", 0),
        ("max_attempts", selector.MAX_SELECTOR_ATTEMPTS + 1),
    ],
)
def test_selector_identity_requires_exact_bounded_timeout_and_attempts(field: str, invalid: object) -> None:
    with pytest.raises(ValueError, match=f"{field} must be an exact integer"):
        replace(_identity(), **{field: invalid})


def test_selector_identity_accepts_frozen_timeout_and_attempt_bounds() -> None:
    identity = replace(
        _identity(),
        timeout_milliseconds=selector.MAX_SELECTOR_TIMEOUT_MILLISECONDS,
        max_attempts=selector.MAX_SELECTOR_ATTEMPTS,
    )
    assert identity.timeout_milliseconds == selector.MAX_SELECTOR_TIMEOUT_MILLISECONDS
    assert identity.max_attempts == selector.MAX_SELECTOR_ATTEMPTS


def _climate_row(
    *,
    observed_at: datetime = CUTOFF - timedelta(minutes=1),
    temp_avg_f: object = 79.2,
    vpd_avg_kpa: object = 1.37,
) -> dict[str, object]:
    values: dict[str, object] = {field: None for field in selector._CLIMATE_VALUE_FIELDS}
    values["temp_avg_f"] = temp_avg_f
    values["vpd_avg_kpa"] = vpd_avg_kpa
    return {
        "schema": "verdify-selector-climate-source-v1",
        "observed_at": observed_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "source_row_sha256": "a" * 64,
        "values": values,
    }


def _forecast_row(
    *,
    valid_at: datetime = CUTOFF + timedelta(hours=1),
    fetched_at: datetime = CUTOFF - timedelta(minutes=5),
) -> dict[str, object]:
    values: dict[str, object] = {field: None for field in selector._FORECAST_VALUE_FIELDS}
    values.update({"temp_f": 75.0, "rh_pct": 51.0, "vpd_kpa": 1.18})
    return {
        "schema": "verdify-selector-forecast-source-v1",
        "valid_at": valid_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "fetched_at": fetched_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "source_row_sha256": "b" * 64,
        "values": values,
    }


def _context(*, temp_avg_f: object = 79.2) -> selector.FrozenContext:
    return selector.freeze_context(
        local_date="2026-09-01",
        climate_observations=[_climate_row(temp_avg_f=temp_avg_f)],
        forecast_vintage=[_forecast_row()],
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
    assert len(frozen.records) == 2
    assert frozen.records[0]["values"]["temp_avg_f"] == 79.2
    bad_arm = _climate_row()
    bad_arm["values"]["physical_arm"] = 2
    with pytest.raises(ValueError, match="must contain exactly"):
        selector.freeze_context(
            local_date="2026-09-01",
            climate_observations=[bad_arm],
            forecast_vintage=[_forecast_row()],
            cutoff_at=CUTOFF,
            boundary_at=BOUNDARY,
        )
    bad_text = _climate_row(temp_avg_f="physical arm B won")
    with pytest.raises(ValueError, match="finite JSON number or null"):
        selector.freeze_context(
            local_date="2026-09-01",
            climate_observations=[bad_text],
            forecast_vintage=[_forecast_row()],
            cutoff_at=CUTOFF,
            boundary_at=BOUNDARY,
        )
    post_cutoff = _climate_row(observed_at=CUTOFF + timedelta(microseconds=1))
    with pytest.raises(ValueError, match="post-cutoff"):
        selector.freeze_context(
            local_date="2026-09-01",
            climate_observations=[post_cutoff],
            forecast_vintage=[_forecast_row()],
            cutoff_at=CUTOFF,
            boundary_at=BOUNDARY,
        )
    with pytest.raises(ValueError, match="strictly before"):
        selector.freeze_context(
            local_date="2026-09-01",
            climate_observations=[_climate_row()],
            forecast_vintage=[],
            cutoff_at=BOUNDARY,
            boundary_at=BOUNDARY,
        )


def test_context_requires_every_nullable_field_and_one_latest_forecast_vintage() -> None:
    missing = _climate_row()
    del missing["values"]["flow_gpm"]
    with pytest.raises(ValueError, match="must contain exactly"):
        selector.freeze_context(
            local_date="2026-09-01",
            climate_observations=[missing],
            forecast_vintage=[_forecast_row()],
            cutoff_at=CUTOFF,
            boundary_at=BOUNDARY,
        )

    with pytest.raises(ValueError, match="48-row climate bound"):
        selector.freeze_context(
            local_date="2026-09-01",
            climate_observations=[_climate_row()] * (selector.MAX_CLIMATE_OBSERVATIONS + 1),
            forecast_vintage=[_forecast_row()],
            cutoff_at=CUTOFF,
            boundary_at=BOUNDARY,
        )
    duplicate = _forecast_row(fetched_at=CUTOFF - timedelta(minutes=4))
    duplicate["source_row_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="one as-of row"):
        selector.freeze_context(
            local_date="2026-09-01",
            climate_observations=[_climate_row()],
            forecast_vintage=[_forecast_row(), duplicate],
            cutoff_at=CUTOFF,
            boundary_at=BOUNDARY,
        )


def test_db_authoritative_context_bytes_need_not_match_python_float_serialization() -> None:
    original = _context()
    payload = json.loads(original.canonical_bytes)
    db_style_bytes = json.dumps(payload, sort_keys=True, separators=(", ", ": ")).encode()
    db_frozen = selector.FrozenContext(
        local_date=original.local_date,
        cutoff_at=original.cutoff_at,
        boundary_at=original.boundary_at,
        records=original.records,
        canonical_bytes=db_style_bytes,
        canonical_sha256=hashlib.sha256(db_style_bytes).hexdigest(),
    )
    provider = Provider()
    choice = selector.select_once(
        ledger=selector.TestingChoiceLedger(),
        provider=provider,
        namespace_uuid=NAMESPACE,
        study_id="study-v2",
        local_date="2026-09-01",
        context=db_frozen,
        identity=_identity(),
        now=BOUNDARY - timedelta(minutes=10),
    )
    assert choice.context_sha256 == hashlib.sha256(db_style_bytes).hexdigest()
    assert json.loads(provider.calls[0][0])["context"]["schema"] == "verdify-selector-context-v2"


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


def test_mutating_convenience_context_view_cannot_change_frozen_request() -> None:
    context = _context()
    # FrozenContext retains a human-readable view, but provider bytes come only
    # from its immutable canonical_bytes/hash pair.
    context.records[0]["values"]["temp_avg_f"] = 999.0
    provider = Provider()
    selector.select_once(
        ledger=selector.TestingChoiceLedger(),
        provider=provider,
        namespace_uuid=NAMESPACE,
        study_id="study-v2",
        local_date="2026-09-01",
        context=context,
        identity=_identity(),
        now=BOUNDARY - timedelta(minutes=10),
    )
    request = json.loads(provider.calls[0][0])
    assert request["context"]["climate_observations"][0]["values"]["temp_avg_f"] == 79.2


def test_public_frozen_context_constructor_cannot_bypass_record_validation() -> None:
    original = _context()
    envelope = json.loads(original.canonical_bytes)
    envelope["climate_observations"][0]["values"]["selected_profile_code"] = 2
    canonical = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    forged = selector.FrozenContext(
        local_date="2026-09-01",
        cutoff_at=CUTOFF,
        boundary_at=BOUNDARY,
        records=tuple(envelope["climate_observations"] + envelope["forecast_vintage"]),
        canonical_bytes=canonical,
        canonical_sha256=hashlib.sha256(canonical).hexdigest(),
    )
    provider = Provider()
    with pytest.raises(ValueError, match="must contain exactly"):
        selector.select_once(
            ledger=selector.TestingChoiceLedger(),
            provider=provider,
            namespace_uuid=NAMESPACE,
            study_id="study-v2",
            local_date="2026-09-01",
            context=forged,
            identity=_identity(),
            now=BOUNDARY - timedelta(minutes=10),
        )
    assert provider.calls == []


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
    changed_context = _context(temp_avg_f=80.1)
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
        (Provider(b'{"profile":"baseline","profile":"aggressive"}'), "malformed"),
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


def test_malformed_provider_object_falls_back_without_attribute_error() -> None:
    class NoneProvider:
        def __init__(self) -> None:
            self.calls: list[tuple[bytes, str, int]] = []

        def infer(self, request: bytes, *, idempotency_key: str, timeout_milliseconds: int):
            self.calls.append((request, idempotency_key, timeout_milliseconds))

    choice = _select(NoneProvider())
    assert choice.profile == "baseline" and choice.fallback_reason == "malformed"

    choice = _select(Provider(completed_at="not-a-datetime"))
    assert choice.profile == "baseline" and choice.fallback_reason == "malformed"


def test_trusted_local_completion_clock_rejects_backdated_provider_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter((BOUNDARY - timedelta(minutes=6), BOUNDARY, BOUNDARY))
    monkeypatch.setattr(selector, "_trusted_utc_now", lambda: next(times))
    provider = Provider(completed_at=BOUNDARY - timedelta(minutes=5))
    choice = _select(provider)
    assert choice.profile == "baseline"
    assert choice.fallback_reason == "boundary_elapsed_before_choice_persist"
    assert choice.raw_request_sha256 == choice.context_sha256
    assert choice.raw_response_sha256 is None


def test_trusted_persistence_clock_rejects_timeout_that_crosses_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter((BOUNDARY - timedelta(seconds=1), BOUNDARY))
    monkeypatch.setattr(selector, "_trusted_utc_now", lambda: next(times))
    provider = TimeoutProvider()
    choice = selector.select_once(
        ledger=selector.TestingChoiceLedger(),
        provider=provider,
        namespace_uuid=NAMESPACE,
        study_id="study-v2",
        local_date="2026-09-01",
        context=_context(),
        identity=replace(_identity(), max_attempts=1),
        now=BOUNDARY - timedelta(minutes=10),
    )
    assert choice.profile == "baseline"
    assert choice.fallback_reason == "boundary_elapsed_before_choice_persist"
    assert choice.raw_request_sha256 == choice.context_sha256
    assert choice.raw_response_sha256 is None
    assert choice.accepted_at == BOUNDARY


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


def test_trusted_entry_clock_rejects_backdated_caller_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(selector, "_trusted_utc_now", lambda: BOUNDARY)
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
            now=BOUNDARY - timedelta(minutes=10),
        )
    assert provider.calls == []


def test_selector_choice_schema_exactly_matches_persisted_record_fields() -> None:
    schema = json.loads((MODULE_DIR / "protocols/selector-choice-v2.schema.json").read_text())
    choice = _select(Provider())
    assert set(schema["required"]) == set(asdict(choice))
    assert schema["additionalProperties"] is False
