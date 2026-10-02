"""Logical target-only admission in one bounded native owner session.

This explicit mode preserves source locks across a genuine savepoint rollback
and a separately checked installation. It is not ordinary password-auth proof.
Default transition/physical transaction modes remain unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import selectors
import subprocess
import time
import uuid
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("retained_target", ROOT / "scripts/cnpg-target-runtime-transition.py")
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
VERSION = "cnpg-native-retained-session-transition-v1"
TOTAL_SECONDS = 900
PHASE_SECONDS = 240
MAX_STREAM = 2 * t.c0.WITNESS_MAX_BYTES + 1024 * 1024


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def object_output(raw):
    lines = [line for line in raw.splitlines(keepends=True) if line.startswith(b"{")]
    t.c0.require(
        len(lines) == 1 and all(line.startswith(b"{") or line.strip() in (b"", b"t") for line in raw.splitlines()),
        "one complete native JSON output required",
    )
    return json.loads(lines[0], object_pairs_hook=t.c0.boundary._pairs), lines[0]


def record(raw, mode):
    """Preserve the actual closed record; no relabeling/version substitution."""
    value, raw_record = object_output(raw)
    t.c0.require(len(raw_record) <= 2 * t.c0.WITNESS_MAX_BYTES + 1024, "closed two-witness record bound exceeded")
    t.c0.require(
        set(value) == {"version", "mode", "ddl_sha256", "before_witness", "post_witness"}, "closed record required"
    )
    t.c0.require(value["version"] == VERSION and value["mode"] == mode, "wrong retained mode")
    for name in ("before_witness", "post_witness"):
        t.c0.require(
            len(json.dumps(value[name], ensure_ascii=False, separators=(",", ":")).encode()) <= t.c0.WITNESS_MAX_BYTES,
            "single full witness bound exceeded",
        )
    return value, raw_record


def review(before, actual, reference):
    post = t.checked_qualification(before, actual, retained_session=True)
    # The old record is solely the reviewed body/native/semantic reference.
    # New raw before/post remain independently checked, never normalized.
    t.c0.require(
        reference["version"] == t.VERSION and reference["mode"] == "rollback-qualification",
        "reviewed ordinary qualification reference required",
    )
    t.c0.require(actual["ddl_sha256"] == reference["ddl_sha256"], "reviewed source DDL changed")
    t.c0.require(set(post) == set(reference["post_witness"]), "reviewed witness shape changed")
    t.c0.require(
        all(post[k] == reference["post_witness"][k] for k in post if k != "portability_native_facts"),
        "reviewed native/semantic/body boundary changed",
    )
    t.validate_raw_delta(before, post)
    return post


def identity_sql():
    return f"""DO $retained_identity$ BEGIN
 IF current_database()<>'verdify_rehearsal' OR current_setting('server_version_num')::int<>{t.SERVER}
  OR current_setting('cluster_name')<>'verdify-cnpg-rehearsal' OR inet_client_addr() IS NOT NULL
  OR pg_is_in_recovery() OR current_user<>'verdify' OR session_user<>'verdify'
  OR (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database())<>'verdify' THEN
  RAISE EXCEPTION 'retained admission refuses target/session'; END IF;
END $retained_identity$;"""


def custody_sql():
    return """SELECT jsonb_build_object('pid',pg_backend_pid(),
 'backend_start',(SELECT backend_start FROM pg_stat_activity WHERE pid=pg_backend_pid()),
 'postmaster_start',pg_postmaster_start_time(),'database',current_database(),
 'cluster',current_setting('cluster_name'),'server',current_setting('server_version_num'),
 'session_user',session_user,'current_user',current_user,'primary',NOT pg_is_in_recovery(),
 'locks',(SELECT jsonb_agg(jsonb_build_array(relation::regclass::text,mode,granted) ORDER BY relation)
  FROM pg_locks WHERE pid=pg_backend_pid() AND relation IN
  ('public.v_relay_stuck'::regclass,'public.v_climate_merged'::regclass) AND mode='AccessShareLock'));
"""


def checked_custody(value, original=None):
    t.c0.require(
        value["database"] == t.DATABASE
        and value["cluster"] == t.operator.CLUSTER
        and value["server"] == str(t.SERVER)
        and value["primary"] is True
        and value["session_user"] == value["current_user"] == "verdify",
        "native owner custody mismatch",
    )
    t.c0.require(
        sorted(value["locks"])
        == sorted(
            [["public.v_relay_stuck", "AccessShareLock", True], ["public.v_climate_merged", "AccessShareLock", True]]
        ),
        "two source locks required",
    )
    if original is not None:
        t.c0.require(value == original, "retained backend/lock identity changed")
    return value


def outer_begin():
    dummy = "\\set ON_ERROR_STOP on\nBEGIN;\nROLLBACK;\n"
    prefix = t.bootstrap_owner_sql(dummy).split("\nBEGIN;\n", 1)[0]
    return (
        prefix
        + "\nBEGIN;\nSET LOCAL search_path=pg_catalog,pg_temp;\n"
        + """SET LOCAL statement_timeout='180s';
SET LOCAL lock_timeout='2s';
SET LOCAL idle_in_transaction_session_timeout='60s';
"""
        + identity_sql()
        + """
DO $roles_before$ BEGIN
 PERFORM set_config('verdify.cnpg_retained_roles_before',
  jsonb_build_object('roles',(SELECT jsonb_agg(to_jsonb(r)-'rolpassword' ORDER BY oid) FROM pg_roles r),
    'memberships',(SELECT jsonb_agg(to_jsonb(m) ORDER BY oid) FROM pg_auth_members m))::text,true);
END $roles_before$;
SELECT pg_advisory_xact_lock(hashtext('verdify-schema-migrations'));
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
 public.mcp_runtime_boundary_receipt IN SHARE MODE;
"""
        + t.refresh_custody_sql()
        + "\n"
        + custody_sql()
    )


def capture_sql():
    query = t.c0.emit_sql(target=True, bootstrap_grantor_profile=True)
    start = query.index("DO $guard$")
    return query[start : query.rindex("COMMIT;")]


def rollback_proof(before):
    return (
        identity_sql()
        + """
DO $rolled_back$ BEGIN
 IF to_regclass('public.cnpg_qualified_runtime_receipts') IS NOT NULL THEN
  RAISE EXCEPTION 'qualification receipt survived savepoint rollback'; END IF;
END $rolled_back$;
"""
        + t.transaction_witness_inputs_sql(before, None)
        + "\nDO $exact_rollback$ DECLARE v_actual jsonb; BEGIN\n"
        + t.witness_select(before)[t.witness_select(before).index("SELECT jsonb_build_object(") :]
        .rstrip()
        .removesuffix(";")
        + " INTO v_actual;\n IF v_actual IS DISTINCT FROM current_setting('verdify.cnpg_transition_expected_before')::jsonb THEN\n RAISE EXCEPTION 'retained rollback changed full predecessor'; END IF; END $exact_rollback$;\n"
        + custody_sql()
    )


class Session:
    """One native psql backend; integrity-checked gzip members per phase."""

    def __init__(self, command, directory, *, popen=subprocess.Popen):
        self.directory = directory
        self.started = time.monotonic()
        self.buffer = b""
        self.commit_sent = False
        self.committed = False
        self.stderr = (directory / "native-private.stderr").open("wb")
        self.process = popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)

    def phase(self, name, sql):
        t.c0.require(time.monotonic() - self.started < TOTAL_SECONDS, "retained total deadline exceeded")
        token = "cnpg_" + uuid.uuid4().hex
        start, end = (token + "_start\n").encode(), (token + "_end\n").encode()
        # psql closes its gzip output pipe at \o, flushing a complete member
        # while the database connection and the earlier locks remain alive.
        command = "\\echo " + token + "_start\n\\o | gzip -c\n" + sql + "\n\\o\n\\echo " + token + "_end\n"
        (self.directory / (name + ".sql")).write_text(sql)
        if name == "install":
            self.commit_sent = True
        # Read and write together: psql can produce its complete witness before
        # consuming the trailing savepoint/COMMIT commands. Blocking stdin first
        # would deadlock against stdout pipe backpressure on large full records.
        pending = memoryview(command.encode())
        deadline = min(self.started + TOTAL_SECONDS, time.monotonic() + PHASE_SECONDS)
        os.set_blocking(self.process.stdin.fileno(), False)
        self.selector.register(self.process.stdin, selectors.EVENT_WRITE)
        data = self.buffer
        try:
            with (self.directory / (name + ".native-stream")).open("wb") as receipt:
                receipt.write(data)
                while end not in data:
                    remaining = deadline - time.monotonic()
                    t.c0.require(remaining > 0, "retained phase deadline exceeded")
                    events = self.selector.select(min(1, remaining))
                    for key, _ in events:
                        if key.fileobj is self.process.stdin:
                            written = os.write(self.process.stdin.fileno(), pending[:65536])
                            pending = pending[written:]
                            if not pending:
                                self.selector.unregister(self.process.stdin)
                        else:
                            chunk = os.read(self.process.stdout.fileno(), 65536)
                            t.c0.require(bool(chunk), "native session ended before complete phase")
                            receipt.write(chunk)
                            data += chunk
                            t.c0.require(len(data) <= MAX_STREAM, "bounded native stream exceeded")
                    if not events and self.process.poll() is not None:
                        raise ValueError("native session terminated before complete phase")
            t.c0.require(not pending, "native phase ended before complete SQL input")
        finally:
            if self.process.stdin.fileno() in self.selector.get_map():
                self.selector.unregister(self.process.stdin)
        chunk, self.buffer = data.split(end, 1)
        t.c0.require(start in chunk, "missing native phase start")
        compressed = chunk.split(start, 1)[1]
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = decoder.decompress(compressed, MAX_STREAM + 1)
        t.c0.require(not decoder.unconsumed_tail, "decompressed stream bound exceeded")
        raw += decoder.flush()
        t.c0.require(
            decoder.eof and not decoder.unused_data and len(raw) <= MAX_STREAM, "incomplete/oversize native gzip member"
        )
        (self.directory / (name + ".gz")).write_bytes(compressed)
        (self.directory / (name + ".stdout")).write_bytes(raw)
        return raw

    def close(self):
        try:
            if self.process.poll() is None:
                try:
                    self.process.stdin.write(b"ROLLBACK;\n\\q\n")
                    self.process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5)
        finally:
            self.selector.close()
            self.stderr.close()
        return {
            "native_exit": self.process.returncode,
            "commit_sent": self.commit_sent,
            "commit_verified": self.committed,
            "outcome": "committed" if self.committed else "unknown-commit" if self.commit_sent else "not-installed",
        }


def operator_source(revision):
    t.c0.require(re.fullmatch(r"[0-9a-f]{40}", revision) is not None, "exact reviewed operator source required")
    files = [
        "cnpg-retained-session-admission.py",
        "cnpg-target-runtime-transition.py",
        "cnpg-c0-restore-qualification.py",
        "cnpg-paired-restore.py",
        "cnpg-restore-role-parity.py",
        "c0-boundary-transition.py",
        "ordinary-boundary-diff.py",
        "check_migration_rollback_safety.py",
    ]
    hashes = {}
    for name in files:
        raw = (ROOT / "scripts" / name).read_bytes()
        committed = subprocess.check_output(["git", "show", revision + ":scripts/" + name], cwd=ROOT)
        t.c0.require(raw == committed, "operator bytes differ from reviewed source")
        hashes[name] = sha(raw)
    return {"source": revision, "module_sha256": hashes}


def role_export(binding, directory, label):
    guard = 'test "$VERDIFY_REHEARSAL_POD_UID" = "$1" || exit 42; shift; unset PGOPTIONS PGSERVICE PGSERVICEFILE PGPASSWORD PGPASSFILE; exec "$@"'
    command = t.operator.kube(
        "exec",
        binding["pod"],
        "-c",
        "postgres",
        "--",
        "sh",
        "-c",
        guard,
        "uid-guard",
        binding["pod_uid"],
        "pg_dumpall",
        "-h",
        "/controller/run",
        "-U",
        "postgres",
        "-l",
        "postgres",
        "--roles-only",
        "--no-role-passwords",
        "--no-comments",
        "--no-security-labels",
    )
    result = subprocess.run(command, capture_output=True, timeout=60)
    (directory / (label + "-roles-password-free.sql")).write_bytes(result.stdout)
    (directory / (label + "-roles-private.stderr")).write_bytes(result.stderr)
    t.c0.require(result.returncode == 0, "native password-free role export failed")
    return result.stdout.decode()


def bootstrap_return_sql():
    wrapped = t.bootstrap_owner_sql("\\set ON_ERROR_STOP on\nBEGIN;\nROLLBACK;\n")
    tail = wrapped[wrapped.index("RESET SESSION AUTHORIZATION;") :]
    return (
        tail
        + """SELECT jsonb_build_object('pid',pg_backend_pid(),
 'backend_start',(SELECT backend_start FROM pg_stat_activity WHERE pid=pg_backend_pid()),
 'postmaster_start',pg_postmaster_start_time(),'current_user',current_user,'session_user',session_user);
"""
    )


def install_custody_sql(installation):
    custody = (
        """DO $bootstrap_custody$ BEGIN
 IF current_user<>'verdify' OR session_user<>'verdify'
 OR """
        + t.bootstrap_facts_sql()
        + """ IS DISTINCT FROM current_setting('verdify.cnpg_bootstrap_before')::jsonb THEN
 RAISE EXCEPTION 'retained admission changed bootstrap/owner custody'; END IF;
 IF jsonb_build_object('roles',(SELECT jsonb_agg(to_jsonb(r)-'rolpassword' ORDER BY oid) FROM pg_roles r),
'memberships',(SELECT jsonb_agg(to_jsonb(m) ORDER BY oid) FROM pg_auth_members m))
IS DISTINCT FROM current_setting('verdify.cnpg_retained_roles_before')::jsonb THEN
 RAISE EXCEPTION 'retained admission changed password-free role posture'; END IF;
END $bootstrap_custody$;
"""
    )
    t.c0.require(installation.endswith("COMMIT;\n"), "install terminal required")
    return installation.removesuffix("COMMIT;\n") + custody + "COMMIT;\n"


def execute(args, source, binding, reference, roles):
    directory = args.receipt_dir
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    native_args = t.SimpleNamespace(**{k: binding[k] for k in ("cluster_uid", "pod", "pod_uid")})
    native_before = t.operator.read_target(native_args)
    t.c0.require(
        set(binding) == {"cluster_uid", "pod", "pod_uid", "operand_digest"}
        and binding["operand_digest"] == t.operator.DIGEST
        and native_before["cluster"]["status"]["currentPrimary"] == binding["pod"],
        "exact primary binding required",
    )
    for key in ("cluster_uid", "pod_uid"):
        uuid.UUID(binding[key])
    (directory / "native-binding-before.json").write_text(json.dumps(native_before, indent=2) + "\n")
    guard = 'test "$VERDIFY_REHEARSAL_POD_UID" = "$1" || exit 42; shift; unset PGOPTIONS PGSERVICE PGSERVICEFILE PGPASSWORD PGPASSFILE; exec timeout 900 "$@"'
    command = t.operator.kube(
        "exec",
        "-i",
        binding["pod"],
        "-c",
        "postgres",
        "--",
        "sh",
        "-c",
        guard,
        "uid-guard",
        binding["pod_uid"],
        "psql",
        "-X",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        "/controller/run",
        "-U",
        "postgres",
        "-d",
        t.DATABASE,
    )
    (directory / "operator-source.json").write_text(json.dumps(operator_source(args.operator_source), indent=2) + "\n")
    roles_before = role_export(binding, directory, "before")
    session = Session(command, directory)
    try:
        original = checked_custody(object_output(session.phase("begin", outer_begin()))[0])
        raw = session.phase("capture", capture_sql())
        before = object_output(raw)[0]
        t.c0.require(len(raw) <= t.c0.WITNESS_MAX_BYTES, "capture witness bound exceeded")
        t.validate_inputs(source, before, binding)
        t.role_parity.bootstrap_mapping(source, roles, before["bootstrap_identity"])
        mapping = t.c0.bootstrap_profile(source, before)
        role_parity_before = t.role_parity.verify(roles, roles_before, mapping=mapping)
        qualification = t.emit_sql(before, retained_session=True)
        actual, actual_raw = record(session.phase("qualification", qualification), "savepoint-rollback-qualification")
        (directory / "actual-native-qualification.json").write_bytes(actual_raw)
        checked_custody(object_output(session.phase("rollback-custody", rollback_proof(before)))[0], original)
        post = review(before, actual, reference)
        (directory / "independent-review.json").write_text(
            json.dumps(
                {
                    "full_checked_qualification": "PASS",
                    "previous_reviewed_fields_exact": True,
                    "actual_record_sha256": sha(actual_raw),
                    "ordinary_password_authentication": False,
                }
            )
            + "\n"
        )
        installation = t.emit_sql(
            before, reviewed_post=post, qualification_sha256=sha(actual_raw), retained_session=True
        )
        installation = install_custody_sql(installation)
        installed, installed_raw = record(session.phase("install", installation), "install")
        t.c0.require(
            installed["before_witness"] == before and installed["ddl_sha256"] == actual["ddl_sha256"],
            "installed predecessor/source changed",
        )
        t.validate_installed_post(before, post, installed["post_witness"])
        (directory / "actual-native-install.json").write_bytes(installed_raw)
        session.committed = True
        returned = object_output(session.phase("post-commit-bootstrap", bootstrap_return_sql()))[0]
        t.c0.require(
            returned["session_user"] == returned["current_user"] == "postgres"
            and all(returned[k] == original[k] for k in ("pid", "backend_start", "postmaster_start")),
            "native privileged session return/custody changed",
        )
        roles_after = role_export(binding, directory, "after")
        t.c0.require(
            t.role_parity.verify(roles, roles_after, mapping=mapping) == role_parity_before,
            "native role metadata changed",
        )
    finally:
        outcome = session.close()
        (directory / "terminal.json").write_text(json.dumps(outcome, indent=2) + "\n")
        native_after = t.operator.read_target(native_args)
        (directory / "native-binding-after.json").write_text(json.dumps(native_after, indent=2) + "\n")
        before_container = next(
            x for x in native_before["pod"]["status"]["containerStatuses"] if x["name"] == "postgres"
        )
        after_container = next(x for x in native_after["pod"]["status"]["containerStatuses"] if x["name"] == "postgres")
        t.c0.require(
            native_after["cluster"]["status"]["currentPrimary"] == binding["pod"]
            and before_container["containerID"] == after_container["containerID"]
            and before_container["restartCount"] == after_container["restartCount"],
            "native primary/container custody changed",
        )
    t.c0.require(outcome["commit_verified"], "retained admission not verified")
    return outcome


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "binding", "reviewed-reference", "source-roles"):
        parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--operator-source", required=True)
    parser.add_argument("--receipt-dir", type=Path, required=True)
    args = parser.parse_args()
    data = {}
    for name in ("source", "binding"):
        data[name], digest = t.c0.read_witness(getattr(args, name))
        t.c0.require(digest == getattr(args, name + "_sha256"), "input custody mismatch")
    reference, digest = t.read_transition_record(
        args.reviewed_reference, version=t.VERSION, mode="rollback-qualification"
    )
    t.c0.require(digest == args.reviewed_reference_sha256, "reviewed reference custody mismatch")
    roles = args.source_roles.read_bytes()
    t.c0.require(
        not args.source_roles.is_symlink() and len(roles) <= 1_000_000 and sha(roles) == args.source_roles_sha256,
        "password-free source roles custody mismatch",
    )
    t.c0.require(args.execute, "explicit isolated-target execution required")
    print(json.dumps(execute(args, data["source"], data["binding"], reference, roles.decode())))


if __name__ == "__main__":
    main()
