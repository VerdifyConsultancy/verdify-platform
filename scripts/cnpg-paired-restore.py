"""Run one verified pair import on the exact isolated CNPG primary UID.

No namespace/cluster/Secret/PVC creation, source DB connection, retry, reseal,
production endpoint change or role password provisioning occurs here. Partial
imports and their on-PVC custody are retained on failure.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NS = "verdify-db-rehearsal"
CLUSTER = "verdify-cnpg-rehearsal"
DIGEST = "sha256:8b461e37d18aa049704eb6a9cde2ba9af0f955f8d3725e921070d2450bb2f137"
STAGE = "/var/lib/postgresql/data/restore-custody"
SCRIPTS = (
    "verify-backup-pair.sh",
    "restore-backup-pair.sh",
    "logical-restore-audit.sql",
    "check-timescale-ownership.sql",
    "test-timescale-parent-owner.sql",
    "test-restored-timescale-parent-owner.sql",
    "qualify-v2-restored-interface.sql",
    "ordinary-boundary-diff.py",
    "cnpg-c0-restore-qualification.py",
    "cnpg-restore-role-parity.py",
    "cnpg-source-database-acl.py",
)
MIGRATIONS = ("217-runtime-role-boundary.sql", "263-mcp-timescale-chunk-boundary-digest.sql")
spec = importlib.util.spec_from_file_location("c0_witness", ROOT / "scripts/cnpg-c0-restore-qualification.py")
c0 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c0)


def kube(*args):
    return ["kubectl", "--context", "vallery", "-n", NS, *args]


def target_identity(cluster, pod, *, cluster_uid, pod_uid, cluster_name=CLUSTER):
    c0.require(cluster_name in (CLUSTER, "verdify-cnpg-s2"), "unsupported isolated restore profile")
    c0.require(
        cluster["metadata"]["name"] == cluster_name
        and cluster["metadata"]["namespace"] == NS
        and cluster["metadata"]["uid"] == cluster_uid,
        "cluster identity mismatch",
    )
    c0.require(cluster["spec"]["imageName"].endswith("@" + DIGEST), "unqualified operand")
    c0.require(pod["metadata"]["namespace"] == NS and pod["metadata"]["uid"] == pod_uid, "pod identity mismatch")
    c0.require(pod["metadata"]["labels"].get("cnpg.io/cluster") == cluster_name, "wrong cluster label")
    c0.require(
        any(o["kind"] == "Cluster" and o["uid"] == cluster_uid for o in pod["metadata"]["ownerReferences"]),
        "wrong pod owner",
    )
    status = [s for s in pod["status"]["containerStatuses"] if s["name"] == "postgres"]
    c0.require(
        len(status) == 1 and status[0]["ready"] is True and status[0]["imageID"].endswith("@" + DIGEST),
        "operand not adopted/ready",
    )


def read_target(args):
    cluster = json.loads(subprocess.check_output(kube("get", "cluster", args.cluster_name, "-o", "json"), timeout=30))
    pod = json.loads(subprocess.check_output(kube("get", "pod", args.pod, "-o", "json"), timeout=30))
    target_identity(cluster, pod, cluster_uid=args.cluster_uid, pod_uid=args.pod_uid, cluster_name=args.cluster_name)
    return {"cluster": cluster, "pod": pod}


def regular(path):
    c0.require(path.is_file() and not path.is_symlink(), "regular custody file required")
    return path


def stage_path(name, prior_hash, management_path, management_hash):
    if name is None:
        c0.require(not any((prior_hash, management_path, management_hash)), "unexpected attempt predecessor")
        return STAGE
    c0.require(re.fullmatch(r"restore-custody-[a-z0-9]{8,32}", name), "invalid new custody directory")
    c0.require(
        transaction_hash(prior_hash) and transaction_hash(management_hash),
        "new attempt requires exact predecessor hashes",
    )
    c0.require(management_path is not None, "new attempt management custody required")
    return "/var/lib/postgresql/data/" + name


def transaction_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster-name", choices=(CLUSTER, "verdify-cnpg-s2"), default=CLUSTER)
    parser.add_argument("--cluster-uid", required=True)
    parser.add_argument("--pod", required=True)
    parser.add_argument("--pod-uid", required=True)
    parser.add_argument("--pair-dir", required=True, type=Path)
    parser.add_argument("--stem", required=True)
    parser.add_argument("--source-witness", required=True, type=Path)
    parser.add_argument("--source-witness-sha256", required=True)
    parser.add_argument("--receipt-dir", required=True, type=Path)
    parser.add_argument("--stage-name")
    parser.add_argument("--prior-custody-manifest-sha256")
    parser.add_argument("--management-before", type=Path)
    parser.add_argument("--management-before-sha256")
    parser.add_argument("--bootstrap-grantor-profile", action="store_true")
    parser.add_argument("--role-prefix-custody", type=Path)
    parser.add_argument("--role-prefix-custody-sha256")
    parser.add_argument("--role-prefix-current", type=Path)
    parser.add_argument("--bash", default="/opt/homebrew/bin/bash")
    args = parser.parse_args()
    stage = stage_path(
        args.stage_name, args.prior_custody_manifest_sha256, args.management_before, args.management_before_sha256
    )
    if args.management_before:
        raw_management = regular(args.management_before).read_bytes()
        c0.require(
            hashlib.sha256(raw_management).hexdigest() == args.management_before_sha256, "management custody mismatch"
        )
    prefix = None
    if args.role_prefix_custody:
        c0.require(args.stage_name and args.role_prefix_current, "new exclusive prefix continuation custody required")
        prefix_raw = regular(args.role_prefix_custody).read_bytes()
        c0.require(
            hashlib.sha256(prefix_raw).hexdigest() == args.role_prefix_custody_sha256,
            "partial descriptor custody mismatch",
        )
        role_spec = importlib.util.spec_from_file_location("role_custody", ROOT / "scripts/cnpg-restore-role-parity.py")
        role_helper = importlib.util.module_from_spec(role_spec)
        role_spec.loader.exec_module(role_helper)
        prefix = role_helper.prefix_descriptor(prefix_raw)
        c0.require(prefix["stage_name"] != args.stage_name, "partial custody reuse refused")
        c0.require(
            hashlib.sha256(regular(args.role_prefix_current).read_bytes()).hexdigest() == prefix["current_sha256"],
            "partial current artifact mismatch",
        )
    else:
        c0.require(
            not args.role_prefix_current and not args.role_prefix_custody_sha256, "unexpected partial continuation"
        )
    c0.require(re.fullmatch(r"verdify-\d{8}T\d{6}Z", args.stem), "invalid pair identity")
    c0.require(re.fullmatch(re.escape(args.cluster_name) + r"-[1-9]\d*", args.pod), "invalid rehearsal pod")
    for uid in (args.cluster_uid, args.pod_uid):
        c0.require(re.fullmatch(r"[0-9a-f-]{36}", uid), "invalid UID")
    source, sha = c0.read_witness(args.source_witness)
    c0.require(sha == args.source_witness_sha256, "source witness custody mismatch")
    c0.checked(source, target=False)
    subprocess.run(
        [args.bash, str(ROOT / "scripts/verify-backup-pair.sh"), str(args.pair_dir), args.stem],
        check=True,
        stdout=subprocess.DEVNULL,
        timeout=120,
    )
    os.umask(0o077)
    args.receipt_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    files = {
        f"backups/{args.stem}.{suffix}": regular(args.pair_dir / f"{args.stem}.{suffix}")
        for suffix in ("dump", "roles.sql", "sha256")
    }
    files["source-witness.json"] = regular(args.source_witness)
    if args.management_before:
        files["management-before.sql"] = regular(args.management_before)
    if prefix:
        files["role-prefix-custody.json"] = regular(args.role_prefix_custody)
        files["role-prefix-current.sql"] = regular(args.role_prefix_current)
    files.update({f"scripts/{name}": regular(ROOT / "scripts" / name) for name in SCRIPTS})
    files.update({f"db/migrations/{name}": regular(ROOT / "db/migrations" / name) for name in MIGRATIONS})
    hashes = {}
    for name, path in files.items():
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        hashes[name] = digest.hexdigest()
    receipt = {
        "source_witness_sha256": sha,
        "files": hashes,
        "before": read_target(args),
        "runtime_transition_installed": False,
        "stage": stage,
        "prior_custody_manifest_sha256": args.prior_custody_manifest_sha256,
        "management_before_sha256": args.management_before_sha256,
        "production_endpoint_changed": False,
        "role_prefix_custody": prefix,
        "bootstrap_grantor_profile": "cnpg-source-bootstrap-grantor-v1" if args.bootstrap_grantor_profile else None,
    }
    (args.receipt_dir / "custody-before.json").write_text(json.dumps(receipt, indent=2) + "\n")
    manifest = args.receipt_dir / "custody.sha256"
    manifest.write_text("".join(f"{sha256}  {name}\n" for name, sha256 in sorted(hashes.items())))
    files["custody.sha256"] = manifest
    exec_args = kube("exec", "-i", args.pod, "-c", "postgres", "--")
    # A downward-API UID comparison is performed inside the selected pod, not
    # merely a name-based kubectl check susceptible to ordinal replacement.
    guard = 'test "$VERDIFY_REHEARSAL_POD_UID" = "$1" || exit 42; shift; exec "$@"'
    if args.stage_name:
        # Read and revalidate the entire original staged custody before allocating a new path.
        predecessor = f'test "$(cd {STAGE} && pwd -P)" = {STAGE} && test "$(sha256sum {STAGE}/custody.sha256 | cut -d\' \' -f1)" = {args.prior_custody_manifest_sha256} && cd {STAGE} && sha256sum -c custody.sha256'
        with (
            (args.receipt_dir / "prior-custody.stdout").open("wb") as out,
            (args.receipt_dir / "prior-custody.stderr").open("wb") as err,
        ):
            subprocess.run(
                exec_args + ["sh", "-c", guard, "uid-guard", args.pod_uid, "sh", "-c", predecessor],
                check=True,
                stdout=out,
                stderr=err,
                timeout=120,
            )
    if prefix:
        old = "/var/lib/postgresql/data/" + prefix["stage_name"]
        checks = [
            f'test "$(cd {old} && pwd -P)" = {old}',
            f"test \"$(sha256sum {old}/custody.sha256 | cut -d' ' -f1)\" = {prefix['manifest_sha256']}",
            f"cd {old} && sha256sum -c custody.sha256",
        ]
        for filename, field in [
            ("roles.before.sql", "before_sha256"),
            ("roles.replay.sql", "replay_sha256"),
            ("roles.stderr", "error_sha256"),
        ]:
            checks.append(f"test \"$(sha256sum {old}/work/{filename} | cut -d' ' -f1)\" = {prefix[field]}")
        with (
            (args.receipt_dir / "partial-custody.stdout").open("wb") as out,
            (args.receipt_dir / "partial-custody.stderr").open("wb") as err,
        ):
            subprocess.run(
                exec_args + ["sh", "-c", guard, "uid-guard", args.pod_uid, "sh", "-c", " && ".join(checks)],
                check=True,
                stdout=out,
                stderr=err,
                timeout=120,
            )
    with (args.receipt_dir / "stage.stdout").open("wb") as out, (args.receipt_dir / "stage.stderr").open("wb") as err:
        proc = subprocess.Popen(
            exec_args
            + [
                "sh",
                "-c",
                guard,
                "uid-guard",
                args.pod_uid,
                "sh",
                "-c",
                f"umask 077; mkdir {stage} && tar -xf - -C {stage}",
            ],
            stdin=subprocess.PIPE,
            stdout=out,
            stderr=err,
        )
        try:
            with tarfile.open(fileobj=proc.stdin, mode="w|") as archive:
                for name, path in files.items():
                    archive.add(path, arcname=name, recursive=False)
            proc.stdin.close()
            c0.require(proc.wait(timeout=600) == 0, "custody staging failed; retained, no retry")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    read_target(args)
    with (
        (args.receipt_dir / "stage-verify.stdout").open("wb") as out,
        (args.receipt_dir / "stage-verify.stderr").open("wb") as err,
    ):
        subprocess.run(
            exec_args
            + ["sh", "-c", guard, "uid-guard", args.pod_uid, "sh", "-c", f"cd {stage} && sha256sum -c custody.sha256"],
            check=True,
            stdout=out,
            stderr=err,
            timeout=120,
        )
    env = {
        "RESTORE_SERVER_MODE": "cnpg",
        "CNPG_RESTORE_CUSTODY": stage,
        "RESTORE_WORK_DIR": stage + "/work",
        "BACKUP_DIR": stage + "/backups",
        "BACKUP_STEM": args.stem,
        "PGDATA": "/var/lib/postgresql/data/pgdata",
        "PGHOST": "/controller/run",
        "PGPORT": "5432",
        "PGDATABASE": "verdify_rehearsal",
        "VERIFY_SCRIPT": stage + "/scripts/verify-backup-pair.sh",
        "AUDIT_SQL": stage + "/scripts/logical-restore-audit.sql",
        "OWNERSHIP_SQL": stage + "/scripts/check-timescale-ownership.sql",
        "OWNER_REPAIR_TEST_SQL": stage + "/scripts/test-timescale-parent-owner.sql",
        "RESTORED_OWNER_TEST_SQL": stage + "/scripts/test-restored-timescale-parent-owner.sql",
        "V2_INTERFACE_SQL": stage + "/scripts/qualify-v2-restored-interface.sql",
        "CNPG_ROLE_HELPER": stage + "/scripts/cnpg-restore-role-parity.py",
        "CNPG_ACL_HELPER": stage + "/scripts/cnpg-source-database-acl.py",
        "CNPG_SOURCE_WITNESS": stage + "/source-witness.json",
        "CNPG_SOURCE_WITNESS_SHA256": sha,
        "PGOPTIONS": "-c statement_timeout=180000 -c lock_timeout=10000",
    }
    if args.management_before:
        env["CNPG_MANAGEMENT_BEFORE"] = stage + "/management-before.sql"
        env["CNPG_MANAGEMENT_BEFORE_SHA256"] = args.management_before_sha256
    if args.bootstrap_grantor_profile:
        env["CNPG_BOOTSTRAP_GRANTOR_PROFILE"] = "cnpg-source-bootstrap-grantor-v1"
    if prefix:
        env["CNPG_ROLE_PREFIX_CUSTODY"] = stage + "/role-prefix-custody.json"
        env["CNPG_ROLE_PREFIX_CURRENT"] = stage + "/role-prefix-current.sql"
    command = [
        "env",
        *[f"{key}={value}" for key, value in env.items()],
        "timeout",
        "1800",
        "bash",
        stage + "/scripts/restore-backup-pair.sh",
    ]
    with (
        (args.receipt_dir / "restore.stdout").open("wb") as out,
        (args.receipt_dir / "restore.stderr").open("wb") as err,
    ):
        result = subprocess.run(
            exec_args + ["sh", "-c", guard, "uid-guard", args.pod_uid, *command], stdout=out, stderr=err, timeout=1900
        )
    (args.receipt_dir / "restore-result.json").write_text(
        json.dumps(
            {"exit_code": result.returncode, "after": read_target(args), "runtime_transition_installed": False},
            indent=2,
        )
        + "\n"
    )
    c0.require(result.returncode == 0, "actual restore failed; custody retained, no retry")


if __name__ == "__main__":
    main()
