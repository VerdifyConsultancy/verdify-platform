from __future__ import annotations

import copy
import hashlib
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiment_orchestrator.contracts import (  # noqa: E402
    OUTCOME_IDENTITY_SCHEMA,
    OutcomePayload,
    canonical_json_bytes,
)
from experiment_orchestrator.database import AttestedPool  # noqa: E402
from experiment_orchestrator.outcome import (  # noqa: E402
    EQUIPMENT_COMPONENTS,
    EQUIPMENT_SOURCE_MAP_REVISION,
    EQUIPMENT_SOURCE_MAP_SHA256,
    EQUIPMENT_STREAMS,
    OUTCOME_CLIMATE_SCHEMA,
    OUTCOME_SOURCE_SCHEMA,
    OutcomeSourceCandidate,
    _postgres_jsonb_text,
    evaluate_outcome,
    load_locked_evaluator,
)
from experiment_orchestrator.service import run_outcome_cycle  # noqa: E402
from experiment_orchestrator.stores import OutcomeFunctionStore  # noqa: E402

EXPERIMENT = "11111111-1111-4111-8111-111111111111"
SUBJECT = "22222222-2222-4222-8222-222222222222"
RUNTIME = "33333333-3333-4333-8333-333333333333"
SNAPSHOT = "44444444-4444-4444-8444-444444444444"
DIRECT_EPOCH = "55555555-5555-4555-8555-555555555555"
COUNTER_EPOCH = "66666666-6666-4666-8666-666666666666"
OUTCOME_SCHEMA_SHA = "7" * 64
REVISION_SHA = "8" * 64
ANALYZER_SHA = "9" * 64
WINDOW_START = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 26, 6, 0, tzinfo=UTC)
RESOLVED = WINDOW_END + timedelta(minutes=5)
SOURCE_MAP_ARTIFACT = (
    Path(__file__).resolve().parents[1]
    / "research"
    / "planner-efficacy"
    / "baseline"
    / "experiment-v2-equipment-source-map.json"
)


def timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def source_hash(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def events_sha256(events: list[dict[str, object]]) -> str:
    return hashlib.sha256(_postgres_jsonb_text(events).encode()).hexdigest()


def receipt_sha256(receipt: dict[str, object]) -> str:
    preimage = {
        "device_id": "esp32:vallery",
        "domain": "verdify-equipment-state-source-receipt-v2",
        "event_count": receipt["event_count"],
        "events_sha256": receipt["events_sha256"],
        "firmware_revision": receipt["firmware_revision"],
        "gap_before": receipt["gap_before"],
        "gap_reason": receipt["gap_reason"],
        "gap_requested": receipt["gap_requested"],
        "greenhouse_id": "vallery",
        "previous_receipt_sha256": receipt["previous_receipt_sha256"],
        "receipt_id": receipt["receipt_id"],
        "recorded_at": receipt["recorded_at"],
        "source_connection_generation": receipt["connection_generation"],
        "source_observed_through": receipt["source_observed_through"],
        "source_runtime_instance_id": receipt["runtime_instance_id"],
        "source_sequence": receipt["source_sequence"],
    }
    return hashlib.sha256(_postgres_jsonb_text(preimage).encode()).hexdigest()


def rehash_receipt_chain(chain: dict[str, object]) -> None:
    receipts = chain["receipts"]
    for index, receipt in enumerate(receipts):
        if index:
            receipt["previous_receipt_sha256"] = receipts[index - 1]["receipt_sha256"]
        receipt["events"].sort(key=lambda row: (row["source_observed_at"], row["equipment"], row["state"]))
        receipt["event_count"] = len(receipt["events"])
        receipt["events_sha256"] = events_sha256(receipt["events"])
        receipt["receipt_sha256"] = receipt_sha256(receipt)


def seal_receipt_chain(bundle: dict[str, Any]) -> None:
    chain = bundle["equipment_ingestion_receipt_chain"]
    rehash_receipt_chain(chain)
    receipts = chain["receipts"]
    for source in bundle["equipment_streams"].values():
        for component, transitions in source["transition_components"].items():
            for transition_row in transitions:
                receipt = next(
                    row
                    for row in receipts
                    if any(
                        event["equipment"] == component
                        and event["source_observed_at"] == transition_row["observed_at"]
                        and event["state"] == transition_row["state"]
                        for event in row["events"]
                    )
                )
                transition_row.update(
                    {
                        "source_receipt_id": receipt["receipt_id"],
                        "source_receipt_sequence": receipt["source_sequence"],
                        "source_receipt_sha256": receipt["receipt_sha256"],
                    }
                )
                payload = {
                    key: transition_row[key]
                    for key in (
                        "observed_at",
                        "source_receipt_id",
                        "source_receipt_sequence",
                        "source_receipt_sha256",
                        "state",
                        "stream",
                    )
                }
                transition_row["source_row_sha256"] = hashlib.sha256(
                    b"verdify-experiment-v2-outcome-state-transition-v1\x00" + _postgres_jsonb_text(payload).encode()
                ).hexdigest()


def complete_receipt_chain() -> dict[str, object]:
    earliest_seed = WINDOW_START - timedelta(seconds=60)
    barrier = WINDOW_START - timedelta(seconds=90)
    sequence = 10
    previous_sha = source_hash("receipt-before-anchor")
    receipts: list[dict[str, object]] = []
    while True:
        receipts.append(
            {
                "connection_generation": 1,
                "event_count": 0,
                "events": [],
                "events_sha256": events_sha256([]),
                "firmware_revision": "firmware-v2",
                # The anchor's predecessor interval is outside source
                # coverage, so its historical gap is intentionally retained.
                "gap_before": len(receipts) == 0,
                "gap_reason": "source_time_gap" if not receipts else None,
                "gap_requested": False,
                "previous_receipt_sha256": previous_sha,
                "receipt_id": f"77777777-7777-4777-8777-{sequence:012d}",
                "receipt_sha256": "0" * 64,
                "recorded_at": timestamp(barrier + timedelta(seconds=1)),
                "runtime_instance_id": RUNTIME,
                "source_observed_through": timestamp(barrier),
                "source_sequence": sequence,
            }
        )
        if barrier >= WINDOW_END:
            break
        previous_sha = receipts[-1]["receipt_sha256"]
        sequence += 1
        barrier = min(barrier + timedelta(seconds=60), WINDOW_END)
    chain = {
        "schema": "verdify-equipment-state-receipt-chain-v1",
        "maximum_source_barrier_gap_seconds": 60,
        "coverage_start_at": timestamp(earliest_seed),
        "coverage_end_at": timestamp(WINDOW_END),
        "receipts": receipts,
    }
    rehash_receipt_chain(chain)
    return chain


def identity_file(tmp_path: Path) -> tuple[Path, str]:
    _module, evaluator_sha = load_locked_evaluator()
    payload = {
        "schema": OUTCOME_IDENTITY_SCHEMA,
        "outcome_schema_sha256": OUTCOME_SCHEMA_SHA,
        "evaluator_source_sha256": evaluator_sha,
        "temperature_duplicate_tolerance_f": 0.1,
        "vpd_duplicate_tolerance_kpa": 0.01,
    }
    raw = canonical_json_bytes(payload, reject_forbidden_fields=False)
    path = tmp_path / "identity.json"
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def complete_source_bundle(endpoint_sha: str, *, kind: str = "randomized") -> dict[str, Any]:
    climate = []
    corridors = []
    for bucket_index in range(72):
        bucket = WINDOW_START + timedelta(minutes=15 * bucket_index)
        corridors.append(
            {
                "bucket_start": timestamp(bucket),
                "temperature_high_f": 80.0,
                "temperature_low_f": 70.0,
                "vpd_high_kpa": 2.0,
                "vpd_low_kpa": 1.0,
            }
        )
        for minute in range(12):
            observed = bucket + timedelta(minutes=minute)
            climate.append(
                {
                    "schema": OUTCOME_CLIMATE_SCHEMA,
                    "observed_at": timestamp(observed),
                    "source_row_sha256": source_hash(f"climate-{observed.isoformat()}"),
                    "values": {
                        "temp_avg_f": 75.0,
                        "temp_east_f": 75.0,
                        "temp_north_f": 75.0,
                        "temp_south_f": 75.0,
                        "temp_west_f": 75.0,
                        "vpd_avg_kpa": 1.5,
                        "vpd_east_kpa": 1.5,
                        "vpd_north_kpa": 1.5,
                        "vpd_south_kpa": 1.5,
                        "vpd_west_kpa": 1.5,
                    },
                }
            )

    equipment = {}
    for index, stream in enumerate(EQUIPMENT_STREAMS):
        seed_observed = WINDOW_START - timedelta(seconds=60)
        start_observed = WINDOW_START - timedelta(seconds=30)
        end_observed = WINDOW_END - timedelta(seconds=30)
        native_unit = "minutes" if not stream.startswith("mister_") else "hours"
        seeds = {
            component: {
                "device_uptime_seconds": 100.0,
                "firmware_revision": "firmware-v2",
                "recorded_at": timestamp(seed_observed + timedelta(seconds=1)),
                "snapshot_id": SNAPSHOT,
                "source_bundle_sha256": "a" * 64,
                "source_connection_generation": 1,
                "source_epoch_id": DIRECT_EPOCH,
                "source_observed_at": timestamp(seed_observed),
                "source_row_sha256": source_hash(f"seed-{component}"),
                "source_runtime_instance_id": RUNTIME,
                "state": False,
                "stream": component,
            }
            for component in EQUIPMENT_COMPONENTS[stream]
        }

        def counter(label: str, observed: datetime, uptime: float, suffix: int) -> dict[str, Any]:
            return {
                "counter_reset_epoch_id": COUNTER_EPOCH,
                "counter_value_minutes": 0.0,
                "device_uptime_seconds": uptime,
                "firmware_revision": "firmware-v2",
                "native_unit": native_unit,
                "native_value": 0.0,
                "recorded_at": timestamp(observed + timedelta(seconds=1)),
                "sample_id": f"00000000-0000-4000-8000-{suffix:012d}",
                "sample_sha256": source_hash(f"{label}-{stream}"),
                "source_connection_generation": 1,
                "source_observed_at": timestamp(observed),
                "source_runtime_instance_id": RUNTIME,
                "stream": stream,
            }

        equipment[stream] = {
            "counter_start": counter("start", start_observed, 130.0, index * 2 + 1),
            "counter_end": counter("end", end_observed, 64_900.0, index * 2 + 2),
            "direct_state_components": seeds,
            "transition_components": {component: [] for component in EQUIPMENT_COMPONENTS[stream]},
        }
    return {
        "schema": OUTCOME_SOURCE_SCHEMA,
        "source_kind": kind,
        "subject_id": SUBJECT,
        "local_date": "2026-08-25",
        "timezone": "America/Denver",
        "window_start_at": timestamp(WINDOW_START),
        "window_end_at": timestamp(WINDOW_END),
        "revision_bundle_sha256": REVISION_SHA,
        "outcome_schema_sha256": OUTCOME_SCHEMA_SHA,
        "endpoint_artifact_sha256": endpoint_sha,
        "analyzer_environment_sha256": None if kind == "shadow" else ANALYZER_SHA,
        "climate_observations": climate,
        "corridors": corridors,
        "equipment_streams": equipment,
        "equipment_ingestion_receipt_chain": complete_receipt_chain(),
        "equipment_source_map_revision": EQUIPMENT_SOURCE_MAP_REVISION,
        "equipment_source_map_sha256": EQUIPMENT_SOURCE_MAP_SHA256,
        "selector_context_status": "frozen" if kind == "shadow" else None,
        "selector_failure_reason": None,
        "delivery_failed": False,
        "fallback_used": False,
        "facility_rescue": False,
    }


def candidate_from_bundle(bundle: dict[str, Any]) -> OutcomeSourceCandidate:
    # PostgreSQL jsonb::text owns the source spelling; the adapter binds these
    # exact bytes and deliberately does not reserialize them.
    raw = json.dumps(bundle, sort_keys=True, separators=(", ", ": ")).encode()
    return OutcomeSourceCandidate.from_row(
        {
            "source_kind": bundle["source_kind"],
            "subject_id": SUBJECT,
            "local_date": date(2026, 8, 25),
            "timezone": "America/Denver",
            "window_start_at": WINDOW_START,
            "window_end_at": WINDOW_END,
            "outcome_schema_sha256": OUTCOME_SCHEMA_SHA,
            "endpoint_artifact_sha256": bundle["endpoint_artifact_sha256"],
            "source_bundle_canonical": raw,
            "source_bundle_sha256": hashlib.sha256(raw).hexdigest(),
            "delivery_failed": bundle["delivery_failed"],
            "fallback_used": bundle["fallback_used"],
            "facility_rescue": bundle["facility_rescue"],
            "resolved_at": RESOLVED,
        }
    )


def outcome_fixture(tmp_path: Path, *, kind: str = "randomized") -> tuple[OutcomeSourceCandidate, Path]:
    path, endpoint_sha = identity_file(tmp_path)
    return candidate_from_bundle(complete_source_bundle(endpoint_sha, kind=kind)), path


def transition(
    bundle: dict[str, Any],
    component: str,
    observed_at: datetime,
    state: bool,
    _label: str,
) -> dict[str, object]:
    receipts = bundle["equipment_ingestion_receipt_chain"]["receipts"]
    receipt = next(
        current
        for previous, current in zip(receipts, receipts[1:], strict=False)
        if datetime.strptime(previous["source_observed_through"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
        < observed_at
        <= datetime.strptime(current["source_observed_through"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    )
    event = {
        "equipment": component,
        "source_observed_at": timestamp(observed_at),
        "state": state,
    }
    receipt["events"].append(event)
    receipt["events"].sort(key=lambda row: (row["source_observed_at"], row["equipment"]))
    receipt["event_count"] = len(receipt["events"])
    receipt["events_sha256"] = events_sha256(receipt["events"])
    payload = {
        "observed_at": timestamp(observed_at),
        "source_receipt_id": receipt["receipt_id"],
        "source_receipt_sequence": receipt["source_sequence"],
        "source_receipt_sha256": receipt["receipt_sha256"],
        "state": state,
        "stream": component,
    }
    payload["source_row_sha256"] = hashlib.sha256(
        b"verdify-experiment-v2-outcome-state-transition-v1\x00" + _postgres_jsonb_text(payload).encode()
    ).hexdigest()
    return payload


def set_counter_end_minutes(bundle: dict[str, Any], stream: str, minutes: float) -> None:
    counter = bundle["equipment_streams"][stream]["counter_end"]
    counter["counter_value_minutes"] = minutes
    counter["native_value"] = minutes if counter["native_unit"] == "minutes" else minutes / 60.0


def test_locked_evaluator_accepts_complete_hash_bound_source(tmp_path: Path) -> None:
    candidate, identity_path = outcome_fixture(tmp_path)
    outcome = evaluate_outcome(candidate, identity_path=identity_path)
    assert outcome.temperature_corridor_distance_f == 0.0
    assert outcome.vpd_corridor_distance_kpa == 0.0
    assert outcome.nine_control_state_minutes == 0.0
    assert outcome.climate_missing_reason is None
    assert outcome.equipment_missing_reason is None


def test_postgres_jsonb_event_spelling_matches_pg15_canonical_bytes() -> None:
    assert _postgres_jsonb_text(
        {
            "equipment": "heat1",
            "source_observed_at": "2026-01-01T00:00:00.000000Z",
            "state": True,
        }
    ) == ('{"state": true, "equipment": "heat1", "source_observed_at": "2026-01-01T00:00:00.000000Z"}')


def test_equipment_source_map_hash_and_adapter_parity_are_canonical() -> None:
    payload = json.loads(SOURCE_MAP_ARTIFACT.read_text())
    canonical = canonical_json_bytes(payload, reject_forbidden_fields=False)
    assert hashlib.sha256(canonical).hexdigest() == EQUIPMENT_SOURCE_MAP_SHA256
    assert payload["revision"] == EQUIPMENT_SOURCE_MAP_REVISION
    assert payload["timezone"] == "America/Denver"
    assert payload["counter_publish_interval_seconds"] == 30
    assert set(payload["streams"]) == set(EQUIPMENT_COMPONENTS)
    raw_components = [component for components in EQUIPMENT_COMPONENTS.values() for component in components]
    assert len(raw_components) == len(set(raw_components)) == 11
    for logical, components in EQUIPMENT_COMPONENTS.items():
        source = payload["streams"][logical]
        assert len(source["state_object_ids"]) == len(components)
        assert source["state_reduce"] == ("identity" if len(components) == 1 else "boolean_or_require_no_overlap")


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda bundle: bundle["equipment_streams"]["heat1"]["direct_state_components"].__setitem__("heat1", None),
            "direct_state_snapshot_unavailable",
        ),
        (
            lambda bundle: bundle["equipment_streams"]["heat1"].__setitem__("counter_start", None),
            "counter_samples_unavailable",
        ),
        (
            lambda bundle: bundle["equipment_streams"]["heat1"]["counter_end"].__setitem__(
                "counter_reset_epoch_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            ),
            "counter_reset_or_wrap",
        ),
        (
            lambda bundle: bundle["equipment_streams"]["heat1"]["counter_end"].update(
                {"counter_value_minutes": 20.0, "native_value": 20.0}
            ),
            "counter_state_reconciliation",
        ),
        (
            lambda bundle: bundle["equipment_streams"]["heat1"]["counter_start"].__setitem__(
                "device_uptime_seconds", 200.0
            ),
            "counter_reset_or_wrap",
        ),
        (
            lambda bundle: bundle["equipment_streams"]["heat1"]["direct_state_components"]["heat1"].__setitem__(
                "source_observed_at", timestamp(WINDOW_START)
            ),
            "direct_state_snapshot_invalid",
        ),
    ],
)
def test_equipment_missing_and_lineage_failures_are_explicit(
    tmp_path: Path,
    mutation,
    reason: str,
) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha)
    mutation(bundle)
    outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
    assert outcome.nine_control_state_minutes is None
    assert outcome.equipment_missing_reason == reason
    assert outcome.temperature_corridor_distance_f == 0.0


def test_malformed_or_unbound_source_never_fabricates_values(tmp_path: Path) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha)
    bundle["ingestion_gap"] = True
    outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
    assert outcome == OutcomePayload.missing(
        source_bundle_sha256=outcome.source_bundle_sha256,
        climate_reason="source_contract_invalid",
        equipment_reason="source_contract_invalid",
    )

    candidate = candidate_from_bundle(complete_source_bundle(endpoint_sha))
    missing_identity = evaluate_outcome(candidate, identity_path=tmp_path / "absent.json")
    assert missing_identity.climate_missing_reason == "source_contract_invalid"
    assert missing_identity.equipment_missing_reason == "source_contract_invalid"


def test_ingestion_receipt_chain_must_match_source_lineage(tmp_path: Path) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha)
    for receipt in bundle["equipment_ingestion_receipt_chain"]["receipts"]:
        receipt["connection_generation"] = 2
    rehash_receipt_chain(bundle["equipment_ingestion_receipt_chain"])
    outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
    assert outcome.temperature_corridor_distance_f == 0.0
    assert outcome.nine_control_state_minutes is None
    assert outcome.equipment_missing_reason == "counter_reset_or_wrap"


def test_absent_ingestion_receipt_chain_is_explicitly_missing_not_continuity_inference(
    tmp_path: Path,
) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha)
    bundle["equipment_ingestion_receipt_chain"] = None
    outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
    assert outcome.temperature_corridor_distance_f == 0.0
    assert outcome.nine_control_state_minutes is None
    assert outcome.equipment_missing_reason == "source_contract_invalid"


@pytest.mark.parametrize(
    "mutation",
    (
        lambda chain: chain["receipts"][1].__setitem__("source_sequence", 999_999),
        lambda chain: chain["receipts"][1].__setitem__("previous_receipt_sha256", "f" * 64),
        lambda chain: chain["receipts"][1].update({"gap_before": True, "gap_reason": "source_time_gap"}),
        lambda chain: chain["receipts"][-1].__setitem__(
            "source_observed_through",
            timestamp(WINDOW_END - timedelta(microseconds=1)),
        ),
        lambda chain: chain["receipts"][1].__setitem__("events_sha256", "e" * 64),
    ),
)
def test_broken_ingestion_receipt_chain_fails_source_contract(
    tmp_path: Path,
    mutation,
) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha)
    mutation(bundle["equipment_ingestion_receipt_chain"])
    outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
    assert outcome.temperature_corridor_distance_f == 0.0
    assert outcome.nine_control_state_minutes is None
    assert outcome.equipment_missing_reason == "source_contract_invalid"


def test_receipt_hash_binds_events_even_when_projection_and_event_hash_are_rewritten(
    tmp_path: Path,
) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha)
    projected = transition(
        bundle,
        "heat1",
        WINDOW_START + timedelta(hours=1),
        True,
        "tamper-target",
    )
    bundle["equipment_streams"]["heat1"]["transition_components"]["heat1"] = [projected]
    set_counter_end_minutes(bundle, "heat1", 1_019.5)
    seal_receipt_chain(bundle)

    receipts = bundle["equipment_ingestion_receipt_chain"]["receipts"]
    receipt = next(item for item in receipts if item["events"])
    receipt["events"][0]["state"] = False
    receipt["events_sha256"] = events_sha256(receipt["events"])
    projected["state"] = False
    projection_preimage = {
        key: projected[key]
        for key in (
            "observed_at",
            "source_receipt_id",
            "source_receipt_sequence",
            "source_receipt_sha256",
            "state",
            "stream",
        )
    }
    projected["source_row_sha256"] = hashlib.sha256(
        b"verdify-experiment-v2-outcome-state-transition-v1\x00" + _postgres_jsonb_text(projection_preimage).encode()
    ).hexdigest()
    # Deliberately leave receipt_sha256 and its successor pointer untouched.
    outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
    assert outcome.temperature_corridor_distance_f == 0.0
    assert outcome.nine_control_state_minutes is None
    assert outcome.equipment_missing_reason == "source_contract_invalid"


def test_hash_valid_unknown_receipt_stream_is_not_treated_as_ledger_evidence(
    tmp_path: Path,
) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha)
    chain = bundle["equipment_ingestion_receipt_chain"]
    receipts = chain["receipts"]
    prior_barrier = datetime.strptime(
        receipts[0]["source_observed_through"],
        "%Y-%m-%dT%H:%M:%S.%fZ",
    ).replace(tzinfo=UTC)
    receipts[1]["events"].append(
        {
            "equipment": "invented_source_stream",
            "source_observed_at": timestamp(prior_barrier + timedelta(seconds=1)),
            "state": True,
        }
    )
    rehash_receipt_chain(chain)
    outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
    assert outcome.temperature_corridor_distance_f == 0.0
    assert outcome.nine_control_state_minutes is None
    assert outcome.equipment_missing_reason == "source_contract_invalid"


def test_anchor_precoverage_gap_is_allowed_but_later_gap_is_not(tmp_path: Path) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha)
    chain = bundle["equipment_ingestion_receipt_chain"]
    assert chain["receipts"][0]["gap_before"] is True
    outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
    assert outcome.equipment_missing_reason is None


def test_initial_anchor_may_preserve_startup_requested_gap_outside_coverage(
    tmp_path: Path,
) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha)
    receipts = bundle["equipment_ingestion_receipt_chain"]["receipts"]
    for sequence, receipt in enumerate(receipts, start=1):
        receipt["source_sequence"] = sequence
    receipts[0].update(
        {
            "previous_receipt_sha256": None,
            "gap_before": True,
            "gap_reason": "initial_receipt",
            "gap_requested": True,
        }
    )
    rehash_receipt_chain(bundle["equipment_ingestion_receipt_chain"])
    outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
    assert outcome.equipment_missing_reason is None


@pytest.mark.parametrize(
    "mutation",
    (
        lambda bundle: bundle["equipment_streams"]["heat1"]["counter_start"].__setitem__(
            "device_uptime_seconds", 1_000_000_001.0
        ),
        lambda bundle: bundle["equipment_streams"]["heat1"]["counter_end"].update(
            {"native_value": 1_500.1, "counter_value_minutes": 1_500.1}
        ),
        lambda bundle: bundle["equipment_streams"]["mister_center"]["counter_end"].update(
            {"native_value": 25.1, "counter_value_minutes": 1_506.0}
        ),
    ),
)
def test_raw_source_rows_outside_sql_native_bounds_fail_closed(
    tmp_path: Path,
    mutation,
) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha)
    mutation(bundle)
    outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
    assert outcome.temperature_corridor_distance_f == 0.0
    assert outcome.nine_control_state_minutes is None
    assert outcome.equipment_missing_reason == "source_contract_invalid"


def test_shadow_allows_null_analyzer_but_randomized_does_not(tmp_path: Path) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    shadow = evaluate_outcome(
        candidate_from_bundle(complete_source_bundle(endpoint_sha, kind="shadow")),
        identity_path=identity_path,
    )
    assert shadow.climate_missing_reason is None
    randomized_bundle = complete_source_bundle(endpoint_sha)
    randomized_bundle["analyzer_environment_sha256"] = None
    randomized = evaluate_outcome(
        candidate_from_bundle(randomized_bundle),
        identity_path=identity_path,
    )
    assert randomized.climate_missing_reason == "source_contract_invalid"


def test_shadow_unavailable_selector_context_freezes_nulls_without_raw_evaluation(
    tmp_path: Path,
) -> None:
    _identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha, kind="shadow")
    bundle["selector_context_status"] = "unavailable"
    bundle["selector_failure_reason"] = "no_usable_precutoff_climate_source"
    bundle["equipment_streams"] = {"untrusted_raw_source": "must-not-be-evaluated"}
    outcome = evaluate_outcome(
        candidate_from_bundle(bundle),
        identity_path=tmp_path / "identity-deliberately-unavailable.json",
    )
    assert outcome == OutcomePayload.missing(
        source_bundle_sha256=outcome.source_bundle_sha256,
        climate_reason="source_unavailable",
        equipment_reason="source_unavailable",
    )


def test_selector_status_and_equipment_map_are_exactly_source_bound(tmp_path: Path) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    for field, value in (
        ("equipment_source_map_sha256", "0" * 64),
        ("selector_context_status", "unavailable"),
        ("selector_failure_reason", "private-provider-detail"),
    ):
        bundle = complete_source_bundle(endpoint_sha)
        bundle[field] = value
        outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
        assert outcome.climate_missing_reason == "source_contract_invalid"
        assert outcome.equipment_missing_reason == "source_contract_invalid"


def test_combined_mister_components_use_atomic_or_handoff(tmp_path: Path) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha)
    south = bundle["equipment_streams"]["mister_south"]
    south["direct_state_components"]["mister_south"]["state"] = True
    handoff = WINDOW_START + timedelta(hours=1)
    south["transition_components"]["mister_south"] = [
        transition(bundle, "mister_south", handoff, False, "south-normal-off")
    ]
    south["transition_components"]["mister_south_fert"] = [
        transition(bundle, "mister_south_fert", handoff, True, "south-fert-on")
    ]
    set_counter_end_minutes(bundle, "mister_south", 1_080.0)
    seal_receipt_chain(bundle)
    outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
    assert outcome.nine_control_state_minutes == 1_080.0
    assert outcome.equipment_missing_reason is None


@pytest.mark.parametrize("overlap_kind", ["seeded", "positive_interval"])
def test_combined_mister_positive_duration_overlap_fails_closed(
    tmp_path: Path,
    overlap_kind: str,
) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha)
    south = bundle["equipment_streams"]["mister_south"]
    south["direct_state_components"]["mister_south"]["state"] = True
    if overlap_kind == "seeded":
        south["direct_state_components"]["mister_south_fert"]["state"] = True
        south["transition_components"]["mister_south"] = [
            transition(bundle, "mister_south", WINDOW_START, False, "seeded-overlap-end")
        ]
    else:
        overlap_start = WINDOW_START + timedelta(hours=1)
        overlap_end = overlap_start + timedelta(seconds=30)
        south["transition_components"]["mister_south_fert"] = [
            transition(bundle, "mister_south_fert", overlap_start, True, "overlap-start"),
            transition(bundle, "mister_south_fert", overlap_end, False, "overlap-end"),
        ]
    # A 30-second double-count is below the evaluator's ordinary one-minute
    # tolerance, so this proves the component adapter rejects the ambiguity
    # explicitly instead of relying on eventual counter mismatch.
    set_counter_end_minutes(bundle, "mister_south", 1_080.5)
    seal_receipt_chain(bundle)
    outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
    assert outcome.nine_control_state_minutes is None
    assert outcome.equipment_missing_reason == "counter_state_reconciliation"


def test_component_conflict_and_missing_fertilized_seed_never_collapse(tmp_path: Path) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    missing = complete_source_bundle(endpoint_sha)
    missing["equipment_streams"]["mister_west"]["direct_state_components"]["mister_west_fert"] = None
    missing_outcome = evaluate_outcome(candidate_from_bundle(missing), identity_path=identity_path)
    assert missing_outcome.equipment_missing_reason == "direct_state_snapshot_unavailable"

    conflict = complete_source_bundle(endpoint_sha)
    moment = WINDOW_START + timedelta(hours=2)
    rows = [
        transition(conflict, "mister_west_fert", moment, False, "conflict-false"),
        transition(conflict, "mister_west_fert", moment, True, "conflict-true"),
    ]
    conflict["equipment_streams"]["mister_west"]["transition_components"]["mister_west_fert"] = sorted(
        rows, key=lambda row: row["source_row_sha256"]
    )
    seal_receipt_chain(conflict)
    conflict_outcome = evaluate_outcome(
        candidate_from_bundle(conflict),
        identity_path=identity_path,
    )
    assert conflict_outcome.equipment_missing_reason == "source_contract_invalid"


def test_identical_unknown_commit_transition_retry_collapses_safely(tmp_path: Path) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha)
    moment = WINDOW_START + timedelta(hours=1)
    first = transition(bundle, "heat1", moment, True, "identical-retry-1")
    second = transition(bundle, "heat1", moment, True, "identical-retry-2")
    bundle["equipment_streams"]["heat1"]["transition_components"]["heat1"] = [
        first,
        second,
    ]
    # Counter interval ends 30 seconds before the window, so it contains
    # 16h59m30s of active state while the locked window endpoint is 17h.
    set_counter_end_minutes(bundle, "heat1", 1_019.5)
    seal_receipt_chain(bundle)
    outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
    assert outcome.nine_control_state_minutes == 1_020.0
    assert outcome.equipment_missing_reason is None


def test_transition_projection_must_exactly_match_receipt_events(tmp_path: Path) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha)
    transition(bundle, "heat1", WINDOW_START + timedelta(hours=1), True, "receipt-only")
    seal_receipt_chain(bundle)
    outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
    assert outcome.temperature_corridor_distance_f == 0.0
    assert outcome.nine_control_state_minutes is None
    assert outcome.equipment_missing_reason == "source_contract_invalid"


def test_receipt_coverage_start_must_equal_earliest_of_all_eleven_seeds(
    tmp_path: Path,
) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha)
    bundle["equipment_ingestion_receipt_chain"]["coverage_start_at"] = timestamp(WINDOW_START - timedelta(seconds=59))
    outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
    assert outcome.equipment_missing_reason == "source_contract_invalid"


def test_valid_seed_burst_may_span_multiple_continuous_receipt_intervals(
    tmp_path: Path,
) -> None:
    identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha)
    late_seed_at = WINDOW_START - timedelta(seconds=10)
    late_seed = bundle["equipment_streams"]["mister_west"]["direct_state_components"]["mister_west_fert"]
    late_seed["source_observed_at"] = timestamp(late_seed_at)
    late_seed["recorded_at"] = timestamp(late_seed_at + timedelta(seconds=1))
    late_seed["device_uptime_seconds"] = 130.0
    start_counter = bundle["equipment_streams"]["mister_west"]["counter_start"]
    start_counter["source_observed_at"] = timestamp(WINDOW_START - timedelta(seconds=5))
    start_counter["recorded_at"] = timestamp(WINDOW_START - timedelta(seconds=4))
    start_counter["device_uptime_seconds"] = 131.0
    outcome = evaluate_outcome(candidate_from_bundle(bundle), identity_path=identity_path)
    assert outcome.equipment_missing_reason is None


class Acquire:
    def __init__(self, connection) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class Connection:
    def __init__(self, rows) -> None:
        self.rows = list(rows)
        self.calls = []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        return self.rows.pop(0)


class Pool:
    def __init__(self, connection) -> None:
        self.connection = connection

    def acquire(self):
        return Acquire(self.connection)

    async def close(self):
        return None


def attested(connection: Connection) -> AttestedPool:
    from experiment_orchestrator.contracts import OrchestratorMode

    return AttestedPool(Pool(connection), OrchestratorMode.FREEZER)


@pytest.mark.asyncio
async def test_freezer_store_uses_one_source_call_then_kind_specific_write(tmp_path: Path) -> None:
    candidate, identity_path = outcome_fixture(tmp_path)
    source_row = {field: getattr(candidate, field) for field in candidate.__dataclass_fields__}
    connection = Connection([source_row, {"assignment_id": SUBJECT}])
    store = OutcomeFunctionStore(attested(connection))
    disposition = await run_outcome_cycle(
        store,
        experiment_id=EXPERIMENT,
        identity_path=identity_path,
    )
    assert disposition == "frozen"
    assert len(connection.calls) == 2
    source_query, source_args = connection.calls[0]
    write_query, write_args = connection.calls[1]
    assert "fn_experiment_v2_outcome_source_cycle" in source_query
    assert source_args == (EXPERIMENT,)
    assert "fn_experiment_v2_freeze_outcome" in write_query
    assert write_args[:2] == (EXPERIMENT, SUBJECT)
    assert json.loads(write_args[2]) == {
        "schema": "verdify-assigned-day-outcome-v2",
        "temperature_corridor_distance_f": 0.0,
        "vpd_corridor_distance_kpa": 0.0,
        "nine_control_state_minutes": 0.0,
        "climate_missing_reason": None,
        "equipment_missing_reason": None,
        "source_bundle_sha256": candidate.source_bundle_sha256,
    }
    assert write_args[3:8] == (False, False, False, True, False)


@pytest.mark.asyncio
async def test_shadow_store_never_calls_randomized_freeze(tmp_path: Path) -> None:
    candidate, identity_path = outcome_fixture(tmp_path, kind="shadow")
    source_row = {field: getattr(candidate, field) for field in candidate.__dataclass_fields__}
    connection = Connection([source_row, {"cycle_id": SUBJECT}])
    disposition = await run_outcome_cycle(
        OutcomeFunctionStore(attested(connection)),
        experiment_id=EXPERIMENT,
        identity_path=identity_path,
    )
    assert disposition == "frozen"
    assert "fn_experiment_v2_record_shadow_outcome_preview" in connection.calls[1][0]
    assert "freeze_outcome" not in connection.calls[1][0]


@pytest.mark.asyncio
async def test_unavailable_shadow_cycle_persists_exact_all_null_preview(tmp_path: Path) -> None:
    _identity_path, endpoint_sha = identity_file(tmp_path)
    bundle = complete_source_bundle(endpoint_sha, kind="shadow")
    bundle["selector_context_status"] = "unavailable"
    bundle["selector_failure_reason"] = "source_relation_unavailable"
    candidate = candidate_from_bundle(bundle)
    source_row = {field: getattr(candidate, field) for field in candidate.__dataclass_fields__}
    connection = Connection([source_row, {"cycle_id": SUBJECT}])
    disposition = await run_outcome_cycle(
        OutcomeFunctionStore(attested(connection)),
        experiment_id=EXPERIMENT,
        identity_path=tmp_path / "not-required-for-unavailable.json",
    )
    assert disposition == "frozen_missing"
    payload = json.loads(connection.calls[1][1][2])
    assert payload == {
        "schema": "verdify-assigned-day-outcome-v2",
        "temperature_corridor_distance_f": None,
        "vpd_corridor_distance_kpa": None,
        "nine_control_state_minutes": None,
        "climate_missing_reason": "source_unavailable",
        "equipment_missing_reason": "source_unavailable",
        "source_bundle_sha256": candidate.source_bundle_sha256,
    }


def test_candidate_rejects_caller_time_flags_and_extra_result_fields(tmp_path: Path) -> None:
    candidate, _identity_path = outcome_fixture(tmp_path)
    row = {field: copy.deepcopy(getattr(candidate, field)) for field in candidate.__dataclass_fields__}
    row["accepted_at"] = RESOLVED
    with pytest.raises(Exception, match="shape mismatch"):
        OutcomeSourceCandidate.from_row(row)
