"""Frozen native route receipts stay typed, bounded, and nonphysical."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import asyncpg
import pytest
from pydantic import ValidationError

from verdify_schemas.fixed_panel_native_route import (
    NativeFixedPanelRouteEvidence,
    parse_native_fixed_panel_route_row,
    read_native_fixed_panel_route_evidence,
)

DAY = date(2026, 9, 27)
FROZEN = datetime(2026, 9, 28, 7, tzinfo=UTC)


def row(**changes):
    data = {
        "day": DAY,
        "greenhouse_id": "vallery",
        "served_at": FROZEN,
        "receipt_id": 1,
        "frozen_at": FROZEN,
        "projection_sha256": "a" * 64,
        "target_revision_id": 4,
        "contributor_revision_ids": [5, 6, 7],
        "source_binding_id": 8,
        "panel_source_sha256": "b" * 64,
        "firmware_revision": "fw-reported",
        "collector_revision": "c" * 40,
        "declared_route_sha256": "d" * 64,
        "source_callback_count": 5184,
        "source_continuity_verified": True,
        "eligible_bins": 72,
        "joint_in_band_bins": 60,
        "route_18h_available": True,
        "unavailable_reason": None,
        "physical_serial_verified": False,
        "modbus_poll_time_verified": False,
        "physical_proof_eligible": False,
        "experiment_endpoint_eligible": False,
        "causal_effect_estimate": False,
    }
    return data | changes


def test_frozen_route_receipt_is_separate_typed_nonphysical_evidence():
    evidence = parse_native_fixed_panel_route_row(row(), DAY)
    assert evidence.availability == "route_observation"
    assert evidence.eligible_bins == 72 and evidence.joint_in_band_bins == 60
    assert evidence.source_callback_count == 5184
    assert not evidence.physical_serial_verified
    assert not evidence.experiment_endpoint_eligible
    assert NativeFixedPanelRouteEvidence.model_validate_json(evidence.model_dump_json()) == evidence


@pytest.mark.parametrize(
    "change",
    [
        {"physical_serial_verified": True},
        {"modbus_poll_time_verified": True},
        {"causal_effect_estimate": True},
        {"source_continuity_verified": False},
        {"eligible_bins": 71},
        {"frozen_at": datetime(2026, 9, 27, 7, tzinfo=UTC)},
        {"source_callback_count": None},
        {"day": date(2026, 9, 26)},
    ],
)
def test_invalid_or_overclaimed_latest_receipt_fails_closed(change):
    evidence = parse_native_fixed_panel_route_row(row(**change), DAY)
    assert evidence.availability == "unavailable"
    assert evidence.unavailable_reason == "invalid_receipt"
    assert evidence.receipt_id is None


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


@pytest.mark.parametrize(
    "error,reason",
    [
        (asyncpg.QueryCanceledError, "db_statement_timeout"),
        (TimeoutError, "db_statement_timeout"),
        (asyncpg.UndefinedFunctionError, "reader_unavailable"),
        (asyncpg.InsufficientPrivilegeError, "reader_unavailable"),
    ],
)
def test_reader_uses_bounded_nested_transaction_and_safe_reason(error, reason):
    calls = []

    class Connection:
        def transaction(self):
            return Transaction()

        async def execute(self, sql):
            calls.append(sql)

        async def fetchrow(self, sql, *args, **kwargs):
            assert "fn_fixed_panel_native_route_" in sql
            assert args == (DAY, "vallery") and kwargs == {"timeout": 3.5}
            raise error("private database detail")

    evidence = asyncio.run(read_native_fixed_panel_route_evidence(Connection(), DAY))
    assert evidence.unavailable_reason == reason
    assert calls == ["SET LOCAL statement_timeout = '3000ms'"]
    assert "private database" not in evidence.model_dump_json()


def test_false_physical_flag_cannot_be_promoted_by_model():
    with pytest.raises(ValidationError):
        NativeFixedPanelRouteEvidence(physical_proof_eligible=True)


def measured_row():
    import json
    from pathlib import Path

    return json.loads((Path(__file__).parent / "fixtures/native-route-sep29-measurement.json").read_text())


def test_real_historical_native_projection_retains_actual_axes_and_frozen_joint_parity():
    evidence = NativeFixedPanelRouteEvidence.model_validate(measured_row())
    assert evidence.availability == "route_measurement"
    assert evidence.diagnostic.temp.eligible_bins == 72
    assert evidence.diagnostic.temp.in_band_bins == evidence.joint_in_band_bins == 47
    assert evidence.diagnostic.temp.high_miss_bins == 25
    assert evidence.diagnostic.vpd.in_band_bins == 72
    assert evidence.diagnostic.temp.mean_outside_distance == pytest.approx(0.5744447)
    assert evidence.diagnostic.temp.worst_measured_zone == "north"
    assert not evidence.route_18h_available and not evidence.physical_proof_eligible


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["diagnostic"].update(physical_hardware_identity_verified=True),
        lambda d: d["diagnostic"].update(temp_unavailable_reasons={"missing": 1}),
        lambda d: d["diagnostic"].update(completed_bin_count=71),
        lambda d: d.update(availability="unavailable", unavailable_reason="no_source"),
        lambda d: d.update(joint_in_band_bins=48),
    ],
)
def test_native_diagnostic_rejects_overclaim_missingness_and_projection_conflicts(mutate):
    data = measured_row()
    mutate(data)
    with pytest.raises(ValidationError):
        NativeFixedPanelRouteEvidence.model_validate(data)


def test_reader_decodes_typed_json_and_rejects_wrong_day():
    import json

    class Connection:
        def transaction(self):
            return Transaction()

        async def execute(self, sql):
            pass

        async def fetchrow(self, *_args, **_kwargs):
            return {"evidence": json.dumps(measured_row())}

    evidence = asyncio.run(read_native_fixed_panel_route_evidence(Connection(), date(2026, 9, 29)))
    assert evidence.diagnostic.temp.in_band_bins == 47
    rejected = asyncio.run(read_native_fixed_panel_route_evidence(Connection(), DAY))
    assert rejected.unavailable_reason == "invalid_measurement"


def test_public_renderer_presents_route_distance_without_physical_qualification():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "evidence_snapshot_route_test", Path(__file__).parents[1] / "scripts/update-evidence-snapshots.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rendered = module.native_route_measurement_block(measured_row(), "2026-09-29")
    assert "65.3%" in rendered and "100.0%" in rendered
    assert "0.574" in rendered and "25/0" in rendered and "north" in rendered
    assert "Physical proof and experiment eligibility are false" in rendered
    assert "Unavailable" in module.native_route_measurement_block(measured_row(), "2026-10-05")


def test_temperature_remains_measured_when_humidity_axis_is_missing():
    data = measured_row()
    d = data["diagnostic"]
    d["vpd"] = {
        "eligible_bins": 0,
        "in_band_bins": 0,
        "in_band_pct": None,
        "high_miss_bins": 0,
        "low_miss_bins": 0,
        "mean_high_distance": None,
        "mean_low_distance": None,
        "mean_outside_distance": None,
        "worst_measured_zone": None,
    }
    d["joint"] = {"eligible_bins": 0, "in_band_bins": 0, "in_band_pct": None}
    d["vpd_unavailable_reasons"] = {"fewer_than_12_fresh_six_field_minutes": 72}
    data.update(eligible_bins=0, joint_in_band_bins=0)
    evidence = NativeFixedPanelRouteEvidence.model_validate(data)
    assert evidence.diagnostic.temp.in_band_bins == 47
    assert evidence.diagnostic.vpd.in_band_pct is None
    assert evidence.diagnostic.joint.in_band_pct is None
    assert not evidence.physical_proof_eligible


def test_pre_migration_reader_falls_back_inside_savepoint():
    calls = []

    class Connection:
        def transaction(self):
            calls.append("transaction")
            return Transaction()

        async def execute(self, sql):
            pass

        async def fetchrow(self, sql, *_args, **_kwargs):
            calls.append(sql)
            if "measurement" in sql:
                raise asyncpg.UndefinedFunctionError("not yet migrated")
            return row()

    evidence = asyncio.run(read_native_fixed_panel_route_evidence(Connection(), DAY))
    assert evidence.availability == "route_observation"
    assert calls.count("transaction") == 2
    assert "day_receipt" in calls[-1]
