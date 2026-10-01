"""Opt-in, single-writer staged reconciliation for a broad desired delta.

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
from verdify_schemas.setpoint import SetpointChange

SESSION_ID = uuid.uuid4().hex  # changes on process restart, even in the same pod
PREVIEW_NAME = "writer-stage-preview.json"
APPROVAL_NAME = "writer-stage-approval.json"
STATE_NAME = "writer-stage-state.json"
RECOVERY_NAME = "writer-stage-recovery.json"
MAX_STAGE_AGE = timedelta(minutes=6)
CONFIRM_DEADLINE = timedelta(minutes=8)
CONFIRM_RECHECK_SECONDS = 20
PLAN_SEND_MARGIN = timedelta(minutes=5)  # dispatcher task timeout for a 12-command batch
ZONE_VPD_TARGETS = frozenset({"vpd_target_south", "vpd_target_west", "vpd_target_east", "vpd_target_center"})
# Only these active-plan fields can change effective value when the live
# VPD-high moisture cap engages or releases. Every other fixed candidate stays
# bound to the approved value. New guardrail fields require an explicit review.
DYNAMIC_MOISTURE_GUARDRAIL_PARAMS = frozenset(
    {
        "fog_escalation_kpa",
        "min_fog_off_s",
        "mister_all_delay_s",
        "mister_all_kpa",
        "mister_engage_delay_s",
        "mister_engage_kpa",
        "mister_pulse_gap_s",
    }
)


class SnapshotBehindReadbacks(ValueError):
    """The atomic DB snapshot has not caught up to this connection's cfg values."""


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


def _archive_completed_run(state_dir: Path, state: dict, approval: dict | None) -> None:
    """Retain a completed receipt before allowing a new broad preview in this pod."""
    run_id = uuid.UUID(str(state["run_id"])).hex
    path = state_dir / f"writer-stage-completed-{run_id}.json"
    archived = _read(path)
    if archived is None:
        if approval is None or approval.get("run_id") != state["run_id"]:
            raise ValueError("completed stage approval missing or changed")
        if approval.get("fingerprint") != state["approved_preview"]["fingerprint"]:
            raise ValueError("completed stage approval fingerprint changed")
        archived = {"schema": "verdify-writer-stage-completed-v1", "approval": approval, "state": state}
        _write(path, archived)
    if archived.get("schema") != "verdify-writer-stage-completed-v1" or archived.get("state") != state:
        raise ValueError("completed stage archive differs from active receipt")
    if approval is not None and archived.get("approval") != approval:
        raise ValueError("completed stage archive differs from active approval")
    (state_dir / APPROVAL_NAME).unlink(missing_ok=True)
    (state_dir / STATE_NAME).unlink()


def _equal(actual: float, expected: float) -> bool:
    # Snapshot/readback values are copied from the same ESPHome callbacks.
    return math.isclose(float(actual), float(expected), rel_tol=0, abs_tol=1e-5)


def _wake_for_confirmation() -> None:
    # Existing scheduler/event path; no new ESPHome client or extra worker.
    asyncio.get_running_loop().call_later(CONFIRM_RECHECK_SECONDS, shared.setpoint_dispatch_requested.set)


def approval_file_stamp(state_dir: Path) -> tuple[int, int, int] | None:
    """Observe atomic approval replacement without reading its contents."""
    try:
        stat = (state_dir / APPROVAL_NAME).stat()
    except FileNotFoundError:
        return None
    return stat.st_ino, stat.st_mtime_ns, stat.st_size


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
        if param not in current:
            raise ValueError(f"current connection missing readback: {param}")
        if not _equal(current[param], row["value"]):
            raise SnapshotBehindReadbacks(f"snapshot does not match current connection: {param}")
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


async def _preview(conn, changes, planned, generation: int, baseline_names: set[str] | None = None) -> dict:
    all_readbacks = await _fresh_readbacks(conn, generation)
    plan_rows, earliest = await _plan_identity(conn, planned)
    ordered = [[str(param), float(value)] for param, value in changes]
    if len(ordered) != len({param for param, _value in ordered}):
        raise ValueError("candidate contains duplicate parameters")
    required = (
        {field.name for field in wire_fields()} | {param for param, _value in ordered} | (baseline_names or set())
    )
    if any(param not in all_readbacks for param in required):
        raise ValueError("candidate contains a field without current-generation readback")
    # Bind all 48 canonical fields and every candidate, including controls
    # outside the canonical vector. Keep other live diagnostics for audit only.
    readbacks = {param: all_readbacks[param] for param in sorted(required)}
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
        "diagnostic_readbacks": all_readbacks,
        **identity,
    }


def _validated_residual(
    preview: dict,
    state: dict,
    zone_vpd_targets: dict[str, float] | None,
    limit: int,
    guardrail_values: dict[str, float] | None = None,
) -> list[tuple[str, float]]:
    """Bind fixed values; rebase only crop VPD and proven live moisture caps."""
    approved = state["approved_preview"]
    completed = set(state["completed"])
    approved_changes = dict(approved["changes"])
    current_changes = dict(preview["changes"])
    approved_plan_params = {row["parameter"] for row in approved["plan_rows"]}
    dynamic_guardrails = (
        DYNAMIC_MOISTURE_GUARDRAIL_PARAMS & approved_plan_params & approved_changes.keys()
        if guardrail_values is not None
        else frozenset()
    )
    approved_fixed = [
        [param, value]
        for param, value in approved["changes"]
        if param not in ZONE_VPD_TARGETS and param not in dynamic_guardrails and param not in completed
    ]
    from .dispatcher import readback_values_equivalent

    current_fixed = []
    for param, value in preview["changes"]:
        if param in ZONE_VPD_TARGETS:
            continue
        if param in dynamic_guardrails:
            continue
        if param in completed:
            if (
                param not in state.get("completed_values", {})
                or float(state["completed_values"][param]) != float(approved_changes[param])
                or float(value) != float(approved_changes[param])
                or not readback_values_equivalent(param, preview["readbacks"].get(param), value)
            ):
                raise ValueError(f"completed fixed candidate drifted: {param}")
            continue
        current_fixed.append([param, value])
    if current_fixed != approved_fixed:
        raise ValueError("desired fixed candidate changed or completed field reappeared")
    if any(param not in approved_changes for param in current_changes):
        raise ValueError("unapproved candidate appeared")
    # The dispatcher supplies these values after applying the current live
    # guardrail to the unchanged active plan. A missing candidate is valid only
    # when the current-generation readback already matches that value.
    dynamic_residual = []
    for param, _approved_value in approved["changes"]:
        if param not in dynamic_guardrails:
            continue
        if guardrail_values is None or param not in guardrail_values:
            raise ValueError(f"live guardrail source unavailable: {param}")
        effective = float(guardrail_values[param])
        observed = preview["readbacks"].get(param)
        current = current_changes.get(param)
        if current is not None and float(current) != effective:
            raise ValueError(f"live guardrail candidate changed outside source: {param}")
        if current is None and not readback_values_equivalent(param, observed, effective):
            raise ValueError(f"omitted live guardrail candidate lacks equivalent readback: {param}")
        if current is not None and not readback_values_equivalent(param, observed, effective):
            dynamic_residual.append((param, effective))
    for param, value in current_changes.items():
        if param not in ZONE_VPD_TARGETS:
            continue
        if param in completed and (approved_fixed or len(current_changes) > limit):
            raise ValueError(f"completed zone VPD target exceeds ordinary handoff: {param}")
        if zone_vpd_targets is None or param not in zone_vpd_targets or not _equal(value, zone_vpd_targets[param]):
            raise ValueError(f"zone VPD source changed or unavailable: {param}")
    for param in (approved_changes.keys() & ZONE_VPD_TARGETS) - current_changes.keys():
        # Dispatcher omits a target only when its current cfg readback is
        # equivalent to the fresh crop target. Prove that before skipping it.
        if (
            zone_vpd_targets is None
            or param not in zone_vpd_targets
            or not readback_values_equivalent(param, preview["readbacks"].get(param), zone_vpd_targets[param])
        ):
            raise ValueError(f"omitted zone VPD target lacks equivalent readback: {param}")
    # Fixed fields retain their approved ordering. VPD is delivered last so
    # solar-time changes during the earlier stages do not stale its request.
    return [
        *(tuple(item) for item in approved_fixed),
        *dynamic_residual,
        *(
            (param, current_changes[param])
            for param, _ in approved["changes"]
            if param in ZONE_VPD_TARGETS and param not in completed and param in current_changes
        ),
    ]


def _validate_state(
    preview: dict,
    state: dict,
    now: datetime,
    zone_vpd_targets: dict[str, float] | None,
    limit: int,
    guardrail_values: dict[str, float] | None = None,
) -> list[tuple[str, float]]:
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
    completed_values = state.get("completed_values", {})
    for param, baseline in approved["readbacks"].items():
        expected = completed_values.get(param, desired.get(param)) if param in completed else baseline
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
    return _validated_residual(preview, state, zone_vpd_targets, limit, guardrail_values)


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


async def _expire_recovered_confirmation_window(
    conn, preview: dict, state: dict, recovery: dict, rows, state_dir: Path
):
    """Close only a recovered stage's elapsed confirmation window, never infer delivery."""
    from ._common import HEAP_DEFER_FREE_KB, HEAP_DEFER_LARGEST_BLOCK_KB
    from .dispatcher import readback_values_equivalent

    now = datetime.now(UTC)
    deadline = _time(state["stage_started_at"]) + CONFIRM_DEADLINE
    original = state["approved_preview"]
    if state["status"] != "rollback_complete" or now <= deadline:
        raise ValueError("recovered confirmation window not closed")
    custody = _read(state_dir / "writer-stage-recovery-custody.json")
    if custody is None or custody.get("schema") != "verdify-recovered-confirmation-custody-v1":
        raise ValueError("recovered confirmation custody missing")
    if (
        custody.get("run_id") != state["run_id"]
        or custody.get("source_revision") != original["source_revision"]
        or custody.get("recovery_session_id") != recovery["session_id"]
        or custody.get("recovery_generation") != recovery["generation"]
        or now - _time(preview["captured_at"]) > MAX_STAGE_AGE
        or preview["session_id"] != SESSION_ID
    ):
        raise ValueError("recovered confirmation custody changed")
    for name, expected in custody["retained_sha256"].items():
        if name not in {STATE_NAME, APPROVAL_NAME, RECOVERY_NAME}:
            raise ValueError("invalid recovered custody artifact")
        if hashlib.sha256((state_dir / name).read_bytes()).hexdigest() != expected:
            raise ValueError("retained recovered custody bytes changed")
    if set(custody["retained_sha256"]) != {STATE_NAME, APPROVAL_NAME, RECOVERY_NAME}:
        raise ValueError("retained recovered custody incomplete")
    proof_raw = custody["terminal_proof_raw"]
    if hashlib.sha256(proof_raw.encode()).hexdigest() != custody["terminal_proof_sha256"]:
        raise ValueError("recovered terminal proof changed")
    proof = json.loads(proof_raw)
    if (
        proof["run_id"] != state["run_id"]
        or proof["status"] != "rollback_complete"
        or proof["source"] != original["source_revision"]
        or proof["session"] != recovery["session_id"]
        or proof["generation"] != recovery["generation"]
        or proof["diagnostics"]["firmware_version"] != custody["firmware_version"]
        or {r["parameter"] for r in proof["stage12_baseline"]} != set(state["stage_parameters"])
        or {r["parameter"] for r in proof["first12_preserved"]} != set(state["completed_values"])
        or not all(r["matches"] for r in proof["stage12_baseline"] + proof["first12_preserved"])
    ):
        raise ValueError("invalid recovered terminal proof")

    def serialized(rs):
        return [{k: v.isoformat() if isinstance(v, datetime) else v for k, v in dict(r).items()} for r in rs]

    if serialized(rows) != custody["original_requests"]:
        raise ValueError("recovered original requests changed")
    current = await _fresh_readbacks(conn, preview["generation"])
    if any(
        not readback_values_equivalent(p, current.get(p), original["readbacks"][p]) for p in state["stage_parameters"]
    ):
        raise ValueError("recovered confirmation baseline changed")
    if any(
        not readback_values_equivalent(p, current.get(p), value)
        for p, value in state.get("completed_values", {}).items()
    ):
        raise ValueError("earlier completed recovered stage changed")
    if not shared.writer_lease_strictly_held(minimum_remaining_s=3):
        raise ValueError("recovered confirmation lease not held")
    pending = [r for r in rows if r["delivery_status"] == "sent" and r["confirmed_at"] is None]
    for row in pending:
        SetpointChange.model_validate({**dict(row), "source": row["source"], "delivery_status": "expired"})
    if not pending or any(
        r["delivery_status"]
        not in {"sent", "deferred_heap_pressure", "confirmed", "failed", "cancelled", "superseded", "expired"}
        or (r["delivery_status"] == "confirmed" and r["confirmed_at"] is None)
        for r in rows
    ):
        raise ValueError("recovered confirmation outcome is not an elapsed sent window")
    if any(r["ts"] + CONFIRM_DEADLINE >= now for r in pending):
        raise ValueError("request confirmation window still open")
    if not state.get("rollback_records"):
        raise ValueError("real rollback confirmation evidence missing")

    async with conn.transaction():
        heap = await conn.fetchrow(
            "SELECT heap_bytes, heap_largest_free_block_kb, ts, firmware_version FROM diagnostics ORDER BY ts DESC LIMIT 1"
        )
        if (
            heap is None
            or heap["ts"] < now - timedelta(seconds=120)
            or heap["heap_bytes"] is None
            or heap["heap_bytes"] < HEAP_DEFER_FREE_KB
            or heap["heap_largest_free_block_kb"] is None
            or heap["heap_largest_free_block_kb"] < HEAP_DEFER_LARGEST_BLOCK_KB
        ):
            raise ValueError("recovered confirmation numeric safety guard")
        if heap["firmware_version"] != custody["firmware_version"]:
            raise ValueError("recovered firmware changed")
        for record in state["rollback_records"]:
            row = await conn.fetchrow(
                "SELECT ts, parameter, value, source, delivery_status, confirmed_at FROM v_runtime_setpoint_changes_write WHERE ts = $1 AND parameter = $2 FOR UPDATE",
                _time(record["requested_at"]),
                record["parameter"],
            )
            if row is None or row["delivery_status"] != "confirmed" or row["confirmed_at"] is None:
                raise ValueError("real rollback confirmation changed")
            if serialized([row])[0] not in custody["rollback_confirmations"]:
                raise ValueError("recovered rollback request custody changed")
        locked = await conn.fetch(
            "SELECT ts, parameter, value, source, delivery_status, confirmed_at, expired_at FROM v_runtime_setpoint_changes_write "
            "WHERE parameter = ANY($1::text[]) AND ts >= $2 AND ts <= $3 "
            "AND COALESCE(source, '') <> 'esp32' ORDER BY ts, parameter FOR UPDATE",
            state["stage_parameters"],
            _time(state["stage_started_at"]),
            _time(recovery["approved_at"]),
        )
        if [dict(r) for r in locked] != [dict(r) for r in rows]:
            raise ValueError("original recovered request history changed")
        later = await conn.fetch(
            "SELECT delivery_status, confirmed_at FROM v_runtime_setpoint_changes_write "
            "WHERE parameter = ANY($1::text[]) AND ts > $2 AND COALESCE(source, '') <> 'esp32' FOR UPDATE",
            state["stage_parameters"],
            _time(recovery["approved_at"]),
        )
        if any(
            r["delivery_status"]
            not in {"deferred_heap_pressure", "confirmed", "failed", "cancelled", "superseded", "expired"}
            or (r["delivery_status"] == "confirmed" and r["confirmed_at"] is None)
            for r in later
        ):
            raise ValueError("later recovered request remains unknown")
        # Retain original sent/null-confirmation truth before any lifecycle update.
        path = state_dir / f"writer-stage-expired-window-{uuid.UUID(str(state['run_id'])).hex}.json"
        evidence = {
            "custody": custody,
            "schema": "verdify-recovered-confirmation-window-v1",
            "run_id": state["run_id"],
            "confirmation_deadline_at": deadline.isoformat(),
            "source_revision": original["source_revision"],
            "state": state,
            "recovery": recovery,
            "original_requests": [
                {k: v.isoformat() if isinstance(v, datetime) else v for k, v in dict(r).items()} for r in rows
            ],
            "meaning": "confirmation window elapsed; physical dispatch outcome remains unknown; transition must be read back from DB",
        }
        existing = _read(path)
        if existing is not None and existing != evidence:
            raise ValueError("recovered confirmation expiry evidence changed")
        if existing is None:
            _write(path, evidence)
            directory_fd = os.open(state_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        _write(
            state_dir / f"writer-stage-expiry-attempt-{uuid.uuid4().hex}.json",
            {
                "schema": "verdify-recovered-expiry-attempt-v1",
                "run_id": state["run_id"],
                "source_revision": preview["source_revision"],
                "session_id": preview["session_id"],
                "generation": preview["generation"],
                "attempted_at": now.isoformat(),
                "prior_evidence_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "meaning": "attempt only, not a committed DB outcome",
            },
        )
        # Validate every prospective update before the first DML; keep the
        # persisted original source rather than the model's default source.
        for row in pending:
            SetpointChange.model_validate({**dict(row), "source": row["source"], "delivery_status": "expired"})
        for row in pending:
            changed = await conn.fetchrow(
                "UPDATE v_runtime_setpoint_changes_write SET delivery_status = 'expired', expired_at = clock_timestamp() "
                "WHERE ts = $1 AND parameter = $2 AND value = $3 AND delivery_status = 'sent' "
                "AND confirmed_at IS NULL AND expired_at IS NULL "
                "RETURNING ts, parameter, value, source, delivery_status, confirmed_at, expired_at",
                row["ts"],
                row["parameter"],
                row["value"],
            )
            if changed is None:
                raise ValueError("recovered confirmation expiry compare-and-set failed")
            validated = SetpointChange.model_validate(dict(changed))
            if (
                validated.delivery_status != "expired"
                or validated.confirmed_at is not None
                or validated.expired_at is None
            ):
                raise ValueError("recovered confirmation expiry returned invalid outcome")
    # Read actual committed transition times; a failed readback is retried by
    # the existing archive path without re-expiring or rewriting prior facts.
    committed = await conn.fetch(
        "SELECT ts, parameter, value, source, delivery_status, confirmed_at, expired_at FROM setpoint_changes "
        "WHERE parameter = ANY($1::text[]) AND ts >= $2 AND ts <= $3 "
        "AND COALESCE(source, '') <> 'esp32' ORDER BY ts, parameter",
        state["stage_parameters"],
        _time(state["stage_started_at"]),
        _time(recovery["approved_at"]),
    )

    if len(committed) != len(rows) or {(r["ts"], r["parameter"]) for r in committed} != {
        (r["ts"], r["parameter"]) for r in rows
    }:
        raise ValueError("recovered expiry committed readback incomplete")
    closed = {(r["ts"], r["parameter"]) for r in pending}
    if any(
        (r["ts"], r["parameter"]) in closed
        and (r["delivery_status"] != "expired" or r["confirmed_at"] is not None or r["expired_at"] is None)
        for r in committed
    ):
        raise ValueError("recovered expiry committed outcome changed")
    return committed


async def _settled_recovery_archive(conn, preview: dict, state: dict, approval: dict | None, state_dir: Path) -> bool:
    """Retain a stopped run and admit only a new independently guarded approval."""
    from .dispatcher import readback_values_equivalent

    parameters = state.get("stage_parameters", [])
    if not parameters or len(parameters) > 12:
        raise ValueError("settled recovery lacks bounded last stage")
    baseline = state["approved_preview"]["readbacks"]
    if any(not readback_values_equivalent(p, preview["readbacks"].get(p), baseline[p]) for p in parameters):
        raise ValueError("settled last-stage baseline changed")
    recovery = _read(state_dir / RECOVERY_NAME)
    if recovery is None or recovery.get("source_run_id") != state["run_id"] or recovery.get("parameters") != parameters:
        raise ValueError("settled recovery evidence missing")
    rows = await conn.fetch(
        "SELECT ts, parameter, value, source, delivery_status, confirmed_at, expired_at FROM setpoint_changes "
        "WHERE parameter = ANY($1::text[]) AND ts >= $2 AND ts <= $3 "
        "AND COALESCE(source, '') <> 'esp32' ORDER BY ts, parameter",
        parameters,
        _time(state["stage_started_at"]),
        _time(recovery["approved_at"]),
    )
    known = {"deferred_heap_pressure", "confirmed", "failed", "cancelled", "superseded", "expired"}
    if len(rows) != len(parameters) or set(r["parameter"] for r in rows) != set(parameters):
        raise ValueError("last-stage durable request evidence incomplete")
    if any(r["delivery_status"] not in known for r in rows):
        rows = await _expire_recovered_confirmation_window(conn, preview, state, recovery, rows, state_dir)
    if any(
        r["delivery_status"] not in known or (r["delivery_status"] == "confirmed" and r["confirmed_at"] is None)
        for r in rows
    ):
        raise ValueError("last-stage outcome remains unknown")
    outstanding = await conn.fetch(
        "SELECT delivery_status, confirmed_at FROM setpoint_changes "
        "WHERE parameter = ANY($1::text[]) AND ts > $2 AND COALESCE(source, '') <> 'esp32'",
        parameters,
        _time(recovery["approved_at"]),
    )
    if any(
        r["delivery_status"] not in known or (r["delivery_status"] == "confirmed" and r["confirmed_at"] is None)
        for r in outstanding
    ):
        raise ValueError("later last-stage request outcome remains unknown")
    for record in state.get("rollback_records", []):
        row = await conn.fetchrow(
            "SELECT delivery_status, confirmed_at FROM setpoint_changes WHERE ts = $1 AND parameter = $2",
            _time(record["requested_at"]),
            record["parameter"],
        )
        if row is None or row["delivery_status"] != "confirmed" or row["confirmed_at"] is None:
            raise ValueError("settled rollback confirmation changed")
    archive_path = state_dir / f"writer-stage-recovered-{uuid.UUID(str(state['run_id'])).hex}.json"
    archive = _read(archive_path)
    expiry_evidence = _read(state_dir / f"writer-stage-expired-window-{uuid.UUID(str(state['run_id'])).hex}.json")
    v1 = "verdify-writer-stage-recovered-v1"
    v2 = "verdify-writer-stage-recovered-v2"

    def request_receipt(version):
        # Preserve the exact historical v1 projection; source/expired_at were
        # not present in that immutable archive contract.
        fields = (
            ("ts", "parameter", "value", "delivery_status", "confirmed_at")
            if version == v1
            else ("ts", "parameter", "value", "source", "delivery_status", "confirmed_at", "expired_at")
        )
        return [{k: r[k].isoformat() if isinstance(r[k], datetime) else r[k] for k in fields if k in r} for r in rows]

    if archive is None:
        if approval is None or approval.get("run_id") != state["run_id"]:
            raise ValueError("original stopped approval missing before archive")
        version = v2 if expiry_evidence is not None else v1
        archive = {
            "schema": version,
            "state": state,
            "approval": approval,
            "recovery": recovery,
            "settled_baseline": {p: preview["readbacks"][p] for p in parameters},
            "known_requests": request_receipt(version),
        }
        if version == v2:
            archive["confirmation_window_evidence"] = expiry_evidence
        _write(archive_path, archive)
    if (
        archive.get("schema") not in {v1, v2}
        or archive.get("state") != state
        or archive.get("recovery") != recovery
        or (approval is not None and approval.get("run_id") == state["run_id"] and archive.get("approval") != approval)
        or archive.get("known_requests") != request_receipt(archive.get("schema"))
        or (archive.get("schema") == v2 and archive.get("confirmation_window_evidence") != expiry_evidence)
    ):
        raise ValueError("settled history changed")
    return approval is not None and approval.get("run_id") != state["run_id"]


async def choose_stage(
    conn,
    changes,
    planned,
    generation: int,
    state_dir: Path,
    limit: int,
    zone_vpd_targets: dict[str, float] | None = None,
    guardrail_values: dict[str, float] | None = None,
) -> Decision:
    """Write a read-only preview; select at most one durable stage if approved."""
    approval_path = state_dir / APPROVAL_NAME
    state_path = state_dir / STATE_NAME
    recovered_admission = False
    try:
        approval = _read(approval_path)
        state = _read(state_path)
        prior = state.get("approved_preview") if state else approval.get("approved_preview") if approval else None
        baseline_names = set(prior["readbacks"]) if prior else set()
        try:
            preview = await _preview(conn, changes, planned, generation, baseline_names)
        except SnapshotBehindReadbacks as error:
            # The 48-field DB batch is written on a slower cadence than live
            # cfg callbacks. A just-sent stage can be confirmed before its
            # next atomic snapshot arrives. Keep only that pending stage
            # durable and let the existing bounded recheck wait for the batch.
            if state is None or state.get("status") != "awaiting_confirmation":
                raise
            approved = state["approved_preview"]
            if approval is None or state["run_id"] != approval.get("run_id"):
                raise ValueError("approval changed during run") from error
            if (
                approved["session_id"] != SESSION_ID
                or approved["pod"] != os.environ.get("HOSTNAME", "")
                or approved["source_revision"] != os.environ.get("VERDIFY_GIT_SHA", "unknown")
                or approved["generation"] != generation
            ):
                raise ValueError("writer identity changed") from error
            plan_rows, _expiry = await _plan_identity(conn, planned)
            if plan_rows != approved["plan_rows"]:
                raise ValueError("effective plan or one-shot changed") from error
            now = datetime.now(UTC)
            if now - _time(state["stage_started_at"]) > CONFIRM_DEADLINE:
                raise ValueError("stage confirmation deadline exceeded") from error
            if now >= _time(state["expires_at"]) or now + PLAN_SEND_MARGIN >= _time(approved["earliest_plan_expiry"]):
                raise ValueError("stage approval or effective plan expired") from error
            _wake_for_confirmation()
            return Decision("hold", reason=f"awaiting fresh atomic snapshot: {error}", run_id=state["run_id"])
        _write(state_dir / PREVIEW_NAME, preview)
        if state is not None and state.get("status") == "complete" and len(changes) > limit:
            # A later broad desired delta needs its own fingerprint and
            # approval. Archive both completed receipts before clearing the
            # active names; a crash between unlinks resumes from the archive.
            _archive_completed_run(state_dir, state, approval)
            return Decision("ordinary")
        if state is not None and state.get("status") == "rollback_complete":
            if not await _settled_recovery_archive(conn, preview, state, approval, state_dir):
                return Decision("hold", reason="settled rollback archived; fresh approval required")
            # Keep the terminal state on disk until the new approval validates.
            # Its history is already immutable; no deferred DB row is replayed.
            state = None
            recovered_admission = True
        if approval is None and state is None:
            return Decision("ordinary")
        if approval is None:
            raise ValueError("approval removed during run")
        now = datetime.now(UTC)
        if state is None:
            approved_preview = approval.get("approved_preview", preview)
            identity = {
                key: approved_preview[key]
                for key in ("session_id", "pod", "source_revision", "generation", "changes", "plan_rows", "readbacks")
            }
            if (
                approval.get("version") != 1
                or approval.get("fingerprint") != approved_preview["fingerprint"]
                or _digest(identity) != approved_preview["fingerprint"]
            ):
                raise ValueError("approval does not match captured candidate/baseline")
            if "approved_preview" not in approval and approval["fingerprint"] != preview["fingerprint"]:
                raise ValueError("approval does not match fresh candidate/baseline")
            if approval.get("session_id") != SESSION_ID or approval.get("generation") != generation:
                raise ValueError("approval bound to another writer generation")
            if not approval.get("run_id") or not isinstance(approval["run_id"], str):
                raise ValueError("approval run ID missing")
            if now - _time(approved_preview["captured_at"]) > MAX_STAGE_AGE:
                raise ValueError("preview stale")
            if now >= _time(approval["expires_at"]):
                raise ValueError("approval expired")
            if _time(approval["expires_at"]) > now + timedelta(minutes=30):
                raise ValueError("approval duration exceeds 30 minutes")
            state = {
                "version": 1,
                "run_id": approval["run_id"],
                "expires_at": approval["expires_at"],
                "approved_preview": approved_preview,
                "completed": [],
                "completed_values": {},
                "status": "ready",
            }
            _validate_state(preview, state, now, zone_vpd_targets, limit, guardrail_values)
            _write(state_path, state)
            if recovered_admission:
                (state_dir / RECOVERY_NAME).unlink()  # Exact old authority retained in recovered archive.
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
            if (
                state["approved_preview"]["session_id"] != SESSION_ID
                or state["approved_preview"]["generation"] != generation
            ):
                return Decision("ordinary")
            from .dispatcher import readback_values_equivalent

            completed_fixed = {
                param: value
                for param, value in state.get("completed_values", {}).items()
                if param not in ZONE_VPD_TARGETS
            }
            remaining = tuple(
                (param, value)
                for param, value in preview["changes"]
                if param not in completed_fixed
                or float(value) != float(completed_fixed[param])
                or not readback_values_equivalent(param, preview["readbacks"].get(param), value)
            )
            return Decision("complete", changes=remaining, run_id=state["run_id"])
        if state["status"] == "awaiting_confirmation":
            records = state["records"]
            if not await _check_records(conn, records, preview["readbacks"], state["stage_started_at"]):
                return Decision("hold", reason="awaiting durable confirmation", run_id=state["run_id"])
            state["completed"].extend(record["parameter"] for record in records)
            state.setdefault("completed_values", {}).update(
                {record["parameter"]: record["value"] for record in records}
            )
            state["records"] = []
            state["status"] = "ready"
            _write(state_path, state)
        residual = _validate_state(preview, state, now, zone_vpd_targets, limit, guardrail_values)
        if shared.esp32.get("client") is None:
            raise ValueError("sole ESPHome client unavailable")
        if not shared.writer_lease_strictly_held(minimum_remaining_s=3):
            raise ValueError("sole-writer lease not strictly held")
        if not residual:
            # A fresh <=limit crop VPD delta may remain after the last stage.
            # Returning complete lets dispatcher send it through its ordinary
            # lifecycle and global cap in this same sole-writer pass.
            state["status"] = "complete"
            _write(state_path, state)
            handoff = tuple((param, value) for param, value in preview["changes"] if param in ZONE_VPD_TARGETS)
            return Decision("complete", changes=handoff, run_id=state["run_id"])
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


def finish_stage(
    state_dir: Path,
    decision: Decision,
    records: list[dict],
    failures: list,
    *,
    deferred_records: list[dict] | None = None,
    api_dispatch_attempts: int | None = None,
) -> None:
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
    deferred = deferred_records or []
    if (
        expected
        and not decision.rollback
        and not failures
        and not records
        and api_dispatch_attempts == 0
        and [(r["parameter"], float(r["value"])) for r in deferred] == expected
        and all(
            r.get("delivery_status") == "deferred_heap_pressure" and isinstance(r.get("requested_at"), datetime)
            for r in deferred
        )
    ):
        state.setdefault("deferred_stages", []).append(
            {
                "stage_started_at": state["stage_started_at"],
                "records": [{**r, "requested_at": r["requested_at"].isoformat()} for r in deferred],
                "api_dispatch_attempts": 0,
            }
        )
        state["status"] = "ready"
        state["defer_reason"] = "entire bounded stage durably deferred for heap pressure; no API attempt"
    elif failures or actual != expected or deferred:
        state["status"] = "rollback_failed" if decision.rollback else "halted"
        state["halt_reason"] = (
            f"stage dispatch incomplete: {len(failures)} failures, {len(actual)}/{len(expected)} records"
        )
        # A partial transport send is still a real request. Retain its exact
        # lifecycle keys without treating it as confirmed or allowing replay.
        key = "rollback_records" if decision.rollback else "records"
        state[key] = [
            {
                "parameter": record["parameter"],
                "value": float(record["value"]),
                "requested_at": record["requested_at"].isoformat(),
            }
            for record in records
        ]
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
