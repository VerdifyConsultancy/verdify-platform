#!/usr/bin/env python3
"""Capture one truthful ``--current-state`` artifact for the prefix-replay tool.

``scripts/prepare_component_prefix_replay.py`` accepts one or more
``--current-state LABEL=PATH`` captures, each a JSON file matching
``CURRENT_STATE_SCHEMA = "verdify-component-current-state-v1"``:
``{schema, device_id, firmware_revision, grid_revision, observed_at, values}``
with ``values`` holding all 48 canonical policy-wire fields
(``verdify_schemas.component_executor.CANONICAL_FIELD_ORDER``). Until this
script, no tool produced that file. This one does, and it is read-only: it
opens exactly one short-lived database connection, sets the session to
``default_transaction_read_only``, and issues only ``SELECT`` statements. It
never opens an ESPHome connection, never calls a setter/service, and never
requires the confirmed-component experiment to hold physical admission —
capture works whether or not any experiment is armed.

Data-source decision (and why the alternatives were rejected)
---------------------------------------------------------------
The 48 field values must reflect the device's *actual current* configuration,
not a computed target. Three sources were considered:

* **The in-memory ``RawCfgSourceEpoch`` / ``component_cfg_source_epochs()``
  gate** (``ingestor/tasks/component_experiment.py``). This is the most
  precise per-field provenance available (it freezes an epoch only once all
  48 ESPHome callbacks advance together), but its only database persistence
  path — ``record_runtime_snapshot`` / ``fn_experiment_v2_record_runtime_
  snapshot`` — fires *only* while an experiment has an open physical exposure
  or a just-finished delivery bundle. ``verdify_schemas.component_executor.
  physical_execution_qualified()`` is unconditionally ``False`` today (GRID_
  REVISION/ORDER_REVISION are still provisional strings, not evidence-
  addressed), so nothing is ever persisted there right now — and even once
  qualified, requiring an open exposure to capture "current state" would make
  this tool depend on the very authority it exists to help qualify. Rejected:
  it cannot satisfy "must not require the experiment to be active", and nothing
  is available there pre-#641.
* **A new/second ESPHome or MCP connection to the device.** The ingestor is
  the sole authenticated ESPHome client (the PR #668 live-grid attestor
  deliberately reuses that *existing* connection rather than opening another
  one, for exactly this reason). A second process dialing the device directly
  would violate that single-writer/single-reader architecture. Rejected.
* **``setpoint_snapshot`` (chosen).** Every ``cfg_*`` readback sensor report
  from the ESP32 lands in ``ingestor.py:_record_cfg_readback``, which does two
  things with the *same* observed value: it calls ``record_component_cfg_
  readback()`` (the gated in-memory L3 path above) *and* it stages the value
  into ``state.cfg_readback``, which the flush loop persists into
  ``setpoint_snapshot`` unconditionally every ~60 s (FW-4/Sprint 20),
  independent of any experiment state. It is the identical device-echoed raw
  value, just recorded on an ordinary always-on telemetry path instead of a
  gated one. ``setpoint_snapshot`` also carries separate per-zone band-audit
  rows (``zone``/``band_role``/``target_value`` populated — the *computed*
  crop-curve target, not a device echo); this tool filters those out
  (``zone IS NULL AND band_role IS NULL``) to keep only plain device-cfg-
  readback rows. ``fn_*`` band/curve functions were considered and rejected
  for the same reason: they return intended/served targets, not what the
  device itself is currently holding.

``firmware_revision`` comes from ``public.diagnostics.firmware_version`` —
the exact column ``ingestor.py:_record_diagnostic`` writes and the same one
that feeds ``record_component_grid_firmware_revision()`` (the live-grid
attestor itself). The freshness bound mirrors the 120 s window used for this
same read in ``research/planner-efficacy/protocols/shadow-v2/README.md``.

Coherence and a disclosed limitation
-------------------------------------
The flush loop writes every currently-known ``cfg_*`` parameter in one batch
sharing a single ``ts`` per tick. This tool requires one ``ts`` at which all
48 canonical fields are present — a genuinely simultaneous read — rather than
the latest row per parameter independently, which could silently stitch
together values observed at different moments if one field's readback had
briefly stalled. The disclosed limitation: that shared ``ts`` is the
ingestor's flush-tick time, not each field's original ESPHome callback
instant (that finer per-field provenance exists only in the gated in-memory
epoch this tool deliberately does not depend on). Treat ``observed_at`` here
as tick-granularity, not an exact per-field receipt.

Usage
-----
    python3 scripts/capture_component_current_state.py \\
        --grid-revision "live-entity-grid-v1:sha256:<64 lower-case hex>" \\
        --output /path/to/current-state-observed.json

``--grid-revision`` is never computed by this script — paste the exact value
the ingestor's own ``component_entity_grid_attestation`` log line reports
(``status=pass grid_revision=...``); this tool only checks its shape.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import os
import re
import sys
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER  # noqa: E402
from verdify_schemas.experiment_config import policy_device_id  # noqa: E402
from verdify_schemas.policy_vector import canonical_json_bytes  # noqa: E402
from verdify_schemas.tunable_registry import REGISTRY, wire_value_bounds  # noqa: E402

# Must stay byte-identical to scripts/prepare_component_prefix_replay.py's
# CURRENT_STATE_SCHEMA. tests/test_capture_component_current_state.py asserts
# this equality directly against that module so drift fails loudly, not
# silently at packet-preparation time.
CURRENT_STATE_SCHEMA = "verdify-component-current-state-v1"

_GRID_REVISION = re.compile(r"^live-entity-grid-v[1-9][0-9]*:sha256:[0-9a-f]{64}$")
_FIELD_COUNT = len(CANONICAL_FIELD_ORDER)

_DEFAULT_GREENHOUSE_ID = "vallery"
_DEFAULT_MAX_BATCH_AGE_S = 180
_DEFAULT_FIRMWARE_MAX_AGE_S = 120

# Only plain cfg_* device-readback rows: zone/band_role/target_value are NULL
# on those and non-NULL on per-zone band-audit rows (db/schema.sql), which
# carry a computed served/curve value alongside, not a device echo.
_COMPLETE_BATCH_SQL = """
WITH candidate AS (
    SELECT ts
      FROM public.setpoint_snapshot
     WHERE greenhouse_id = $1
       AND zone IS NULL
       AND band_role IS NULL
       AND parameter = ANY($2::text[])
       AND ts >= clock_timestamp() - make_interval(secs => $3::int)
     GROUP BY ts
    HAVING count(DISTINCT parameter) = $4::bigint
     ORDER BY ts DESC
     LIMIT 1
)
SELECT s.parameter, s.value, s.ts
  FROM public.setpoint_snapshot s
  JOIN candidate c ON s.ts = c.ts
 WHERE s.greenhouse_id = $1
   AND s.zone IS NULL
   AND s.band_role IS NULL
   AND s.parameter = ANY($2::text[])
"""

_FIRMWARE_REVISION_SQL = """
SELECT ts, firmware_version
  FROM public.diagnostics
 WHERE greenhouse_id = $1
   AND ts >= clock_timestamp() - make_interval(secs => $2::int)
   AND firmware_version IS NOT NULL
   AND firmware_version <> ''
 ORDER BY ts DESC
 LIMIT 1
"""


class CaptureError(RuntimeError):
    """The live read cannot produce a truthful current-state artifact."""


def _bounded_text(value: str, label: str, *, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise CaptureError(f"{label} must be nonempty text of at most {maximum} characters")
    if unicodedata.normalize("NFC", value) != value:
        raise CaptureError(f"{label} must be NFC-normalized text")
    return value


def _grid_revision(value: str) -> str:
    text = _bounded_text(value, "grid revision")
    if _GRID_REVISION.fullmatch(text) is None:
        raise CaptureError(
            "grid revision must match live-entity-grid-vN:sha256:<64 lower-case hex> "
            "— paste the exact value the ingestor logged from "
            "component_entity_grid_attestation status=pass, not a source constant "
            "or an invented value"
        )
    return text


def _observed_at_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CaptureError("observed_at must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _typed_value(field_name: str, raw: object) -> bool | float:
    """Project one raw ``setpoint_snapshot.value`` into its exact wire type.

    ``setpoint_snapshot.value`` is always ``double precision``, including for
    switch-kind fields (the firmware cfg_* contract stores those as exactly
    0.0/1.0). ``CURRENT_STATE_SCHEMA`` requires an exact Python ``bool`` for
    those fields: ``prepare_component_prefix_replay.py``'s ``_finite_raw_state``
    rejects a bare 0/1 float with ``type(raw) is not bool`` — a real type
    error, not a truthy coercion. No rounding or clamping is ever applied for
    numeric fields; an out-of-envelope echo is a hard error here, matching
    this repo's "reject, never round or clamp" evidence convention.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise CaptureError(f"{field_name} readback {raw!r} is not a numeric value")
    numeric = float(raw)
    if not math.isfinite(numeric):
        raise CaptureError(f"{field_name} readback {raw!r} is not finite")
    definition = REGISTRY[field_name]
    if definition.wire_kind == "bool":
        if numeric == 0.0:
            return False
        if numeric == 1.0:
            return True
        raise CaptureError(f"{field_name} readback {numeric!r} is not an exact 0.0/1.0 switch echo")
    lower, upper = wire_value_bounds(field_name)
    if numeric < lower or numeric > upper:
        raise CaptureError(f"{field_name} readback {numeric} is outside its wire bounds [{lower}, {upper}]")
    return numeric


async def _read_complete_batch(
    conn: Any,
    *,
    greenhouse_id: str,
    max_age_s: int,
) -> tuple[dict[str, bool | float], datetime]:
    """Return all 48 canonical fields observed at one shared ``ts``, typed."""
    rows = await conn.fetch(
        _COMPLETE_BATCH_SQL,
        greenhouse_id,
        list(CANONICAL_FIELD_ORDER),
        max_age_s,
        _FIELD_COUNT,
    )
    if not rows:
        raise CaptureError(
            f"no setpoint_snapshot batch within the last {max_age_s}s contains all "
            f"{_FIELD_COUNT} canonical fields at one timestamp for "
            f"greenhouse_id={greenhouse_id!r}; the device/ingestor may be disconnected, "
            "still warming its cfg_* cache, or the batch aged out"
        )
    raw_by_field: dict[str, object] = {}
    observed_at: datetime | None = None
    for row in rows:
        parameter = row["parameter"]
        value = row["value"]
        ts = row["ts"]
        if observed_at is None:
            observed_at = ts
        elif ts != observed_at:
            raise CaptureError("setpoint_snapshot batch rows do not share one ts; refusing a stitched snapshot")
        if parameter in raw_by_field and raw_by_field[parameter] != value:
            raise CaptureError(f"conflicting duplicate setpoint_snapshot rows for {parameter!r} at {ts}")
        raw_by_field[parameter] = value
    missing = sorted(set(CANONICAL_FIELD_ORDER) - set(raw_by_field))
    if missing:
        raise CaptureError(f"setpoint_snapshot batch is missing canonical fields: {missing}")
    assert observed_at is not None
    values = {field: _typed_value(field, raw_by_field[field]) for field in CANONICAL_FIELD_ORDER}
    return values, observed_at


async def _read_firmware_revision(conn: Any, *, greenhouse_id: str, max_age_s: int) -> str:
    row = await conn.fetchrow(_FIRMWARE_REVISION_SQL, greenhouse_id, max_age_s)
    if row is None:
        raise CaptureError(
            f"no diagnostics.firmware_version reported within the last {max_age_s}s for greenhouse_id={greenhouse_id!r}"
        )
    return _bounded_text(str(row["firmware_version"]), "firmware_version")


def build_current_state(
    *,
    device_id: str,
    firmware_revision: str,
    grid_revision: str,
    observed_at: datetime,
    values: Mapping[str, bool | float],
) -> dict[str, object]:
    """Assemble one CURRENT_STATE_SCHEMA object. Pure — no I/O."""
    if set(values) != set(CANONICAL_FIELD_ORDER):
        missing = sorted(set(CANONICAL_FIELD_ORDER) - set(values))
        extra = sorted(set(values) - set(CANONICAL_FIELD_ORDER))
        raise CaptureError(f"values must contain exactly the 48 canonical fields: missing={missing} extra={extra}")
    return {
        "schema": CURRENT_STATE_SCHEMA,
        "device_id": _bounded_text(device_id, "device_id"),
        "firmware_revision": _bounded_text(firmware_revision, "firmware_revision"),
        "grid_revision": _grid_revision(grid_revision),
        "observed_at": _observed_at_text(observed_at),
        "values": {field: values[field] for field in CANONICAL_FIELD_ORDER},
    }


def _write_current_state_output(raw: bytes, output_path: Path) -> None:
    """Publish one current-state artifact atomically, privately, never overwriting.

    This is a complete 48-field operational device snapshot — the same
    sensitivity class as ``prepare_component_prefix_replay.py``'s own packet
    output — so it gets the same contract: mode 0600, atomic hard-link publish,
    and an unconditional refusal to overwrite an existing file, directory,
    FIFO or symlink. Reimplemented locally (rather than imported from that
    sibling script) so this tool has no import-time dependency on another
    script's internals; the round-trip test still exercises the real sibling
    validator directly.
    """
    if not isinstance(raw, bytes) or not raw:
        raise CaptureError("output artifact must be nonempty bytes")
    unresolved = output_path.absolute()
    parent = unresolved.parent
    if parent.exists():
        if parent.is_symlink() or not parent.is_dir():
            raise CaptureError("output parent must be a real directory")
    else:
        try:
            parent.mkdir(parents=True, mode=0o700)
        except OSError as exc:
            raise CaptureError("output parent could not be created privately") from exc
    if unresolved.exists() or unresolved.is_symlink():
        raise CaptureError("output target already exists; overwrite refused")

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
            raise CaptureError("output target appeared concurrently; overwrite refused") from exc
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except CaptureError:
        raise
    except OSError as exc:
        raise CaptureError("output artifact could not be published safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _database_dsn(explicit: str | None) -> str:
    if explicit:
        return explicit
    if dsn := os.environ.get("VERDIFY_DSN"):
        return dsn
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        raise CaptureError("--dsn, VERDIFY_DSN, or POSTGRES_PASSWORD is required")
    return f"postgresql://verdify:{password}@127.0.0.1:5432/verdify"


def _resolve_device_id(explicit: str | None, greenhouse_id: str) -> str:
    return _bounded_text(explicit or policy_device_id(greenhouse_id), "device_id")


async def capture(
    *,
    dsn: str,
    greenhouse_id: str,
    device_id: str,
    grid_revision: str,
    max_batch_age_s: int,
    firmware_max_age_s: int,
) -> dict[str, object]:
    """Open one short-lived read-only connection and build the packet."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SET default_transaction_read_only = on")
        values, observed_at = await _read_complete_batch(conn, greenhouse_id=greenhouse_id, max_age_s=max_batch_age_s)
        firmware_revision = await _read_firmware_revision(
            conn, greenhouse_id=greenhouse_id, max_age_s=firmware_max_age_s
        )
    finally:
        await conn.close()
    return build_current_state(
        device_id=device_id,
        firmware_revision=firmware_revision,
        grid_revision=grid_revision,
        observed_at=observed_at,
        values=values,
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--greenhouse-id", default=_DEFAULT_GREENHOUSE_ID, help="setpoint_snapshot/diagnostics partition key."
    )
    parser.add_argument(
        "--device-id",
        default=None,
        help="Defaults to policy_device_id(greenhouse-id), i.e. esp32-<greenhouse-id>.",
    )
    parser.add_argument(
        "--grid-revision",
        required=True,
        help="The exact live-entity-grid-vN:sha256:<hex> value the ingestor's "
        "component_entity_grid_attestation log line reported.",
    )
    parser.add_argument(
        "--max-batch-age-seconds",
        type=_positive_int,
        default=_DEFAULT_MAX_BATCH_AGE_S,
        help="Reject if the newest complete 48-field setpoint_snapshot batch is older than this.",
    )
    parser.add_argument(
        "--firmware-max-age-seconds",
        type=_positive_int,
        default=_DEFAULT_FIRMWARE_MAX_AGE_S,
        help="Reject if the newest diagnostics.firmware_version report is older than this.",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help="Overrides VERDIFY_DSN / POSTGRES_PASSWORD-derived DSN. Never logged or printed.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        grid_revision = _grid_revision(args.grid_revision)
        greenhouse_id = _bounded_text(args.greenhouse_id, "greenhouse_id", maximum=64)
        device_id = _resolve_device_id(args.device_id, greenhouse_id)
        dsn = _database_dsn(args.dsn)
        packet = asyncio.run(
            capture(
                dsn=dsn,
                greenhouse_id=greenhouse_id,
                device_id=device_id,
                grid_revision=grid_revision,
                max_batch_age_s=args.max_batch_age_seconds,
                firmware_max_age_s=args.firmware_max_age_seconds,
            )
        )
        output = canonical_json_bytes(packet)
        _write_current_state_output(output, args.output)
    except CaptureError as exc:
        parser.exit(2, f"current-state capture refused: {exc}\n")
    packet_sha256 = hashlib.sha256(output).hexdigest()
    print(
        f"schema={CURRENT_STATE_SCHEMA} device_id={packet['device_id']} "
        f"firmware_revision={packet['firmware_revision']} observed_at={packet['observed_at']} "
        f"packet_sha256={packet_sha256} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
