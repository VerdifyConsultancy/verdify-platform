#!/usr/bin/env python3
"""Render a disposable, network-denied Job for one exact backup pair."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_NAMES = (
    "verify-backup-pair.sh",
    "restore-backup-pair.sh",
    "logical-restore-audit.sql",
    "check-timescale-ownership.sql",
    "test-timescale-parent-owner.sql",
)


def render(stem: str, run_id: str) -> list[dict]:
    if not re.fullmatch(r"verdify-[0-9]{8}T[0-9]{6}Z", stem):
        raise ValueError("backup stem must be verdify-YYYYMMDDTHHMMSSZ")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,15}", run_id):
        raise ValueError("run id must be 1-16 lowercase letters, digits or hyphens")
    name = f"verdify-backup-pair-restore-{run_id}"
    component = f"backup-pair-restore-{run_id}"
    labels = {"app.kubernetes.io/part-of": "verdify", "app.kubernetes.io/component": component}
    scripts = {script: (ROOT / "scripts" / script).read_text() for script in SCRIPT_NAMES}
    source_digest = hashlib.sha256("".join(scripts.values()).encode()).hexdigest()
    rehearsal_job = yaml.safe_load(
        (ROOT / "deploy/k8s/components/experiment-v2-restore-rehearsal/restore-rehearsal-job.yaml").read_text()
    )
    image = rehearsal_job["spec"]["template"]["spec"]["containers"][0]["image"]
    configmap = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": f"{name}-scripts", "namespace": "verdify-prod", "labels": labels},
        "data": scripts,
    }
    policy = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": f"{name}-deny-all", "namespace": "verdify-prod", "labels": labels},
        "spec": {"podSelector": {"matchLabels": labels}, "policyTypes": ["Ingress", "Egress"]},
    }
    env = {
        "BACKUP_DIR": "/backups",
        "BACKUP_STEM": stem,
        "PGDATA": "/restore/pgdata",
        "PGHOST": "/run/postgresql",
        "PGPORT": "55432",
        "PGDATABASE": "verdify_rehearsal",
        "VERIFY_SCRIPT": "/scripts/verify-backup-pair.sh",
        "AUDIT_SQL": "/scripts/logical-restore-audit.sql",
        "OWNERSHIP_SQL": "/scripts/check-timescale-ownership.sql",
        "OWNER_REPAIR_TEST_SQL": "/scripts/test-timescale-parent-owner.sql",
    }
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": "verdify-prod",
            "labels": labels,
            "annotations": {"verdify.ai/restore-script-sha256": source_digest},
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 3600,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 999,
                        "runAsGroup": 999,
                        "fsGroup": 999,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "restore-and-audit",
                            "image": image,
                            "command": ["/bin/bash", "/scripts/restore-backup-pair.sh"],
                            "env": [{"name": key, "value": value} for key, value in env.items()],
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {"cpu": "500m", "memory": "1Gi"},
                                "limits": {"cpu": "2", "memory": "4Gi"},
                            },
                            "volumeMounts": [
                                {"name": "dumps", "mountPath": "/backups", "readOnly": True},
                                {"name": "restore-data", "mountPath": "/restore"},
                                {"name": "postgres-run", "mountPath": "/run/postgresql"},
                                {"name": "tmp", "mountPath": "/tmp"},  # noqa: S108 - pod emptyDir mount
                                {"name": "scripts", "mountPath": "/scripts", "readOnly": True},
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "dumps", "persistentVolumeClaim": {"claimName": "verdify-db-dumps", "readOnly": True}},
                        {"name": "restore-data", "emptyDir": {}},
                        {"name": "postgres-run", "emptyDir": {}},
                        {"name": "tmp", "emptyDir": {}},
                        {"name": "scripts", "configMap": {"name": f"{name}-scripts", "defaultMode": 0o555}},
                    ],
                },
            },
        },
    }
    return [configmap, policy, job]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-stem", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    print(yaml.safe_dump_all(render(args.backup_stem, args.run_id), sort_keys=False), end="")


if __name__ == "__main__":
    main()
