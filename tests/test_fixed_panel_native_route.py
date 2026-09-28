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
            assert "fn_fixed_panel_native_route_day_receipt" in sql
            assert args == (DAY, "vallery") and kwargs == {"timeout": 3.5}
            raise error("private database detail")

    evidence = asyncio.run(read_native_fixed_panel_route_evidence(Connection(), DAY))
    assert evidence.unavailable_reason == reason
    assert calls == ["SET LOCAL statement_timeout = '3000ms'"]
    assert "private database" not in evidence.model_dump_json()


def test_false_physical_flag_cannot_be_promoted_by_model():
    with pytest.raises(ValidationError):
        NativeFixedPanelRouteEvidence(physical_proof_eligible=True)
