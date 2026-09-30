"""Scoped C1 grid qualification through the existing sole-writer lifecycle.

This is a temporary explicit projection of the current ordinary base policy.
It has no ESPHome client, setter, replay, or generic override input.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import shared

from verdify_schemas.c1_grid_projection import project_c1_grid_state
from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER
from verdify_schemas.tunable_registry import REGISTRY

from . import bounded_reconcile

WORKSHEET_NAME = "c1-qualification-worksheet.json"
STATE_NAME = "c1-qualification-state.json"
PREVIEW_NAME = "c1-qualification-preview.json"
MAX_AUTHORITY_AGE = timedelta(minutes=6)


@dataclass(frozen=True)
class Selection:
    phase: str = "ordinary"
    changes: tuple[tuple[str, float], ...] = ()
    worksheet_id: str | None = None
    reason: str = ""
    override_fields: tuple[str, ...] = ()


def ordinary_base48(planner_params, mister_defaults):
    """Materialize the same resolved policy; retained operator fields stay defaults."""
    values = {name: REGISTRY[name].default for name in CANONICAL_FIELD_ORDER}
    for source in (mister_defaults, planner_params):
        values.update({name: value for name, value in source.items() if name in values})
    return {name: bool(value) if REGISTRY[name].kind == "switch" else float(value) for name, value in values.items()}


def validate_worksheet(worksheet, preview, *, now, physics, guardrails):
    if worksheet.get("schema") != "verdify-c1-qualification-worksheet-v1":
        raise ValueError("invalid C1 worksheet schema")
    if str(UUID(worksheet["worksheet_id"])) != worksheet["worksheet_id"]:
        raise ValueError("invalid C1 worksheet identity")
    expires = bounded_reconcile._time(worksheet["expires_at"])
    captured = bounded_reconcile._time(worksheet["preview"]["captured_at"])
    if not captured <= now < expires <= captured + MAX_AUTHORITY_AGE:
        raise ValueError("C1 worksheet expired or duration invalid")
    expected = worksheet["preview"]
    for name in ("identity", "base_values", "base_inputs_sha256"):
        if expected[name] != preview[name]:
            raise ValueError("C1 base policy or identity changed: " + name)
    if bounded_reconcile._digest(expected["base_inputs"]) != expected["base_inputs_sha256"]:
        raise ValueError("C1 base input provenance mismatch")
    projection = project_c1_grid_state(preview["base_values"], worksheet["decisions"])
    if projection != worksheet["projection"]:
        raise ValueError("C1 immutable decisions/projection mismatch")
    if projection["grid_revision"] != preview["identity"]["grid_revision"]:
        raise ValueError("C1 actual device grid changed")
    decisions = worksheet["decisions"]
    if not 1 <= len(decisions) <= 12:
        raise ValueError("C1 decision bundle exceeds existing bound")
    # Validate after projection; never silently round or change an explicit
    # choice to fit physics, registry or current moisture guardrails.
    for param, value in projection["proposed_values"].items():
        applied, violation = physics(param, float(value))
        if violation is not None or not bounded_reconcile._equal(applied, value):
            raise ValueError("C1 choice contradicts physics: " + param)
        if param in guardrails and float(value) > float(guardrails[param]):
            raise ValueError("C1 choice contradicts moisture guardrail: " + param)
    return projection


def _upsert(changes, targets, readbacks):
    # Restoration targets are first, so unrelated ordinary drift cannot starve
    # an expired worksheet's return to freshly resolved source policy.
    selected = dict(targets)
    selected.update({p: v for p, v in changes if p not in targets})
    for param, value in targets.items():
        if param not in readbacks:
            raise ValueError("C1 target lacks current-generation cfg route: " + param)
        if bounded_reconcile._equal(readbacks[param], value):
            selected.pop(param, None)
        else:
            selected[param] = float(value)
    return tuple(selected.items())[:12]


async def choose(conn, changes, *, base_values, base_inputs, guardrails, physics, state_dir, generation):
    from .component_experiment import RUNTIME_INSTANCE_ID, component_entity_grid_attestation

    state_path = state_dir / STATE_NAME
    try:
        state = bounded_reconcile._read(state_path)
    except (ValueError, OSError):
        return Selection("hold", reason="existing C1 outcome history unavailable")
    try:
        worksheet = bounded_reconcile._read(state_dir / WORKSHEET_NAME)
    except (ValueError, OSError):
        if state is None:
            return Selection(reason="unadmitted C1 worksheet malformed")
        worksheet = None  # Retained admitted history owns fresh restoration.
    admitted = state is not None and state.get("status") != "yielded"
    now = datetime.now(UTC)
    evidence = component_entity_grid_attestation()
    if evidence is None or not shared.transport_readbacks_ready(generation):
        return Selection("hold" if admitted else "ordinary", reason="current grid/readback evidence unavailable")
    readbacks = shared.current_cfg_readbacks(generation)
    if not set(CANONICAL_FIELD_ORDER).issubset(readbacks):
        return Selection("hold" if admitted else "ordinary", reason="current complete 48 cfg readbacks unavailable")
    # Source inputs include Iris, crop anchor/lighting sources, activity and
    # safety defaults. Ordinary solar curves continue resolving from those
    # same inputs; no cached target is substituted into the five policy layers.
    source_inputs = json.loads(json.dumps(base_inputs, sort_keys=True, default=str))
    preview = {
        "schema": "verdify-c1-qualification-preview-v1",
        "captured_at": now.isoformat(),
        "identity": {
            "source_revision": os.environ.get("VERDIFY_GIT_SHA"),
            "pod": os.environ.get("HOSTNAME"),
            "runtime_instance_id": RUNTIME_INSTANCE_ID,
            "writer_session_id": bounded_reconcile.SESSION_ID,
            "connection_generation": generation,
            "firmware_revision": evidence.firmware_revision,
            "grid_revision": evidence.grid_revision,
        },
        "base_values": base_values,
        "base_inputs": source_inputs,
        "base_inputs_sha256": bounded_reconcile._digest(source_inputs),
        "readbacks": {name: readbacks[name] for name in CANONICAL_FIELD_ORDER},
        "base_converged": all(bounded_reconcile._equal(readbacks[n], base_values[n]) for n in CANONICAL_FIELD_ORDER),
        "qualification_claimed": False,
    }
    bounded_reconcile._write(state_dir / PREVIEW_NAME, preview)
    old = state
    if old and old.get("status") == "yielded" and worksheet and old["worksheet_id"] != worksheet.get("worksheet_id"):
        # Completed history stays archived before a distinct worksheet starts.
        bounded_reconcile._write(state_dir / ("c1-qualification-history-" + str(old["worksheet_id"]) + ".json"), old)
        old = None
    if worksheet is None and old is None:
        return Selection()
    worksheet = worksheet or {}
    if old and old.get("status") == "yielded" and worksheet.get("worksheet_id") == old["worksheet_id"]:
        return Selection(reason="worksheet authority ended and ordinary restoration confirmed")
    # Retain the exact original worksheet with its native delivery records.
    # File replacement or deletion removes authority; it cannot erase history.
    if old and old.get("status") != "yielded":
        if worksheet.get("worksheet_id") != old["worksheet_id"]:
            worksheet = old["worksheet"]
            reason = "worksheet replaced or removed"
        elif worksheet != old["worksheet"]:
            worksheet = old["worksheet"]
            reason = "immutable worksheet changed"
        else:
            reason = ""
    else:
        reason = ""
    projection = None
    try:
        if reason:
            raise ValueError(reason)
        projection = validate_worksheet(worksheet, preview, now=now, physics=physics, guardrails=guardrails)
        if not shared.writer_lease_strictly_held(minimum_remaining_s=3):
            raise ValueError("sole writer lease unavailable")
        if not preview["identity"]["source_revision"] or not preview["identity"]["pod"]:
            raise ValueError("runtime source identity unavailable")
        if old is None and now - bounded_reconcile._time(worksheet["preview"]["captured_at"]) > timedelta(seconds=60):
            raise ValueError("fresh admission preview exceeds 60seconds")
        if old is None and (not preview["base_converged"] or changes):
            raise ValueError("fresh ordinary baseline is not converged")
        if old and old["status"] in ("inflight", "restore_inflight"):
            return Selection("hold", reason="native outcome uncertain; no stale retry")
        if old and old["status"] in ("halted", "yielding", "yielded", "restore_awaiting"):
            raise ValueError(old.get("reason", "prior worksheet authority ended"))
        if old and old["status"] == "awaiting_confirmation":
            if not await bounded_reconcile._check_records(conn, old["records"], readbacks, old["stage_started_at"]):
                bounded_reconcile._wake_for_confirmation()
                return Selection("awaiting_confirmation", worksheet_id=worksheet["worksheet_id"])
        targets = {name: projection["proposed_values"][name] for name in worksheet["decisions"]}
        selected = _upsert(changes, targets, readbacks)
        if not selected:
            bounded_reconcile._write(
                state_path,
                {
                    **(old or {}),
                    "worksheet": worksheet,
                    "worksheet_id": worksheet["worksheet_id"],
                    "status": "active",
                    "validated_at": now.isoformat(),
                    "qualification_claimed": False,
                },
            )
            bounded_reconcile._wake_for_confirmation()
            return Selection("active", worksheet_id=worksheet["worksheet_id"])
        bounded_reconcile._write(
            state_path,
            {
                **(old or {}),
                "worksheet": worksheet,
                "worksheet_id": worksheet["worksheet_id"],
                "status": "inflight",
                "touched": sorted(set((old or {}).get("touched", [])) | set(worksheet["decisions"])),
                "stage_started_at": now.isoformat(),
                "selected": list(selected),
                "qualification_claimed": False,
            },
        )
        bounded_reconcile._wake_for_confirmation()
        return Selection("send", selected, worksheet["worksheet_id"], override_fields=tuple(worksheet["decisions"]))
    except (ValueError, KeyError, TypeError) as error:
        reason = str(error)
    if old is None:
        # Invalid/unadmitted files grant neither overlay nor batching authority.
        return Selection(reason=reason)
    # The stale/expired worksheet has no further authority. Force fresh ordinary
    # source targets for its touched canonical fields even if the ordinary
    # delta comparator would consider the tiny C1 grid displacement equivalent.
    if old and old["status"] in ("inflight", "restore_inflight"):
        return Selection("hold", reason="native outcome uncertain; no stale retry")
    if old and old["status"] == "restore_awaiting":
        try:
            if not await bounded_reconcile._check_records(conn, old["records"], readbacks, old["stage_started_at"]):
                return Selection("hold", reason="awaiting fresh ordinary restoration confirmation")
        except ValueError as error:
            bounded_reconcile._write(state_path, {**old, "status": "restore_failed", "reason": str(error)})
            return Selection("hold", reason="fresh restoration failed; retain outcomes")
    if old and old["status"] == "restore_failed":
        return Selection("hold", reason="fresh restoration failed; retain outcomes")
    touched = set(old.get("touched", [])) if old else set()
    # An unadmitted worksheet has touched no hardware and grants no restoration
    # authority. Never send arbitrary fields from a malformed file.
    targets = {name: base_values[name] for name in sorted(touched)}
    selected = _upsert(changes, targets, readbacks)
    if not shared.writer_lease_strictly_held(minimum_remaining_s=3):
        return Selection("hold", reason="sole writer lease unavailable")
    bounded_reconcile._write(
        state_path,
        {
            **(old or {}),
            "worksheet": worksheet,
            "worksheet_id": worksheet.get("worksheet_id"),
            "status": "restore_inflight" if selected else "yielded",
            "reason": reason,
            "stage_started_at": now.isoformat(),
            "touched": sorted(touched),
            "qualification_claimed": False,
        },
    )
    if selected:
        bounded_reconcile._wake_for_confirmation()
    return Selection("yield", selected, worksheet.get("worksheet_id"), reason)


def finish(state_dir, selection, records, failures):
    if selection.phase not in ("send", "yield") or not selection.changes:
        return
    path = state_dir / STATE_NAME
    state = bounded_reconcile._read(path)
    if (
        state is None
        or state["worksheet_id"] != selection.worksheet_id
        or state["status"] not in ("inflight", "restore_inflight")
    ):
        raise RuntimeError("C1 state changed during native dispatch")
    actual = [(r["parameter"], float(r["value"])) for r in records]
    expected = list(selection.changes)
    restoring = selection.phase == "yield"
    failed = bool(failures or actual != expected)
    state["status"] = (
        ("restore_failed" if failed else "restore_awaiting")
        if restoring
        else ("halted" if failed else "awaiting_confirmation")
    )
    state["records"] = [
        {"parameter": r["parameter"], "value": float(r["value"]), "requested_at": r["requested_at"].isoformat()}
        for r in records
    ]
    state.setdefault("stages", []).append(
        {
            "phase": selection.phase,
            "records": state["records"],
            "failures": list(failures),
            "selected": list(expected),
            "started_at": state["stage_started_at"],
        }
    )
    state["qualification_claimed"] = False
    state["failure_count"] = len(failures)
    bounded_reconcile._write(path, state)
    bounded_reconcile._wake_for_confirmation()


def file_stamp(state_dir):
    try:
        stat = (state_dir / WORKSHEET_NAME).stat()
    except FileNotFoundError:
        return None
    return stat.st_ino, stat.st_mtime_ns, stat.st_size


def poll(state_dir):
    """Wake the existing dispatcher; do not create another device worker."""
    try:
        state = bounded_reconcile._read(state_dir / STATE_NAME)
        worksheet = bounded_reconcile._read(state_dir / WORKSHEET_NAME)
    except (ValueError, OSError):
        # Dispatcher logs malformed authority; it cannot kill the scheduler.
        shared.setpoint_dispatch_requested.set()
        return
    new_worksheet = worksheet and (state is None or worksheet.get("worksheet_id") != state.get("worksheet_id"))
    if new_worksheet or (state and state.get("status") not in ("yielded", "restore_failed")):
        shared.setpoint_dispatch_requested.set()


def owns_bounded_stage(state_dir):
    """An admitted worksheet may restore via the same bound after broad drift."""
    state = bounded_reconcile._read(state_dir / STATE_NAME)
    ordinary = bounded_reconcile._read(state_dir / bounded_reconcile.STATE_NAME)
    return bool(
        state
        and state.get("status") not in ("yielded", "restore_failed")
        and (ordinary is None or ordinary.get("status") == "complete")
    )


def physical_fence(state_dir, selection):
    """Recheck immutable worksheet/expiry at the existing physical chokepoint."""
    if selection.phase != "send":
        return None
    state = bounded_reconcile._read(state_dir / STATE_NAME)
    worksheet = bounded_reconcile._read(state_dir / WORKSHEET_NAME)
    if state is None or worksheet != state.get("worksheet") or state.get("worksheet_id") != selection.worksheet_id:
        return "c1_worksheet_changed"
    if datetime.now(UTC) >= bounded_reconcile._time(worksheet["expires_at"]):
        return "c1_worksheet_expired"
    identity = worksheet["preview"]["identity"]
    from .component_experiment import component_entity_grid_attestation

    evidence = component_entity_grid_attestation()
    if (
        evidence is None
        or evidence.firmware_revision != identity["firmware_revision"]
        or evidence.grid_revision != identity["grid_revision"]
        or not shared.writer_lease_strictly_held(minimum_remaining_s=3)
    ):
        return "c1_firmware_grid_or_lease_changed"
    if (
        identity["source_revision"] != os.environ.get("VERDIFY_GIT_SHA")
        or identity["pod"] != os.environ.get("HOSTNAME")
        or identity["writer_session_id"] != bounded_reconcile.SESSION_ID
        or identity["connection_generation"] != shared.transport_generation
    ):
        return "c1_runtime_identity_changed"
    return None
