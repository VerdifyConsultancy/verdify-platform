#!/usr/bin/env python3
"""Offline firmware digital-twin driver (Phase-1 LOCAL harness).

Design: docs/design/firmware-digital-twin.md §2.1 (native logic twin) and §5.3
(read-only / no-actuation safety model, layer L2). This driver wraps the EXACT
``replay_emit_follow`` binary (the ``-DREPLAY_EMIT_STREAM`` build of
``firmware/test/replay_emit.cpp`` — the same code the rule-8 firmware-replay-diff
gate trusts) and replays a telemetry CSV through it, parsing the decision row
the harness flushes per tick. The decision rows map 1:1 to ``twin_decisions``
columns (migration 155 / issue #33).

This is the **offline** harness: it feeds a checked-in / mounted corpus CSV,
not the live stream — so it runs with no live DB and no device path. It is the
local stand-in for the real prod twin (which shadows live telemetry and needs
the prod DB / applied migration 155; that part is Root/prod-migration-blocked).

Read-only by construction (§5.3 L2):
  * No actuation client, no MQTT publish, no ESP32 route — this process only
    reads a CSV and (optionally) INSERTs into twin_decisions.
  * If TWIN_DSN is set it connects as the INSERT-only ``twin_ro`` login user and
    asserts at startup that the role CANNOT write a control-plane table; it
    aborts loudly if that assertion fails. With no TWIN_DSN it runs in
    dry-run/stdout mode (pure offline replay, no DB at all).

Env:
  TWIN_CSV     telemetry CSV to replay        (default /data/replay_overrides.csv)
  TWIN_REF     git sha / fw_version pinned     (default "last-good")
  TWIN_ENV     dev|stage|prod                  (default "prod")
  TWIN_DSN     optional INSERT-only twin_ro DSN; omit for stdout dry-run
  TWIN_BINARY  follower path                   (default replay_emit_follow)
  TWIN_LIMIT   stop after N ticks              (default 0 = whole CSV)
"""

from __future__ import annotations

import gzip
import io
import os
import subprocess
import sys
from datetime import UTC, datetime

# replay_emit_follow's flushed decision header (replay_emit.cpp stream main()).
DECISION_COLS = (
    "ts",
    "mode",
    "relay_fog",
    "relay_vent",
    "relay_fan1",
    "relay_fan2",
    "relay_heat1",
    "relay_heat2",
    "mist_stage",
    "reason",
    "override_bits",
    "climate_action",
)
RELAY_COLS = ("relay_fog", "relay_vent", "relay_fan1", "relay_fan2", "relay_heat1", "relay_heat2")

# Control-plane table the twin_ro role MUST NOT be able to write (safety probe).
CONTROL_TABLE = "equipment_state"


def _open_csv(path: str) -> io.TextIOBase:
    if path.endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8")
    return open(path, encoding="utf-8")  # noqa: SIM115 - caller closes


def _assert_read_only(conn) -> None:
    """§5.3 L2: prove this role cannot write a control-plane table.

    We probe in a savepoint and require an insufficient_privilege error. If the
    INSERT unexpectedly succeeds (or any other outcome), the twin is NOT
    read-only by construction and we refuse to run.
    """
    import psycopg

    # Probe inside a nested transaction (savepoint) so the rolled-back attempt
    # leaves no trace even in the impossible case it were permitted.
    with conn.transaction():
        try:
            with conn.transaction():
                conn.execute(f"INSERT INTO {CONTROL_TABLE} (ts, equipment, state) VALUES (now(), 'twin_probe', false)")
        except psycopg.errors.InsufficientPrivilege:
            return  # expected — the role is correctly write-locked
        raise SystemExit(
            f"FATAL: twin role could WRITE {CONTROL_TABLE}; not read-only by "
            "construction (design §5.3 L2). Refusing to run."
        )


def main() -> int:
    csv_path = os.environ.get("TWIN_CSV", "/data/replay_overrides.csv")
    twin_ref = os.environ.get("TWIN_REF", "last-good")
    twin_env = os.environ.get("TWIN_ENV", "prod")
    dsn = os.environ.get("TWIN_DSN", "").strip()
    binary = os.environ.get("TWIN_BINARY", "replay_emit_follow")
    limit = int(os.environ.get("TWIN_LIMIT", "0"))

    if twin_env not in ("dev", "stage", "prod"):
        print(f"FATAL: TWIN_ENV must be dev|stage|prod, got {twin_env!r}", file=sys.stderr)
        return 2

    conn = None
    if dsn:
        import psycopg

        conn = psycopg.connect(dsn, autocommit=True)
        _assert_read_only(conn)
        print(f"[twin] connected read-only; {CONTROL_TABLE} write-lock verified", file=sys.stderr)
    else:
        print("[twin] no TWIN_DSN — dry-run, decisions go to stdout only", file=sys.stderr)

    # Launch the follower; prime + follow from the same CSV by piping data rows.
    proc = subprocess.Popen(  # noqa: S603 - fixed binary, no shell
        [binary, "--stream", "--header-from", csv_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stdin is not None and proc.stdout is not None
    decision_header = proc.stdout.readline()  # consume the harness header line
    if not decision_header.startswith("ts\tmode\t"):
        print(f"FATAL: unexpected follower header: {decision_header!r}", file=sys.stderr)
        return 3

    written = 0
    src = _open_csv(csv_path)
    try:
        # The replay corpus is TAB-separated (replay_emit.cpp splits on '\t');
        # forward each data line to the follower verbatim — no re-encoding.
        header = src.readline().rstrip("\n").split("\t")
        ts_idx = header.index("ts")
        for raw in src:
            line = raw.rstrip("\n")
            if not line:
                continue
            row = line.split("\t")
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
            decision = proc.stdout.readline().rstrip("\n").split("\t")
            if len(decision) != len(DECISION_COLS):
                continue  # skip malformed / gap-reset lines
            rec = dict(zip(DECISION_COLS, decision, strict=True))
            input_ts = row[ts_idx]
            if conn is not None:
                conn.execute(
                    "INSERT INTO twin_decisions "
                    "(ts, twin_env, twin_ref, input_ts, mode, climate_action, mist_stage, "
                    " relay_fog, relay_vent, relay_fan1, relay_fan2, relay_heat1, relay_heat2, "
                    " mode_reason, override_bits, twin_metadata) "
                    "VALUES (%(ts)s, %(env)s, %(ref)s, %(input_ts)s, %(mode)s, %(action)s, "
                    " %(mist)s, %(fog)s, %(vent)s, %(fan1)s, %(fan2)s, %(heat1)s, %(heat2)s, "
                    " %(reason)s, %(ovr)s, %(meta)s::jsonb)",
                    {
                        "ts": datetime.now(UTC),
                        "env": twin_env,
                        "ref": twin_ref,
                        "input_ts": input_ts,
                        "mode": rec["mode"],
                        "action": rec["climate_action"],
                        "mist": int(rec["mist_stage"]),
                        "fog": rec["relay_fog"] in ("1", "true", "True"),
                        "vent": rec["relay_vent"] in ("1", "true", "True"),
                        "fan1": rec["relay_fan1"] in ("1", "true", "True"),
                        "fan2": rec["relay_fan2"] in ("1", "true", "True"),
                        "heat1": rec["relay_heat1"] in ("1", "true", "True"),
                        "heat2": rec["relay_heat2"] in ("1", "true", "True"),
                        "reason": rec["reason"],
                        "ovr": int(rec["override_bits"]),
                        "meta": '{"vpd_zone_inputs":"homogenized","driver":"offline"}',
                    },
                )
            else:
                relays = "".join("1" if rec[c] in ("1", "true", "True") else "0" for c in RELAY_COLS)
                print(f"{input_ts}\t{rec['mode']}\t{relays}\t{rec['climate_action']}")
            written += 1
            if limit and written >= limit:
                break
    finally:
        src.close()
        proc.stdin.close()
        proc.wait(timeout=10)
        if conn is not None:
            conn.close()

    print(f"[twin] replayed {written} ticks (env={twin_env}, ref={twin_ref})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
