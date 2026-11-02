"""Shared sole-writer helpers kept after plain convergence (VD-62).

Ordinary dispatch no longer stages: the dispatcher converges any desired delta
in paced passes of at most 12 commands, with readback, and never waits for an
approval file or halted-run custody. Staging survives only in the C1
qualification overlay (``c1_overlay``) and its restore to the ordinary base,
which keep their own worksheet and state files. This module holds the helpers
that ``dispatcher``, ``c1_overlay``, ``c1_capture`` and ``drift_probe`` still
use, plus the start-up archive of halted-run files that nothing reads any more.
No second ESPHome client is constructed here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import shared

from verdify_schemas.policy_vector import wire_fields

SESSION_ID = uuid.uuid4().hex  # changes on process restart, even in the same pod
CONFIRM_DEADLINE = timedelta(minutes=8)
CONFIRM_RECHECK_SECONDS = 20
# Only these active-plan fields can change effective value when the live
# VPD-high moisture cap engages or releases. The C1 overlay lets an admitted
# decision survive exactly these named caps moving; nothing else is rebased.
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
# Halted-run files of the retired ordinary staging (#433/#949). Nothing reads
# or writes them after VD-62; start-up renames them into ARCHIVE_DIR_NAME.
ARCHIVE_DIR_NAME = "archived"
STAGE_FILE_GLOB = "writer-stage-*.json"
POLICY_HOLD_NAME = "writer-policy-hold.json"
_observation_log = logging.getLogger(__name__)


class SnapshotBehindReadbacks(ValueError):
    """The atomic DB snapshot has not caught up to this connection's cfg values."""


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
    if not shared.transport_readbacks_ready(generation):
        raise ValueError("current-generation replay changed during snapshot read")
    current = shared.current_cfg_readbacks(generation)
    if len(rows) != len(canonical) or set(canonical) != {row["parameter"] for row in rows}:
        raise ValueError("canonical 48-field snapshot incomplete or stale")
    if len({row["ts"] for row in rows}) != 1:
        raise ValueError("canonical readbacks are not one atomic batch")
    for row in rows:
        param = row["parameter"]
        if param not in current:
            raise ValueError(f"current connection missing readback: {param}")
        if not _equal(current[param], row["value"]):
            _observation_log.info(
                "setpoint_snapshot_compare status=mismatch parameter=%s generation=%d "
                "batch_at=%s durable_value=%r current_cfg_value=%r",
                param,
                generation,
                row["ts"].isoformat(),
                row["value"],
                current[param],
            )
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


def _new_archive_dir(state_dir: Path, now: datetime) -> Path:
    """Create a fresh ``archived/<UTC yyyymmddThhmmssZ>/``; never reuse an existing one."""
    base = state_dir / ARCHIVE_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    for suffix in range(100):
        target = base / (stamp if suffix == 0 else f"{stamp}-{suffix}")
        try:
            target.mkdir()
        except FileExistsError:
            continue
        return target
    raise FileExistsError(f"no free archive directory for {stamp}")


def archive_halted_runs(state_dir: Path, *, now: datetime | None = None) -> list[Path]:
    """Rename every retired halted-run file into ``archived/<UTC stamp>/``.

    Called once at ingestor start-up, before the task loop. It moves every
    ``writer-stage-*.json`` file (state, approval, preview, recovery and their
    receipts) and ``writer-policy-hold.json``, which nothing reads or writes
    after VD-62, and the drift probe's ``writer-drift-state.json`` unless its
    status is ``complete`` (a halted probe, or one a crash left in flight),
    together with ``writer-drift-approval.json``. Files are renamed with
    ``os.rename`` into a new directory, so nothing is overwritten or deleted.
    A failure is logged and never stops the ingestor: no ordinary pass reads
    these files, and a retained drift state stops holding within its deadline.
    """
    from . import drift_probe  # drift_probe imports this module at load time

    try:
        names = sorted(path.name for path in state_dir.glob(STAGE_FILE_GLOB) if path.is_file())
        if (state_dir / POLICY_HOLD_NAME).is_file():
            names.append(POLICY_HOLD_NAME)
        drift_state = state_dir / drift_probe.STATE_NAME
        if drift_state.is_file():
            try:
                status = (_read(drift_state) or {}).get("status")
            except (OSError, ValueError):
                status = None  # unreadable is not provably complete
            if status != "complete":
                names.append(drift_probe.STATE_NAME)
                if (state_dir / drift_probe.APPROVAL_NAME).is_file():
                    names.append(drift_probe.APPROVAL_NAME)
        if not names:
            return []
        target = _new_archive_dir(state_dir, now or datetime.now(UTC))
    except OSError as error:
        _observation_log.error("halted_run_archive status=unavailable error=%s", type(error).__name__)
        return []
    moved: list[Path] = []
    relative = f"{ARCHIVE_DIR_NAME}/{target.name}"
    for name in names:
        try:
            os.rename(state_dir / name, target / name)
        except OSError as error:
            _observation_log.error(
                "halted_run_archive status=failed file=%s archive=%s error=%s", name, relative, type(error).__name__
            )
            continue
        _observation_log.info("halted_run_archive status=archived file=%s archive=%s", name, relative)
        moved.append(target / name)
    return moved
