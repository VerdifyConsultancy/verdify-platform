"""Offline C1 proposal from an ordinary 48-field desired state.

The ordinary writer's desired values are evidence, not experiment commands.
Every off-grid value needs a reviewed, exact field decision before this module
can produce a proposed experiment state.  The component executor keeps its
reject-not-round contract; this module never contacts a device or database.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from decimal import Decimal

from .component_executor import (
    CANONICAL_FIELD_ORDER,
    ENTITY_GRIDS,
    GRID_REVISION,
    ComponentContractError,
    normalize_complete_state,
    normalize_component_value,
)
from .policy_vector import canonical_json_bytes, decode_policy_vector, encode_policy_vector, wire_manifest_digest
from .tunable_registry import WIRE_SCHEMA_VERSION


class C1ProjectionError(ValueError):
    """A C1 proposal lacks an exact, reviewable field decision."""


def _same_value(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is bool and type(right) is bool and left == right
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return False
    if (isinstance(left, float) and not math.isfinite(left)) or (isinstance(right, float) and not math.isfinite(right)):
        return False
    return Decimal(str(left)) == Decimal(str(right))


def _source_hash(values: Mapping[str, object]) -> str:
    return hashlib.sha256(b"verdify-c1-ordinary-desired-v1\x00" + canonical_json_bytes(values)).hexdigest()


def _state_hash(vector: bytes) -> str:
    return hashlib.sha256(
        b"verdify-policy-state-content-v1\x00" + bytes([WIRE_SCHEMA_VERSION]) + wire_manifest_digest() + vector
    ).hexdigest()


def project_c1_grid_state(
    source_values: Mapping[str, object], decisions: Mapping[str, Mapping[str, object]]
) -> dict[str, object]:
    """Return a complete, non-actuating proposal and 48 field receipts.

    ``decisions`` has an entry only for each off-grid source field, with exact
    ``from`` and ``to`` JSON values and a nonempty ``rationale``.  There is no
    nearest-point rule, clamping, switch coercion, or implicit design choice.
    A changed ordinary desired state invalidates its prior decisions.
    """
    expected = frozenset(CANONICAL_FIELD_ORDER)
    provided = frozenset(source_values)
    if provided != expected:
        raise C1ProjectionError(
            f"source must have exactly 48 fields: missing={sorted(expected - provided)} extra={sorted(provided - expected)}"
        )
    if not isinstance(decisions, Mapping):
        raise C1ProjectionError("decisions must be a field-keyed object")
    extra_decisions = set(decisions) - expected
    if extra_decisions:
        raise C1ProjectionError(f"unknown decision fields: {sorted(extra_decisions)}")

    selected: dict[str, float | bool] = {}
    receipt: list[dict[str, object]] = []
    for field_name in CANONICAL_FIELD_ORDER:
        source = source_values[field_name]
        if isinstance(source, bool):
            valid_source = True
        elif isinstance(source, int) and not isinstance(source, bool):
            valid_source = True
        elif isinstance(source, float) and math.isfinite(source):
            valid_source = True
        else:
            valid_source = False
        if not valid_source:
            raise C1ProjectionError(f"{field_name}: source must be a finite JSON number or boolean")

        try:
            retained = normalize_component_value(field_name, source)
        except ComponentContractError as exc:
            if exc.code != "value_off_entity_grid":
                raise C1ProjectionError(f"{field_name}: source {exc.code}; C1 does not clamp or coerce") from exc
            retained = None

        decision = decisions.get(field_name)
        if retained is not None:
            if decision is not None:
                raise C1ProjectionError(f"{field_name}: an on-grid source cannot be changed by a grid decision")
            target = retained
            rationale = "ordinary desired value already lies on the exact entity grid"
            action = "retained"
        else:
            if not isinstance(decision, Mapping) or set(decision) != {"from", "to", "rationale"}:
                raise C1ProjectionError(f"{field_name}: off-grid source requires from, to, and rationale")
            if not _same_value(decision["from"], source):
                raise C1ProjectionError(
                    f"{field_name}: decision source does not match the current ordinary desired value"
                )
            if (
                isinstance(decision["to"], bool)
                or not isinstance(decision["to"], (int, float))
                or (isinstance(decision["to"], float) and not math.isfinite(decision["to"]))
            ):
                raise C1ProjectionError(f"{field_name}: selected value must be a finite JSON number")
            rationale = decision["rationale"]
            if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 500:
                raise C1ProjectionError(f"{field_name}: rationale must be nonempty and at most 500 characters")
            try:
                target = normalize_component_value(field_name, decision["to"])
            except ComponentContractError as exc:
                raise C1ProjectionError(f"{field_name}: selected value {exc.code}") from exc
            action = "explicit_selection"

        grid = ENTITY_GRIDS[field_name]
        selected[field_name] = target
        receipt.append(
            {
                "action": action,
                "field": field_name,
                "from": source,
                "grid": {
                    "entity_type": grid.entity_type,
                    "minimum": float(grid.minimum) if grid.minimum is not None else None,
                    "maximum": float(grid.maximum) if grid.maximum is not None else None,
                    "step": float(grid.step) if grid.step is not None else None,
                },
                "rationale": rationale,
                "to": target,
            }
        )

    # Keep this stricter than the policy wire codec, whose own quantization is
    # allowed for ordinary policy vectors but does not prove entity-grid fit.
    normalized = normalize_complete_state(selected)
    if normalized["mister_engage_kpa"] > normalized["mister_all_kpa"]:
        raise C1ProjectionError("selected mister_engage_kpa exceeds mister_all_kpa")
    vector = encode_policy_vector(normalized)
    if decode_policy_vector(vector) != normalized:
        raise C1ProjectionError("selected entity-grid state does not round-trip through the policy wire codec")
    source_ordered = {field: source_values[field] for field in CANONICAL_FIELD_ORDER}
    return {
        "schema": "verdify-c1-grid-projection-v1",
        "status": "OFFLINE_PROPOSAL_NO_DEVICE_WRITE",
        "source_kind": "ordinary_source_owned_desired_values",
        "source_values_sha256": _source_hash(source_ordered),
        "grid_revision": GRID_REVISION,
        "field_count": len(normalized),
        "explicit_selection_count": sum(row["action"] == "explicit_selection" for row in receipt),
        "field_decisions": receipt,
        "proposed_values": normalized,
        "wire_manifest_digest_sha256": wire_manifest_digest().hex(),
        "wire_schema_version": WIRE_SCHEMA_VERSION,
        "wire_vector_hex": vector.hex(),
        "policy_state_content_sha256": _state_hash(vector),
        "qualification_claimed": False,
    }
