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


def target_identity(cluster, pod, *, cluster_uid, pod_uid):
    c0.require(
        cluster["metadata"]["name"] == CLUSTER
        and cluster["metadata"]["namespace"] == NS
        and cluster["metadata"]["uid"] == cluster_uid,
        "cluster identity mismatch",
    )
    c0.require(cluster["spec"]["imageName"].endswith("@" + DIGEST), "unqualified operand")
    c0.require(pod["metadata"]["namespace"] == NS and pod["metadata"]["uid"] == pod_uid, "pod identity mismatch")
    c0.require(pod["metadata"]["labels"].get("cnpg.io/cluster") == CLUSTER, "wrong cluster label")
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
    cluster = json.loads(subprocess.check_output(kube("get", "cluster", CLUSTER, "-o", "json"), timeout=30))
    pod = json.loads(subprocess.check_output(kube("get", "pod", args.pod, "-o", "json"), timeout=30))
    target_identity(cluster, pod, cluster_uid=args.cluster_uid, pod_uid=args.pod_uid)
    return {"cluster": cluster, "pod": pod}


def regular(path):
    c0.require(path.is_file() and not path.is_symlink(), "regular custody file required")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster-uid", required=True)
    parser.add_argument("--pod", required=True)
    parser.add_argument("--pod-uid", required=True)
    parser.add_argument("--pair-dir", required=True, type=Path)
    parser.add_argument("--stem", required=True)
    parser.add_argument("--source-witness", required=True, type=Path)
    parser.add_argument("--source-witness-sha256", required=True)
    parser.add_argument("--receipt-dir", required=True, type=Path)
    parser.add_argument("--bash", default="/opt/homebrew/bin/bash")
    args = parser.parse_args()
    c0.require(re.fullmatch(r"verdify-\d{8}T\d{6}Z", args.stem), "invalid pair identity")
    c0.require(re.fullmatch(r"verdify-cnpg-rehearsal-[1-9]\d*", args.pod), "invalid rehearsal pod")
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
        "production_endpoint_changed": False,
    }
    (args.receipt_dir / "custody-before.json").write_text(json.dumps(receipt, indent=2) + "\n")
    manifest = args.receipt_dir / "custody.sha256"
    manifest.write_text("".join(f"{sha256}  {name}\n" for name, sha256 in sorted(hashes.items())))
    files["custody.sha256"] = manifest
    exec_args = kube("exec", "-i", args.pod, "-c", "postgres", "--")
    # A downward-API UID comparison is performed inside the selected pod, not
    # merely a name-based kubectl check susceptible to ordinal replacement.
    guard = 'test "$VERDIFY_REHEARSAL_POD_UID" = "$1" || exit 42; shift; exec "$@"'
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
                f"umask 077; mkdir {STAGE} && tar -xf - -C {STAGE}",
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
            + ["sh", "-c", guard, "uid-guard", args.pod_uid, "sh", "-c", f"cd {STAGE} && sha256sum -c custody.sha256"],
            check=True,
            stdout=out,
            stderr=err,
            timeout=120,
        )
    env = {
        "RESTORE_SERVER_MODE": "cnpg",
        "BACKUP_DIR": STAGE + "/backups",
        "BACKUP_STEM": args.stem,
        "PGDATA": "/var/lib/postgresql/data/pgdata",
        "PGHOST": "/controller/run",
        "PGPORT": "5432",
        "PGDATABASE": "verdify_rehearsal",
        "VERIFY_SCRIPT": STAGE + "/scripts/verify-backup-pair.sh",
        "AUDIT_SQL": STAGE + "/scripts/logical-restore-audit.sql",
        "OWNERSHIP_SQL": STAGE + "/scripts/check-timescale-ownership.sql",
        "OWNER_REPAIR_TEST_SQL": STAGE + "/scripts/test-timescale-parent-owner.sql",
        "RESTORED_OWNER_TEST_SQL": STAGE + "/scripts/test-restored-timescale-parent-owner.sql",
        "V2_INTERFACE_SQL": STAGE + "/scripts/qualify-v2-restored-interface.sql",
        "CNPG_ROLE_HELPER": STAGE + "/scripts/cnpg-restore-role-parity.py",
        "CNPG_ACL_HELPER": STAGE + "/scripts/cnpg-source-database-acl.py",
        "CNPG_SOURCE_WITNESS": STAGE + "/source-witness.json",
        "CNPG_SOURCE_WITNESS_SHA256": sha,
        "PGOPTIONS": "-c statement_timeout=180000 -c lock_timeout=10000",
    }
    command = [
        "env",
        *[f"{key}={value}" for key, value in env.items()],
        "timeout",
        "1800",
        "bash",
        STAGE + "/scripts/restore-backup-pair.sh",
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
