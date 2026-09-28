"""Frozen protocol-v2 paired analyzer interface (no data access).

The analyzer accepts only the complete, frozen assigned-day export.  It has no
database/provider/device client and no exposure-completeness filter.  Missing
locked pairs produce an inconclusive integrity result instead of pair
replacement, imputation, or a changed denominator.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

import numpy as np
from scipy.stats import t as student_t

from .v2_day1_export import replay_blinded_paired_day_export

ONE_SIDED_CONFIDENCE_LEVEL = 0.975
SQL_EXPORT_DOMAIN = b"verdify-experiment-v2-frozen-export-v1\x00"
_SQL_EXPORT_FIELDS = {"analyzer_environment_sha256", "evidence_bundle_sha256", "experiment_id", "rows"}
_SQL_ROW_FIELDS = {
    "assigned_local_date",
    "assignment_id",
    "blinded_arm",
    "day_index",
    "delivery_failed",
    "deviation_sha256",
    "environment_sha256",
    "evidence_bundle_sha256",
    "facility_rescue",
    "fallback_used",
    "fidelity_sha256",
    "integrity_sha256",
    "itt_range",
    "null_value_retained",
    "outcome",
    "outcome_sha256",
    "pair_index",
    "zero_value_retained",
}


@dataclass(frozen=True)
class EndpointSpec:
    name: str
    unit: str
    boundary: float


ENDPOINTS: tuple[EndpointSpec, ...] = (
    EndpointSpec("vpd_corridor_distance_kpa", "kPa", 0.05),
    EndpointSpec("temperature_corridor_distance_f", "degF", 0.50),
    EndpointSpec("nine_control_state_minutes", "active-or-open-state minutes", 0.0),
)


@dataclass(frozen=True)
class PairContrast:
    pair_index: int
    values: dict[str, float | None]


def paired_upper_bound(values: list[float], boundary: float) -> dict[str, float | bool | int]:
    if len(values) < 2 or any(not math.isfinite(value) for value in values):
        raise ValueError("paired upper bound requires at least two finite locked contrasts")
    data = np.asarray(values, dtype=float)
    pairs = data.size
    mean = float(data.mean())
    sd = float(data.std(ddof=1))
    critical = float(student_t.ppf(ONE_SIDED_CONFIDENCE_LEVEL, pairs - 1))
    standard_error = sd / math.sqrt(pairs)
    upper = mean + critical * standard_error
    return {
        "pairs": pairs,
        "mean": mean,
        "sample_sd": sd,
        "standard_error": standard_error,
        "t_critical": critical,
        "upper_bound": upper,
        "boundary": boundary,
        "passes": upper < boundary,
    }


def analyze_frozen_pairs(rows: list[PairContrast], *, locked_pairs: int) -> dict[str, Any]:
    indexes = [row.pair_index for row in rows]
    if indexes != list(range(locked_pairs)):
        return {
            "decision": "inconclusive_incomplete_locked_pairs",
            "locked_pairs": locked_pairs,
            "observed_pair_indexes": indexes,
            "no_pair_replacement": True,
        }
    missing = [
        (row.pair_index, endpoint.name)
        for row in rows
        for endpoint in ENDPOINTS
        if row.values.get(endpoint.name) is None
    ]
    if missing:
        return {
            "decision": "inconclusive_null_endpoint",
            "locked_pairs": locked_pairs,
            "missing": missing,
            "no_pair_replacement": True,
        }
    summaries = {
        endpoint.name: paired_upper_bound(
            [float(row.values[endpoint.name]) for row in rows],
            endpoint.boundary,
        )
        for endpoint in ENDPOINTS
    }
    return {
        "decision": "advance" if all(summary["passes"] for summary in summaries.values()) else "do_not_advance",
        "locked_pairs": locked_pairs,
        "one_sided_confidence_level": ONE_SIDED_CONFIDENCE_LEVEL,
        "endpoints": summaries,
        "primary_itt_includes_every_assignment": True,
        "exposure_is_not_an_analyzer_input": True,
        "no_pair_replacement": True,
    }


def frozen_interface_manifest() -> dict[str, Any]:
    return {
        "schema": "verdify-switchback-v2-analyzer-interface",
        "version": 1,
        "pair_contrast": "AI physical admission minus Frozen baseline, independent of chronological order",
        "required_pair_fields": [endpoint.name for endpoint in ENDPOINTS],
        "selected_benefit_endpoint": "nine_control_state_minutes",
        "one_sided_confidence_level": ONE_SIDED_CONFIDENCE_LEVEL,
        "missingness": "any null locked pair is inconclusive; no imputation/replacement/denominator change",
        "exposure_filter": "forbidden",
    }


def analyze_revealed_paired_export(
    raw: bytes, expected_sha256: str, *, locked_pairs: int, ai_label: Literal["X", "Y"]
) -> dict[str, Any]:
    """Consume the exact frozen bytes after one-way arm reveal; retain ITT rows."""
    if type(locked_pairs) is not int or locked_pairs < 2 or ai_label not in ("X", "Y"):
        raise ValueError("revealed analysis needs locked pair count and exact X/Y mapping")
    assigned = replay_blinded_paired_day_export(raw, expected_sha256)
    by_pair: dict[int, dict[str, Any]] = {}
    for index, row in assigned:
        if index >= locked_pairs:
            raise ValueError("frozen assignment exceeds the locked pair count")
        by_pair.setdefault(index, {})[row.blinded_label] = row
    if any(set(by_pair.get(index, {})) != {"X", "Y"} for index in range(locked_pairs)):
        return {
            "decision": "inconclusive_incomplete_locked_pairs",
            "locked_pairs": locked_pairs,
            "assigned_days": len(assigned),
            "observed_pair_indexes": sorted(by_pair),
            "source_export_sha256": expected_sha256,
            "no_pair_replacement": True,
        }
    frozen_label = "Y" if ai_label == "X" else "X"
    contrasts = []
    for index in range(locked_pairs):
        ai = by_pair[index][ai_label]
        frozen = by_pair[index][frozen_label]
        values = {}
        for endpoint in ENDPOINTS:
            a = getattr(ai, endpoint.name)
            b = getattr(frozen, endpoint.name)
            values[endpoint.name] = None if a is None or b is None else a - b
        contrasts.append(PairContrast(index, values))
    return {
        **analyze_frozen_pairs(contrasts, locked_pairs=locked_pairs),
        "assigned_days": len(assigned),
        "source_export_sha256": expected_sha256,
        "reveal_applied_after_freeze": True,
    }


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("frozen SQL export has a duplicate JSON key")
        result[key] = value
    return result


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def analyze_revealed_sql_export(
    raw: bytes,
    expected_sha256: str,
    *,
    expected_experiment_id: str,
    expected_analyzer_environment_sha256: str,
    locked_pairs: int,
    ai_label: Literal["X", "Y"],
) -> dict[str, Any]:
    """Analyze the DB freezer's exact ``export_payload::text`` bytes.

    The SQL export is a different immutable contract from the research-only
    day-export fixture. No field is copied into a new export or rehashed under
    another domain before the paired decision.
    """
    from experiment_orchestrator.contracts import OUTCOME_PAYLOAD_SCHEMA, ContractError, OutcomePayload

    if type(locked_pairs) is not int or locked_pairs < 2 or ai_label not in ("X", "Y"):
        raise ValueError("revealed SQL analysis needs locked pairs and exact X/Y mapping")
    if type(raw) is not bytes or not 0 < len(raw) <= 2_000_000:
        raise ValueError("frozen SQL export bytes are missing or unbounded")
    if hashlib.sha256(SQL_EXPORT_DOMAIN + raw).hexdigest() != expected_sha256:
        raise ValueError("frozen SQL export byte/hash binding mismatch")
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("frozen SQL export is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or set(payload) != _SQL_EXPORT_FIELDS:
        raise ValueError("frozen SQL export shape mismatch")
    if (
        payload["experiment_id"] != expected_experiment_id
        or str(UUID(expected_experiment_id)) != expected_experiment_id
    ):
        raise ValueError("frozen SQL export experiment identity mismatch")
    if payload["analyzer_environment_sha256"] != expected_analyzer_environment_sha256:
        raise ValueError("frozen SQL export analyzer identity mismatch")
    hashes = (expected_sha256, expected_analyzer_environment_sha256, payload["evidence_bundle_sha256"])
    if any(not _is_sha256(value) for value in hashes):
        raise ValueError("frozen SQL export hash is malformed")
    rows = payload["rows"]
    if not isinstance(rows, list) or not rows or len(rows) > locked_pairs * 2:
        raise ValueError("frozen SQL export assignment count is invalid")

    by_pair: dict[int, dict[str, OutcomePayload]] = {}
    seen_assignments: set[str] = set()
    first_date: date | None = None
    expected_outcome_fields = set(OutcomePayload.__dataclass_fields__) | {"schema"}
    for position, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) != _SQL_ROW_FIELDS:
            raise ValueError("frozen SQL export row shape mismatch")
        if type(row["day_index"]) is not int or row["day_index"] != position:
            raise ValueError("frozen SQL export day order differs from assignment order")
        index = row["pair_index"]
        label = row["blinded_arm"]
        if type(index) is not int or index != (position - 1) // 2 or label not in ("X", "Y"):
            raise ValueError("frozen SQL export pair/label identity is invalid")
        assignment = row["assignment_id"]
        try:
            if str(UUID(assignment)) != assignment or assignment in seen_assignments:
                raise ValueError("frozen SQL export repeats or malforms an assignment")
            local_date = date.fromisoformat(row["assigned_local_date"])
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("frozen SQL export assignment/date identity is invalid") from exc
        if local_date.isoformat() != row["assigned_local_date"]:
            raise ValueError("frozen SQL export local date is not canonical")
        first_date = first_date or local_date
        if local_date != first_date + timedelta(days=position - 1):
            raise ValueError("frozen SQL export changed the fixed adjacent-day calendar")
        seen_assignments.add(assignment)
        if any(
            type(row[name]) is not bool
            for name in (
                "delivery_failed",
                "fallback_used",
                "facility_rescue",
                "zero_value_retained",
                "null_value_retained",
            )
        ):
            raise ValueError("frozen SQL export flags are malformed")
        if (
            not isinstance(row["itt_range"], str)
            or not row["itt_range"]
            or any(
                not _is_sha256(row[name])
                for name in (
                    "deviation_sha256",
                    "environment_sha256",
                    "evidence_bundle_sha256",
                    "fidelity_sha256",
                    "integrity_sha256",
                    "outcome_sha256",
                )
            )
        ):
            raise ValueError("frozen SQL export row hashes are malformed")
        outcome = row["outcome"]
        if (
            not isinstance(outcome, dict)
            or set(outcome) != expected_outcome_fields
            or outcome["schema"] != OUTCOME_PAYLOAD_SCHEMA
        ):
            raise ValueError("frozen SQL export outcome schema mismatch")
        try:
            typed_outcome = OutcomePayload(**{key: value for key, value in outcome.items() if key != "schema"})
        except (ContractError, TypeError) as exc:
            raise ValueError("frozen SQL export outcome contract mismatch") from exc
        values = tuple(getattr(typed_outcome, endpoint.name) for endpoint in ENDPOINTS)
        if row["zero_value_retained"] != any(value == 0 for value in values if value is not None) or row[
            "null_value_retained"
        ] != any(value is None for value in values):
            raise ValueError("frozen SQL export zero/null flags differ from endpoints")
        labels = by_pair.setdefault(index, {})
        if label in labels:
            raise ValueError("frozen SQL export repeats a blinded label in one pair")
        labels[label] = typed_outcome

    if len(rows) != locked_pairs * 2 or any(set(by_pair.get(index, {})) != {"X", "Y"} for index in range(locked_pairs)):
        return {
            "decision": "inconclusive_incomplete_locked_pairs",
            "locked_pairs": locked_pairs,
            "assigned_days": len(rows),
            "source_export_sha256": expected_sha256,
            "no_pair_replacement": True,
        }
    frozen_label = "Y" if ai_label == "X" else "X"
    contrasts = [
        PairContrast(
            index,
            {
                endpoint.name: None
                if getattr(by_pair[index][ai_label], endpoint.name) is None
                or getattr(by_pair[index][frozen_label], endpoint.name) is None
                else getattr(by_pair[index][ai_label], endpoint.name)
                - getattr(by_pair[index][frozen_label], endpoint.name)
                for endpoint in ENDPOINTS
            },
        )
        for index in range(locked_pairs)
    ]
    return {
        **analyze_frozen_pairs(contrasts, locked_pairs=locked_pairs),
        "assigned_days": len(rows),
        "source_export_sha256": expected_sha256,
        "source_contract": "verdify-experiment-v2-frozen-export-v1",
        "reveal_applied_after_freeze": True,
    }
