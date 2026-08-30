"""Deterministic blinded assigned-day ITT export and replay contract.

Every immutable assignment produces exactly one row.  Exposure is nested under
fidelity and cannot select, truncate, weight, or remove the primary fixed-window
outcome.  The module contains no database, provider, randomization, or reveal
client and rejects mapping/secret/physical-arm fields.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC
from typing import Any

from .v2_outcomes import ANALYZED_SECONDS, RandomizedIttRow, _local_window, make_randomized_itt_row

DAY1_EXPORT_SCHEMA = "verdify-experiment-v2-blinded-day-export-v1"
DAY1_EXPORT_DOMAIN = b"verdify-experiment-v2-blinded-day-export-v1\x00"

_TOP_FIELDS = {"rows", "schema"}
_ROW_FIELDS = {
    "assignment_id",
    "blinded_label",
    "execution_phase",
    "fallback_or_rescue",
    "fidelity",
    "fixed_window_utc_end",
    "fixed_window_utc_start",
    "local_date",
    "missing_reason",
    "nine_control_state_minutes",
    "outcome_complete",
    "temperature_corridor_distance_f",
    "vpd_corridor_distance_kpa",
}
_FIDELITY_FIELDS = {"exposure_seconds", "per_protocol_exposure_complete"}
_FORBIDDEN = {
    "mapping",
    "physical_arm",
    "randomization_secret",
    "secret",
    "x_physical_arm",
    "y_physical_arm",
}


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _row_payload(row: RandomizedIttRow, *, timezone: str) -> dict[str, Any]:
    start, end = _local_window(row.local_date, timezone)
    values = asdict(row)
    exposure_seconds = values.pop("exposure_seconds")
    per_protocol = values.pop("per_protocol_exposure_complete")
    values.update(
        {
            "fidelity": {
                "exposure_seconds": exposure_seconds,
                "per_protocol_exposure_complete": per_protocol,
            },
            "fixed_window_utc_end": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fixed_window_utc_start": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    return values


def freeze_blinded_day_export(
    rows: list[RandomizedIttRow],
    *,
    timezone: str = "America/Denver",
) -> tuple[bytes, str]:
    if not rows:
        raise ValueError("blinded day export requires at least one immutable assignment")
    ordered = sorted(rows, key=lambda row: (row.local_date, row.assignment_id))
    identities = [(row.local_date, row.assignment_id) for row in ordered]
    if len(set(identities)) != len(identities):
        raise ValueError("blinded day export contains a duplicate assignment row")
    payload = {"rows": [_row_payload(row, timezone=timezone) for row in ordered], "schema": DAY1_EXPORT_SCHEMA}
    raw = _canonical(payload)
    lowered = raw.decode().lower()
    if any(f'"{field}"' in lowered for field in _FORBIDDEN):
        raise ValueError("blinded day export contains restricted randomization material")
    return raw, hashlib.sha256(DAY1_EXPORT_DOMAIN + raw).hexdigest()


def replay_blinded_day_export(
    raw: bytes,
    expected_sha256: str,
    *,
    timezone: str = "America/Denver",
) -> tuple[RandomizedIttRow, ...]:
    if type(raw) is not bytes or hashlib.sha256(DAY1_EXPORT_DOMAIN + raw).hexdigest() != expected_sha256:
        raise ValueError("blinded day export byte/hash binding mismatch")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("blinded day export is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or set(payload) != _TOP_FIELDS or payload["schema"] != DAY1_EXPORT_SCHEMA:
        raise ValueError("blinded day export schema mismatch")
    if _canonical(payload) != raw or not isinstance(payload["rows"], list) or not payload["rows"]:
        raise ValueError("blinded day export is not canonical or has no rows")
    replayed: list[RandomizedIttRow] = []
    for item in payload["rows"]:
        if not isinstance(item, dict) or set(item) != _ROW_FIELDS:
            raise ValueError("blinded day row shape mismatch")
        fidelity = item["fidelity"]
        if not isinstance(fidelity, dict) or set(fidelity) != _FIDELITY_FIELDS:
            raise ValueError("blinded day fidelity shape mismatch")
        start, end = _local_window(item["local_date"], timezone)
        if item["fixed_window_utc_start"] != start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") or item[
            "fixed_window_utc_end"
        ] != end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"):
            raise ValueError("blinded day export changed the fixed [06:00,24:00) window")
        climate_missing = item["temperature_corridor_distance_f"] is None or item["vpd_corridor_distance_kpa"] is None
        equipment_missing = item["nine_control_state_minutes"] is None
        row = make_randomized_itt_row(
            assignment_id=item["assignment_id"],
            local_date=item["local_date"],
            blinded_label=item["blinded_label"],
            climate=(
                item["temperature_corridor_distance_f"],
                item["vpd_corridor_distance_kpa"],
                item["missing_reason"] if climate_missing else None,
            ),
            equipment=(
                item["nine_control_state_minutes"],
                item["missing_reason"] if equipment_missing and not climate_missing else None,
            ),
            fallback_or_rescue=item["fallback_or_rescue"],
            exposure_seconds=fidelity["exposure_seconds"],
        )
        if asdict(row)["outcome_complete"] is not item["outcome_complete"] or (
            fidelity["per_protocol_exposure_complete"] is not row.per_protocol_exposure_complete
        ):
            raise ValueError("blinded day derived completeness fields are inconsistent")
        replayed.append(row)
    if [(row.local_date, row.assignment_id) for row in replayed] != sorted(
        (row.local_date, row.assignment_id) for row in replayed
    ):
        raise ValueError("blinded day rows are not in deterministic assignment order")
    if any(not 0 <= row.exposure_seconds <= ANALYZED_SECONDS for row in replayed):
        raise ValueError("blinded day fidelity exposure is outside the fixed window")
    reproduced, reproduced_sha = freeze_blinded_day_export(list(replayed), timezone=timezone)
    if reproduced != raw or reproduced_sha != expected_sha256:
        raise ValueError("blinded day replay did not reproduce identical bytes and hash")
    return tuple(replayed)
