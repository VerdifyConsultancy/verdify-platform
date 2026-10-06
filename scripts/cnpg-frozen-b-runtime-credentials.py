"""Initial nine-verifier custody for the separately admitted current275 frozen B.

No production rotation, grant changes, stale274 admission or automatic retry.
ROOT reviews the exact admission packet and serialized mutation window first.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import subprocess
import uuid
from pathlib import Path

NS = "verdify-db-rehearsal"
CLUSTER = "verdify-cnpg-s2-pitr-b-frozen"
CLUSTER_UID = "3981d0f2-4c72-48ac-9e09-826fe6bd4ed6"
SERVICE_UID = "1778c541-900f-4743-b9be-62b13f96c569"
OPERAND_DIGEST = "sha256:8b461e37d18aa049704eb6a9cde2ba9af0f955f8d3725e921070d2450bb2f137"
DATABASE = "verdify_rehearsal"
TABLE = "public.cnpg_s2_275_runtime_receipts"
MIGRATION_SHA = "f4f8da110236461680f41770bad766a7e28b1a6db1982ecc632e93930a375dc4"
TARGET_SECRET = "verdify-cnpg-s2-pitr-b-frozen-runtime-client-auth"
SOURCES = {
    "api": ("verdify-app-secrets", "VERDIFY_API_RUNTIME_DB_PASSWORD"),
    "ingestor": ("verdify-app-secrets", "VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD"),
    "mcp": ("verdify-mcp-runtime-db", "password"),
    **{
        d: ("verdify-" + d.replace("_", "-") + "-runtime-db", "password")
        for d in ("planner", "setpoint_server", "ha_backfill", "vision", "lab_publisher", "grafana")
    },
}
LOGINS = {d: "verdify_" + d + "_runtime_login" for d in SOURCES}
CONTROLS = "SET log_statement='none';SET log_min_error_statement='panic';SET log_min_messages='panic';SET client_min_messages='error';SET log_duration=off;SET log_min_duration_statement=-1;SET log_min_duration_sample=-1;SET log_statement_sample_rate=0;SET log_transaction_sample_rate=0;SET log_parameter_max_length=0;SET log_parameter_max_length_on_error=0;SET pg_stat_statements.track='none';DO $$ BEGIN IF current_setting('pgaudit.log',true) IS NOT NULL THEN PERFORM set_config('pgaudit.log','none',false);END IF;END $$;"
LOGGING_GUARD = "current_setting('log_statement')='none' AND current_setting('log_min_error_statement')='panic' AND current_setting('pg_stat_statements.track')='none' AND coalesce(current_setting('pgaudit.log',true),'none')='none'"
POSTURE = "jsonb_build_object('roles',(SELECT jsonb_agg(to_jsonb(r)-'rolpassword' ORDER BY oid) FROM pg_roles r),'members',(SELECT jsonb_agg(to_jsonb(r) ORDER BY roleid,member,grantor) FROM pg_auth_members r),'settings',(SELECT jsonb_agg(to_jsonb(r) ORDER BY setdatabase,setrole) FROM pg_db_role_setting r),'comments',(SELECT jsonb_agg(to_jsonb(r) ORDER BY objoid,classoid) FROM pg_shdescription r))"


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def lit(value):
    return "'" + value.replace("'", "''") + "'"


def checked_packet(packet):
    require(packet["schema"] == "cnpg-frozen-b-current275-client-admission-v1", "new275 packet required")
    require(packet["cluster"] == CLUSTER and packet["cluster_uid"] == CLUSTER_UID, "exact frozen-B authority required")
    require(packet["database_oid"] == 16447 and packet["service_uid"] == SERVICE_UID, "target OID/service changed")
    require(
        packet["full_data_sequence_seal"] is True and packet["new275_admission_installed"] is True,
        "complete installed275 admission required",
    )
    binding = packet["primary"]
    require(binding["name"] in (CLUSTER + "-1", CLUSTER + "-2", CLUSTER + "-3"), "wrong primary name")
    require(str(uuid.UUID(binding["uid"])) == binding["uid"], "primary UID required")
    require(ipaddress.ip_address(binding["ip"]) in ipaddress.ip_network("10.42.0.0/16"), "primary IP required")
    require(binding["image_digest"] == OPERAND_DIGEST, "unqualified operand image")
    require(type(packet["timeline"]) is int and packet["timeline"] > 0, "native timeline required")
    for key in (
        "installation_sha256",
        "qualification_sha256",
        "full_data_sequence_receipt_sha256",
        "password_free_posture_sha256",
    ):
        require(bool(re.fullmatch(r"[0-9a-f]{64}", packet[key])), "source custody hash required")
    receipts = packet["receipts"]
    require(
        {r["login_name"] for r in receipts} == {LOGINS[d] for d in ("api", "ingestor", "mcp")} and len(receipts) == 3,
        "exact three new275 native receipts required",
    )
    for row in receipts:
        require(
            bool(re.fullmatch(r"[0-9a-f]{64}", row["boundary_sha256"]))
            and row["qualification_sha256"] == packet["qualification_sha256"],
            "unqualified275 receipt",
        )
    require(
        set(packet["attester_definition_sha256"])
        == {"public.fn_runtime_attest_ordinary_login()", "public.fn_mcp_runtime_attest_ordinary_login()"},
        "both new275 target attesters required",
    )
    require(
        all(re.fullmatch(r"[0-9a-f]{64}", v) for v in packet["attester_definition_sha256"].values()),
        "attester definition custody required",
    )
    require(
        set(packet["source_secret_uids"]) == {name for name, _ in SOURCES.values()}, "complete source custody required"
    )
    return packet


def verify_target_secret(obj, packet, data, uid=None):
    metadata = obj["metadata"]
    require(
        metadata["name"] == TARGET_SECRET
        and metadata["namespace"] == NS
        and metadata.get("uid")
        and not metadata.get("deletionTimestamp"),
        "wrong target Secret identity",
    )
    require(uid is None or metadata["uid"] == uid, "target Secret UID changed")
    require(
        metadata["labels"]["verdify.ai/qualification-target"] == CLUSTER
        and metadata["annotations"]["verdify.ai/cluster-uid"] == CLUSTER_UID
        and metadata["annotations"]["verdify.ai/admission-sha256"] == packet["reviewed_admission_sha256"],
        "target Secret ownership changed",
    )
    require(obj["type"] == "Opaque" and obj["data"] == data, "target Secret data/type changed")


def stable_authority(packet, native):
    c, p, s = (native[k] for k in ("cluster", "pod", "service"))
    expected = packet["primary"]
    for obj in (c, p, s):
        require(
            obj["metadata"]["namespace"] == NS and not obj["metadata"].get("deletionTimestamp"),
            "terminating/wrong namespace",
        )
    require(c["metadata"]["name"] == CLUSTER and c["metadata"]["uid"] == CLUSTER_UID, "cluster changed")
    require(
        c["status"]["currentPrimary"] == expected["name"] and c["status"]["readyInstances"] == 3,
        "fresh primary/readiness required",
    )
    require(
        (p["metadata"]["name"], p["metadata"]["uid"], p["status"]["podIP"])
        == (expected["name"], expected["uid"], expected["ip"]),
        "primary changed",
    )
    require(
        any(
            v.get("kind") == "Cluster" and v.get("uid") == CLUSTER_UID for v in p["metadata"].get("ownerReferences", [])
        ),
        "primary owner changed",
    )
    require(
        p["metadata"]["labels"]["cnpg.io/cluster"] == CLUSTER
        and p["status"]["phase"] == "Running"
        and any(v["type"] == "Ready" and v["status"] == "True" for v in p["status"]["conditions"]),
        "primary not ready",
    )
    require(
        any(
            v["name"] == "postgres" and v["ready"] and v["imageID"].endswith(expected["image_digest"])
            for v in p["status"]["containerStatuses"]
        ),
        "operand image changed",
    )
    require(s["metadata"]["name"] == CLUSTER + "-rw" and s["metadata"]["uid"] == SERVICE_UID, "service changed")
    require(
        s["spec"].get("type", "ClusterIP") == "ClusterIP" and s["spec"]["selector"]["cnpg.io/cluster"] == CLUSTER,
        "wrong target route",
    )
    selectors = [s["spec"]["selector"][k] for k in ("role", "cnpg.io/instanceRole") if k in s["spec"]["selector"]]
    require(selectors and all(v == "primary" for v in selectors), "service is not primary-only")
    require(
        any(
            v.get("kind") == "Cluster" and v.get("uid") == CLUSTER_UID for v in s["metadata"].get("ownerReferences", [])
        ),
        "service owner changed",
    )
    require(any(v["port"] == v.get("targetPort", 5432) == 5432 for v in s["spec"]["ports"]), "wrong service port")
    return {
        "cluster_uid": CLUSTER_UID,
        "primary_name": expected["name"],
        "primary_uid": expected["uid"],
        "primary_ip": expected["ip"],
        "image_digest": expected["image_digest"],
        "service_uid": SERVICE_UID,
        "service_selector": s["spec"]["selector"],
        "database_oid": 16447,
    }


def stopped_actors(pods):
    """No qualification/app actor may race the initial-password-only window."""
    for pod in pods:
        if pod.get("status", {}).get("phase") in {"Succeeded", "Failed"}:
            continue
        labels = pod["metadata"].get("labels", {})
        require(
            labels.get("cnpg.io/cluster", "").startswith("verdify-cnpg-")
            and any(c.get("name") == "postgres" for c in pod.get("spec", {}).get("containers", [])),
            "non-database actor active in isolated namespace",
        )


def admission_predicate(packet):
    checks = [
        "current_user='postgres'",
        "session_user='postgres'",
        "inet_client_addr() IS NULL",
        "current_database()='verdify_rehearsal'",
        "current_setting('cluster_name')=" + lit(CLUSTER),
        "NOT pg_is_in_recovery()",
        "NOT EXISTS(SELECT 1 FROM pg_stat_activity WHERE backend_type='client backend' AND pid<>pg_backend_pid() AND (usename LIKE 'verdify_%_runtime_login' OR usename LIKE 'verdify_experiment%'))",
        "current_setting('server_version_num')='160013'",
        "(SELECT timeline_id FROM pg_control_checkpoint())=" + str(packet["timeline"]),
        "(SELECT oid FROM pg_database WHERE datname=current_database())=16447",
        "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=275",
        "EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq=275 AND filename='db/migrations/275-lighting-minutes-policy-bounded-jit.sql' AND stamp_method='runner' AND sha256="
        + lit(MIGRATION_SHA)
        + ")",
        "(SELECT proconfig FROM pg_proc WHERE oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure)=ARRAY['jit=off']::text[]",
        "(SELECT count(*) FROM " + TABLE + ")=3",
        "(SELECT pg_get_userbyid(relowner)='verdify' FROM pg_class WHERE oid=" + lit(TABLE) + "::regclass)",
    ]
    for r in packet["receipts"]:
        login = r["login_name"]
        digest = (
            "public.fn_mcp_runtime_boundary_digest()"
            if login == LOGINS["mcp"]
            else "public.fn_runtime_ordinary_boundary_digest(" + lit(login) + ")"
        )
        checks += [
            "EXISTS(SELECT 1 FROM "
            + TABLE
            + " WHERE login_name="
            + lit(login)
            + " AND encode(boundary_sha256,'hex')="
            + lit(r["boundary_sha256"])
            + " AND qualification_sha256="
            + lit(r["qualification_sha256"])
            + ")",
            "encode(" + digest + ",'hex')=" + lit(r["boundary_sha256"]),
        ]
    for signature, digest in packet["attester_definition_sha256"].items():
        checks.append(
            "encode(sha256(convert_to(pg_get_functiondef("
            + lit(signature)
            + "::regprocedure),'UTF8')),'hex')="
            + lit(digest)
        )
    return "(" + " AND ".join(checks) + ")"


def atomic_write(path, value):
    raw = (json.dumps(value, indent=2) + "\n").encode()
    temporary = path.with_name(path.name + ".pending")
    with temporary.open("wb") as out:
        os.chmod(temporary, 0o600)
        out.write(raw)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def scram(password):
    require(bool(re.fullmatch(rb"[\x21-\x7e]{16,256}", password)), "unsupported credential shape")
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password, salt, 4096)
    client = hmac.new(salted, b"Client Key", "sha256").digest()
    stored = hashlib.sha256(client).digest()
    server = hmac.new(salted, b"Server Key", "sha256").digest()

    def encode(value):
        return base64.b64encode(value).decode()

    return "SCRAM-SHA-256$4096:" + encode(salt) + "$" + encode(stored) + ":" + encode(server)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--admission-sha256", required=True)
    parser.add_argument("--installation", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--data-sequence-seal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    packet = checked_packet(json.loads(args.admission.read_text()))
    require(sha(args.admission.read_bytes()) == args.admission_sha256, "reviewed admission changed")
    packet["reviewed_admission_sha256"] = args.admission_sha256
    for path, key in (
        (args.installation, "installation_sha256"),
        (args.qualification, "qualification_sha256"),
        (args.data_sequence_seal, "full_data_sequence_receipt_sha256"),
    ):
        require(sha(path.read_bytes()) == packet[key], "authoritative source custody changed")
    installation = json.loads(args.installation.read_text())
    require(
        installation["mode"] == "install"
        and max(v[2] for v in installation["post_witness"]["ledger"] if v[0] == "db/migrations") == 275,
        "installed native275 witness required",
    )
    for row in packet["receipts"]:
        require(
            installation["post_witness"]["boundaries"][row["login_name"]]["native"] == row["boundary_sha256"],
            "receipt differs from installed full witness",
        )
    args.output.mkdir(parents=True, exist_ok=True)
    state_path = args.output / "state.json"
    require(not state_path.exists(), "prior state exists; inspect commit-unknown custody, never rerun")
    k = ["kubectl", "--context", "vallery"]

    def command(argv, data=None):
        result = subprocess.run(argv, input=data, capture_output=True)
        require(result.returncode == 0, "operation refused; raw errors withheld")
        return result.stdout

    def native():
        objects = {
            kind: json.loads(command(k + ["-n", NS, "get", kind, name, "-o", "json"]))
            for kind, name in (("cluster", CLUSTER), ("pod", packet["primary"]["name"]), ("service", CLUSTER + "-rw"))
        }
        return stable_authority(packet, objects), sha(json.dumps(objects, sort_keys=True).encode())

    def peer(sql):
        # UID guard runs inside the exact Pod before touching the peer socket.
        argv = k + [
            "-n",
            NS,
            "exec",
            "-i",
            packet["primary"]["name"],
            "-c",
            "postgres",
            "--",
            "sh",
            "-c",
            'test "$VERDIFY_REHEARSAL_POD_UID" = "$1" || exit 42; shift; unset PGOPTIONS PGSERVICE PGSERVICEFILE PGPASSWORD PGPASSFILE; exec "$@"',
            "uid-guard",
            packet["primary"]["uid"],
            "psql",
            "-X",
            "-U",
            "postgres",
            "-d",
            DATABASE,
            "-Atq",
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            "VERBOSITY=sqlstate",
        ]
        return json.loads(command(argv, sql.encode()).decode())

    names = ",".join(lit(v) for v in LOGINS.values())
    predicate = admission_predicate(packet)
    nulls = (
        "(SELECT count(*)=9 AND bool_and(rolpassword IS NULL AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolbypassrls AND NOT rolreplication) FROM pg_authid WHERE rolname IN ("
        + names
        + "))"
    )
    before_authority, before_raw = native()
    pre = peer(
        "BEGIN READ ONLY;SET LOCAL statement_timeout='30s';SELECT json_build_object('admitted',"
        + predicate
        + ",'all_nine_null',"
        + nulls
        + ",'posture_sha256',encode(sha256(convert_to("
        + POSTURE
        + "::text,'UTF8')),'hex'));ROLLBACK;"
    )
    require(
        pre["admitted"] is True
        and pre["all_nine_null"] is True
        and pre["posture_sha256"] == packet["password_free_posture_sha256"],
        "native275/NULL/posture prerequisite refused",
    )
    stopped_actors(json.loads(command(k + ["-n", NS, "get", "pods", "-o", "json"]))["items"])
    absence = subprocess.run(k + ["-n", NS, "get", "secret", TARGET_SECRET, "-o", "json"], capture_output=True)
    require(
        absence.returncode != 0 and b"(NotFound)" in absence.stderr, "target Secret must be absent; never overwrite"
    )
    if not args.execute:
        atomic_write(
            args.output / "prepared.json",
            {
                "execute": False,
                "authority": before_authority,
                "native_observation_sha256": before_raw,
                "admission_sha256": args.admission_sha256,
                "all_nine_null": True,
                "target_secret_absent": True,
                "credential_values_read": False,
            },
        )
        print(json.dumps({"prepared": True, "execute": False}))
        return
    state = {
        "phase": "source_custody",
        "database_commit_state": "not_attempted",
        "created_secret": None,
        "authority": before_authority,
        "admission_sha256": args.admission_sha256,
    }
    atomic_write(state_path, state)
    cached, passwords, source_uids = {}, {}, {}
    for duty, (name, key) in SOURCES.items():
        if name not in cached:
            cached[name] = json.loads(command(k + ["-n", "verdify-prod", "get", "secret", name, "-o", "json"]))
        obj = cached[name]
        require(not obj["metadata"].get("deletionTimestamp"), "source custody terminating")
        passwords[duty] = base64.b64decode(obj["data"][key], validate=True)
        source_uids[name] = obj["metadata"]["uid"]
        require(source_uids[name] == packet["source_secret_uids"][name], "source credential custody changed")
    require(len(set(passwords.values())) == 9, "ordinary credentials must remain distinct")
    data = {d: base64.b64encode(v).decode() for d, v in passwords.items()}
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": TARGET_SECRET,
            "namespace": NS,
            "labels": {"verdify.ai/qualification-target": CLUSTER},
            "annotations": {
                "verdify.ai/cluster-uid": CLUSTER_UID,
                "verdify.ai/admission-sha256": args.admission_sha256,
            },
        },
        "type": "Opaque",
        "data": data,
    }
    state.update(
        phase="target_secret_creation_inflight", target_secret_name=TARGET_SECRET, target_custody_state="unknown"
    )
    atomic_write(state_path, state)
    created = json.loads(command(k + ["-n", NS, "create", "-f", "-", "-o", "json"], json.dumps(secret).encode()))
    state.update(
        created_secret={"name": TARGET_SECRET, "uid": created["metadata"]["uid"]},
        target_custody_state="response_received",
    )
    atomic_write(state_path, state)
    verify_target_secret(created, packet, data)
    reread = json.loads(command(k + ["-n", NS, "get", "secret", TARGET_SECRET, "-o", "json"]))
    verify_target_secret(reread, packet, data, created["metadata"]["uid"])
    for name, obj in cached.items():
        fresh = json.loads(command(k + ["-n", "verdify-prod", "get", "secret", name, "-o", "json"]))
        require(
            fresh["metadata"]["uid"] == obj["metadata"]["uid"] and fresh["data"] == obj["data"],
            "production source bytes changed",
        )
    stopped_actors(json.loads(command(k + ["-n", NS, "get", "pods", "-o", "json"]))["items"])
    fresh_authority, raw_before_write = native()
    require(fresh_authority == before_authority, "authority changed before verifier installation")
    state.update(
        phase="verifier_transaction_inflight",
        database_commit_state="unknown",
        pre_write_observation_sha256=raw_before_write,
        recovery="Inspect nine NULL/non-NULL flags and existing owned Secret; never overwrite, rerun or rotate after uncertain commit.",
    )
    atomic_write(state_path, state)
    guard = (
        "DO $$ BEGIN IF ("
        + predicate
        + " AND "
        + nulls
        + " AND "
        + LOGGING_GUARD
        + ") IS DISTINCT FROM TRUE OR encode(sha256(convert_to("
        + POSTURE
        + "::text,'UTF8')),'hex')<>"
        + lit(pre["posture_sha256"])
        + " THEN RAISE EXCEPTION 'initial nine verifier authority refused';END IF;END $$;"
    )
    alters = "".join("ALTER ROLE " + LOGINS[d] + " PASSWORD " + lit(scram(v)) + ";" for d, v in passwords.items())
    post = peer(
        CONTROLS
        + "BEGIN;SET LOCAL statement_timeout='30s';SET LOCAL lock_timeout='3s';LOCK TABLE pg_catalog.pg_authid IN SHARE ROW EXCLUSIVE MODE;"
        + guard
        + alters
        + "DO $$ BEGIN IF (SELECT count(*)=9 AND bool_and(rolpassword LIKE 'SCRAM-SHA-256$%') FROM pg_authid WHERE rolname IN ("
        + names
        + ")) IS DISTINCT FROM TRUE OR encode(sha256(convert_to("
        + POSTURE
        + "::text,'UTF8')),'hex')<>"
        + lit(pre["posture_sha256"])
        + " THEN RAISE EXCEPTION 'verified installation refused';END IF;END $$;SELECT json_build_object('installed',true);COMMIT;"
    )
    require(post["installed"] is True, "verified transaction response missing")
    receipt = {
        "schema": "cnpg-frozen-b-nine-initial-credential-custody-v1",
        "authority": before_authority,
        "admission_sha256": args.admission_sha256,
        "source_secret_uids": source_uids,
        "target_secret": state["created_secret"],
        "initial_null_only": True,
        "all_nine_verified_scram": True,
        "password_free_posture_sha256": pre["posture_sha256"],
        "source_credentials_rotated": False,
        "actual_client_authentication_proven": False,
        "post_binding_status": "pending",
    }
    # Durable custody precedes every post-readback that may fail after COMMIT.
    atomic_write(args.output / "custody.json", receipt)
    state.update(phase="installed", database_commit_state="committed_response_received")
    atomic_write(state_path, state)
    try:
        for name, obj in cached.items():
            fresh = json.loads(command(k + ["-n", "verdify-prod", "get", "secret", name, "-o", "json"]))
            require(
                fresh["metadata"]["uid"] == obj["metadata"]["uid"] and fresh["data"] == obj["data"],
                "production source bytes changed after installation",
            )
        receipt["post_source_bytes_unchanged"] = True
        after_authority, after_raw = native()
        receipt.update(
            post_binding_status="passed" if after_authority == before_authority else "failed_authority_changed",
            post_observation_sha256=after_raw,
        )
    except BaseException:
        receipt["post_binding_status"] = "failed_readback"
        atomic_write(args.output / "custody.json", receipt)
        raise
    atomic_write(args.output / "custody.json", receipt)
    require(receipt["post_binding_status"] == "passed", "installed custody retained; post-authority changed")
    print(
        json.dumps(
            {
                "installed": True,
                "runtime_login_count": 9,
                "target_secret_uid": receipt["target_secret"]["uid"],
                "actual_client_authentication_proven": False,
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_category": type(error).__name__,
                    "details": "private; inspect durable custody before recovery",
                }
            )
        )
        raise SystemExit(1) from None
