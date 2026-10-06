"""Bounded real TCP bootstrap writes during isolated frozen-B primary loss.

Each sequence is dispatched exactly once. A failed/ambiguous dispatch advances
to a new marker, and never claims acknowledgement from an INSERT alone.
"""

import datetime
import ipaddress
import json
import os
import re
import subprocess
import time

CLUSTER = "verdify-cnpg-s2-pitr-b-frozen"
TABLE = "public.cnpg_s2_failover_20261006"
PSQL = ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1"]
GUARD = f"""DO $failover_client_guard$ BEGIN
 IF current_database()<>'rehearsal_bootstrap' OR current_user<>'rehearsal_bootstrap'
    OR current_user<>session_user OR current_setting('cluster_name')<>'{CLUSTER}'
    OR current_setting('server_version_num')::int<>160013 OR pg_is_in_recovery()
    OR (SELECT oid FROM pg_database WHERE datname=current_database())<>16385
    OR current_setting('timescaledb.max_background_workers')<>'0'
    OR EXISTS(SELECT 1 FROM pg_extension WHERE extname='timescaledb')
    OR coalesce(current_setting('timescaledb.restoring',true),'off')<>'off'
    OR current_setting('synchronous_commit')<>'on'
    OR inet_client_addr() IS NULL OR inet_server_port()<>5432
    OR (SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='{TABLE}'::regclass)<>'rehearsal_bootstrap'
    OR pg_walfile_name(pg_current_wal_flush_lsn()) IS NULL THEN
   RAISE EXCEPTION 'S2 failover client refuses target/session';
 END IF;
END $failover_client_guard$;
"""


def statement(run_id, sequence):
    if not re.fullmatch(r"s2-failover-[a-z0-9-]{8,48}", run_id) or type(sequence) is not int or sequence <= 0:
        raise ValueError("invalid bounded failover marker")
    marker = f"{run_id}-{sequence}"
    return rf"""\set ON_ERROR_STOP on
SET timezone='UTC'; SET search_path=pg_catalog,public,pg_temp;
SET statement_timeout='2s'; SET lock_timeout='1s';
{GUARD}
BEGIN;
INSERT INTO {TABLE}(marker_id,run_id,sequence_number)
VALUES ('{marker}','{run_id}',{sequence});
COMMIT;
SELECT jsonb_build_object('kind','ack','marker_id',marker_id,'sequence',sequence_number,
 'created_at',created_at,'xid',xmin::text,'acknowledged_at',clock_timestamp(),
 'flush_lsn',pg_current_wal_flush_lsn()::text,
 'timeline',('x'||left(pg_walfile_name(pg_current_wal_flush_lsn()),8))::bit(32)::bigint,
 'server_address',pg_catalog.host(inet_server_addr()),'server_port',inet_server_port(),
 'backend_pid',pg_backend_pid(),'cluster',current_setting('cluster_name'))
FROM {TABLE} WHERE marker_id='{marker}';
"""


def emit(value):
    print(json.dumps(value, separators=(",", ":")), flush=True)


def main():
    run_id = os.environ["FAILOVER_RUN_ID"]
    initial_address = str(ipaddress.IPv4Address(os.environ["FAILOVER_INITIAL_SERVER_ADDRESS"]))
    statement(run_id, 1)
    started = time.monotonic()
    deadline = started + 170
    for _attempt in range(20):
        ready = subprocess.run(["pg_isready", "-t", "2"], capture_output=True, timeout=3)
        if ready.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError("bounded preSQL readiness exhausted")
    # Readonly first dispatch; rejects reuse before any marker write.
    initial = (
        "SET search_path=pg_catalog,public,pg_temp; SET statement_timeout='2s'; BEGIN READ ONLY;"
        + GUARD
        + f"DO $address$ BEGIN IF pg_catalog.host(inet_server_addr())<>'{initial_address}' THEN "
        + "RAISE EXCEPTION 'initial service endpoint differs'; END IF; END $address$;"
        + f"DO $reuse$ BEGIN IF EXISTS(SELECT 1 FROM {TABLE} WHERE run_id='{run_id}') THEN "
        + "RAISE EXCEPTION 'failover run already exists'; END IF; END $reuse$; COMMIT;"
    )
    check = subprocess.run(PSQL, input=initial, capture_output=True, text=True, timeout=4)
    if check.returncode or check.stderr:
        raise RuntimeError("initial readonly target/reuse guard failed")
    emit({"kind": "ready", "run_id": run_id, "client_monotonic": time.monotonic()})
    sequence = 0
    while time.monotonic() < deadline - 4:
        sequence += 1
        before = time.monotonic()
        record = {
            "kind": "attempt",
            "sequence": sequence,
            "client_started_monotonic": before,
            "client_started_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        try:
            result = subprocess.run(PSQL, input=statement(run_id, sequence), capture_output=True, text=True, timeout=4)
            records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
            # Any warning or failed client exit makes the outcome ambiguous.
            if result.returncode == 0 and not result.stderr and len(records) == 1 and records[0]["kind"] == "ack":
                record["ack"] = records[0]
                record["outcome"] = "acknowledged"
            else:
                record["outcome"] = "unacknowledged_or_ambiguous"
                record["native_exit"] = result.returncode
        except subprocess.TimeoutExpired:
            record["outcome"] = "client_timeout_ambiguous"
        record["client_finished_monotonic"] = time.monotonic()
        emit(record)
        time.sleep(0.2)
    emit({"kind": "complete", "run_id": run_id, "attempts": sequence, "elapsed_seconds": time.monotonic() - started})


if __name__ == "__main__":
    main()
