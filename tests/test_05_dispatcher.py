"""Issue #433: desired/observed and durable dispatcher lifecycle guards."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INGESTOR_PATH = str(ROOT / "ingestor")
if INGESTOR_PATH not in sys.path:
    sys.path.insert(0, INGESTOR_PATH)

import shared  # noqa: E402
from esp32_push import DeviceCommandOutcome  # noqa: E402
from tasks import confirmation, dispatcher  # noqa: E402


@pytest.mark.parametrize(
    ("parameter", "observed", "desired", "equivalent"),
    [
        ("mister_vpd_weight", 0.75, 0.75, True),
        ("mister_vpd_weight", 0.7501, 0.75, True),
        ("mister_vpd_weight", 0.70, 0.75, False),
        ("mister_engage_delay_s", 44.0, 45.0, True),
        ("mister_engage_delay_s", 43.0, 45.0, False),
    ],
)
def test_normalized_desired_observed_comparison(parameter, observed, desired, equivalent):
    assert dispatcher.readback_values_equivalent(parameter, observed, desired) is equivalent


def test_targeted_readback_drift_does_not_depend_on_reconnect_event(monkeypatch):
    monkeypatch.setattr(shared, "cfg_readback", {"mister_vpd_weight": 0.4})
    monkeypatch.setattr(shared, "transport_generation", 11)
    monkeypatch.setattr(shared, "reconciled_transport_generation", 11)

    assert dispatcher._readback_drift("mister_vpd_weight", 0.7) is True
    assert shared.transport_generation == 11
    assert shared.reconciled_transport_generation == 11


def test_dispatcher_source_never_persists_sent_before_physical_callback():
    source = (ROOT / "ingestor" / "tasks" / "dispatcher.py").read_text()
    requested_insert = "VALUES ($1, $2, $3, $4, $5::uuid, $6, 'requested')"
    physical_call = "result = await push_to_esp32_detailed("
    sent_cache = "_last_pushed[param] = value"

    assert requested_insert in source
    assert physical_call in source
    assert sent_cache in source
    assert source.index(requested_insert) < source.index(physical_call)
    sent_guard = source.index('if outcome.status == "sent":')
    assert sent_guard < source.index(sent_cache)
    executable_lines = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    assert "shared.recently_pushed[param] = time.time()" not in executable_lines
    assert "_last_pushed[param] = val\n            dispatchable_changes" not in source


def test_confirmation_sql_excludes_unsent_new_lifecycle_states():
    ingestor_source = (ROOT / "ingestor" / "ingestor.py").read_text()
    confirmation_source = (ROOT / "ingestor" / "tasks" / "confirmation.py").read_text()

    assert "COALESCE(sc.delivery_status, 'pending') IN ('sent', 'pending')" in ingestor_source
    assert "COALESCE(sc.delivery_status, 'pending') IN ('pending', 'sent')" in confirmation_source
    assert "IN ('requested', 'queued', 'retrying')" in ingestor_source


def test_reconnect_reconcile_is_generation_gated_and_cfg_drift_is_separate():
    source = (ROOT / "ingestor" / "tasks" / "dispatcher.py").read_text()

    assert "reconnect_generation > int(shared.reconciled_transport_generation)" in source
    assert 'dispatch_reason = "cfg_drift"' in source
    assert "if reconnect_event_set and reconnect_pending:" in source
    assert "shared.clear_cfg_drift(drift_versions)" in source
    assert "shared.mark_transport_reconciled(reconnect_generation)" in source


def test_runtime_probe_classifies_reconnect_drift_retry_and_broad_batches():
    summary = confirmation.summarize_writer_log_lines(
        [
            "writer_reconcile reason=transport_reconnect generation=8 action=reconcile_requested",
            "writer_reconcile reason=cfg_drift param=x generation=8 drift_version=2",
            "writer_dispatch reason=cfg_drift generation=8 command_count=1 anchor_count=0 "
            "unchanged_anchor_count=0 comparison=desired_vs_observed",
            "writer_dispatch reason=retry generation=8 attempt=2 failed_count=1",
            "writer_dispatch reason=transport_reconnect generation=9 command_count=56 anchor_count=56 "
            "unchanged_anchor_count=0 comparison=desired_vs_observed",
            "writer_delivery phase=transport status=sent reason=api_command_returned param=x generation=8 attempt=1",
            "writer_delivery phase=persisted status=sent reason=api_command_returned param=x generation=8 attempt=1",
            "writer_delivery phase=persisted status=failed reason=transport_disconnected param=y generation=8 attempt=3",
        ]
    )

    assert summary["transport_reconnects"] == 1
    assert summary["cfg_drifts"] == 1
    assert summary["retry_batches"] == 1
    assert summary["dispatch_commands"] == 57
    assert summary["anchor_commands"] == 56
    assert summary["broad_anchor_batches"] == 1
    assert summary["unchanged_broad_anchor_batches"] == 0
    assert summary["delivery_sent"] == 1
    assert summary["delivery_failed"] == 1


def test_unchanged_anchor_probe_uses_observed_readback_not_constant(monkeypatch):
    anchor = next(iter(dispatcher.band_anchors.ANCHOR_SYNC_PARAMS))
    monkeypatch.setattr(shared, "cfg_readback", {anchor: 1.25})

    assert dispatcher._unchanged_anchor_dispatch_count([(anchor, 1.25, "band")]) == 1
    assert dispatcher._unchanged_anchor_dispatch_count([(anchor, 1.5, "band")]) == 0


def test_durable_lifecycle_mapping_keeps_retry_nonterminal_and_other_states_exact():
    def outcome(status):
        return DeviceCommandOutcome(
            index=0,
            object_id="x",
            value=1.0,
            entity_type="number",
            parameter="x",
            status=status,
            reason="fixture",
            attempt=1,
            connection_generation=2,
        )

    assert dispatcher._persisted_delivery_status(outcome("failed"), final_attempt=False) == "retrying"
    assert dispatcher._persisted_delivery_status(outcome("failed"), final_attempt=True) == "failed"
    assert dispatcher._persisted_delivery_status(outcome("sent"), final_attempt=False) == "sent"
    assert dispatcher._persisted_delivery_status(outcome("cancelled"), final_attempt=True) == "cancelled"
    assert dispatcher._persisted_delivery_status(outcome("superseded"), final_attempt=True) == "superseded"


def test_unknown_physical_outcome_is_terminal_and_never_retryable():
    outcome = DeviceCommandOutcome(
        index=0,
        object_id="x",
        value=1.0,
        entity_type="number",
        parameter="x",
        status="failed",
        reason="command_timeout_outcome_unknown",
        attempt=1,
        connection_generation=2,
    )

    assert dispatcher.delivery_failure_retryable(outcome) is False
    assert dispatcher._persisted_delivery_status(outcome, final_attempt=False) == "failed"


def test_approved_lifecycle_consumers_cover_full_state_vocabulary():
    brief = (ROOT / "slack_ops" / "briefs.py").read_text()
    irrigation = (ROOT / "scripts" / "validate-irrigation-stack.py").read_text()

    for source in (brief, irrigation):
        for status in (
            "requested",
            "queued",
            "retrying",
            "sent",
            "confirmed",
            "failed",
            "cancelled",
            "superseded",
        ):
            assert f"'{status}'" in source
    assert "setpoints_terminal_unconfirmed" in brief
    assert "lifecycle_terminal_unconfirmed" in irrigation
    assert "terminal_unsent" not in brief
    assert "terminal_unsent" not in irrigation


def test_failed_reconcile_never_marks_transport_or_drift_trigger_complete():
    assert dispatcher._dispatch_trigger_completed([]) is True
    assert dispatcher._dispatch_trigger_completed([("mister_vpd_weight", "transport_disconnected")]) is False
