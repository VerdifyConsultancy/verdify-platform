"""Canonical source contract for the dormant M8 direct-launch packet.

This module prepares and validates metadata only.  It cannot lock a database,
finalize randomization, call a provider, or reach a device.  The actual design
lock remains owned by the authenticated lifecycle API and the exactly-once
randomization draw remains owned by PostgreSQL.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .contracts import ContractError, canonical_json_bytes, require_sha256, require_uuid

DESIGN_SCHEMA = "verdify-experiment-v2-direct-launch-design-v2"
EXPERIMENT_ID = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"
STUDY_ID = "verdify-confirmed-component-switchback-v2-2026-08"
TIMEZONE = "America/Denver"
WINDOW_DAYS = 60
PAIR_COUNT = 30
MODELED_JOINT_ADVANCE_POWER = "0.13776"
SELECTOR_CONTEXT_CUTOFF_LOCAL = "23:45:00"

_FIELDS = frozenset(
    {
        "accepted_underpowered_design",
        "analyzer_environment_sha256",
        "context_schema_sha256",
        "design_lock_sha256",
        "endpoint_artifact_sha256",
        "experiment_id",
        "generalized_vector_mode",
        "modeled_joint_advance_power",
        "outcome_schema_sha256",
        "power_artifact_sha256",
        "randomized_pair_count",
        "rollback_artifact_sha256",
        "schedule_schema_sha256",
        "schema",
        "selector_artifact_sha256",
        "selector_context_cutoff_local",
        "selector_identity_sha256",
        "source_git_sha",
        "study_id",
        "study_start_local_date",
        "timezone",
        "window_days",
    }
)
_SHA_FIELDS = frozenset(
    {
        "analyzer_environment_sha256",
        "context_schema_sha256",
        "endpoint_artifact_sha256",
        "outcome_schema_sha256",
        "power_artifact_sha256",
        "rollback_artifact_sha256",
        "schedule_schema_sha256",
        "selector_artifact_sha256",
        "selector_identity_sha256",
    }
)
_FORBIDDEN_TOKENS = frozenset(
    {
        "mapping",
        "mapping_secret",
        "physical_arm",
        "randomization_secret",
        "secret",
        "x_physical_arm",
        "y_physical_arm",
    }
)


def _exact_local_date(value: object) -> str:
    if not isinstance(value, str):
        raise ContractError("study_start_local_date must be canonical YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError("study_start_local_date must be canonical YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ContractError("study_start_local_date must be canonical YYYY-MM-DD")
    return value


def _window_has_one_utc_offset(start_local_date: str) -> bool:
    start = date.fromisoformat(start_local_date)
    zone = ZoneInfo(TIMEZONE)
    offsets = {
        datetime.combine(start + timedelta(days=offset), datetime.min.time(), tzinfo=zone).utcoffset()
        for offset in range(WINDOW_DAYS + 1)
    }
    return len(offsets) == 1


def earliest_offset_stable_start(*, not_before: date) -> date:
    """Return the earliest future 60-day local window with one UTC offset."""

    if type(not_before) is not date:
        raise TypeError("not_before must be an exact date")
    for offset in range(732):
        candidate = not_before + timedelta(days=offset)
        if _window_has_one_utc_offset(candidate.isoformat()):
            return candidate
    raise ContractError("no offset-stable 60-day start exists in the two-year search horizon")


@dataclass(frozen=True)
class DirectLaunchDesign:
    payload: Mapping[str, Any]
    canonical_bytes: bytes
    canonical_sha256: str

    @property
    def experiment_id(self) -> str:
        return str(self.payload["experiment_id"])

    @property
    def study_start_local_date(self) -> str:
        return str(self.payload["study_start_local_date"])

    def api_lock_fields(self) -> dict[str, object]:
        """Return only fields accepted by the audited direct-launch command."""

        keys = (
            "study_start_local_date",
            "randomized_pair_count",
            "selector_context_cutoff_local",
            "design_lock_sha256",
            "source_git_sha",
            "schedule_schema_sha256",
            "selector_identity_sha256",
            "selector_artifact_sha256",
            "context_schema_sha256",
            "endpoint_artifact_sha256",
            "outcome_schema_sha256",
            "analyzer_environment_sha256",
            "power_artifact_sha256",
        )
        return {key: self.payload[key] for key in keys}


def parse_direct_launch_design(
    raw: bytes,
    *,
    now_local_date: date | None = None,
) -> DirectLaunchDesign:
    if type(raw) is not bytes or len(raw) > 65_536:
        raise ContractError("direct-launch design must be bounded immutable bytes")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("direct-launch design is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise ContractError("direct-launch design field set differs from the source contract")
    if canonical_json_bytes(payload, reject_forbidden_fields=False) != raw:
        raise ContractError("direct-launch design is not canonical JSON")
    if _FORBIDDEN_TOKENS & set(payload):
        raise ContractError("restricted randomization material is forbidden from the launch design")
    if payload["schema"] != DESIGN_SCHEMA:
        raise ContractError("direct-launch design schema mismatch")
    if require_uuid(payload["experiment_id"], "experiment_id") != EXPERIMENT_ID:
        raise ContractError("direct-launch design experiment identity mismatch")
    if payload["study_id"] != STUDY_ID or payload["timezone"] != TIMEZONE:
        raise ContractError("direct-launch design study identity mismatch")
    if (
        payload["window_days"] != WINDOW_DAYS
        or payload["randomized_pair_count"] != PAIR_COUNT
        or payload["selector_context_cutoff_local"] != SELECTOR_CONTEXT_CUTOFF_LOCAL
    ):
        raise ContractError("direct-launch design schedule differs from the accepted 30-pair contract")
    if (
        payload["modeled_joint_advance_power"] != MODELED_JOINT_ADVANCE_POWER
        or payload["accepted_underpowered_design"] is not True
    ):
        raise ContractError("direct-launch design must truthfully retain the accepted 0.13776 power waiver")
    if payload["generalized_vector_mode"] != "off":
        raise ContractError("generalized vector mode must remain off")
    start = _exact_local_date(payload["study_start_local_date"])
    if not _window_has_one_utc_offset(start):
        raise ContractError("direct-launch 60-day window crosses a UTC-offset transition")
    today = date.today() if now_local_date is None else now_local_date
    if type(today) is not date or date.fromisoformat(start) <= today:
        raise ContractError("direct-launch start must remain future; missed starts require a new preregistration")
    if not isinstance(payload["source_git_sha"], str) or not re.fullmatch(r"[0-9a-f]{40}", payload["source_git_sha"]):
        raise ContractError("source_git_sha must be exact lowercase 40-hex")
    for field in _SHA_FIELDS:
        require_sha256(payload[field], field)
    lock_hash = require_sha256(payload["design_lock_sha256"], "design_lock_sha256")
    preimage = {key: value for key, value in payload.items() if key != "design_lock_sha256"}
    if hashlib.sha256(canonical_json_bytes(preimage, reject_forbidden_fields=False)).hexdigest() != lock_hash:
        raise ContractError("direct-launch immutable design hash mismatch")
    return DirectLaunchDesign(payload, raw, lock_hash)


def build_direct_launch_design(**fields: object) -> DirectLaunchDesign:
    payload: dict[str, object] = {
        "accepted_underpowered_design": True,
        "experiment_id": EXPERIMENT_ID,
        "generalized_vector_mode": "off",
        "modeled_joint_advance_power": MODELED_JOINT_ADVANCE_POWER,
        "randomized_pair_count": PAIR_COUNT,
        "schema": DESIGN_SCHEMA,
        "selector_context_cutoff_local": SELECTOR_CONTEXT_CUTOFF_LOCAL,
        "study_id": STUDY_ID,
        "timezone": TIMEZONE,
        "window_days": WINDOW_DAYS,
        **fields,
    }
    if "design_lock_sha256" in payload:
        raise ContractError("design_lock_sha256 is computed internally")
    payload["design_lock_sha256"] = hashlib.sha256(
        canonical_json_bytes(payload, reject_forbidden_fields=False)
    ).hexdigest()
    raw = canonical_json_bytes(payload, reject_forbidden_fields=False)
    return parse_direct_launch_design(raw, now_local_date=date.min)
