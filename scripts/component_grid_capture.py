#!/usr/bin/env python3
"""component_grid_capture.py — Tool A, the offline current-state capture.

This tool is the qualification half of the live-grid proof.  It is deliberately
OFFLINE and NON-ACTUATING: it opens no socket, imports no ESPHome client, reads
no database, reads no Kubernetes Secret and never invokes a setter or service.
It consumes ONE input artifact (``verdify-component-grid-capture-input-v1``, see
INPUT ARTIFACT SCHEMA below) produced by a separately-approved, read-only in-pod
emitter, and answers three questions with fail-closed evidence:

1. **grid parity** — is the LIVE running device's 48-field entity grid
   byte-parity with the source ``ENTITY_GRIDS``?  Entity type, numeric
   (min, max, step) and both route slugs must match exactly, and the attributed
   entity set must equal ``CANONICAL_FIELD_ORDER`` with nothing missing, extra
   or duplicated.
2. **#424 three-layer coherence** — do the *served* (versioned DB/crop-band
   corridor), *control* (firmware globals actually consumed) and *observed*
   (raw ``cfg_*`` readback) layers expose the same truthful value, name, unit
   and a fresh timestamp for every required temperature/VPD series?  A layer
   that cannot expose a truthful value is classified ``unobservable`` and
   BLOCKS; convergence is never inferred.
3. **current state** — is the observed 48-field component state exactly on the
   deployed entity grid, and what is its ``state_content_sha256`` under the
   migration-214 cross-language identity?

Only when all three pass does the tool emit the qualified ``GRID_REVISION``
string.  Together with a separate Tool B order revision that string unblocks
``verdify_schemas.component_executor.physical_execution_qualified``.  Nothing
here modifies that module; adopting the revision is a separate reviewed change.

Everything fails closed: a grid mismatch, an off-grid or out-of-range observed
value, a missing/extra/duplicated entity, a route mismatch, or any layer that
cannot expose a truthful value produces no revision and a non-zero exit.

Exit code is 0 iff no check FAILs and a revision was emitted.

REVISION-DERIVATION NOTE (deliberate deviation, read before changing)
--------------------------------------------------------------------
The grid revision is NOT recomputed here.  It is produced verbatim by
``verdify_schemas.component_qualification.build_live_entity_grid_evidence``,
the same pure function the ingestor already calls on its existing authenticated
enumeration (``ingestor/tasks/component_experiment.py``
``record_component_grid_firmware_revision``).  ``physical_execution_qualified``
requires ``observed_grid_revision == GRID_REVISION``, so an offline tool that
hashed a *different* preimage — for example one that folded the firmware,
config or registry revision into the grid identity — would derive a revision
the live ingestor attestation can never reproduce, permanently wedging the
gate.  Firmware/config/registry pins are therefore carried in the observation
receipt and in this tool's result payload, exactly where the shipped evidence
module puts them, not inside the semantic identity of an unchanged grid.

INPUT ARTIFACT SCHEMA (``verdify-component-grid-capture-input-v1``)
-------------------------------------------------------------------
A future in-pod ``commissioning_probe`` emitter (#641-gated, root-controller
owned) must produce exactly this shape.  Every field is read-only device/DB
metadata; no secret, credential or command belongs in it.  Unknown top-level
or per-row keys are rejected, so the emitter cannot smuggle extra state past
this contract::

    {
      "schema": "verdify-component-grid-capture-input-v1",
      "device_id": "vallery/greenhouse-controller",   # policy device id, NFC text
      "observed_at": "2026-08-26T01:02:03.456789Z",   # capture instant, UTC, Z
      "runtime": {
        "runtime_instance_id": "<uuid4>",             # ingestor process identity
        "connection_generation": 7                    # transport generation >= 1
      },
      "revisions": {
        "source_revision": "<40 lowercase hex>",      # running image git sha
        "firmware_revision": "2026.7.10.1500.09ee886",
        "config_revision": "<verdify.io/config-revision>",
        "registry_revision": "<tunable registry pin>",
        "crop_band_resolver_revision": "<served-layer resolver pin>",
        "sensor_registry_revision": "<sensor registry pin>"
      },
      "entities": [                                   # RAW list_entities_services
        {                                             # projection — NOT attributed
          "object_id": "min_fog_on_s",
          "entity_type": "number" | "switch" | "sensor",
          "key": 1234,                                # > 0, per-type unique
          "device_id": 0,                             # optional, default 0
          "disabled_by_default": false,               # optional, default false
          "minimum": 15, "maximum": 300, "step": 15,  # numbers only; JSON number
          "unit": "s",                                #   (binary32 as decoded) or
          "assumed_state": null                       #   an exact decimal string
        }, ...
      ],
      "observed_components": {                        # #424 observed layer, x48
        "<canonical_field>": {
          "slug": "cfg_min_fog_on_s",                 # == registry cfg readback
          "unit": "s",
          "observed_at": "2026-08-26T01:01:59.000000Z",
          "value": 45                                 # raw cfg_* readback value
        }, ...
      },
      "band_layers": [                                # #424 three-layer proof
        {
          "series": "vpd_low",                        # >= REQUIRED_BAND_SERIES
          "served":   {"value": 0.875, "unit": "kPa", "as_of": "...Z",
                       "source": "fn_band_setpoints(now())"},
          "control":  {"value": 0.56,  "unit": "kPa", "as_of": "...Z",
                       "source": "firmware global gh_vpd_low"},
          "observed": {"value": 0.56,  "unit": "kPa", "as_of": "...Z",
                       "source": "setpoint_snapshot",
                       "slug": "cfg___vpd_low__kpa_"}
        }, ...
      ]
    }

A layer whose ``value`` is ``null``, whose ``unit`` is missing/empty, whose
``as_of`` is missing, in the future or older than ``--max-observation-age-s``,
or (for ``observed``) whose ``slug`` is missing, cannot expose a truthful value:
it is classified ``unobservable`` and blocks.  Layers that all expose truthful
values but disagree are classified ``present`` (the #424 discrepancy is still
present) and also block.  Only exact agreement is ``resolved``.

Exactness rule for numbers: an artifact may carry a grid/layer number either as
an exact decimal string (compared with ``Decimal`` equality) or as a JSON number
decoded from the ESPHome protobuf ``float`` transport (compared by exact
IEEE-754 binary32 bytes, the same rule ``component_qualification`` already
uses).  Neither path is a tolerance window: binary32 equality is the exact
representation the transport can carry, and one adjacent ULP is a mismatch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas.component_executor import (  # noqa: E402
    CANONICAL_FIELD_ORDER,
    ENTITY_GRIDS,
    ComponentContractError,
    normalize_complete_state,
)
from verdify_schemas.component_executor import GRID_REVISION as SOURCE_GRID_REVISION  # noqa: E402
from verdify_schemas.component_qualification import (  # noqa: E402
    ComponentGridEvidenceError,
    LiveEntityGridEvidence,
    RuntimeEntityMetadata,
    build_live_entity_grid_evidence,
)
from verdify_schemas.policy_vector import (  # noqa: E402
    POLICY_VECTOR_SIZE,
    encode_policy_vector,
    wire_manifest_digest,
)
from verdify_schemas.tunable_registry import REGISTRY, WIRE_SCHEMA_VERSION  # noqa: E402

INPUT_SCHEMA = "verdify-component-grid-capture-input-v1"
RESULT_SCHEMA = "verdify-component-grid-capture-result-v1"
# Emitted verbatim so scripts/prepare_component_prefix_replay.py can consume the
# captured start state as a --current-state input without a translation step.
CURRENT_STATE_SCHEMA = "verdify-component-current-state-v1"
POLICY_STATE_DOMAIN = b"verdify-policy-state-content-v1"

# #424 acceptance: "every temperature and VPD low/high/target series relevant to
# the fast study, not only target difference".  temp_target/vpd_target have no
# cfg_* number readback (they are computed publishes, migration 182), so their
# observed slug comes from the artifact rather than the registry.
REQUIRED_BAND_SERIES: tuple[str, ...] = (
    "temp_low",
    "temp_high",
    "temp_target",
    "vpd_low",
    "vpd_high",
    "vpd_target",
)

REQUIRED_REVISIONS: tuple[str, ...] = (
    "source_revision",
    "firmware_revision",
    "config_revision",
    "registry_revision",
    "crop_band_resolver_revision",
    "sensor_registry_revision",
)

DEFAULT_MAX_OBSERVATION_AGE_S = 900

_ENTITY_TYPES = ("number", "switch", "sensor")
_ENTITY_KEYS = frozenset(
    {
        "object_id",
        "entity_type",
        "key",
        "device_id",
        "disabled_by_default",
        "minimum",
        "maximum",
        "step",
        "unit",
        "assumed_state",
    }
)
_LAYER_KEYS = frozenset({"value", "unit", "as_of", "source", "slug"})
_SERIES_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")
_QUALIFIED_GRID_REVISION = re.compile(r"^live-entity-grid-v[1-9][0-9]*:sha256:[0-9a-f]{64}$")

Classification = Literal["resolved", "present", "unobservable"]


class GridCaptureError(ValueError):
    """The supplied artifact cannot prove a truthful current-state capture."""


# ──────────────────────────────────────────────────────────────────────────────
# Pure-logic core
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LiveEntityGrid:
    """One canonical field as the LIVE device actually exposes it.

    ``esp_object_id`` / ``cfg_readback_object_id`` / ``entity_type`` are ``None``
    when the device did not expose that route at all; ``compare_live_grid``
    reports the absence rather than substituting a source value.
    """

    field_name: str
    esp_object_id: str | None
    cfg_readback_object_id: str | None
    entity_type: str | None
    minimum: Decimal | float | str | None = None
    maximum: Decimal | float | str | None = None
    step: Decimal | float | str | None = None


@dataclass(frozen=True)
class LayerTriple:
    """One #424 series across the served / control / observed semantic layers."""

    field_name: str
    served: str | None
    control: str | None
    observed: str | None
    observed_slug: str | None
    observed_unit: str | None
    observed_ts: str | None
    coherent: bool
    classification: Classification
    detail: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "coherent": self.coherent,
            "control": self.control,
            "detail": self.detail,
            "field_name": self.field_name,
            "observed": self.observed,
            "observed_slug": self.observed_slug,
            "observed_ts": self.observed_ts,
            "observed_unit": self.observed_unit,
            "served": self.served,
        }


@dataclass(frozen=True)
class GridCaptureResult:
    """The complete, fail-closed capture verdict."""

    grid_parity_ok: bool
    band_coherence_ok: bool
    observed_state_ok: bool
    observed_start_state: dict[str, bool | float] | None
    observed_state_content_sha256: str | None
    observed_wire_vector_hex: str | None
    revisions: dict[str, str]
    runtime: dict[str, Any]
    device_id: str
    observed_at: str
    live_grid: tuple[LiveEntityGrid, ...]
    layer_triples: tuple[LayerTriple, ...]
    grid_content_sha256: str | None
    observation_receipt_sha256: str | None
    grid_revision: str | None
    failures: tuple[str, ...]

    @property
    def qualified(self) -> bool:
        return self.grid_revision is not None


def _decimal_text(value: Decimal) -> str:
    """Canonical decimal text: no exponent, no negative zero, no trailing zeros."""
    text = format(value.normalize(), "f")
    return "0" if text in {"-0", "-0.0"} else text


def _as_decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool):
        raise GridCaptureError(f"{label} must be a number, not bool")
    if isinstance(value, float) and not math.isfinite(value):
        raise GridCaptureError(f"{label} must be finite")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GridCaptureError(f"{label} is not a decimal number") from exc
    if not number.is_finite():
        raise GridCaptureError(f"{label} must be finite")
    return number


def _binary32(value: object, label: str) -> bytes:
    """Exact IEEE-754 binary32 bytes — the ESPHome protobuf representation."""
    if isinstance(value, bool):
        raise GridCaptureError(f"{label} must be a number, not bool")
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise GridCaptureError(f"{label} is not a number") from exc
    if not math.isfinite(numeric):
        raise GridCaptureError(f"{label} must be finite")
    try:
        return struct.pack("!f", numeric)
    except (OverflowError, struct.error) as exc:
        raise GridCaptureError(f"{label} is out of binary32 range") from exc


def _grid_number_matches(observed: object, expected: Decimal, label: str) -> bool:
    """Exact equality against one source grid number — never a tolerance window.

    An exact decimal string (or ``Decimal``) is compared with ``Decimal``
    equality.  A JSON number decoded off the protobuf ``float`` transport is
    compared by exact binary32 bytes, mirroring
    ``component_qualification._float32_bytes``: one adjacent ULP is a mismatch,
    and there is no rounding of a candidate value onto the grid.
    """
    if isinstance(observed, (str, Decimal)):
        return _as_decimal(observed, label) == expected
    return _binary32(observed, label) == _binary32(expected, f"{label}:expected")


def _observed_grid_text(value: object, label: str) -> str:
    if isinstance(value, (str, Decimal)):
        return _decimal_text(_as_decimal(value, label))
    return _decimal_text(_as_decimal(repr(float(value)), label))  # type: ignore[arg-type]


def compare_live_grid(live: Sequence[LiveEntityGrid]) -> list[str]:
    """Return the complete list of grid-parity failures; ``[]`` iff byte-parity.

    Every canonical field must appear exactly once, carry the registry's setter
    and cfg-readback slugs, carry the source entity type and — for numbers — the
    exact source ``(minimum, maximum, step)``.  Switches must carry no numeric
    grid at all, because a switch with a derived numeric grid is a different
    deployed entity, not a cosmetic difference.
    """
    failures: list[str] = []
    seen: dict[str, int] = {}
    for row in live:
        if not isinstance(row, LiveEntityGrid):
            failures.append(f"invalid_live_grid_row:{row!r}")
            continue
        seen[row.field_name] = seen.get(row.field_name, 0) + 1

    expected_fields = set(CANONICAL_FIELD_ORDER)
    for name in sorted(set(seen) - expected_fields):
        failures.append(f"extra_entity:{name}")
    for name in sorted(expected_fields - set(seen)):
        failures.append(f"missing_entity:{name}")
    for name in sorted(field for field, count in seen.items() if count > 1):
        failures.append(f"duplicate_entity:{name}:{seen[name]}")

    by_field = {row.field_name: row for row in live if isinstance(row, LiveEntityGrid)}
    for field_name in CANONICAL_FIELD_ORDER:
        row = by_field.get(field_name)
        if row is None:
            continue
        definition = REGISTRY[field_name]
        expected_grid = ENTITY_GRIDS[field_name]

        if definition.esp_object_id is None or definition.cfg_readback_object_id is None:
            failures.append(f"source_route_missing:{field_name}")
            continue
        if row.esp_object_id is None:
            failures.append(f"setter_route_absent:{field_name}:{definition.esp_object_id}")
        elif row.esp_object_id != definition.esp_object_id:
            failures.append(
                f"setter_route_mismatch:{field_name}:expected={definition.esp_object_id}:observed={row.esp_object_id}"
            )
        if row.cfg_readback_object_id is None:
            failures.append(f"readback_route_absent:{field_name}:{definition.cfg_readback_object_id}")
        elif row.cfg_readback_object_id != definition.cfg_readback_object_id:
            failures.append(
                f"readback_route_mismatch:{field_name}:expected={definition.cfg_readback_object_id}"
                f":observed={row.cfg_readback_object_id}"
            )

        if row.entity_type != expected_grid.entity_type:
            failures.append(
                f"entity_type_mismatch:{field_name}:expected={expected_grid.entity_type}:observed={row.entity_type}"
            )
            continue

        if expected_grid.entity_type == "switch":
            if any(value is not None for value in (row.minimum, row.maximum, row.step)):
                failures.append(f"switch_carries_numeric_grid:{field_name}")
            continue

        assert expected_grid.minimum is not None and expected_grid.maximum is not None
        assert expected_grid.step is not None
        bounds = (
            ("minimum", row.minimum, expected_grid.minimum),
            ("maximum", row.maximum, expected_grid.maximum),
            ("step", row.step, expected_grid.step),
        )
        for label, observed, expected in bounds:
            if observed is None:
                failures.append(f"number_grid_missing:{field_name}:{label}")
                continue
            try:
                matched = _grid_number_matches(observed, expected, f"{field_name}:{label}")
            except GridCaptureError as exc:
                failures.append(f"invalid_number_grid:{field_name}:{label}:{exc}")
                continue
            if not matched:
                observed_text = _observed_grid_text(observed, f"{field_name}:{label}")
                failures.append(
                    f"number_grid_mismatch:{field_name}:{label}"
                    f":expected={_decimal_text(expected)}:observed={observed_text}"
                )
    return failures


def project_live_entity_grid(entities: Sequence[RuntimeEntityMetadata]) -> tuple[LiveEntityGrid, ...]:
    """Attribute a raw runtime inventory onto the 48 canonical fields.

    Routes are keyed by ``(entity_type, object_id)``, exactly as
    ``component_qualification`` keys them: three switch fields
    (``sw_direct_wet_gate_enabled``, ``sw_fog_closes_vent``,
    ``sw_mister_closes_vent``) deliberately publish their cfg readback under the
    SAME slug as their setter, distinguished only by ESPHome entity type, so
    object_id alone would double-attribute them.

    When the expected setter type is absent the projection still looks for the
    slug under another type — a route present under the WRONG type must reach
    ``compare_live_grid`` as an ``entity_type_mismatch`` rather than vanish into
    ``setter_route_absent`` — but it never re-uses the sensor row that is
    already serving as that field's readback.  Duplicate routes are surfaced by
    emitting the field twice; ``compare_live_grid`` reports the duplicate.
    """
    by_route: dict[tuple[str, str], list[RuntimeEntityMetadata]] = {}
    for entity in entities:
        by_route.setdefault((entity.entity_type, entity.object_id), []).append(entity)

    rows: list[LiveEntityGrid] = []
    for field_name in CANONICAL_FIELD_ORDER:
        definition = REGISTRY[field_name]
        expected_type = ENTITY_GRIDS[field_name].entity_type
        setter_slug = definition.esp_object_id or ""
        readback_slug = definition.cfg_readback_object_id or ""
        found_readbacks = by_route.get(("sensor", readback_slug), [])
        found_setters = by_route.get((expected_type, setter_slug), [])
        if not found_setters:
            for other_type in _ENTITY_TYPES:
                if other_type == expected_type or (other_type == "sensor" and setter_slug == readback_slug):
                    continue
                candidates = by_route.get((other_type, setter_slug), [])
                if candidates:
                    found_setters = candidates
                    break
        repeats = max(len(found_setters), len(found_readbacks), 1)
        for index in range(repeats):
            setter = found_setters[index] if index < len(found_setters) else None
            readback = found_readbacks[index] if index < len(found_readbacks) else None
            rows.append(
                LiveEntityGrid(
                    field_name=field_name,
                    esp_object_id=None if setter is None else setter.object_id,
                    cfg_readback_object_id=None if readback is None else readback.object_id,
                    entity_type=None if setter is None else setter.entity_type,
                    minimum=None if setter is None else setter.minimum,
                    maximum=None if setter is None else setter.maximum,
                    step=None if setter is None else setter.step,
                )
            )
    return tuple(rows)


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise GridCaptureError(f"{label} must be an RFC-3339 UTC timestamp")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise GridCaptureError(f"{label} is not an RFC-3339 timestamp: {value!r}") from exc
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise GridCaptureError(f"{label} must carry an explicit UTC offset")
    return moment.astimezone(UTC)


def _timestamp_text(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _layer_value_text(layer: Mapping[str, Any], label: str) -> str | None:
    value = layer.get("value")
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return _decimal_text(_as_decimal(value, label))


def _layer_freshness(
    layer: Mapping[str, Any],
    *,
    label: str,
    observed_at: datetime,
    max_age_s: int,
) -> str | None:
    """Return a blocking reason when the layer's timestamp is not truthful."""
    raw = layer.get("as_of")
    if raw is None:
        return f"{label}_timestamp_missing"
    moment = _timestamp(raw, f"{label}.as_of")
    age = (observed_at - moment).total_seconds()
    if age < 0:
        return f"{label}_timestamp_in_future"
    if age > max_age_s:
        return f"{label}_timestamp_stale:{age:.0f}s>{max_age_s}s"
    return None


def evaluate_layer_triple(
    row: Mapping[str, Any],
    *,
    observed_at: datetime,
    max_age_s: int,
) -> LayerTriple:
    """Classify one #424 series as resolved / present / unobservable.

    ``resolved`` requires all three layers to expose a truthful, fresh,
    unit-coherent value AND exact agreement.  Anything less blocks: a layer that
    cannot expose a truthful value is ``unobservable``; truthful-but-disagreeing
    layers are ``present`` (the historical discrepancy is still present).
    Convergence is never inferred from a tolerance window.
    """
    series = row.get("series")
    if not isinstance(series, str) or _SERIES_NAME.fullmatch(series) is None:
        raise GridCaptureError(f"band_layers series name is invalid: {series!r}")

    layers: dict[str, Mapping[str, Any]] = {}
    for name in ("served", "control", "observed"):
        layer = row.get(name)
        if not isinstance(layer, Mapping):
            raise GridCaptureError(f"band_layers[{series}].{name} must be an object")
        unknown = sorted(set(layer) - _LAYER_KEYS)
        if unknown:
            raise GridCaptureError(f"band_layers[{series}].{name} has unknown keys: {unknown}")
        if name != "observed" and "slug" in layer:
            raise GridCaptureError(f"band_layers[{series}].{name} must not carry an entity slug")
        layers[name] = layer

    values: dict[str, str | None] = {}
    malformed: dict[str, str] = {}
    for name in ("served", "control", "observed"):
        try:
            values[name] = _layer_value_text(layers[name], f"band_layers[{series}].{name}.value")
        except GridCaptureError as exc:
            # A malformed number is not a truthful value; it must classify as
            # unobservable rather than abort the whole capture.
            values[name] = None
            malformed[name] = f"{name}_value_invalid:{exc}"
    served_text, control_text, observed_text = values["served"], values["control"], values["observed"]
    observed_slug = layers["observed"].get("slug")
    observed_unit = layers["observed"].get("unit")
    observed_as_of = layers["observed"].get("as_of")
    try:
        observed_ts = (
            None if observed_as_of is None else _timestamp_text(_timestamp(observed_as_of, f"{series}.observed"))
        )
    except GridCaptureError:
        observed_ts = None

    def triple(classification: Classification, detail: str, coherent: bool = False) -> LayerTriple:
        return LayerTriple(
            field_name=series,
            served=served_text,
            control=control_text,
            observed=observed_text,
            observed_slug=observed_slug if isinstance(observed_slug, str) else None,
            observed_unit=observed_unit if isinstance(observed_unit, str) else None,
            observed_ts=observed_ts,
            coherent=coherent,
            classification=classification,
            detail=detail,
        )

    blockers: list[str] = sorted(malformed.values())
    for name, text in (("served", served_text), ("control", control_text), ("observed", observed_text)):
        if text is None and name not in malformed:
            blockers.append(f"{name}_value_missing")
        unit = layers[name].get("unit")
        if not isinstance(unit, str) or not unit:
            blockers.append(f"{name}_unit_missing")
        source = layers[name].get("source")
        if not isinstance(source, str) or not source:
            blockers.append(f"{name}_provenance_missing")
        try:
            stale = _layer_freshness(layers[name], label=name, observed_at=observed_at, max_age_s=max_age_s)
        except GridCaptureError as exc:
            stale = f"{name}_timestamp_invalid:{exc}"
        if stale is not None:
            blockers.append(stale)
    if not isinstance(observed_slug, str) or not observed_slug:
        blockers.append("observed_slug_missing")
    units = {layers[name].get("unit") for name in ("served", "control", "observed")}
    if len(units) != 1:
        blockers.append(f"unit_incoherent:{sorted(str(unit) for unit in units)}")
    if blockers:
        return triple("unobservable", ",".join(sorted(set(blockers))))

    if served_text == control_text == observed_text:
        return triple("resolved", "", coherent=True)

    detail = f"served={served_text} control={control_text} observed={observed_text}"
    try:
        differences = {
            "control_minus_served": _decimal_text(
                _as_decimal(control_text, "control") - _as_decimal(served_text, "served")
            ),
            "observed_minus_control": _decimal_text(
                _as_decimal(observed_text, "observed") - _as_decimal(control_text, "control")
            ),
        }
        detail = f"{detail} delta={differences}"
    except GridCaptureError:
        # Booleans (or any non-numeric layer value) have no difference to report;
        # the raw three-layer values above are already the whole story.
        pass
    return triple("present", detail)


def evaluate_layer_triples(
    rows: Sequence[Mapping[str, Any]],
    *,
    observed_at: datetime,
    max_age_s: int,
    required: Sequence[str] = REQUIRED_BAND_SERIES,
) -> tuple[tuple[LayerTriple, ...], list[str]]:
    """Evaluate every supplied series and report the blocking failures."""
    triples = tuple(evaluate_layer_triple(row, observed_at=observed_at, max_age_s=max_age_s) for row in rows)
    failures: list[str] = []
    seen = [triple.field_name for triple in triples]
    for name in sorted(set(name for name in seen if seen.count(name) > 1)):
        failures.append(f"duplicate_band_series:{name}")
    for name in required:
        if name not in seen:
            failures.append(f"missing_band_series:{name}")
    for triple in triples:
        if triple.classification == "unobservable":
            failures.append(f"band_layer_unobservable:{triple.field_name}:{triple.detail}")
        elif not triple.coherent:
            failures.append(f"band_layer_incoherent:{triple.field_name}:{triple.detail}")
    return triples, failures


def observed_state_content_sha256(normalized: Mapping[str, bool | float]) -> tuple[str, str]:
    """Reproduce the migration-214 cross-language state identity in Python.

    ``db/migrations/214-confirmed-component-experiment-v2.sql``
    ``fn_experiment_v2_state_content_sha256(schema_u8, manifest[32], vector[178])``
    is the golden::

        sha256('verdify-policy-state-content-v1' || 0x00 || schema_u8
               || wire_manifest_digest[32] || wire_vector[178])

    Returns ``(state_content_sha256, wire_vector_hex)``.
    """
    vector = encode_policy_vector(normalized)
    if len(vector) != POLICY_VECTOR_SIZE or POLICY_VECTOR_SIZE != 178:
        raise GridCaptureError(f"policy vector must be 178 bytes, got {len(vector)}")
    manifest = wire_manifest_digest()
    if len(manifest) != 32:
        raise GridCaptureError("wire manifest digest must be 32 bytes")
    digest = hashlib.sha256(
        POLICY_STATE_DOMAIN + b"\x00" + bytes([WIRE_SCHEMA_VERSION]) + manifest + vector
    ).hexdigest()
    return digest, vector.hex()


def evaluate_observed_components(
    observed: Mapping[str, Mapping[str, Any]],
    *,
    observed_at: datetime,
    max_age_s: int,
) -> tuple[dict[str, bool | float] | None, list[str]]:
    """Validate the observed cfg readback layer for all 48 component fields."""
    failures: list[str] = []
    expected = set(CANONICAL_FIELD_ORDER)
    provided = set(observed)
    for name in sorted(provided - expected):
        failures.append(f"observed_component_unknown:{name}")
    for name in sorted(expected - provided):
        failures.append(f"observed_component_missing:{name}")

    raw: dict[str, Any] = {}
    for field_name in CANONICAL_FIELD_ORDER:
        row = observed.get(field_name)
        if row is None:
            continue
        if not isinstance(row, Mapping):
            failures.append(f"observed_component_malformed:{field_name}")
            continue
        unknown = sorted(set(row) - {"slug", "unit", "observed_at", "value"})
        if unknown:
            failures.append(f"observed_component_unknown_keys:{field_name}:{unknown}")
            continue
        expected_slug = REGISTRY[field_name].cfg_readback_object_id
        if row.get("slug") != expected_slug:
            failures.append(
                f"observed_component_slug_mismatch:{field_name}:expected={expected_slug}:observed={row.get('slug')}"
            )
        try:
            stale = _layer_freshness(
                {"as_of": row.get("observed_at")},
                label=f"observed_component:{field_name}",
                observed_at=observed_at,
                max_age_s=max_age_s,
            )
        except GridCaptureError as exc:
            failures.append(f"observed_component_timestamp_invalid:{field_name}:{exc}")
            stale = None
        if stale is not None:
            failures.append(stale)
        if "value" not in row or row["value"] is None:
            failures.append(f"observed_component_value_missing:{field_name}")
            continue
        raw[field_name] = row["value"]

    if failures:
        return None, failures

    try:
        normalized = normalize_complete_state(raw)
    except ComponentContractError as exc:
        return None, [f"observed_component_state_rejected:{exc.code}:{exc.detail}"]
    return normalized, []


def derive_grid_revision(result: GridCaptureResult) -> str | None:
    """Return the qualified grid revision, or ``None`` when anything blocks.

    The revision is the one ``build_live_entity_grid_evidence`` already produced
    (see REVISION-DERIVATION NOTE): the offline capture never invents a second
    preimage, because the runtime attestation must be able to reproduce the same
    string for ``physical_execution_qualified`` to ever close.

    Grid parity and band coherence are the two semantic gates.  The observed
    current state is a third: an off-grid, out-of-range, mistyped, mis-slugged,
    stale, missing or extra readback means the capture did not observe a
    truthful current state, and a capture that cannot state where the device IS
    must not hand out a revision that authorizes moving it.
    """
    if not (result.grid_parity_ok and result.band_coherence_ok and result.observed_state_ok):
        return None
    if result.grid_content_sha256 is None:
        return None
    revision = f"live-entity-grid-v1:sha256:{result.grid_content_sha256}"
    if _QUALIFIED_GRID_REVISION.fullmatch(revision) is None:
        return None
    return revision


def capture(
    *,
    device_id: str,
    observed_at: datetime,
    entities: Sequence[RuntimeEntityMetadata],
    observed_components: Mapping[str, Mapping[str, Any]],
    band_layers: Sequence[Mapping[str, Any]],
    revisions: Mapping[str, str],
    runtime: Mapping[str, Any],
    max_observation_age_s: int = DEFAULT_MAX_OBSERVATION_AGE_S,
    required_band_series: Sequence[str] = REQUIRED_BAND_SERIES,
) -> GridCaptureResult:
    """Run the whole offline capture and return a fail-closed verdict."""
    failures: list[str] = []
    live_grid = project_live_entity_grid(entities)
    grid_failures = compare_live_grid(live_grid)
    failures.extend(grid_failures)

    evidence: LiveEntityGridEvidence | None = None
    try:
        evidence = build_live_entity_grid_evidence(
            tuple(entities),
            device_id=device_id,
            firmware_revision=revisions["firmware_revision"],
            source_revision=revisions["source_revision"],
            runtime_instance_id=str(runtime["runtime_instance_id"]),
            connection_generation=int(runtime["connection_generation"]),
            observed_at=observed_at,
        )
    except ComponentGridEvidenceError as exc:
        failures.append(f"live_grid_evidence:{exc.code}:{exc.detail}")
    except (KeyError, TypeError, ValueError) as exc:
        failures.append(f"live_grid_evidence_inputs:{exc}")
    grid_parity_ok = not grid_failures and evidence is not None

    triples, band_failures = evaluate_layer_triples(
        band_layers,
        observed_at=observed_at,
        max_age_s=max_observation_age_s,
        required=required_band_series,
    )
    failures.extend(band_failures)
    band_coherence_ok = not band_failures

    normalized, state_failures = evaluate_observed_components(
        observed_components,
        observed_at=observed_at,
        max_age_s=max_observation_age_s,
    )
    failures.extend(state_failures)
    state_sha256: str | None = None
    wire_hex: str | None = None
    if normalized is not None:
        try:
            state_sha256, wire_hex = observed_state_content_sha256(normalized)
        except (GridCaptureError, ValueError) as exc:
            failures.append(f"observed_state_identity:{exc}")
            normalized = None
    observed_state_ok = normalized is not None and state_sha256 is not None

    result = GridCaptureResult(
        grid_parity_ok=grid_parity_ok,
        band_coherence_ok=band_coherence_ok,
        observed_state_ok=observed_state_ok,
        observed_start_state=normalized,
        observed_state_content_sha256=state_sha256,
        observed_wire_vector_hex=wire_hex,
        revisions=dict(revisions),
        runtime=dict(runtime),
        device_id=device_id,
        observed_at=_timestamp_text(observed_at),
        live_grid=live_grid,
        layer_triples=triples,
        grid_content_sha256=None if evidence is None else evidence.grid_content_sha256,
        observation_receipt_sha256=None if evidence is None else evidence.observation_receipt_sha256,
        grid_revision=None,
        failures=tuple(failures),
    )
    return replace(result, grid_revision=derive_grid_revision(result))


# ──────────────────────────────────────────────────────────────────────────────
# Input artifact parsing (strict; unknown keys are rejected)
# ──────────────────────────────────────────────────────────────────────────────


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GridCaptureError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _nfc_text(value: object, label: str, *, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise GridCaptureError(f"{label} must be bounded non-empty text")
    if unicodedata.normalize("NFC", value) != value:
        raise GridCaptureError(f"{label} must be NFC-normalized text")
    return value


def _entity(row: Mapping[str, Any], index: int) -> RuntimeEntityMetadata:
    unknown = sorted(set(row) - _ENTITY_KEYS)
    if unknown:
        raise GridCaptureError(f"entities[{index}] has unknown keys: {unknown}")
    entity_type = row.get("entity_type")
    if entity_type not in _ENTITY_TYPES:
        raise GridCaptureError(f"entities[{index}].entity_type must be one of {list(_ENTITY_TYPES)}")
    key = row.get("key")
    if isinstance(key, bool) or not isinstance(key, int) or key <= 0:
        raise GridCaptureError(f"entities[{index}].key must be a positive integer")
    device_id = row.get("device_id", 0)
    if isinstance(device_id, bool) or not isinstance(device_id, int) or device_id < 0:
        raise GridCaptureError(f"entities[{index}].device_id must be a non-negative integer")
    disabled = row.get("disabled_by_default", False)
    if type(disabled) is not bool:  # noqa: E721 - evidence must not coerce 0/1
        raise GridCaptureError(f"entities[{index}].disabled_by_default must be an exact boolean")
    assumed = row.get("assumed_state")
    if assumed is not None and type(assumed) is not bool:  # noqa: E721
        raise GridCaptureError(f"entities[{index}].assumed_state must be an exact boolean or null")
    unit = row.get("unit", "")
    if not isinstance(unit, str):
        raise GridCaptureError(f"entities[{index}].unit must be text")
    numbers: dict[str, Decimal | float | None] = {}
    for label in ("minimum", "maximum", "step"):
        value = row.get(label)
        if value is None:
            numbers[label] = None
        elif isinstance(value, str):
            numbers[label] = _as_decimal(value, f"entities[{index}].{label}")
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GridCaptureError(f"entities[{index}].{label} must be a number, decimal string or null")
        else:
            numbers[label] = float(value)
    return RuntimeEntityMetadata(
        object_id=_nfc_text(row.get("object_id"), f"entities[{index}].object_id"),
        entity_type=entity_type,
        key=key,
        device_id=device_id,
        disabled_by_default=disabled,
        minimum=numbers["minimum"],
        maximum=numbers["maximum"],
        step=numbers["step"],
        unit=unit,
        assumed_state=assumed,
    )


@dataclass(frozen=True)
class CaptureInput:
    """The parsed, structurally-valid input artifact."""

    device_id: str
    observed_at: datetime
    entities: tuple[RuntimeEntityMetadata, ...]
    observed_components: dict[str, dict[str, Any]]
    band_layers: tuple[dict[str, Any], ...]
    revisions: dict[str, str]
    runtime: dict[str, Any]


def parse_input_artifact(document: Mapping[str, Any]) -> CaptureInput:
    """Validate the input artifact's shape; raise ``GridCaptureError`` if unsafe."""
    if not isinstance(document, Mapping):
        raise GridCaptureError("input artifact root must be an object")
    expected_keys = {
        "schema",
        "device_id",
        "observed_at",
        "runtime",
        "revisions",
        "entities",
        "observed_components",
        "band_layers",
    }
    unknown = sorted(set(document) - expected_keys)
    if unknown:
        raise GridCaptureError(f"input artifact has unknown keys: {unknown}")
    missing = sorted(expected_keys - set(document))
    if missing:
        raise GridCaptureError(f"input artifact is missing: {missing}")
    if document["schema"] != INPUT_SCHEMA:
        raise GridCaptureError(f"input artifact schema must be {INPUT_SCHEMA!r}")

    revisions_raw = document["revisions"]
    if not isinstance(revisions_raw, Mapping):
        raise GridCaptureError("revisions must be an object")
    unknown = sorted(set(revisions_raw) - set(REQUIRED_REVISIONS))
    if unknown:
        raise GridCaptureError(f"revisions has unknown keys: {unknown}")
    revisions: dict[str, str] = {}
    for name in REQUIRED_REVISIONS:
        revisions[name] = _nfc_text(revisions_raw.get(name), f"revisions.{name}")
    if _SOURCE_REVISION.fullmatch(revisions["source_revision"]) is None:
        raise GridCaptureError("revisions.source_revision must be a full lowercase Git SHA")

    runtime_raw = document["runtime"]
    if not isinstance(runtime_raw, Mapping) or set(runtime_raw) != {"runtime_instance_id", "connection_generation"}:
        raise GridCaptureError("runtime must carry exactly runtime_instance_id and connection_generation")
    try:
        instance_id = str(UUID(str(runtime_raw["runtime_instance_id"])))
    except (TypeError, ValueError) as exc:
        raise GridCaptureError("runtime.runtime_instance_id must be a UUID") from exc
    generation = runtime_raw["connection_generation"]
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise GridCaptureError("runtime.connection_generation must be an integer >= 1")

    entities_raw = document["entities"]
    if not isinstance(entities_raw, list) or not entities_raw:
        raise GridCaptureError("entities must be a non-empty array")
    entities = tuple(_entity(row, index) for index, row in enumerate(entities_raw) if isinstance(row, Mapping))
    if len(entities) != len(entities_raw):
        raise GridCaptureError("every entities row must be an object")

    observed_raw = document["observed_components"]
    if not isinstance(observed_raw, Mapping):
        raise GridCaptureError("observed_components must be an object")
    observed = {str(name): dict(row) for name, row in observed_raw.items() if isinstance(row, Mapping)}
    if len(observed) != len(observed_raw):
        raise GridCaptureError("every observed_components entry must be an object")

    band_raw = document["band_layers"]
    if not isinstance(band_raw, list):
        raise GridCaptureError("band_layers must be an array")
    band_layers = tuple(dict(row) for row in band_raw if isinstance(row, Mapping))
    if len(band_layers) != len(band_raw):
        raise GridCaptureError("every band_layers row must be an object")

    return CaptureInput(
        device_id=_nfc_text(document["device_id"], "device_id"),
        observed_at=_timestamp(document["observed_at"], "observed_at"),
        entities=entities,
        observed_components=observed,
        band_layers=band_layers,
        revisions=revisions,
        runtime={"runtime_instance_id": instance_id, "connection_generation": generation},
    )


def capture_from_artifact(
    document: Mapping[str, Any],
    *,
    max_observation_age_s: int = DEFAULT_MAX_OBSERVATION_AGE_S,
) -> GridCaptureResult:
    """Parse then capture — the single entry point the CLI uses."""
    parsed = parse_input_artifact(document)
    return capture(
        device_id=parsed.device_id,
        observed_at=parsed.observed_at,
        entities=parsed.entities,
        observed_components=parsed.observed_components,
        band_layers=parsed.band_layers,
        revisions=parsed.revisions,
        runtime=parsed.runtime,
        max_observation_age_s=max_observation_age_s,
    )


# ──────────────────────────────────────────────────────────────────────────────
# CLI (PASS/FAIL/WARN + --json, matching scripts/experiment-*.py)
# ──────────────────────────────────────────────────────────────────────────────

MAX_FAILURES_LISTED = 25


@dataclass
class Check:
    name: str
    status: str  # PASS | FAIL | WARN
    detail: str = ""

    def payload(self) -> dict[str, str]:
        return {"detail": self.detail, "name": self.name, "status": self.status}


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def result_sha256(payload: Mapping[str, Any]) -> str:
    """Canonical hash of the result — excludes itself and wall-clock fields."""
    hashed = {k: v for k, v in payload.items() if k not in ("result_sha256", "computed_at")}
    return hashlib.sha256(canonical_json(hashed).encode()).hexdigest()


_BAND_FAILURE_PREFIXES = ("band_layer", "missing_band_series", "duplicate_band_series")
_STATE_FAILURE_PREFIXES = ("observed_component", "observed_state")


def _failure_detail(failures: Sequence[str]) -> str:
    """Join whole failure strings — never truncate a value mid-digit."""
    listed = list(failures[:MAX_FAILURES_LISTED])
    if len(failures) > MAX_FAILURES_LISTED:
        listed.append(f"(+{len(failures) - MAX_FAILURES_LISTED} more; see --json)")
    return "; ".join(listed)


def build_checks(result: GridCaptureResult, *, expected_grid_revision: str | None) -> list[Check]:
    checks: list[Check] = []
    band_failures = [f for f in result.failures if f.startswith(_BAND_FAILURE_PREFIXES)]
    state_failures = [f for f in result.failures if f.startswith(_STATE_FAILURE_PREFIXES)]
    grid_failures = [f for f in result.failures if not f.startswith(_BAND_FAILURE_PREFIXES + _STATE_FAILURE_PREFIXES)]
    checks.append(
        Check(
            "grid_parity",
            "PASS" if result.grid_parity_ok else "FAIL",
            f"{len(CANONICAL_FIELD_ORDER)}/{len(CANONICAL_FIELD_ORDER)} fields byte-parity with source ENTITY_GRIDS"
            if result.grid_parity_ok
            else _failure_detail(grid_failures),
        )
    )
    resolved = sum(1 for t in result.layer_triples if t.classification == "resolved")
    checks.append(
        Check(
            "band_coherence_424",
            "PASS" if result.band_coherence_ok else "FAIL",
            f"{resolved}/{len(result.layer_triples)} series resolved across served/control/observed"
            if result.band_coherence_ok
            else _failure_detail(band_failures),
        )
    )
    checks.append(
        Check(
            "observed_start_state",
            "PASS" if result.observed_state_ok else "FAIL",
            f"48/48 on-grid; state_content_sha256={result.observed_state_content_sha256}"
            if result.observed_state_ok
            else _failure_detail(state_failures),
        )
    )
    if result.grid_revision is None:
        checks.append(Check("grid_revision", "FAIL", "no qualified revision emitted (capture blocked)"))
    elif expected_grid_revision is not None and expected_grid_revision != result.grid_revision:
        checks.append(
            Check(
                "grid_revision",
                "FAIL",
                f"expected={expected_grid_revision} derived={result.grid_revision}",
            )
        )
    else:
        checks.append(Check("grid_revision", "PASS", result.grid_revision))
        if SOURCE_GRID_REVISION != result.grid_revision:
            checks.append(
                Check(
                    "source_grid_revision_adoption",
                    "WARN",
                    "verdify_schemas/component_executor.py GRID_REVISION is still "
                    f"{SOURCE_GRID_REVISION!r}; a separate reviewed change must adopt {result.grid_revision}",
                )
            )
    return checks


def build_payload(
    result: GridCaptureResult,
    checks: Sequence[Check],
    *,
    max_observation_age_s: int,
    computed_at: datetime,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "band_coherence": {
            "ok": result.band_coherence_ok,
            "required_series": list(REQUIRED_BAND_SERIES),
            "series": [triple.payload() for triple in result.layer_triples],
        },
        "checks": [check.payload() for check in checks],
        "device_id": result.device_id,
        "failures": list(result.failures),
        "grid": {
            "field_count": len(CANONICAL_FIELD_ORDER),
            "grid_content_sha256": result.grid_content_sha256,
            "observation_receipt_sha256": result.observation_receipt_sha256,
            "parity_ok": result.grid_parity_ok,
            "wire_manifest_sha256": wire_manifest_digest().hex(),
            "wire_schema_version": WIRE_SCHEMA_VERSION,
        },
        "grid_revision": result.grid_revision,
        "max_observation_age_s": max_observation_age_s,
        "observed_at": result.observed_at,
        "observed_state": {
            "on_grid": result.observed_state_ok,
            "state_content_sha256": result.observed_state_content_sha256,
            "wire_vector_hex": result.observed_wire_vector_hex,
        },
        "qualified": result.qualified,
        "revisions": dict(result.revisions),
        "runtime": dict(result.runtime),
        "schema": RESULT_SCHEMA,
        "source_grid_revision": SOURCE_GRID_REVISION,
        "source_grid_revision_qualified": _QUALIFIED_GRID_REVISION.fullmatch(SOURCE_GRID_REVISION) is not None,
    }
    if result.observed_start_state is not None and result.grid_revision is not None:
        # Emitted in the prefix-replay tool's own --current-state shape so the
        # captured start state feeds Tool B without a translation step.
        payload["current_state_artifact"] = {
            "device_id": result.device_id,
            "firmware_revision": result.revisions["firmware_revision"],
            "grid_revision": result.grid_revision,
            "observed_at": result.observed_at,
            "schema": CURRENT_STATE_SCHEMA,
            "values": dict(result.observed_start_state),
        }
    payload["computed_at"] = _timestamp_text(computed_at)
    payload["result_sha256"] = result_sha256(payload)
    return payload


def write_private_json(payload: Mapping[str, Any], output_path: Path) -> None:
    """Publish the result at mode 0600 without overwriting anything.

    The payload contains a complete 48-field operational snapshot, so it is
    written like ``scripts/prepare_component_prefix_replay.py`` publishes its
    packet: private mode, exclusive create, existing paths refused.
    """
    raw = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    unresolved = output_path.absolute()
    if unresolved.exists() or unresolved.is_symlink():
        raise GridCaptureError(f"output target already exists; overwrite refused: {unresolved}")
    descriptor = os.open(unresolved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline current-state capture: prove the live entity grid and emit its qualified revision."
    )
    parser.add_argument("--input", type=Path, required=True, help=f"path to a {INPUT_SCHEMA} JSON artifact")
    parser.add_argument("--json", dest="json_out", type=Path, help="write the machine-readable result here (0600)")
    parser.add_argument(
        "--max-observation-age-s",
        type=int,
        default=DEFAULT_MAX_OBSERVATION_AGE_S,
        help=f"maximum readback age still considered truthful (default: {DEFAULT_MAX_OBSERVATION_AGE_S})",
    )
    parser.add_argument(
        "--expect-grid-revision",
        help="fail unless the derived revision equals this value (re-verification of a prior capture)",
    )
    args = parser.parse_args(argv)

    if args.max_observation_age_s < 1:
        parser.exit(2, "grid capture refused: --max-observation-age-s must be >= 1\n")
    try:
        raw = args.input.read_bytes()
    except OSError as exc:
        parser.exit(2, f"grid capture refused: input artifact is unavailable: {exc}\n")
    if not raw or len(raw) > 8 * 1024 * 1024:
        parser.exit(2, "grid capture refused: input artifact is empty or exceeds 8 MiB\n")
    try:
        document = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, GridCaptureError) as exc:
        parser.exit(2, f"grid capture refused: input artifact is not strict UTF-8 JSON: {exc}\n")

    try:
        result = capture_from_artifact(document, max_observation_age_s=args.max_observation_age_s)
    except GridCaptureError as exc:
        parser.exit(2, f"grid capture refused: {exc}\n")

    checks = build_checks(result, expected_grid_revision=args.expect_grid_revision)
    payload = build_payload(
        result,
        checks,
        max_observation_age_s=args.max_observation_age_s,
        computed_at=datetime.now(UTC),
    )

    for check in checks:
        line = f"{check.status} — {check.name}"
        if check.detail:
            line += f": {check.detail}"
        print(line)
    failed = sum(1 for check in checks if check.status == "FAIL")
    warned = sum(1 for check in checks if check.status == "WARN")
    print(f"SUMMARY: {len(checks) - failed - warned} pass, {warned} warn, {failed} fail")
    print(f"result_sha256={payload['result_sha256']}")
    if result.grid_revision is not None and not failed:
        print(f"grid_revision={result.grid_revision}")
    else:
        print("grid_revision=NONE (capture blocked; physical execution stays gated)")

    if args.json_out is not None:
        try:
            write_private_json(payload, args.json_out)
        except (GridCaptureError, OSError) as exc:
            parser.exit(2, f"grid capture refused: result could not be published privately: {exc}\n")
        print(f"wrote {args.json_out}")
    return 1 if failed or result.grid_revision is None else 0


__all__ = [
    "CURRENT_STATE_SCHEMA",
    "DEFAULT_MAX_OBSERVATION_AGE_S",
    "INPUT_SCHEMA",
    "REQUIRED_BAND_SERIES",
    "RESULT_SCHEMA",
    "CaptureInput",
    "Check",
    "GridCaptureError",
    "GridCaptureResult",
    "LayerTriple",
    "LiveEntityGrid",
    "build_checks",
    "build_payload",
    "canonical_json",
    "capture",
    "capture_from_artifact",
    "compare_live_grid",
    "derive_grid_revision",
    "evaluate_layer_triple",
    "evaluate_layer_triples",
    "evaluate_observed_components",
    "main",
    "observed_state_content_sha256",
    "parse_input_artifact",
    "project_live_entity_grid",
    "result_sha256",
]


if __name__ == "__main__":
    raise SystemExit(main())
