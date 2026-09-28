"""One-shot, in-process cfg drift proof for the sole Verdify device writer.

An atomic approval names one 0.1 F extension of fan2 cooling exit hysteresis. The dispatcher
persists each phase before using its existing lifecycle and ESPHome queue. A
missing or uncertain receipt stops the probe; it never opens another client.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import shared

from . import bounded_reconcile as bounded

PARAMETER = "cool_stage2_exit_hysteresis_f"
STEP = 0.1
MIN_BASELINE = 0.3
MAX_BASELINE = 2.9
PREVIEW_NAME = "writer-drift-preview.json"
APPROVAL_NAME = "writer-drift-approval.json"
STATE_NAME = "writer-drift-state.json"
MAX_PREVIEW_AGE = timedelta(minutes=6)
MAX_APPROVAL_AGE = timedelta(minutes=10)
CONFIRM_DEADLINE = timedelta(minutes=4)


@dataclass(frozen=True)
class Decision:
    action: str  # ordinary | hold | send
    changes: tuple[tuple[str, float], ...] = ()
    reason: str = ""
    run_id: str | None = None
    phase: str = ""


def approval_file_stamp(state_dir: Path) -> tuple[int, int, int] | None:
    try:
        stat = (state_dir / APPROVAL_NAME).stat()
    except FileNotFoundError:
        return None
    return stat.st_ino, stat.st_mtime_ns, stat.st_size


def _safe_baseline(value: float) -> bool:
    return (
        math.isfinite(value)
        and MIN_BASELINE <= value <= MAX_BASELINE
        and math.isclose(value * 10, round(value * 10), rel_tol=0, abs_tol=1e-5)
    )


async def _preview(conn, planned: list, generation: int, desired: float) -> dict:
    readbacks = await bounded._fresh_readbacks(conn, generation)
    plan_rows, earliest = await bounded._plan_identity(conn, planned)
    row = next((item for item in plan_rows if item["parameter"] == PARAMETER), None)
    if row is None or not _safe_baseline(desired) or not bounded._equal(row["value"], desired):
        raise ValueError("probe field lacks a stable, valid active plan value")
    if PARAMETER not in readbacks:
        raise ValueError("probe cfg readback unavailable")
    identity = {
        "session_id": bounded.SESSION_ID,
        "pod": os.environ.get("HOSTNAME", ""),
        "source_revision": os.environ.get("VERDIFY_GIT_SHA", "unknown"),
        "generation": generation,
        "plan_rows": plan_rows,
        "desired": desired,
        "parameter": PARAMETER,
        "probe_value": round(desired + STEP, 1),
        "readbacks": {
            key: readbacks[key] for key in sorted({PARAMETER} | {field.name for field in bounded.wire_fields()})
        },
    }
    return {
        "version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "earliest_plan_expiry": earliest,
        "fingerprint": bounded._digest(identity),
        **identity,
    }


def _validate_identity(preview: dict, approved: dict) -> None:
    for key in (
        "session_id",
        "pod",
        "source_revision",
        "generation",
        "plan_rows",
        "desired",
        "parameter",
        "probe_value",
    ):
        if preview[key] != approved[key]:
            raise ValueError(f"probe identity changed: {key}")
    if datetime.now(UTC) + timedelta(minutes=1) >= bounded._time(approved["earliest_plan_expiry"]):
        raise ValueError("probe plan expiry too near")
    for param, original in approved["readbacks"].items():
        if param == PARAMETER:
            continue
        if not bounded._equal(preview["readbacks"].get(param), original):
            raise ValueError(f"unapproved cfg readback changed: {param}")


async def _confirmed(conn, state: dict, preview: dict, phase: str) -> bool:
    if datetime.now(UTC) - bounded._time(state["phase_started_at"]) > CONFIRM_DEADLINE:
        raise ValueError("probe confirmation deadline exceeded")
    record = state.get(f"{phase}_record")
    if record is None:
        raise ValueError("probe delivery receipt missing")
    row = await conn.fetchrow(
        "SELECT delivery_status, confirmed_at FROM setpoint_changes WHERE ts = $1 AND parameter = $2",
        bounded._time(record["requested_at"]),
        PARAMETER,
    )
    if row is None or row["delivery_status"] in {"failed", "cancelled", "superseded", "expired"}:
        raise ValueError("probe delivery failed")
    if row["delivery_status"] != "confirmed" or row["confirmed_at"] is None:
        bounded._wake_for_confirmation()
        return False
    from .dispatcher import readback_values_equivalent

    if not readback_values_equivalent(PARAMETER, preview["readbacks"].get(PARAMETER), record["value"]):
        bounded._wake_for_confirmation()
        return False
    return True


async def choose(
    conn,
    changes: list[tuple[str, float]],
    planned: list,
    desired: float | None,
    generation: int,
    reconnect_pending: bool,
    state_dir: Path,
) -> Decision:
    """Choose at most one command for the existing dispatcher, or fail closed."""
    approval_path = state_dir / APPROVAL_NAME
    state_path = state_dir / STATE_NAME
    try:
        state = bounded._read(state_path)
    except Exception as error:
        return Decision("hold", reason=f"probe state unreadable: {error}")
    if state and state.get("status") == "complete":
        return Decision("ordinary")
    if state and state.get("status") == "halted":
        return Decision("hold", reason="probe halted; inspect receipt and cfg")
    try:
        approval = bounded._read(approval_path)
    except Exception as error:
        if state is None:
            return Decision("ordinary", reason=f"unstarted probe approval unreadable: {error}")
        return Decision("hold", reason=f"probe approval unreadable: {error}")
    if approval is None and state is None and (reconnect_pending or desired is None or changes):
        return Decision("ordinary")
    if approval is None and state is None:
        try:
            preview = await _preview(conn, planned, generation, float(desired))
            if not bounded._equal(preview["readbacks"].get(PARAMETER), desired):
                return Decision("ordinary")
            bounded._write(state_dir / PREVIEW_NAME, preview)
        except Exception:
            pass  # A passive preview must never hold the normal writer.
        return Decision("ordinary")
    if state is None and changes:
        # An approval alone has not reserved the writer. Let ordinary desired
        # work run, and revalidate the entire approval if the candidate clears.
        return Decision("ordinary", reason="ordinary candidate is nonempty before probe")

    try:
        if approval is None:
            raise ValueError("probe approval removed")
        if reconnect_pending or not shared.transport_readbacks_ready(generation):
            raise ValueError("probe connection generation not reconciled")
        if desired is None:
            raise ValueError("probe desired plan value unavailable")
        if shared.esp32.get("client") is None or not shared.writer_lease_strictly_held(minimum_remaining_s=3):
            raise ValueError("probe sole-writer lease or client unavailable")
        now = datetime.now(UTC)
        if state is None:
            if approval.get("version") != 1 or approval.get("parameter") != PARAMETER:
                raise ValueError("probe approval invalid")
            if approval.get("session_id") != bounded.SESSION_ID or approval.get("generation") != generation:
                raise ValueError("probe approval bound to another writer")
            if not approval.get("run_id") or not isinstance(approval["run_id"], str):
                raise ValueError("probe run ID missing")
            approved_capture = bounded._time(approval["captured_at"])
            if now - approved_capture > MAX_PREVIEW_AGE or approved_capture > now + timedelta(seconds=15):
                raise ValueError("probe approval preview stale")
            if (
                now >= bounded._time(approval["expires_at"])
                or bounded._time(approval["expires_at"]) > now + MAX_APPROVAL_AGE
            ):
                raise ValueError("probe approval expired or too long")
        if state is not None:
            if state.get("run_id") != approval.get("run_id"):
                raise ValueError("probe approval changed")
            if state.get("approval_fingerprint") != approval.get("fingerprint") or state.get(
                "approval_expires_at"
            ) != approval.get("expires_at"):
                raise ValueError("probe approval replaced during run")
            if state["status"] in {"probe_inflight", "restore_inflight"}:
                return Decision("hold", reason=f"probe {state['status']}; inspect receipt and cfg")
        try:
            preview = await _preview(conn, planned, generation, float(desired))
        except ValueError as error:
            # A cfg callback can precede the next complete 48-field DB batch.
            # Wait for the already bounded confirmation window, then halt.
            transient = (
                "canonical 48-field snapshot" in str(error)
                or "canonical readbacks are not one atomic batch" in str(error)
                or "snapshot does not match current connection" in str(error)
            )
            if (
                state is not None
                and state["status"] in {"probe_awaiting", "restore_awaiting"}
                and transient
                and now - bounded._time(state["phase_started_at"]) <= CONFIRM_DEADLINE
            ):
                bounded._wake_for_confirmation()
                return Decision("hold", reason="awaiting fresh atomic cfg batch")
            raise
        if state is None:
            if approval.get("fingerprint") != preview["fingerprint"]:
                raise ValueError("probe approval does not match fresh source and readbacks")
            if not bounded._equal(preview["readbacks"].get(PARAMETER), desired):
                raise ValueError("probe baseline cfg differs from desired")
            state = {
                "version": 1,
                "run_id": approval["run_id"],
                "approval_fingerprint": approval["fingerprint"],
                "approval_expires_at": approval["expires_at"],
                "approved_preview": preview,
                "status": "probe_inflight",
                "phase_started_at": now.isoformat(),
            }
            bounded._write(state_path, state)  # durable before request row or ESPHome call
            return Decision("send", ((PARAMETER, preview["probe_value"]),), run_id=state["run_id"], phase="probe")

        approved = state["approved_preview"]
        _validate_identity(preview, approved)
        if state["status"] == "probe_awaiting":
            if not await _confirmed(conn, state, preview, "probe"):
                return Decision("hold", reason="awaiting probe cfg confirmation")
            if now >= bounded._time(approval["expires_at"]):
                raise ValueError("probe approval expired before restoration")
            if changes != [(PARAMETER, approved["desired"])]:
                raise ValueError("restoration is not the sole desired candidate")
            state["status"] = "restore_inflight"
            state["phase_started_at"] = now.isoformat()
            bounded._write(state_path, state)
            return Decision("send", ((PARAMETER, approved["desired"]),), run_id=state["run_id"], phase="restore")
        if state["status"] == "restore_awaiting":
            if not await _confirmed(conn, state, preview, "restore"):
                return Decision("hold", reason="awaiting restored cfg confirmation")
            if changes:
                raise ValueError("desired candidate remains after restoration")
            state["status"] = "complete"
            bounded._write(state_path, state)
            return Decision("ordinary")
        raise ValueError("probe state invalid")
    except Exception as error:
        if state is None and not state_path.exists():
            # No phase was durably started, so this approval cannot hold the
            # normal writer. A later pass still needs a fresh exact match.
            return Decision("ordinary", reason=f"unstarted probe skipped: {error}")
        if state_path.exists():
            try:
                state = bounded._read(state_path)
                if state and state.get("status") != "complete":
                    state["status"] = "halted"
                    state["halt_reason"] = str(error)
                    bounded._write(state_path, state)
            except Exception:
                pass
        return Decision("hold", reason=str(error))


def finish(state_dir: Path, decision: Decision, records: list[dict], failures: list) -> None:
    """Fence a one-command phase after the ordinary writer has returned."""
    if decision.action != "send":
        return
    path = state_dir / STATE_NAME
    state = bounded._read(path)
    if state is None or state.get("run_id") != decision.run_id or state.get("status") != f"{decision.phase}_inflight":
        raise RuntimeError("probe phase state lost after physical dispatch")
    matching = [record for record in records if record["parameter"] == PARAMETER]
    if (
        failures
        or len(records) != 1
        or len(matching) != 1
        or not bounded._equal(matching[0]["value"], decision.changes[0][1])
    ):
        state["status"] = "halted"
        state["halt_reason"] = f"probe dispatch incomplete: {len(failures)} failures, {len(records)} records"
    else:
        state["status"] = f"{decision.phase}_awaiting"
        state[f"{decision.phase}_record"] = {
            "requested_at": matching[0]["requested_at"].isoformat(),
            "value": float(matching[0]["value"]),
        }
        bounded._wake_for_confirmation()
    bounded._write(path, state)
