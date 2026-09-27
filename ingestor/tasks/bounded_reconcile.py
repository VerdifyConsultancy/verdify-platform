"""Opt-in, single-writer staged reconciliation after a broad reconnect hold.

The ordinary 12-command cap remains the default. An operator may approve one
fresh preview from the *running* ingestor by atomically placing an approval file
in its state directory. A session/plan/readback change or uncertain lifecycle
halts the run. No second ESPHome client is constructed here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import shared

from verdify_schemas.policy_vector import wire_fields

SESSION_ID = uuid.uuid4().hex  # changes on process restart, even in the same pod
PREVIEW_NAME = "writer-stage-preview.json"
APPROVAL_NAME = "writer-stage-approval.json"
STATE_NAME = "writer-stage-state.json"
RECOVERY_NAME = "writer-stage-recovery.json"
MAX_STAGE_AGE = timedelta(minutes=6)
CONFIRM_DEADLINE = timedelta(minutes=8)
CONFIRM_RECHECK_SECONDS = 20
PLAN_SEND_MARGIN = timedelta(minutes=5)  # dispatcher task timeout for a 12-command batch


@dataclass(frozen=True)
class Decision:
    action: str  # ordinary | hold | send | complete
    changes: tuple[tuple[str, float], ...] = ()
    reason: str = ""
    run_id: str | None = None
    rollback: bool = False


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must have timezone")
    return parsed.astimezone(UTC)


def _read(path: Path) -> dict | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"invalid {path.name}")
    return value


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _equal(actual: float, expected: float) -> bool:
    # Snapshot/readback values are copied from the same ESPHome callbacks.
    return math.isclose(float(actual), float(expected), rel_tol=0, abs_tol=1e-5)


def _wake_for_confirmation() -> None:
    # Existing scheduler/event path; no new ESPHome client or extra worker.
    asyncio.get_running_loop().call_later(CONFIRM_RECHECK_SECONDS, shared.setpoint_dispatch_requested.set)


async def _fresh_readbacks(conn, generation: int) -> dict[str, float]:
    if not shared.transport_readbacks_ready(generation):
        raise ValueError("current-generation replay incomplete")
    current = shared.current_cfg_readbacks(generation)
    canonical = [field.name for field in wire_fields()]
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (parameter) parameter, value, ts
          FROM setpoint_snapshot
         WHERE parameter = ANY($1::text[]) AND zone IS NULL
           AND ts > now() - interval '120 seconds'
         ORDER BY parameter, ts DESC
        """,
        canonical,
    )
    if len(rows) != len(canonical) or set(canonical) != {row["parameter"] for row in rows}:
        raise ValueError("canonical 48-field snapshot incomplete or stale")
    if len({row["ts"] for row in rows}) != 1:
        raise ValueError("canonical readbacks are not one atomic batch")
    for row in rows:
        param = row["parameter"]
        if param not in current or not _equal(current[param], row["value"]):
            raise ValueError(f"snapshot does not match current connection: {param}")
    return current


async def _plan_identity(conn, planned: list) -> tuple[list[dict], str]:
    selected = sorted(
        (
            {
                "parameter": str(row["parameter"]),
                "value": float(row["value"]),
                "plan_id": str(row["plan_id"]),
                "ts": row["ts"].isoformat(),
            }
            for row in planned
        ),
        key=lambda row: row["parameter"],
    )
    ids = sorted({row["plan_id"] for row in selected})
    raw = await conn.fetch(
        "SELECT sp.parameter, sp.plan_id, sp.ts, "
        "LEAST(sp.expires_at, COALESCE(pj.expires_at, sp.expires_at)) AS expires_at "
        "FROM setpoint_plan sp LEFT JOIN plan_journal pj "
        "ON pj.plan_id = sp.plan_id AND pj.greenhouse_id = sp.greenhouse_id "
        "WHERE sp.plan_id = ANY($1::text[]) AND sp.expires_at > now()",
        ids,
    )
    expiry = {(str(row["parameter"]), str(row["plan_id"]), row["ts"].isoformat()): row["expires_at"] for row in raw}
    if len(selected) != len({row["parameter"] for row in selected}):
        raise ValueError("ambiguous effective plan")
    for row in selected:
        key = (row["parameter"], row["plan_id"], row["ts"])
        if key not in expiry:
            raise ValueError(f"effective plan expiry unavailable: {row['parameter']}")
        row["expires_at"] = expiry[key].isoformat()
    earliest = min((_time(row["expires_at"]) for row in selected), default=datetime.max.replace(tzinfo=UTC))
    return selected, earliest.isoformat()


async def _preview(conn, changes, planned, generation: int) -> dict:
    readbacks = await _fresh_readbacks(conn, generation)
    plan_rows, earliest = await _plan_identity(conn, planned)
    ordered = [[str(param), float(value)] for param, value in changes]
    if len(ordered) != len({param for param, _value in ordered}):
        raise ValueError("candidate contains duplicate parameters")
    if any(param not in readbacks for param, _value in ordered):
        raise ValueError("candidate contains a field without current-generation readback")
    identity = {
        "session_id": SESSION_ID,
        "pod": os.environ.get("HOSTNAME", ""),
        "source_revision": os.environ.get("VERDIFY_GIT_SHA", "unknown"),
        "generation": generation,
        "changes": ordered,
        "plan_rows": plan_rows,
        "readbacks": readbacks,
    }
    return {
        "version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "earliest_plan_expiry": earliest,
        "fingerprint": _digest(identity),
        **identity,
    }


def _validate_state(preview: dict, state: dict, now: datetime) -> None:
    approved = state["approved_preview"]
    if preview["session_id"] != approved["session_id"] or preview["pod"] != approved["pod"]:
        raise ValueError("writer session changed")
    if preview["source_revision"] != approved["source_revision"]:
        raise ValueError("writer source revision changed")
    if preview["generation"] != approved["generation"]:
        raise ValueError("connection generation changed")
    if preview["plan_rows"] != approved["plan_rows"]:
        raise ValueError("effective plan or one-shot changed")
    if now + PLAN_SEND_MARGIN >= _time(approved["earliest_plan_expiry"]):
        raise ValueError("effective plan expiry too near")
    if now >= _time(state["expires_at"]):
        raise ValueError("stage approval expired")
    completed = set(state["completed"])
    desired = dict(approved["changes"])
    for param, baseline in approved["readbacks"].items():
        expected = desired[param] if param in completed else baseline
        observed = preview["readbacks"].get(param)
        if observed is None:
            raise ValueError(f"readback disappeared: {param}")
        if param in completed:
            # The ESPHome Number values can quantize an accepted desired value.
            from .dispatcher import readback_values_equivalent

            if not readback_values_equivalent(param, observed, expected):
                raise ValueError(f"confirmed readback drifted: {param}")
        elif not _equal(observed, expected):
            raise ValueError(f"unapproved readback changed: {param}")
    residual = [[param, val] for param, val in approved["changes"] if param not in completed]
    if preview["changes"] != residual:
        raise ValueError("desired candidate changed or completed field reappeared")


async def _check_records(conn, records: list[dict], readbacks: dict, started_at: str) -> bool:
    if datetime.now(UTC) - _time(started_at) > CONFIRM_DEADLINE:
        raise ValueError("stage confirmation deadline exceeded")
    for record in records:
        row = await conn.fetchrow(
            "SELECT delivery_status, confirmed_at FROM setpoint_changes WHERE ts = $1 AND parameter = $2",
            _time(record["requested_at"]),
            record["parameter"],
        )
        if row is None or row["delivery_status"] in {"failed", "cancelled", "superseded", "expired"}:
            raise ValueError(f"stage delivery failed: {record['parameter']}")
        if row["delivery_status"] != "confirmed" or row["confirmed_at"] is None:
            _wake_for_confirmation()
            return False
        from .dispatcher import readback_values_equivalent

        observed = readbacks.get(record["parameter"])
        if observed is None or not readback_values_equivalent(record["parameter"], observed, record["value"]):
            raise ValueError(f"confirmed cfg readback mismatch: {record['parameter']}")
    return True


async def _rollback_decision(conn, preview: dict, state: dict, state_dir: Path, limit: int) -> Decision:
    if state["status"] == "rollback_complete":
        return Decision("hold", reason="rollback confirmed; run remains stopped")
    if state["status"] == "rollback_failed":
        return Decision("hold", reason="rollback failed; operator recovery required")
    if state["status"] == "rollback_inflight":
        return Decision("hold", reason="rollback outcome unknown; operator recovery required")
    if state["status"] == "rollback_awaiting":
        if not await _check_records(
            conn, state["rollback_records"], preview["readbacks"], state["rollback_started_at"]
        ):
            return Decision("hold", reason="awaiting rollback confirmation")
        state["status"] = "rollback_complete"
        _write(state_dir / STATE_NAME, state)
        return Decision("hold", reason="rollback confirmed; run remains stopped")
    recovery = _read(state_dir / RECOVERY_NAME)
    if recovery is None:
        return Decision("hold", reason="run halted; bounded rollback available")
    now = datetime.now(UTC)
    if recovery.get("version") != 1 or recovery.get("source_run_id") != state["run_id"]:
        raise ValueError("rollback does not bind halted run")
    if recovery.get("session_id") != SESSION_ID or recovery.get("generation") != preview["generation"]:
        raise ValueError("rollback writer session changed")
    if now >= _time(recovery["expires_at"]) or _time(recovery["expires_at"]) > now + timedelta(minutes=10):
        raise ValueError("rollback approval expired or exceeds 10 minutes")
    parameters = list(state.get("stage_parameters", []))
    if not parameters or len(parameters) > limit or recovery.get("parameters") != parameters:
        raise ValueError("rollback must cover exactly the last bounded stage")
    baseline = state["approved_preview"]["readbacks"]
    expected_fingerprint = _digest(
        {
            "source_run_id": state["run_id"],
            "session_id": SESSION_ID,
            "generation": preview["generation"],
            "parameters": parameters,
            "baseline": {param: baseline[param] for param in parameters},
            "observed": {param: preview["readbacks"][param] for param in parameters},
        }
    )
    if recovery.get("fingerprint") != expected_fingerprint:
        raise ValueError("rollback baseline or fresh readback changed")
    from .dispatcher import readback_values_equivalent

    selected = tuple(
        (param, float(baseline[param]))
        for param in parameters
        if not readback_values_equivalent(param, preview["readbacks"][param], baseline[param])
    )
    if not selected:
        state["status"] = "rollback_complete"
        _write(state_dir / STATE_NAME, state)
        return Decision("hold", reason="last stage already matches baseline")
    if shared.esp32.get("client") is None:
        raise ValueError("sole ESPHome client unavailable for rollback")
    if not shared.writer_lease_strictly_held(minimum_remaining_s=3):
        raise ValueError("sole-writer lease not strictly held for rollback")
    state["status"] = "rollback_inflight"
    state["rollback_started_at"] = now.isoformat()
    state["rollback_parameters"] = [param for param, _value in selected]
    _write(state_dir / STATE_NAME, state)
    return Decision("send", selected, run_id=state["run_id"], rollback=True)


async def choose_stage(conn, changes, planned, generation: int, state_dir: Path, limit: int) -> Decision:
    """Write a read-only preview; select at most one durable stage if approved."""
    approval_path = state_dir / APPROVAL_NAME
    state_path = state_dir / STATE_NAME
    try:
        preview = await _preview(conn, changes, planned, generation)
        _write(state_dir / PREVIEW_NAME, preview)
        approval = _read(approval_path)
        state = _read(state_path)
        if approval is None and state is None:
            return Decision("ordinary")
        if approval is None:
            raise ValueError("approval removed during run")
        now = datetime.now(UTC)
        if state is None:
            if approval.get("version") != 1 or approval.get("fingerprint") != preview["fingerprint"]:
                raise ValueError("approval does not match fresh candidate/baseline")
            if approval.get("session_id") != SESSION_ID or approval.get("generation") != generation:
                raise ValueError("approval bound to another writer generation")
            if not approval.get("run_id") or not isinstance(approval["run_id"], str):
                raise ValueError("approval run ID missing")
            if now - _time(preview["captured_at"]) > MAX_STAGE_AGE:
                raise ValueError("preview stale")
            if _time(approval["expires_at"]) > now + timedelta(minutes=30):
                raise ValueError("approval duration exceeds 30 minutes")
            state = {
                "version": 1,
                "run_id": approval["run_id"],
                "expires_at": approval["expires_at"],
                "approved_preview": preview,
                "completed": [],
                "status": "ready",
            }
            _write(state_path, state)
        if state["run_id"] != approval.get("run_id"):
            raise ValueError("approval changed during run")
        if state["status"] in {
            "halted",
            "rollback_inflight",
            "rollback_awaiting",
            "rollback_complete",
            "rollback_failed",
        }:
            return await _rollback_decision(conn, preview, state, state_dir, limit)
        if state["status"] == "inflight":
            raise ValueError("prior stage outcome unknown; operator recovery required")
        if state["status"] == "complete":
            return Decision("ordinary")
        if state["status"] == "awaiting_confirmation":
            records = state["records"]
            if not await _check_records(conn, records, preview["readbacks"], state["stage_started_at"]):
                return Decision("hold", reason="awaiting durable confirmation", run_id=state["run_id"])
            state["completed"].extend(record["parameter"] for record in records)
            state["records"] = []
            state["status"] = "ready"
            _write(state_path, state)
        _validate_state(preview, state, now)
        if shared.esp32.get("client") is None:
            raise ValueError("sole ESPHome client unavailable")
        if not shared.writer_lease_strictly_held(minimum_remaining_s=3):
            raise ValueError("sole-writer lease not strictly held")
        residual = [
            (param, val) for param, val in state["approved_preview"]["changes"] if param not in state["completed"]
        ]
        if not residual:
            state["status"] = "complete"
            _write(state_path, state)
            return Decision("complete", run_id=state["run_id"])
        selected = tuple(residual[:limit])
        if not selected or len(selected) > limit:
            raise ValueError("invalid stage size")
        state["status"] = "inflight"  # durable before any request row or physical call
        state["stage_started_at"] = now.isoformat()
        state["stage_parameters"] = [param for param, _value in selected]
        _write(state_path, state)
        return Decision("send", selected, run_id=state["run_id"])
    except Exception as error:
        # A malformed approval, changed source, or uncertain stage never falls
        # through to the ordinary dispatcher, which could send the full vector.
        # This also covers a failed read-only DB probe. Cancellation is a
        # BaseException and still propagates to the task owner.
        if state_path.exists():
            try:
                state = _read(state_path)
                if state and state.get("status") not in {"complete", "rollback_complete"}:
                    prior = state.get("status", "")
                    state["status"] = "rollback_failed" if prior.startswith("rollback_") else "halted"
                    state["halt_reason"] = str(error)
                    _write(state_path, state)
            except Exception:
                pass
        return Decision("hold", reason=str(error))


def finish_stage(state_dir: Path, decision: Decision, records: list[dict], failures: list) -> None:
    """Durably fence a stage outcome before the next dispatcher invocation."""
    if decision.action != "send":
        return
    path = state_dir / STATE_NAME
    state = _read(path)
    expected_status = "rollback_inflight" if decision.rollback else "inflight"
    if state is None or state.get("status") != expected_status or state.get("run_id") != decision.run_id:
        raise RuntimeError("stage state lost after physical dispatch")
    expected = list(decision.changes)
    actual = [(record["parameter"], float(record["value"])) for record in records]
    if failures or actual != expected:
        state["status"] = "rollback_failed" if decision.rollback else "halted"
        state["halt_reason"] = (
            f"stage dispatch incomplete: {len(failures)} failures, {len(actual)}/{len(expected)} records"
        )
    else:
        state["status"] = "rollback_awaiting" if decision.rollback else "awaiting_confirmation"
        key = "rollback_records" if decision.rollback else "records"
        state[key] = [
            {
                "parameter": record["parameter"],
                "value": float(record["value"]),
                "requested_at": record["requested_at"].isoformat(),
            }
            for record in records
        ]
    _write(path, state)
    if state["status"] in {"awaiting_confirmation", "rollback_awaiting"}:
        _wake_for_confirmation()
