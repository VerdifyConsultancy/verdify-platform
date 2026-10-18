"""Pure contract tests for the non-actuating direct-proof packet collector."""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import os
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/experiment_v2_proof_packet.py"
SPEC = importlib.util.spec_from_file_location("experiment_v2_proof_packet", SCRIPT)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)


def test_writer_log_request_accepts_kubernetes_stream(monkeypatch) -> None:
    reader = collector.KubeReader.__new__(collector.KubeReader)
    seen = {}

    def get(path, *, accept):
        seen.update(path=path, accept=accept)
        return b"writer attestation\n"

    monkeypatch.setattr(reader, "_get", get)
    assert reader.logs("writer-pod") == "writer attestation\n"
    assert seen["path"].endswith("/pods/writer-pod/log?container=ingestor&timestamps=true")
    assert seen["accept"] == "*/*"


def _stability(logs: str, started_at: str, generation: int, *, observed_at: datetime | None = None):
    return collector.writer_stability(
        logs,
        started_at,
        generation,
        observed_at=observed_at or datetime(2026, 10, 14, 12, 20, tzinfo=UTC),
        source_revision="b" * 40,
        pod="writer-pod",
    )


def _writer_line(seconds: int, message: str) -> str:
    return f"2026-10-14T12:{seconds // 60:02d}:{seconds % 60:02d}Z INFO {message}"


def _writer_attestation(seconds: int, generation: int = 1, receipt: str = "a") -> str:
    return _writer_line(
        seconds,
        f"component_entity_grid_attestation status=pass grid_revision=grid "
        f"observation_receipt_sha256={receipt * 64} field_count=48 firmware_revision=firmware "
        f"source_revision={'b' * 40} connection_generation={generation}",
    )


def _writer_replay(seconds: int, generation: int = 1, *, seeded: bool = False) -> str:
    message = (
        "seeded=241 unobserved_restore_limit=0 comparison=current_generation_readbacks"
        if seeded
        else ("action=awaiting_readbacks expected_readback_count=207")
    )
    return _writer_line(seconds, f"writer_reconcile reason=transport_reconnect generation={generation} {message}")


def _ready_writer() -> list[str]:
    return [_writer_replay(1), _writer_attestation(2), _writer_replay(3, seeded=True)]


def _run_observation(seconds: int, phase: str, **changes) -> dict:
    value = {
        "schema": "verdify-writer-reconciliation-observation/v1",
        "phase": phase,
        "observed_at": _writer_line(seconds, "").split()[0],
        "source_revision": "b" * 40,
        "pod": "writer-pod",
        "process_sha256": "a" * 64,
        "connection_generation": 1,
        "run_id": ("1" if phase == "hold" else "2") * 32,
        "approved_fingerprint": ("e" if phase == "hold" else "f") * 64,
        "state_sha256": ("d" if phase == "hold" else "b") * 64,
        "effects_sha256": "f" * 64,
        "status": "halted" if phase == "hold" else "complete",
        "completed_count": 12 if phase == "hold" else 4,
        "staged_pending_count": 0,
        "handoff_requested_count": 0,
        "handoff_confirmed_count": None if phase == "hold" else 0,
        "handoff_settled": phase == "complete",
        "rollback_count": 0,
        "archive_sha256": "c" * 64 if phase == "hold" else None,
        "supersedes": None
        if phase == "hold"
        else {
            "run_id": "1" * 32,
            "approved_fingerprint": "e" * 64,
            "state_sha256": "d" * 64,
            "archive_sha256": "c" * 64,
        },
    }
    value.update(changes)
    return {**value, "receipt_sha256": collector.receipt(value)}


def _observation_line(seconds: int, phase: str, **changes) -> str:
    body = json.dumps(_run_observation(seconds, phase, **changes), sort_keys=True, separators=(",", ":"))
    message = (
        "writer_reconcile reason=retry generation=1 action=blocked_stage reason=confirmed halt archived; fresh approval required reconciliation_receipt="
        if phase == "hold"
        else "writer_reconciliation_complete receipt="
    )
    return _writer_line(seconds, message + body)


def test_linked_settled_completion_starts_new_quiet_window() -> None:
    logs = [
        *_ready_writer(),
        _observation_line(10, "hold", archive_sha256=None),
        _observation_line(20, "hold"),
        _observation_line(40, "complete"),
    ]
    stable, errors = _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)
    assert stable == datetime(2026, 10, 14, 12, 0, 40, tzinfo=UTC)
    assert errors == 0
    assert datetime(2026, 10, 14, 12, 20, tzinfo=UTC) - stable < timedelta(minutes=30)


@pytest.mark.parametrize(
    "legacy",
    [
        "writer_reconcile reason=retry generation=1 action=blocked_stage reason=unapproved candidate appeared",
        "writer_reconcile reason=retry generation=1 action=blocked_stage reason=unapproved candidate appeared reconciliation_receipt=null",
        "writer_reconcile reason=retry generation=1 action=blocked_broad_restore command_count=16 limit=12",
    ],
)
def test_linked_completion_cannot_clear_legacy_unbound_hold(legacy: str) -> None:
    logs = [*_ready_writer(), _writer_line(8, legacy), _observation_line(10, "hold"), _observation_line(40, "complete")]
    with pytest.raises(collector.CollectionError, match="unresolved"):
        _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)


@pytest.mark.parametrize("mismatch", ["run_id", "approved_fingerprint", "state_sha256", "archive_sha256", "absent"])
def test_completion_requires_exact_archived_hold_link(mismatch: str) -> None:
    link = _run_observation(40, "complete")["supersedes"]
    if mismatch == "absent":
        link = None
    else:
        link[mismatch] = "3" * (32 if mismatch == "run_id" else 64)
    logs = [*_ready_writer(), _observation_line(10, "hold"), _observation_line(40, "complete", supersedes=link)]
    with pytest.raises(collector.CollectionError, match="bound historical hold|unresolved"):
        _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)


@pytest.mark.parametrize(
    "changes",
    [
        {"source_revision": "c" * 40},
        {"pod": "other-pod"},
        {"process_sha256": "0" * 64},
        {"connection_generation": 2},
        {"staged_pending_count": 1},
        {"rollback_count": 1},
        {"handoff_requested_count": 1},
        {"handoff_settled": False},
        {"status": "awaiting_confirmation"},
        {"observed_at": "2026-10-14T12:00:10Z"},
        {"supersedes": {"run_id": "1" * 32}},
        {"completed_count": -1},
        {"run_id": 5},
        {"effects_sha256": None},
        {"handoff_confirmed_count": True},
        {"connection_generation": True},
    ],
)
def test_unsettled_or_mismatched_completion_denies(changes: dict) -> None:
    logs = [*_ready_writer(), _observation_line(10, "hold"), _observation_line(40, "complete", **changes)]
    with pytest.raises(collector.CollectionError, match="malformed|process changed"):
        _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)


@pytest.mark.parametrize("raw", ["null", "{}", "{", "false", '"text"'])
def test_missing_or_truncated_completion_metadata_cannot_qualify(raw: str) -> None:
    logs = [
        *_ready_writer(),
        _observation_line(10, "hold"),
        _writer_line(40, "writer_reconciliation_complete receipt=" + raw),
    ]
    with pytest.raises(collector.CollectionError, match="malformed"):
        _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)


def test_completion_digest_is_checked_and_cannot_be_replaced_by_equality() -> None:
    changed = _run_observation(40, "complete")
    changed["completed_count"] += 1
    logs = [
        *_ready_writer(),
        _observation_line(10, "hold"),
        _writer_line(40, "writer_reconciliation_complete receipt=" + json.dumps(changed)),
    ]
    with pytest.raises(collector.CollectionError, match="malformed"):
        _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)
    logs[-1] = _writer_line(
        40,
        "writer_dispatch reason=retry generation=1 command_count=0 anchor_count=0 unchanged_anchor_count=0 comparison=desired_vs_observed",
    )
    with pytest.raises(collector.CollectionError, match="unresolved"):
        _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)


def test_completion_does_not_clear_unrelated_or_later_bound_hold() -> None:
    resolved = [*_ready_writer(), _observation_line(10, "hold"), _observation_line(40, "complete")]
    for logs in (
        [*resolved, _observation_line(50, "hold")],
        [*resolved[:4], _observation_line(20, "hold", run_id="3" * 32), *resolved[4:]],
        [*resolved, _observation_line(50, "hold"), resolved[-1]],
    ):
        with pytest.raises(collector.CollectionError, match="unresolved"):
            _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)


def test_later_failure_relatches_and_replayed_completion_cannot_reset_clock() -> None:
    complete = _observation_line(40, "complete")
    logs = [
        *_ready_writer(),
        _observation_line(10, "hold"),
        complete,
        _writer_line(50, "writer_lease: renew error"),
        complete,
    ]
    stable, errors = _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)
    assert stable == datetime(2026, 10, 14, 12, 0, 50, tzinfo=UTC) and errors == 1
    logs = [*_ready_writer(), _observation_line(10, "hold"), complete, complete]
    assert _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)[0] == datetime(2026, 10, 14, 12, 0, 40, tzinfo=UTC)


@pytest.mark.parametrize("second", [0, 1210])
def test_writer_metadata_outside_current_pod_lifetime_denies(second: int) -> None:
    logs = [*_ready_writer(), _observation_line(second, "hold")]
    with pytest.raises(collector.CollectionError, match="outside|malformed"):
        _stability("\n".join(logs), "2026-10-14T12:00:06Z", 1)


def test_writer_logs_use_kubernetes_time_instead_of_naive_application_clock(monkeypatch) -> None:
    reader = collector.KubeReader.__new__(collector.KubeReader)
    raw = b"2026-10-14T06:00:03.123456789-06:00 2026-10-14T01:00:00 INFO writer event\n"
    monkeypatch.setattr(reader, "_get", lambda *_args, **_kwargs: raw)
    logs = reader.logs("writer")
    assert logs == "2026-10-14T06:00:03.123456789-06:00 INFO writer event\n"
    assert collector.parse_time(logs.split()[0]) == datetime(2026, 10, 14, 12, 0, 3, 123456, tzinfo=UTC)


def test_first_complete_readback_comparison_advances_clock_but_repeats_do_not() -> None:
    logs = [*_ready_writer(), _writer_replay(300, seeded=True), _writer_replay(600, seeded=True)]
    stable, errors = _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)
    assert stable == datetime(2026, 10, 14, 12, 0, 3, tzinfo=UTC)
    assert errors == 0


def test_new_connection_and_its_complete_readbacks_start_a_new_stability_window() -> None:
    logs = [*_ready_writer(), _writer_replay(300, 2), _writer_attestation(301, 2), _writer_replay(302, 2, seeded=True)]
    stable, errors = _stability("\n".join(logs), "2026-10-14T12:00:00Z", 2)
    assert stable == datetime(2026, 10, 14, 12, 5, 2, tzinfo=UTC)
    assert errors == 0


def test_grid_attestation_does_not_substitute_for_pending_writable_readbacks() -> None:
    logs = [_writer_replay(1), _writer_attestation(2)]
    with pytest.raises(collector.CollectionError, match="unresolved: readbacks_pending"):
        _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)


def test_missing_connection_history_cannot_fall_back_to_pod_start_and_grid_attestation() -> None:
    with pytest.raises(collector.CollectionError, match="completion evidence is absent"):
        _stability(_writer_attestation(2), "2026-10-14T12:00:00Z", 1)


@pytest.mark.parametrize(
    "failure",
    [
        "writer_lease: renew error",
        "writer_lease: SELF-FENCING",
        "Writer lease LOST — SELF-FENCING: disconnecting ESP32 immediately",
        "component_entity_grid_attestation status=fail code=missing connection_generation=1",
        "ESP32 connection lost (unexpected)",
    ],
)
def test_latest_failure_advances_clock_without_erasing_its_own_error_count(failure: str) -> None:
    # Equal timestamps still represent two errors in the ordered log, and the
    # latest error must not zero the count by moving max(stable_events).
    logs = [*_ready_writer(), _writer_line(10, failure), _writer_line(10, failure)]
    stable, errors = _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)
    assert stable == datetime(2026, 10, 14, 12, 0, 10, tzinfo=UTC)
    assert errors == 2


def test_lease_reacquisition_and_replayed_attestation_do_not_erase_a_failure() -> None:
    logs = [
        *_ready_writer(),
        _writer_line(10, "writer_lease: renew error"),
        _writer_line(11, "writer_lease: ACQUIRED writer as writer-pod"),
        _writer_attestation(12),
    ]
    stable, errors = _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)
    assert stable == datetime(2026, 10, 14, 12, 0, 11, tzinfo=UTC)
    assert errors == 1


def test_fresh_grid_reseal_starts_new_error_epoch_and_thirty_minute_clock() -> None:
    logs = [*_ready_writer(), _writer_line(10, "writer_lease: renew error"), _writer_attestation(12, receipt="c")]
    stable, errors = _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)
    assert stable == datetime(2026, 10, 14, 12, 0, 12, tzinfo=UTC)
    assert errors == 0


def test_persistent_halt_stays_denied_after_comparisons_equality_and_grid_reseal() -> None:
    logs = _ready_writer()
    for seconds in (10, 310):
        logs.extend(
            [
                _writer_replay(seconds, seeded=True),
                _writer_line(
                    seconds + 1,
                    "writer_reconcile reason=transport_reconnect generation=1 "
                    "action=blocked_stage reason=run halted; bounded rollback available",
                ),
            ]
        )
    logs.extend(
        [
            _writer_line(
                320,
                "writer_dispatch reason=transport_reconnect generation=1 command_count=0 anchor_count=0 "
                "unchanged_anchor_count=0 comparison=desired_vs_observed",
            ),
            _writer_attestation(321, receipt="c"),
            _writer_replay(322, 2),
            _writer_attestation(323, 2),
            _writer_replay(324, 2, seeded=True),
            _writer_line(
                325,
                "writer_reconcile reason=transport_reconnect generation=2 "
                "action=blocked_stage reason=awaiting durable confirmation",
            ),
        ]
    )
    with pytest.raises(collector.CollectionError, match="unresolved: blocked_stage; recurring errors=2"):
        _stability("\n".join(logs), "2026-10-14T12:00:00Z", 2)


@pytest.mark.parametrize(
    "reason",
    ["awaiting durable confirmation", "awaiting fresh atomic snapshot: pending", "awaiting rollback confirmation"],
)
def test_normal_confirmation_wait_then_completion_does_not_create_a_permanent_halt(reason: str) -> None:
    # The existing bounded-reconcile confirmation test proves this source path
    # can move from a wait to complete in the same process. Neither status nor
    # the unbound equality trace is authority to clear a *terminal* held run.
    logs = [
        *_ready_writer(),
        _writer_line(
            10,
            "writer_reconcile reason=transport_reconnect generation=1 "
            "action=stage_awaiting_confirmation run_id=run-a failure_count=0",
        ),
        _writer_line(
            11, f"writer_reconcile reason=transport_reconnect generation=1 action=blocked_stage reason={reason}"
        ),
        _writer_line(
            20,
            "writer_dispatch reason=transport_reconnect generation=1 command_count=0 anchor_count=0 "
            "unchanged_anchor_count=0 comparison=desired_vs_observed",
        ),
    ]
    stable, errors = _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)
    assert stable == datetime(2026, 10, 14, 12, 0, 11, tzinfo=UTC)
    assert errors == 0
    assert datetime(2026, 10, 14, 12, 20, tzinfo=UTC) - stable < timedelta(minutes=30)
    # Equality did not clear pending evidence. After actual reporting stops,
    # the full quiet interval can elapse without inventing a permanent gate.
    stable, errors = _stability(
        "\n".join(logs), "2026-10-14T12:00:00Z", 1, observed_at=datetime(2026, 10, 14, 12, 31, tzinfo=UTC)
    )
    assert datetime(2026, 10, 14, 12, 31, tzinfo=UTC) - stable >= timedelta(minutes=30)
    assert errors == 0


def test_ongoing_periodic_normal_wait_cannot_borrow_an_old_transport_stability_window() -> None:
    logs = _ready_writer()
    for seconds in (10, 310, 610, 910, 1210, 1510, 1810):
        logs.append(
            _writer_line(
                seconds,
                "writer_reconcile reason=transport_reconnect generation=1 "
                "action=stage_awaiting_confirmation run_id=run-a failure_count=0",
            )
        )
    observed = datetime(2026, 10, 14, 12, 31, tzinfo=UTC)
    stable, errors = _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1, observed_at=observed)
    assert stable == datetime(2026, 10, 14, 12, 30, 10, tzinfo=UTC)
    assert observed - stable < timedelta(minutes=30)
    assert errors == 0


@pytest.mark.parametrize(
    "failure",
    [
        "writer_reconcile reason=transport_reconnect generation=1 action=incomplete failure_count=1",
        "writer_reconcile reason=transport_reconnect generation=1 action=stage_awaiting_confirmation "
        "run_id=run-a failure_count=1",
        "writer_drift_probe action=hold reason=awaiting probe cfg confirmation",
    ],
)
def test_transient_errors_are_counted_in_stability_interval_without_a_permanent_gate(failure: str) -> None:
    logs = "\n".join([*_ready_writer(), _writer_line(10, failure)])
    stable, errors = _stability(logs, "2026-10-14T12:00:00Z", 1)
    assert stable == datetime(2026, 10, 14, 12, 0, 10, tzinfo=UTC)
    assert errors == 1
    stable, errors = _stability(logs, "2026-10-14T12:00:00Z", 1, observed_at=datetime(2026, 10, 14, 12, 31, tzinfo=UTC))
    assert stable == datetime(2026, 10, 14, 12, 0, 10, tzinfo=UTC)
    assert errors == 0


def test_recurring_transient_error_is_still_counted_after_an_old_one_ages_out() -> None:
    logs = "\n".join(
        [
            *_ready_writer(),
            _writer_line(10, "writer_lease: renew error"),
            _writer_line(1801, "writer_lease: renew error"),
        ]
    )
    stable, errors = _stability(logs, "2026-10-14T12:00:00Z", 1, observed_at=datetime(2026, 10, 14, 12, 31, tzinfo=UTC))
    assert stable == datetime(2026, 10, 14, 12, 30, 1, tzinfo=UTC)
    assert errors == 1


def test_terminal_halt_is_not_waived_when_its_old_error_ages_out() -> None:
    logs = "\n".join(
        [
            *_ready_writer(),
            _writer_line(
                10,
                "writer_reconcile reason=transport_reconnect generation=1 "
                "action=blocked_stage reason=run halted; bounded rollback available",
            ),
        ]
    )
    with pytest.raises(collector.CollectionError, match="unresolved: blocked_stage; recurring errors=0"):
        _stability(logs, "2026-10-14T12:00:00Z", 1, observed_at=datetime(2026, 10, 14, 12, 40, tzinfo=UTC))


def test_unknown_hold_reason_cannot_borrow_the_normal_wait_prefix() -> None:
    logs = "\n".join(
        [
            *_ready_writer(),
            _writer_line(
                10,
                "writer_reconcile reason=transport_reconnect generation=1 "
                "action=blocked_stage reason=awaiting durable confirmation failed; run halted",
            ),
        ]
    )
    with pytest.raises(collector.CollectionError, match="unresolved: blocked_stage"):
        _stability(logs, "2026-10-14T12:00:00Z", 1)


@pytest.mark.parametrize("timestamp", ["bad-time", "2026-10-14T12:00:01", ""])
def test_recognized_lifecycle_event_without_authoritative_time_fails_closed(timestamp: str) -> None:
    logs = f"{timestamp} INFO writer_lease: ACQUIRED writer as pod"
    with pytest.raises(collector.CollectionError, match="timestamp is malformed"):
        _stability(logs, "2026-10-14T12:00:00Z", 1)


def test_new_connection_cannot_reuse_the_old_generation_attestation() -> None:
    logs = [*_ready_writer(), _writer_replay(10, 2), _writer_replay(11, 2, seeded=True)]
    with pytest.raises(collector.CollectionError, match="generations differ"):
        _stability("\n".join(logs), "2026-10-14T12:00:00Z", 1)


def test_replay_generation_regression_fails_closed() -> None:
    logs = [_writer_replay(1, 2), _writer_replay(2, 1)]
    with pytest.raises(collector.CollectionError, match="generation regressed"):
        _stability("\n".join(logs), "2026-10-14T12:00:00Z", 2)


def _climate_row(moment: datetime, offset: float) -> dict:
    row = {
        "ts": moment,
        "temp_avg": 71.0 + offset,
        "rh_avg": 60.0 + offset,
        "vpd_avg": 0.8 + offset,
        "active_probe_count": 4,
        "probe_health": "OK",
    }
    for zone, addition in (("north", 0.0), ("south", None), ("east", 1.0), ("west", 0.5)):
        row[f"temp_{zone}"] = None if addition is None else 70.5 + addition + offset
        row[f"rh_{zone}"] = None if addition is None else 59.5 + addition + offset
        row[f"vpd_{zone}"] = None if addition is None else 0.7 + addition / 10 + offset
    return row


def test_backup_packet_uses_current_controller_job_without_expired_one_off(monkeypatch) -> None:
    now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    cron_uid = "00000000-0000-4000-8000-000000000747"
    job_uid = "00000000-0000-4000-8000-000000000748"
    job = {
        "metadata": {
            "name": "verdify-db-backup-current",
            "uid": job_uid,
            "annotations": {
                "batch.kubernetes.io/cronjob-scheduled-timestamp": collector.zulu(now - timedelta(hours=4))
            },
            "ownerReferences": [{"uid": cron_uid, "controller": True}],
        },
        "status": {"conditions": [{"type": "Complete", "status": "True"}]},
    }
    facts = {
        "cronjob": {"metadata": {"uid": cron_uid}},
        "backup_jobs": [job],
        "observed_at": collector.zulu(now),
    }
    artifact = {
        "job_uid": job_uid,
        "pod_uid": "00000000-0000-4000-8000-000000000749",
        "completed_at": collector.zulu(now - timedelta(hours=4)),
        "artifact": "verdify-20260926T080000Z.dump",
        "artifact_bytes": 1024,
        "restorable": True,
        "partial_artifact": False,
    }
    monkeypatch.setattr(collector, "_backup_artifact", lambda actual, _kube: artifact if actual is job else None)
    packet, complete = collector.backup_evidence(None, facts)
    assert complete
    assert packet["controller_owned"]["cronjob_uid"] == cron_uid
    assert packet["controller_owned"]["job_uid"] == job_uid
    assert "corrected_one_off" not in packet
    assert "source_git_pin" not in packet["controller_owned"]


def test_backup_packet_fails_when_no_controller_owned_success_is_retained() -> None:
    facts = {
        "cronjob": {"metadata": {"uid": "00000000-0000-4000-8000-000000000747"}},
        "backup_jobs": [],
        "observed_at": "2026-09-26T12:00:00Z",
    }
    with pytest.raises(collector.CollectionError, match="no successful controller-owned"):
        collector.backup_evidence(None, facts)


@pytest.mark.parametrize("published", ["wrote", "wrote paired"])
def test_backup_log_selects_published_dump_before_pruned_paths(published: str) -> None:
    log = (
        "[backup] pg_dump -Fc verdify@verdify-db -> /backups/verdify-20260926T081706Z.dump\n"
        f"[backup] {published} /backups/verdify-20260926T081706Z.dump (269.4M)\n"
        "[backup] pruning dumps older than 14d in /backups\n"
        "/backups/verdify-20260910T081717Z.dump\n"
        "/backups/verdify-20260911T081708Z.dump\n"
    )
    assert collector._published_backup_name(log) == "verdify-20260926T081706Z.dump"


@pytest.mark.parametrize(
    "log",
    [
        "[backup] pg_dump -Fc verdify@verdify-db -> /backups/verdify-20260926T081706Z.dump\n",
        "[backup] wrote /backups/verdify-20260926T081706Z.dump (269.4M)\n"
        "[backup] wrote paired /backups/verdify-20260926T081707Z.dump (269.4M)\n",
    ],
)
def test_backup_log_fails_closed_without_unique_publish_line(log: str) -> None:
    with pytest.raises(collector.CollectionError, match="no unique published dump path"):
        collector._published_backup_name(log)


def test_climate_projection_uses_exact_supplied_newest_two_rows_without_filtering() -> None:
    first = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    rows = [_climate_row(first, 0), _climate_row(first + timedelta(minutes=1), 0.01)]
    samples = collector.climate_samples(rows)
    assert len(samples) == 2
    assert [sample["sample_at"] for sample in samples] == [collector.zulu(row["ts"]) for row in rows]
    assert all(sample["declared_contributors"] == ["north", "east", "west"] for sample in samples)
    assert samples[0]["zones"]["south"]["temp_f"]["value"] is None
    assert samples[0]["diagnostics"] == {"active_probe_count": 4, "probe_health": "OK"}


def _passive_db(*, disagreement: bool) -> dict:
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    bands = []
    for index, name in enumerate(("temp_low", "temp_high", "vpd_low", "vpd_high"), start=1):
        served = float(index)
        bands.append(
            {
                "parameter": name,
                "dispatcher_value": served,
                "firmware_setpoint_value": served + (1 if disagreement and name == "temp_low" else 0),
                "cfg_readback_value": served,
                "ts": now,
                "firmware_setpoint_ts": now,
                "cfg_readback_ts": now,
            }
        )
    return {
        "bands": bands,
        "targets": {
            "ts": now,
            "reconstructed_temp_target": 70.0,
            "house_temp_target_f": 70.0,
            "reconstructed_vpd_target": 1.0,
            "house_vpd_target": 1.0,
        },
    }


@pytest.mark.parametrize("disagreement", [False, True])
def test_passive_424_never_promotes_database_equality_to_consumed_proof(disagreement) -> None:
    evidence, raw = collector.passive_424(_passive_db(disagreement=disagreement), observed_at="2026-08-30T12:00:00Z")
    assert evidence == {
        "status": "fail",
        "observed_at": "2026-08-30T12:00:00Z",
        "receipt_sha256": collector.receipt(raw),
        "passive": True,
        "agreement": False,
        "series_checked": 6,
        "device_call_count": 0,
        "lineage_contract_version": 2,
        "disposition": "unobservable",
        "consumed_branch": "unobservable",
    }
    assert raw["schema"] == "verdify-experiment-v2-passive-424-v2"
    assert all(row["status"] == "unobservable" for row in raw["series"])
    assert all("control" not in row and "observed" not in row for row in raw["series"])
    assert raw["runtime_connection_identity_verified"] is False
    assert raw["raw_observation_freshness_verified"] is False
    edge = raw["series"][0]["database_diagnostics"][0]
    assert edge["desired_value"] == (2 if disagreement else 1)
    assert edge["cfg_snapshot_value"] == 1


def test_passive_missing_targets_and_duplicate_rows_remain_diagnostics() -> None:
    db = _passive_db(disagreement=False)
    db["bands"].append(copy.deepcopy(db["bands"][0]))
    db["bands"][-1]["cfg_readback_value"] = 99
    db["targets"] = {}
    evidence, raw = collector.passive_424(db, observed_at="2026-08-30T12:00:00Z")
    assert evidence["agreement"] is False
    assert len(raw["series"][0]["database_diagnostics"]) == 2
    assert raw["series"][-1]["target_snapshot_value"] is None
    assert raw["series"][-1]["raw_slug"] == "house_vpd_target_kpa"


def test_db_only_receipt_blocks_actual_gate_p_evaluation() -> None:
    from test_experiment_v2_readiness_guard import BASE, _evaluate

    packet = copy.deepcopy(BASE)
    evidence, _ = collector.passive_424(_passive_db(disagreement=False), observed_at="2026-08-30T12:00:00Z")
    packet["evidence"]["served_control_observed_424"].update(evidence)
    result = _evaluate(packet, {})
    assert result["status"] != "pass"
    assert "served_control_observed_not_resolved" in result["blockers"]
    assert "served_control_observed_consumed_edges_unobservable" in result["blockers"]


@pytest.mark.parametrize("mode", ["proof", "recovery"])
def test_collect_db_preserves_latest_missing_target_without_claiming_consumption(monkeypatch, mode) -> None:
    class Connection:
        def __init__(self):
            self.queries = []
            self.closed = False

        async def fetchrow(self, sql, *args):
            self.queries.append(sql)
            if "fn_experiment_v2_gate_p_sealed_recovery_target" in sql:
                return {"gate_p_recovery_target": False}
            return None if "house_temp_target_f" in sql else {}

        async def fetch(self, sql, *args):
            self.queries.append(sql)
            return (
                [_climate_row(datetime(2026, 8, 30, 12, i, tzinfo=UTC), 0) for i in (0, 1)]
                if "temp_north" in sql
                else []
            )

        async def close(self):
            self.closed = True

    connection = Connection()

    async def connect(*args):
        return connection

    monkeypatch.setattr(collector, "_connect", connect)
    db = asyncio.run(collector.collect_db("45039c86-c1d9-52f6-a0a9-d94a17bc4b14", mode=mode))
    assert connection.closed
    assert db["targets"] == {}
    assert len(db["climate"]) == 2
    target_sql = next(sql for sql in connection.queries if "house_temp_target_f" in sql)
    assert "b.temp_target AS reconstructed_temp_target" in target_sql
    assert "b.vpd_target AS reconstructed_vpd_target" in target_sql
    assert "LEFT JOIN LATERAL" in target_sql
    assert all("IS NOT NULL" not in sql for sql in connection.queries if "house_temp_target_f" in sql)
    climate_sql = next(sql for sql in connection.queries if "temp_north" in sql)
    assert "greenhouse_id='vallery'" in climate_sql
    assert "greenhouse_id=c.greenhouse_id" in climate_sql
    assert "interval '90 seconds'" in climate_sql


def test_passive_cache_checks_version_and_content_identity(tmp_path) -> None:
    evidence, raw = collector.passive_424(_passive_db(disagreement=False), observed_at="2026-08-30T12:00:00Z")
    payload = {
        "schema": collector.PREFLIGHT_CACHE_SCHEMA,
        "authentication_686": {"receipt_sha256": "a" * 64},
        "provider_preflight": {"receipt_sha256": "b" * 64},
        "served_control_observed_424": evidence,
        "supporting": {"served_control_observed_424": raw},
    }
    cache = tmp_path / "preflight.json"
    cache.write_text(json.dumps(payload))
    assert collector.cached_preflights(cache)["served_control_observed_424"]["disposition"] == "unobservable"
    for change in (
        "old_cache",
        "tampered_support",
        "asserted_pass",
        "support_not_object",
        "root_not_object",
        "rehash_false_resolution",
    ):
        changed = copy.deepcopy(payload)
        if change == "old_cache":
            changed["schema"] = "verdify-experiment-v2-proof-preflight-cache-v1"
        elif change == "tampered_support":
            changed["supporting"]["served_control_observed_424"]["series"][0]["status"] = "resolved"
        elif change == "support_not_object":
            changed["supporting"] = []
        elif change == "root_not_object":
            changed = []
        elif change == "rehash_false_resolution":
            changed["supporting"]["served_control_observed_424"]["series"][0]["status"] = "resolved"
            changed["served_control_observed_424"]["receipt_sha256"] = collector.receipt(
                changed["supporting"]["served_control_observed_424"]
            )
        else:
            changed["served_control_observed_424"]["agreement"] = True
            changed["served_control_observed_424"]["status"] = "pass"
        cache.write_text(json.dumps(changed))
        with pytest.raises(collector.CollectionError):
            collector.cached_preflights(cache)


def test_gate_p_preserves_existing_cache_before_any_external_preflight(tmp_path) -> None:
    cache = tmp_path / "prior.json"
    prior = b'{"schema":"prior-attempt"}\n'
    cache.write_bytes(prior)

    class NoExternalCalls:
        def json(self, *args):
            pytest.fail("existing cache must be checked before external reads or provider calls")

    with pytest.raises(collector.CollectionError, match="already exists"):
        collector.gate_p_preflights(
            kube=NoExternalCalls(),
            db={},
            application_source="a" * 40,
            experiment_id="45039c86-c1d9-52f6-a0a9-d94a17bc4b14",
            cache=cache,
        )
    assert cache.read_bytes() == prior


def test_recovery_preflight_does_not_inherit_resolved_proof_metadata() -> None:
    from test_experiment_v2_readiness_guard import APP_SOURCE, BASE, EXPERIMENT_ID, RECOVERY_OVERLAY, _apply, _evaluate

    result = collector.recovery_preflights(
        BASE,
        observed_at="2026-08-30T12:00:00Z",
        application_source=APP_SOURCE,
        experiment_id=EXPERIMENT_ID,
    )["served_control_observed_424"]
    assert result["disposition"] == "unobservable"
    assert result["consumed_branch"] == "unobservable"
    assert result["agreement"] is False
    assert result["series_checked"] == 0
    recovery = _apply(BASE, RECOVERY_OVERLAY["operations"])
    recovery["evidence"]["served_control_observed_424"].update(result)
    assert _evaluate(recovery, RECOVERY_OVERLAY)["blockers"] == []


def test_planner_context_labels_db_lineage_and_keeps_deployed_mirror() -> None:
    source = (ROOT / "scripts/gather-plan-context.sh").read_text()
    mirror = (ROOT / "deploy/k8s/components/ingestor-gather-script/gather-script-configmap.yaml").read_text()
    for text in ("cfg_* readbacks prove acceptance", "AS firmware_push", "AS crop_target"):
        assert text not in source
        assert text not in mirror
    assert "Matching numbers do not prove acceptance or consumption" in source
    assert "firmware_setpoint_value AS desired_value" in source
    assert "cfg_readback_ts BETWEEN ts - interval '15 minutes' AND ts" in source
    literal = "".join(line if not line.strip("\r\n") else f"    {line}" for line in source.splitlines(keepends=True))
    assert f"  gather-plan-context.sh: |\n{literal}" in mirror


def test_chain_uses_only_guard_committed_predecessor(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    gate = collector.chain("proof", "gate-p", state)
    assert gate["sequence"] == 0 and gate["previous_receipt_sha256"] is None
    state.write_text(
        json.dumps(
            {
                "schema": collector.STATE_SCHEMA,
                "attempt_id": gate["attempt_id"],
                "next_sequence": 1,
                "last_receipt_sha256": "a" * 64,
            }
        )
    )
    baseline = collector.chain("proof", "baseline-before", state)
    assert baseline == {
        "attempt_id": gate["attempt_id"],
        "sequence": 1,
        "previous_receipt_sha256": "a" * 64,
    }
    with pytest.raises(collector.CollectionError, match="sequence"):
        collector.chain("proof", "aggressive", state)


def test_gate_p_authorization_is_current_bounded_and_never_uses_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    monkeypatch.setenv("VERDIFY_DIRECT_PROOF_AUTHORIZATION_REF", "issue-641-attended-proof-window")
    monkeypatch.setenv("VERDIFY_DIRECT_PROOF_AUTHORIZED_FROM", collector.zulu(now - timedelta(minutes=1)))
    monkeypatch.setenv("VERDIFY_DIRECT_PROOF_AUTHORIZED_TO", collector.zulu(now + timedelta(hours=2)))
    assert collector.fresh_gate_p_authorization(now) is True
    monkeypatch.setenv("VERDIFY_DIRECT_PROOF_AUTHORIZATION_REF", "REPLACE_BEFORE_ACTIVATION")
    assert collector.fresh_gate_p_authorization(now) is False


def test_source_dependencies_require_the_reviewed_exact_hashes(tmp_path: Path) -> None:
    template = json.loads((ROOT / "tests/fixtures/experiment-v2-readiness/base-proof.json").read_text())
    dependencies = collector.source_dependencies(template, ROOT, application_source="a" * 40)
    assert dependencies["application_source_revision"] == "a" * 40
    expected = {row["name"]: row["source_sha256"] for row in template["dependencies"]["surfaces"]}
    assert {row["name"]: row["source_sha256"] for row in dependencies["surfaces"]} == expected

    for relative in collector.SURFACES.values():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    (tmp_path / collector.SURFACES["selector"]).write_text("# undeclared causal source change\n")
    with pytest.raises(collector.CollectionError, match="reviewed declaration: selector"):
        collector.source_dependencies(template, tmp_path, application_source="a" * 40)


def test_alert_projection_preserves_unknown_alerts_as_causal_blockers() -> None:
    known = [
        {"id": 1, "alert_type": "sensor_offline", "sensor_id": "climate.temp_south", "disposition": "open"},
        {"id": 2, "alert_type": "sensor_offline", "sensor_id": "climate.rh_south", "disposition": "open"},
        {"id": 3, "alert_type": "sensor_offline", "sensor_id": "climate.vpd_south", "disposition": "open"},
        {"id": 4, "alert_type": "sensor_offline", "sensor_id": "climate.hydro_ph", "disposition": "open"},
    ]
    unknown = {
        "id": 5,
        "alert_type": "component_experiment_integrity",
        "sensor_id": "experiment.v2.current",
        "disposition": "acknowledged",
        "message": "must never enter the packet",
    }
    projected = collector.alert_projection(
        {"open_alerts": [*known, unknown]},
        observed_at="2026-08-30T12:00:00Z",
        experiment_id="45039c86-c1d9-52f6-a0a9-d94a17bc4b14",
    )
    assert {row["scope"] for row in projected[:2]} == {"south_wall_probe", "hydroponic_monitor"}
    assert projected[2] == {
        "alert_id": "5",
        "alert_type": "component_experiment_integrity",
        "scope": "open_alert:component_experiment_integrity:experiment.v2.current",
        "disposition": "acknowledged",
        "observed_at": "2026-08-30T12:00:00Z",
        "classification": "unclassified",
        "causal": True,
        "decision_issue_url": "",
        "maintenance_issue_url": "",
    }
    assert "message" not in projected[2]


def test_alert_projection_classifies_only_exact_source_grounded_exceptions() -> None:
    experiment_id = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"
    required = [
        {"id": 1, "alert_type": "sensor_offline", "sensor_id": "climate.temp_south"},
        {"id": 2, "alert_type": "sensor_offline", "sensor_id": "climate.rh_south"},
        {"id": 3, "alert_type": "sensor_offline", "sensor_id": "climate.vpd_south"},
        {"id": 4, "alert_type": "sensor_offline", "sensor_id": "climate.hydro_ph"},
    ]
    recovery = {
        "id": 5,
        "alert_type": "component_experiment_integrity",
        "severity": "critical",
        "sensor_id": f"experiment.v2.{experiment_id}",
        "source": "system",
        "details": {
            "experiment_id": experiment_id,
            "reason": "expired_work_not_terminal",
            "open_exposure_count": 0,
        },
    }
    historical = {
        "id": 6,
        "ts": "2026-07-01T00:00:00Z",
        "alert_type": "forecast_deviation",
        "sensor_id": "forecast.deviation",
        "source": "ingestor",
    }
    heap = {
        "id": 7,
        "alert_type": "heap_pressure_warning",
        "severity": "warning",
        "sensor_id": "equipment.heap_pressure_warning",
        "source": "system",
        "details": {
            "heap_diag_ts": "2026-08-30T11:59:00Z",
            "heap_free_kb": 75.0,
            "heap_largest_free_block_kb": 50.0,
            "heap_low_watermark_warning": True,
            "heap_fragmentation_warning": False,
            "last_true_ts": None,
            "last_warning_log_ts": None,
            "warning_logs_30m": 0,
        },
    }
    projected = collector.alert_projection(
        {"open_alerts": [*required, recovery, historical, heap]},
        observed_at="2026-08-30T12:00:00Z",
        experiment_id=experiment_id,
    )
    by_id = {row["alert_id"]: row for row in projected[2:]}
    assert by_id["5"]["classification"] == "authorized_recovery_target"
    assert by_id["5"]["causal"] is False
    assert by_id["6"]["classification"] == "informational_noncausal"
    assert by_id["7"]["classification"] == "informational_noncausal"

    heap["details"]["last_true_ts"] = "2026-08-30T11:59:30Z"
    blocked = collector.alert_projection(
        {"open_alerts": [*required, heap]},
        observed_at="2026-08-30T12:00:00Z",
        experiment_id=experiment_id,
    )[-1]
    assert blocked["classification"] == "unclassified"
    assert blocked["causal"] is True


def test_gate_p_fault_projection_requires_sealed_target_and_exact_alert() -> None:
    experiment_id = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"
    fault = {
        "id": 8,
        "alert_type": "component_experiment_integrity",
        "severity": "critical",
        "sensor_id": f"experiment.v2.{experiment_id}",
        "source": "system",
        "details": {
            "experiment_id": experiment_id,
            "reason": "runtime_fault_requires_recovery",
            "open_exposure_count": 0,
        },
    }

    def projected(sealed: bool) -> dict:
        return collector.alert_projection(
            {
                "open_alerts": [
                    {"id": 1, "alert_type": "sensor_offline", "sensor_id": "climate.temp_south"},
                    {"id": 2, "alert_type": "sensor_offline", "sensor_id": "climate.rh_south"},
                    {"id": 3, "alert_type": "sensor_offline", "sensor_id": "climate.vpd_south"},
                    {"id": 4, "alert_type": "sensor_offline", "sensor_id": "climate.hydro_ph"},
                    fault,
                ],
                "gate_p_recovery_target": sealed,
            },
            observed_at="2026-08-30T12:00:00Z",
            experiment_id=experiment_id,
        )[-1]

    assert projected(False)["classification"] == "unclassified"
    assert projected(True)["scope"] == (f"recovery_target:runtime_fault_requires_recovery:{experiment_id}")
    fault["details"]["reason"] = "expired_work_not_terminal"
    assert projected(True)["scope"] == (f"recovery_target:runtime_fault_requires_recovery:{experiment_id}")
    fault["details"]["reason"] = "runtime_fault_requires_recovery"
    fault["details"]["open_exposure_count"] = 1
    assert projected(True)["classification"] == "unclassified"


def test_recovery_only_alert_projection_is_bound_to_exact_existing_rows() -> None:
    required = [
        {"id": 1, "alert_type": "sensor_offline", "sensor_id": "climate.temp_south"},
        {"id": 2, "alert_type": "sensor_offline", "sensor_id": "climate.rh_south"},
        {"id": 3, "alert_type": "sensor_offline", "sensor_id": "climate.vpd_south"},
        {"id": 4, "alert_type": "sensor_offline", "sensor_id": "climate.hydro_ph"},
    ]
    observed = []
    for alert_id, (alert_type, sensor, disposition, source, opened, _issue) in collector.RECOVERY_ONLY_ALERTS.items():
        details = (
            {"type": "equipment", "staleness_ratio": 4.7}
            if alert_type == "sensor_offline"
            else {"parameter": "sw_cool_all_fans_at_high_enabled", "failure_reason": "transport_disconnected"}
        )
        observed.append(
            {
                "id": alert_id,
                "ts": opened,
                "alert_type": alert_type,
                "sensor_id": sensor,
                "disposition": disposition,
                "source": source,
                "severity": "warning",
                "details": details,
            }
        )
    projected = collector.alert_projection(
        {"open_alerts": [*required, *observed]},
        observed_at="2026-09-27T08:32:13Z",
        experiment_id="45039c86-c1d9-52f6-a0a9-d94a17bc4b14",
    )
    assert len(projected[2:]) == 11
    assert all(row["classification"] == "accepted_recovery_only_degradation" for row in projected[2:])
    assert all(row["causal"] is False for row in projected[2:])

    for mutation in (
        {"id": 99999},
        {"source": "other"},
        {"severity": "critical"},
        {"ts": "2026-09-09T16:06:11Z"},
        {"details": {}},
    ):
        changed = {**observed[0], **mutation}
        blocked = collector.alert_projection(
            {"open_alerts": [*required, changed]},
            observed_at="2026-09-27T08:32:13Z",
            experiment_id="45039c86-c1d9-52f6-a0a9-d94a17bc4b14",
        )[-1]
        assert blocked["classification"] == "unclassified"
        assert blocked["causal"] is True


def test_collector_source_has_no_device_client_or_mutating_kubernetes_method() -> None:
    source = SCRIPT.read_text()
    for forbidden in (
        "aioesphomeapi",
        'method="POST"',
        'method="PATCH"',
        'method="PUT"',
        'method="DELETE"',
        "pods/exec",
        "kubectl",
    ):
        assert forbidden not in source
    assert 'server_settings={"default_transaction_read_only": "on"' in source
    assert "ORDER BY ts DESC LIMIT 2" in source
    assert '"--readiness-url", "service=http://verdify-mcp:8000/readyz"' in source
    assert os.path.basename(SCRIPT) == "experiment_v2_proof_packet.py"


def test_git_pin_is_either_exact_expected_or_derived_from_argo(tmp_path: Path) -> None:
    common = [
        "--mode",
        "recovery",
        "--boundary",
        "gate-r",
        "--expected-application-source",
        "b" * 40,
        "--experiment-id",
        "45039c86-c1d9-52f6-a0a9-d94a17bc4b14",
        "--state",
        str(tmp_path / "state.json"),
        "--output",
        str(tmp_path / "packet.json"),
    ]
    exact = collector.parser().parse_args([*common, "--expected-git-pin", "a" * 40])
    assert exact.expected_git_pin == "a" * 40
    assert exact.derive_git_pin_from_argo is False
    derived = collector.parser().parse_args([*common, "--derive-git-pin-from-argo"])
    assert derived.expected_git_pin is None
    assert derived.derive_git_pin_from_argo is True
    with pytest.raises(SystemExit):
        collector.parser().parse_args(common)
    with pytest.raises(SystemExit):
        collector.parser().parse_args([*common, "--expected-git-pin", "a" * 40, "--derive-git-pin-from-argo"])


def test_active_argo_operation_revision_wins_over_newer_desired_comparison() -> None:
    active = "a" * 40
    newer_main = "b" * 40
    app = {
        "operation": {"sync": {"revision": active}},
        "status": {
            "sync": {"revision": newer_main, "status": "OutOfSync"},
            "operationState": {
                "phase": "Running",
                "operation": {"sync": {"revision": active}},
                "syncResult": {"revision": active},
            },
        },
    }
    assert collector.current_argo_revision(app) == active

    app.pop("operation")
    assert collector.current_argo_revision(app) == newer_main


def test_active_argo_operation_revision_must_be_exact() -> None:
    app = {
        "operation": {"sync": {"revision": "main"}},
        "status": {"sync": {"revision": "c" * 40}},
    }
    with pytest.raises(collector.CollectionError, match="operation revision"):
        collector.current_argo_revision(app)


def test_recovery_assembly_does_not_require_unrelated_physical_wetting_files(monkeypatch, tmp_path):
    """Gate R cannot acquire a physical Gate P dependency through collection."""
    base = json.loads((ROOT / "tests/fixtures/experiment-v2-readiness/base-proof.json").read_text())
    monkeypatch.setattr(collector, "backup_evidence", lambda *_: (base["backup"], True))
    monkeypatch.setattr(collector, "source_dependencies", lambda *args, **kwargs: base["dependencies"])
    monkeypatch.setattr(collector, "climate_samples", lambda _: base["climate"]["samples"])
    monkeypatch.setattr(collector, "alert_projection", lambda *args, **kwargs: base["alerts"])
    runtime = base["runtime"]
    facts = {
        "attestation": {"connection": runtime["connection_generation"], "receipt": "a" * 64},
        "writer_fact": {},
        "writer_stable_since": collector.zulu(datetime.now(UTC) - timedelta(hours=1)),
        "writer_name": "fixture-writer",
        "writer_digest": base["provenance"]["writer"]["running_digest"],
        "writer_recurring_errors": 0,
        "images": base["provenance"]["images"],
        "workloads": base["workloads"],
        "argo": base["argo"],
        "ingestor_env": {"VERDIFY_ACTIVE_EXPERIMENT_ID": "", "VERDIFY_POLICY_VECTOR_MODE": "off"},
    }
    database = {
        "status": {
            "lease_generation": runtime["lease_generation"],
            "registry_revision": runtime["registry_revision"],
            "component_enabled": False,
            "admission_state": "closed",
            "open_exposure_count": 0,
        },
        "runtime": runtime,
        "generation": {"connection_generation": runtime["connection_generation"]},
        "climate": [],
    }
    inputs = dict(
        git_pin=base["provenance"]["git_pin"],
        application_source=base["provenance"]["application_source_revision"],
        experiment_id=base["provenance"]["experiment_id"],
        state_path=tmp_path / "chain.json",
        repo_root=tmp_path,
        kube=None,
        kube_facts=facts,
        db=database,
        preflights=base["evidence"],
    )
    # Empty temporary source root deliberately has no incident disposition. All
    # Gate R causal-source projections above remain independently controlled.
    packet = collector.assemble(base, mode="recovery", boundary="gate-r", **inputs)
    incident = next(
        row
        for row in packet["issue_state"]["gate_p_641"]["prerequisites"]
        if row["name"] == "wetting_incident_778_disposition"
    )
    assert incident == {"name": "wetting_incident_778_disposition", "complete": False}
    # Proof must still refuse the exact same missing source evidence.
    with pytest.raises(FileNotFoundError):
        collector.assemble(base, mode="proof", boundary="gate-p", **inputs)
