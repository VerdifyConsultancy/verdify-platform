#!/usr/bin/env python3
"""Prepare the exact, non-actuating component prefix-replay corpus.

This tool is intentionally offline.  It reads a frozen profile artifact, an
ESPHome-generated ``main.cpp`` and firmware binary, plus one or more previously
captured complete current-state artifacts.  It never opens a network socket,
subscribes to ESPHome, invokes a setter/service, reads a database, or talks to a
device.

The output enumerates every state reached by the actual activation/rollback
setter lists and by the unconditional full-48 recovery list.  It is always
labelled ``prepared_not_qualified``: compiled replay and HIL runners must attach
passing results for every case before a separate reviewed artifact may become
``ORDER_REVISION`` evidence.

Compiled defaults are extracted from ESPHome's generated C++, not inferred
from YAML.  Raw starts are hashed in their own domain because a real compiled
default may be outside the deployed entity grid.  Such a value is preserved in
the prefix states until the recovery command repairs that exact field; it is
never silently rounded or clamped into a policy-state identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas.component_executor import (  # noqa: E402
    ACTIVATION_ORDER,
    CANONICAL_FIELD_ORDER,
    RECOVERY_ORDER,
    ROLLBACK_ORDER,
    ComponentContractError,
    normalize_complete_state,
)
from verdify_schemas.policy_vector import (  # noqa: E402
    canonical_json_bytes,
    decode_policy_vector,
    encode_policy_vector,
    wire_manifest_digest,
)
from verdify_schemas.tunable_registry import REGISTRY, WIRE_SCHEMA_VERSION, wire_value_bounds  # noqa: E402

PACKET_SCHEMA = "verdify-component-prefix-replay-preparation-v1"
CURRENT_STATE_SCHEMA = "verdify-component-current-state-v1"
STATUS = "prepared_not_qualified"
RAW_STATE_DOMAIN = b"verdify-component-raw-state-v1"
POLICY_STATE_DOMAIN = b"verdify-policy-state-content-v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_GRID_REVISION = re.compile(r"^live-entity-grid-v[1-9][0-9]*:sha256:[0-9a-f]{64}$")
_CPP_DEFAULT = re.compile(
    r"new\((?P<global>[A-Za-z_][A-Za-z0-9_]*)\)\s+"
    r"globals::(?:Restoring)?GlobalsComponent<(?P<type>bool|float|double|int|u?int(?:8|16|32|64)_t)>"
    r"\((?P<literal>true|false|[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))\);"
)


class PrefixPreparationError(ValueError):
    """The offline inputs cannot produce a truthful replay corpus."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_bytes(path: Path, label: str, maximum: int) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PrefixPreparationError(f"{label} is unavailable: {path}") from exc
    if not raw or len(raw) > maximum:
        raise PrefixPreparationError(f"{label} is empty or exceeds {maximum} bytes")
    return raw


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PrefixPreparationError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrefixPreparationError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PrefixPreparationError(f"{label} root must be an object")
    return value


def _finite_raw_state(value: object, label: str) -> dict[str, bool | float]:
    if not isinstance(value, Mapping) or set(value) != set(CANONICAL_FIELD_ORDER):
        missing = sorted(set(CANONICAL_FIELD_ORDER) - set(value if isinstance(value, Mapping) else ()))
        extra = sorted(set(value if isinstance(value, Mapping) else ()) - set(CANONICAL_FIELD_ORDER))
        raise PrefixPreparationError(f"{label} must contain exactly 48 fields: missing={missing} extra={extra}")
    result: dict[str, bool | float] = {}
    for field_name in CANONICAL_FIELD_ORDER:
        definition = REGISTRY[field_name]
        raw = value[field_name]
        if definition.wire_kind == "bool":
            if type(raw) is not bool:  # noqa: E721 - integers are not bool evidence
                raise PrefixPreparationError(f"{label}.{field_name} must be an exact boolean")
            result[field_name] = raw
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
            raise PrefixPreparationError(f"{label}.{field_name} must be a finite number")
        lower, upper = wire_value_bounds(field_name)
        numeric = float(raw)
        if numeric < lower or numeric > upper:
            raise PrefixPreparationError(f"{label}.{field_name} is outside its source-declared wire bounds")
        result[field_name] = numeric
    return result


def _state_identity(values: Mapping[str, bool | float]) -> dict[str, object]:
    canonical_raw = canonical_json_bytes({field: values[field] for field in CANONICAL_FIELD_ORDER})
    raw_digest = hashlib.sha256(RAW_STATE_DOMAIN + b"\x00" + canonical_raw).hexdigest()
    try:
        normalized = normalize_complete_state(values)
    except ComponentContractError as exc:
        return {
            "entity_grid_valid": False,
            "policy_state_content_sha256": None,
            "raw_state_sha256": raw_digest,
            "validation_code": exc.code,
            "validation_detail": exc.detail,
        }
    vector = encode_policy_vector(normalized)
    state_digest = hashlib.sha256(
        POLICY_STATE_DOMAIN + b"\x00" + bytes([WIRE_SCHEMA_VERSION]) + wire_manifest_digest() + vector
    ).hexdigest()
    return {
        "entity_grid_valid": True,
        "policy_state_content_sha256": state_digest,
        "raw_state_sha256": raw_digest,
        "validation_code": None,
        "validation_detail": "",
    }


def extract_compiled_defaults(
    generated_main_cpp: bytes,
    consumer_manifest: Mapping[str, object],
) -> dict[str, bool | float]:
    """Extract the 48 constructors emitted by the ESPHome compiler."""
    try:
        text = generated_main_cpp.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PrefixPreparationError("generated main.cpp is not UTF-8") from exc
    rows = consumer_manifest.get("fields")
    if not isinstance(rows, list):
        raise PrefixPreparationError("consumer manifest has no fields array")
    global_by_field: dict[str, str] = {}
    for row in rows:
        if (
            not isinstance(row, Mapping)
            or not isinstance(row.get("name"), str)
            or not isinstance(row.get("global_id"), str)
        ):
            raise PrefixPreparationError("consumer manifest field row is malformed")
        name = str(row["name"])
        if name in global_by_field:
            raise PrefixPreparationError(f"consumer manifest duplicates {name}")
        global_by_field[name] = str(row["global_id"])
    if set(global_by_field) != set(CANONICAL_FIELD_ORDER):
        raise PrefixPreparationError("consumer manifest does not map exactly the 48 policy fields")

    constructors: dict[str, tuple[str, str]] = {}
    for match in _CPP_DEFAULT.finditer(text):
        global_id = match.group("global")
        if global_id in constructors:
            raise PrefixPreparationError(f"generated main.cpp duplicates global constructor {global_id}")
        constructors[global_id] = (match.group("type"), match.group("literal"))

    values: dict[str, bool | float] = {}
    for field_name in CANONICAL_FIELD_ORDER:
        global_id = global_by_field[field_name]
        if global_id not in constructors:
            raise PrefixPreparationError(f"generated main.cpp lacks compiled default {global_id}")
        cpp_type, literal = constructors[global_id]
        if cpp_type == "bool":
            if literal not in {"true", "false"}:
                raise PrefixPreparationError(f"compiled boolean {global_id} has non-boolean literal")
            values[field_name] = literal == "true"
        else:
            if literal in {"true", "false"}:
                raise PrefixPreparationError(f"compiled numeric {global_id} has boolean literal")
            numeric = float(literal)
            if not math.isfinite(numeric):
                raise PrefixPreparationError(f"compiled default {global_id} is non-finite")
            values[field_name] = numeric
    return _finite_raw_state(values, "compiled_defaults")


def _profiles(artifact: Mapping[str, object]) -> tuple[dict[str, dict[str, bool | float]], dict[str, object]]:
    wire = artifact.get("wire_schema")
    rows = artifact.get("profiles")
    if not isinstance(wire, Mapping) or not isinstance(rows, Mapping):
        raise PrefixPreparationError("profile artifact is malformed")
    if wire.get("version") != WIRE_SCHEMA_VERSION or wire.get("manifest_digest_sha256") != wire_manifest_digest().hex():
        raise PrefixPreparationError("profile artifact wire contract differs from source")
    expected_profiles = {"baseline", "moderate", "aggressive"}
    if set(rows) != expected_profiles:
        raise PrefixPreparationError("profile artifact must contain exactly baseline/moderate/aggressive")
    profiles: dict[str, dict[str, bool | float]] = {}
    public_rows: dict[str, object] = {}
    for name in sorted(expected_profiles):
        row = rows[name]
        if not isinstance(row, Mapping) or not isinstance(row.get("wire_hex"), str):
            raise PrefixPreparationError(f"profile {name} has no wire_hex")
        try:
            vector = bytes.fromhex(str(row["wire_hex"]))
            values = decode_policy_vector(vector)
            normalized = normalize_complete_state(values)
        except (ValueError, ComponentContractError) as exc:
            raise PrefixPreparationError(f"profile {name} is not exact-grid canonical") from exc
        identity = _state_identity(normalized)
        if row.get("policy_state_content_sha256") != identity["policy_state_content_sha256"]:
            raise PrefixPreparationError(f"profile {name} state-content claim differs from its wire bytes")
        profiles[name] = normalized
        public_rows[name] = {
            "policy_state_content_sha256": identity["policy_state_content_sha256"],
            "wire_hex": vector.hex(),
        }
    return profiles, public_rows


def _transition_cases(
    *,
    case_prefix: str,
    transition_kind: str,
    start_label: str,
    start: Mapping[str, bool | float],
    target_label: str,
    target: Mapping[str, bool | float],
    order: Sequence[str],
    complete_bundle: bool,
) -> list[dict[str, object]]:
    fields = list(order) if complete_bundle else [field for field in order if start[field] != target[field]]
    state = dict(start)
    cases: list[dict[str, object]] = []
    for prefix_length in range(len(fields) + 1):
        if prefix_length:
            field = fields[prefix_length - 1]
            state[field] = target[field]
        cases.append(
            {
                "applied_fields": fields[:prefix_length],
                "case_id": f"{case_prefix}/prefix-{prefix_length:02d}",
                "command_fields": fields,
                "expected_state_identity": _state_identity(state),
                "pending_fields": fields[prefix_length:],
                "prefix_length": prefix_length,
                "result_slots": {"compiled_esphome": None, "hardware_in_loop": None},
                "start_label": start_label,
                "target_label": target_label,
                "transition_kind": transition_kind,
            }
        )
    return cases


def build_preparation_packet(
    *,
    profile_artifact: Mapping[str, object],
    profile_artifact_sha256: str,
    consumer_manifest: Mapping[str, object],
    consumer_manifest_sha256: str,
    generated_main_cpp: bytes,
    firmware_binary: bytes,
    source_revision: str,
    firmware_revision: str,
    grid_revision: str,
    current_states: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """Return the canonical replay plan without making a qualification claim."""
    if _GIT_SHA.fullmatch(source_revision) is None:
        raise PrefixPreparationError("source revision must be a full lowercase Git SHA")
    if _GRID_REVISION.fullmatch(grid_revision) is None:
        raise PrefixPreparationError("grid revision must be a live evidence-addressed revision")
    if not isinstance(firmware_revision, str) or not firmware_revision or len(firmware_revision) > 200:
        raise PrefixPreparationError("firmware revision must be bounded nonempty text")
    for value, label in (
        (profile_artifact_sha256, "profile artifact"),
        (consumer_manifest_sha256, "consumer manifest"),
    ):
        if _SHA256.fullmatch(value) is None:
            raise PrefixPreparationError(f"{label} digest must be lowercase SHA-256")

    profiles, public_profiles = _profiles(profile_artifact)
    compiled = extract_compiled_defaults(generated_main_cpp, consumer_manifest)
    starts: dict[str, dict[str, object]] = {
        "compiled-defaults": {
            "identity": _state_identity(compiled),
            "kind": "compiled_defaults",
            "values": compiled,
        }
    }
    parsed_current: dict[str, dict[str, bool | float]] = {}
    for label in sorted(current_states):
        artifact = current_states[label]
        if set(artifact) != {"device_id", "firmware_revision", "grid_revision", "observed_at", "schema", "values"}:
            raise PrefixPreparationError(f"current state {label} has unexpected or missing fields")
        if artifact.get("schema") != CURRENT_STATE_SCHEMA:
            raise PrefixPreparationError(f"current state {label} schema mismatch")
        if artifact.get("grid_revision") != grid_revision or artifact.get("firmware_revision") != firmware_revision:
            raise PrefixPreparationError(f"current state {label} differs from the qualified grid/firmware")
        values = _finite_raw_state(artifact["values"], f"current state {label}")
        parsed_current[label] = values
        starts[label] = {
            "device_id": artifact["device_id"],
            "firmware_revision": artifact["firmware_revision"],
            "grid_revision": artifact["grid_revision"],
            "identity": _state_identity(values),
            "kind": "observed_current",
            "observed_at": artifact["observed_at"],
            "values": values,
        }

    cases: list[dict[str, object]] = []
    for target_name in ("moderate", "aggressive"):
        cases.extend(
            _transition_cases(
                case_prefix=f"activation/baseline-to-{target_name}",
                transition_kind="activation",
                start_label="baseline",
                start=profiles["baseline"],
                target_label=target_name,
                target=profiles[target_name],
                order=ACTIVATION_ORDER,
                complete_bundle=False,
            )
        )
        cases.extend(
            _transition_cases(
                case_prefix=f"rollback/{target_name}-to-baseline",
                transition_kind="rollback",
                start_label=target_name,
                start=profiles[target_name],
                target_label="baseline",
                target=profiles["baseline"],
                order=ROLLBACK_ORDER,
                complete_bundle=False,
            )
        )
    for start_label, values in (("compiled-defaults", compiled), *sorted(parsed_current.items())):
        cases.extend(
            _transition_cases(
                case_prefix=f"recovery/{start_label}-to-baseline",
                transition_kind="recovery",
                start_label=start_label,
                start=values,
                target_label="baseline",
                target=profiles["baseline"],
                order=RECOVERY_ORDER,
                complete_bundle=True,
            )
        )

    qualification_blockers = [
        "compiled_esphome_result_missing_for_every_case",
        "hardware_in_loop_result_missing_for_every_case",
        "qualified_evidence_manifest_not_reviewed",
    ]
    if not parsed_current:
        qualification_blockers.insert(0, "observed_current_state_missing")

    return {
        "artifacts": {
            "consumer_manifest_sha256": consumer_manifest_sha256,
            "firmware_binary_sha256": _sha256(firmware_binary),
            "generated_main_cpp_sha256": _sha256(generated_main_cpp),
            "profile_artifact_sha256": profile_artifact_sha256,
        },
        "cases": cases,
        "firmware_revision": firmware_revision,
        "grid_revision": grid_revision,
        "orders": {
            "activation": list(ACTIVATION_ORDER),
            "recovery": list(RECOVERY_ORDER),
            "rollback": list(ROLLBACK_ORDER),
        },
        "profiles": public_profiles,
        "qualification_blockers": qualification_blockers,
        "required_result_contract": {
            "added_safety_events": 0,
            "compiled_esphome": "pass",
            "hardware_in_loop": "pass",
            "result_evidence_sha256_required": True,
        },
        "schema": PACKET_SCHEMA,
        "source_revision": source_revision,
        "starts": starts,
        "status": STATUS,
        "summary": {
            "activation_cases": sum(case["transition_kind"] == "activation" for case in cases),
            "case_count": len(cases),
            "current_start_count": len(parsed_current),
            "recovery_cases": sum(case["transition_kind"] == "recovery" for case in cases),
            "rollback_cases": sum(case["transition_kind"] == "rollback" for case in cases),
        },
    }


def _current_arg(value: str) -> tuple[str, Path]:
    label, separator, raw_path = value.partition("=")
    if not separator or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", label):
        raise argparse.ArgumentTypeError("current state must be LABEL=PATH with a bounded lowercase label")
    return label, Path(raw_path)


def write_preparation_output(raw: bytes, output_path: Path) -> None:
    """Publish one complete sensitive packet without overwrite or partial name.

    Current-state inputs contain a complete 48-field operational snapshot. The
    final path therefore appears atomically at mode 0600 and an existing file,
    directory, FIFO or symlink is always refused. A private temporary inode in
    the same directory is hard-linked into place only after its bytes and mode
    are durable; no field value is printed.
    """
    if not isinstance(raw, bytes) or not raw:
        raise PrefixPreparationError("output packet must be nonempty bytes")
    unresolved = output_path.absolute()
    parent = unresolved.parent
    if parent.exists():
        if parent.is_symlink() or not parent.is_dir():
            raise PrefixPreparationError("output parent must be a real directory")
    else:
        try:
            parent.mkdir(parents=True, mode=0o700)
        except OSError as exc:
            raise PrefixPreparationError("output parent could not be created privately") from exc
    if unresolved.exists() or unresolved.is_symlink():
        raise PrefixPreparationError("output target already exists; overwrite refused")

    descriptor: int | None = None
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{unresolved.name}.", dir=parent)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, unresolved, follow_symlinks=False)
        except FileExistsError as exc:
            raise PrefixPreparationError("output target appeared concurrently; overwrite refused") from exc
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except PrefixPreparationError:
        raise
    except OSError as exc:
        raise PrefixPreparationError("output packet could not be published safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--consumer-manifest", type=Path, required=True)
    parser.add_argument("--generated-main", type=Path, required=True)
    parser.add_argument("--firmware-binary", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--firmware-revision", required=True)
    parser.add_argument("--grid-revision", required=True)
    parser.add_argument("--current-state", action="append", default=[], type=_current_arg)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        profile_raw = _read_bytes(args.profiles, "profile artifact", 4 * 1024 * 1024)
        manifest_raw = _read_bytes(args.consumer_manifest, "consumer manifest", 4 * 1024 * 1024)
        generated_main = _read_bytes(args.generated_main, "generated main.cpp", 16 * 1024 * 1024)
        firmware_binary = _read_bytes(args.firmware_binary, "firmware binary", 32 * 1024 * 1024)
        current: dict[str, dict[str, object]] = {}
        for label, path in args.current_state:
            if label in current:
                raise PrefixPreparationError(f"duplicate current-state label {label}")
            current[label] = _json(
                _read_bytes(path, f"current state {label}", 256 * 1024),
                f"current state {label}",
            )
        packet = build_preparation_packet(
            profile_artifact=_json(profile_raw, "profile artifact"),
            profile_artifact_sha256=_sha256(profile_raw),
            consumer_manifest=_json(manifest_raw, "consumer manifest"),
            consumer_manifest_sha256=_sha256(manifest_raw),
            generated_main_cpp=generated_main,
            firmware_binary=firmware_binary,
            source_revision=args.source_revision,
            firmware_revision=args.firmware_revision,
            grid_revision=args.grid_revision,
            current_states=current,
        )
        output = canonical_json_bytes(packet)
        write_preparation_output(output, args.output)
    except PrefixPreparationError as exc:
        parser.exit(2, f"prefix preparation refused: {exc}\n")
    print(f"status={STATUS} cases={len(packet['cases'])} packet_sha256={_sha256(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
