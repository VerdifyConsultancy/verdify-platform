#!/usr/bin/env python3
"""Render two new reader-key-only PITR Clusters from captured native custody.

Offline only: validates supplied evidence, never acquires credentials or applies
objects. A render is not proof that input captures are genuine or recovery works.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import yaml

ROOT = Path(__file__).resolve().parents[1]
NS = "verdify-db-rehearsal"
SOURCE = "verdify-cnpg-rehearsal"
SOURCE_UID = "e11f1014-a77e-4ccf-9d97-e8cf5c037484"
IMAGE = (
    "registry.vallery.net/verdifyconsultancy/verdify-timescaledb-cnpg:16.13-ts2.25.2@sha256:"
    "8b461e37d18aa049704eb6a9cde2ba9af0f955f8d3725e921070d2450bb2f137"
)
PLUGIN = "barman-cloud.cloudnative-pg.io"
STORE = "verdify-cnpg-pitr-reader"
SCHEMA = "verdify-cnpg-pitr-marker-custody-v1"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def utc(value):
    require(isinstance(value, str), "missing native UTC timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0, "timestamps must carry explicit UTC")
    return parsed.astimezone(UTC)


def uid(value):
    require(isinstance(value, str) and str(UUID(value)) == value, "missing exact native UID")
    return value


def lsn(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9A-F]+/[0-9A-F]+", value), "invalid native LSN")
    hi, lo = value.split("/")
    require(int(lo, 16) < 2**32, "invalid native LSN low word")
    return int(hi, 16) * 2**32 + int(lo, 16)


def render(cluster, backup, custody, scheduled_backup=None):
    require(cluster.get("kind") == "Cluster", "expected raw native source Cluster")
    meta = cluster["metadata"]
    require(
        (meta["namespace"], meta["name"], meta["uid"]) == (NS, SOURCE, SOURCE_UID),
        "wrong source namespace/name/UID; this profile is not portable",
    )
    require(cluster["spec"]["imageName"] == IMAGE, "source operand drift")
    require(backup.get("kind") == "Backup", "expected raw native Backup")
    bm, bs, status = backup["metadata"], backup["spec"], backup.get("status", {})
    uid(bm["uid"])
    require(bm["namespace"] == NS and bs["cluster"]["name"] == SOURCE, "wrong Backup source")
    owners = bm.get("ownerReferences", [])
    direct_owner = any(
        o.get("kind") == "Cluster" and o.get("name") == SOURCE and o.get("uid") == SOURCE_UID for o in owners
    )
    if not direct_owner:
        require(
            scheduled_backup is not None and scheduled_backup.get("kind") == "ScheduledBackup",
            "Backup needs exact source owner UID or captured ScheduledBackup owner lineage",
        )
        sm, ss = scheduled_backup["metadata"], scheduled_backup["spec"]
        uid(sm["uid"])
        require(
            sm["namespace"] == NS
            and ss["cluster"]["name"] == SOURCE
            and ss["method"] == "plugin"
            and ss["pluginConfiguration"]["name"] == PLUGIN,
            "ScheduledBackup source/method mismatch",
        )
        require(
            any(
                o.get("kind") == "ScheduledBackup" and o.get("name") == sm["name"] and o.get("uid") == sm["uid"]
                for o in owners
            ),
            "ScheduledBackup owner UID mismatch",
        )
        require(
            utc(meta["creationTimestamp"]) < utc(status["startedAt"]), "Backup predates the current source Cluster UID"
        )
    require(
        bs.get("method") == "plugin" and bs.get("pluginConfiguration", {}).get("name") == PLUGIN,
        "wrong native backup method/plugin",
    )
    require(status.get("phase") == "completed", "native Backup is not completed")
    backup_id = status.get("backupId")
    require(
        isinstance(backup_id, str) and re.fullmatch(r"[0-9]{8}T[0-9]{6}", backup_id), "missing actual Barman backupId"
    )
    start, end = utc(status["startedAt"]), utc(status["stoppedAt"])
    require(start < end, "invalid backup interval")
    for field in ("beginWal", "endWal"):
        require(re.fullmatch(r"[0-9A-F]{24}", status.get(field, "")) is not None, "missing native backup WAL bounds")
    require(custody.get("schema") == SCHEMA, "wrong marker custody schema")
    require(
        custody.get("source")
        == {
            "namespace": NS,
            "cluster_name": SOURCE,
            "cluster_uid": SOURCE_UID,
            "image": IMAGE,
            "server_version_num": 160013,
            "database": "rehearsal_bootstrap",
        },
        "marker source identity mismatch",
    )
    require(
        custody.get("backup_uid") == bm["uid"] and custody.get("backup_id") == backup_id,
        "marker/Backup custody mismatch",
    )
    uid(custody["primary_pod_uid"])
    timeline = custody["timeline"]
    require(type(timeline) is int and 0 < timeline < 2**32, "missing exact numeric source timeline")
    require(
        int(status["beginWal"][:8], 16) == timeline and int(status["endWal"][:8], 16) == timeline,
        "backup and marker timeline differ",
    )
    markers = custody["markers"]
    require(set(markers) == {"A", "B", "C"}, "A/B and retained crossing transaction C are required")
    previous_time, previous_lsn = end, 0
    xids, ids = set(), set()
    for name in ("A", "B", "C"):
        marker = markers[name]
        began, ack = utc(marker["transaction_started_at"]), utc(marker["acknowledged_at"])
        require(previous_time < began < ack, "native backup/marker UTC order is not strict")
        require(marker.get("timeline") == timeline, "marker timeline drift")
        require(marker.get("primary_pod_uid") == custody["primary_pod_uid"], "marker primary UID drift")
        xid = marker["xid"]
        require(
            isinstance(xid, str) and xid.isdecimal() and 0 < int(xid) < 2**32,
            "expected native xid32; do not substitute an epoch-qualified xid8",
        )
        marker_id = marker["marker_id"]
        require(
            isinstance(marker_id, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{7,95}", marker_id),
            "missing unique native marker ID",
        )
        require(xid not in xids and marker_id not in ids, "duplicate marker transaction/identity")
        xids.add(xid)
        ids.add(marker_id)
        position = lsn(marker["acknowledged_flush_lsn"])
        require(position > previous_lsn, "marker acknowledged flush LSN is not increasing")
        require(
            isinstance(marker.get("capture_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", marker["capture_sha256"]),
            "missing raw capture hash",
        )
        previous_time, previous_lsn = ack, position
    targets = custody["targets"]
    require(set(targets) == {"A", "B"}, "two distinct native targets required")
    require(
        utc(markers["A"]["acknowledged_at"]) < utc(targets["A"]) < utc(markers["B"]["transaction_started_at"]),
        "target A must be a captured server-clock boundary strictly between A and B",
    )
    require(
        utc(markers["B"]["acknowledged_at"]) < utc(targets["B"]) < utc(markers["C"]["transaction_started_at"]),
        "target B must be crossed by a later genuine retained transaction C",
    )
    spec = importlib.util.spec_from_file_location("rehearsal_render", ROOT / "scripts/render-cnpg-rehearsal.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    base = next(o for o in module.render(IMAGE) if o["kind"] == "Cluster")
    reader = yaml.safe_load((ROOT / "deploy/k8s/cnpg/rehearsal/cluster/object-store.yaml").read_text())
    reader["metadata"]["name"] = STORE
    reader["spec"].pop("retentionPolicy", None)
    credentials = reader["spec"]["configuration"]["s3Credentials"]
    for key in ("accessKeyId", "secretAccessKey"):
        credentials[key]["name"] = "verdify-cnpg-rehearsal-s3-reader"
    objects = [reader]
    for name in ("A", "B"):
        restored = copy.deepcopy(base)
        restored["metadata"]["name"] = "verdify-cnpg-pitr-" + name.lower()
        restored["metadata"]["annotations"] = {
            "verdify.ai/pitr-source-cluster-uid": SOURCE_UID,
            "verdify.ai/pitr-source-backup-uid": bm["uid"],
        }
        restored["spec"].pop("plugins")  # No archive writer on either recovery Cluster.
        restored["spec"]["bootstrap"] = {
            "recovery": {
                "source": "original-rehearsal",
                "database": "rehearsal_bootstrap",
                "owner": "rehearsal_bootstrap",
                "recoveryTarget": {
                    "backupID": backup_id,
                    "targetTime": targets[name],
                    "targetTLI": str(timeline),
                    "exclusive": False,
                },
            }
        }
        restored["spec"]["externalClusters"] = [
            {
                "name": "original-rehearsal",
                "plugin": {"name": PLUGIN, "parameters": {"barmanObjectName": STORE, "serverName": SOURCE}},
            }
        ]
        objects.append(restored)
    return objects


def validate_captures(custody, directory):
    """Bind all supplied native transaction/clock capture bytes before rendering."""
    for name, marker in custody["markers"].items():
        raw = (directory / (name + ".json")).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == marker["capture_sha256"], "raw marker capture hash mismatch")
        expected = {
            "source": custody["source"],
            "marker": {key: value for key, value in marker.items() if key != "capture_sha256"},
        }
        require(json.loads(raw) == expected, "raw marker capture/content mismatch")
    raw = (directory / "target-boundaries.json").read_bytes()
    require(hashlib.sha256(raw).hexdigest() == custody["target_capture_sha256"], "raw target clock hash mismatch")
    require(
        json.loads(raw)
        == {
            "source": custody["source"],
            "primary_pod_uid": custody["primary_pod_uid"],
            "timeline": custody["timeline"],
            "targets": custody["targets"],
        },
        "raw target clock/content mismatch",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--scheduled-backup", type=Path, help="raw ScheduledBackup if it owns the native Backup")
    parser.add_argument("--markers", type=Path, required=True)
    parser.add_argument(
        "--captures", type=Path, required=True, help="A.json/B.json/C.json and target-boundaries.json native captures"
    )
    parser.add_argument("--out", type=Path, required=True, help="new exclusive custody directory")
    args = parser.parse_args()
    paths = {"cluster": args.cluster, "backup": args.backup, "markers": args.markers}
    if args.scheduled_backup:
        paths["scheduled_backup"] = args.scheduled_backup
    raw = {key: path.read_bytes() for key, path in paths.items()}
    custody = json.loads(raw["markers"])
    validate_captures(custody, args.captures)
    objects = render(
        json.loads(raw["cluster"]),
        json.loads(raw["backup"]),
        custody,
        json.loads(raw["scheduled_backup"]) if "scheduled_backup" in raw else None,
    )
    args.out.mkdir(mode=0o700)  # Refuse replacing any earlier render/failure custody.
    rendered = yaml.safe_dump_all(objects, sort_keys=False).encode()
    (args.out / "recovery-pair.yaml").write_bytes(rendered)
    (args.out / "render-custody.json").write_text(
        json.dumps(
            {
                "phase": "render-only",
                "recovery_proven": False,
                "inputs": {
                    key: {"path": str(paths[key].resolve()), "sha256": hashlib.sha256(value).hexdigest()}
                    for key, value in raw.items()
                },
                "render_sha256": hashlib.sha256(rendered).hexdigest(),
                "required_live_checks": [
                    "source/Backup/marker UID freshness",
                    "new target names and PVCs absent",
                    "reader-only permissions",
                    "new storage capacity and physical domains",
                    "native target reached",
                    "A-only versus A+B and no C",
                    "new-cluster C0 admission",
                    "real ordinary role auth and hot SQL",
                ],
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
