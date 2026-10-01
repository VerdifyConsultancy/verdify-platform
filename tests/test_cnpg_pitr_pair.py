"""Refusal tests only; fabricated fixture records never qualify actual recovery."""

import copy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pitr_pair", ROOT / "scripts/render-cnpg-pitr-pair.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixture():
    backup_uid = "12345678-1234-4234-8234-123456789abc"
    pod_uid = "12345678-1234-4234-8234-123456789abd"
    cluster = {
        "kind": "Cluster",
        "metadata": {"name": m.SOURCE, "namespace": m.NS, "uid": m.SOURCE_UID},
        "spec": {"imageName": m.IMAGE},
    }
    backup = {
        "kind": "Backup",
        "metadata": {
            "name": "fixture-backup",
            "namespace": m.NS,
            "uid": backup_uid,
            "ownerReferences": [{"kind": "Cluster", "name": m.SOURCE, "uid": m.SOURCE_UID}],
        },
        "spec": {"cluster": {"name": m.SOURCE}, "method": "plugin", "pluginConfiguration": {"name": m.PLUGIN}},
        "status": {
            "phase": "completed",
            "backupId": "20261001T100000",
            "startedAt": "2026-10-01T10:00:00Z",
            "stoppedAt": "2026-10-01T10:01:00Z",
            "beginWal": "000000010000000000000001",
            "endWal": "000000010000000000000002",
        },
    }
    custody = {
        "schema": m.SCHEMA,
        "source": {
            "namespace": m.NS,
            "cluster_name": m.SOURCE,
            "cluster_uid": m.SOURCE_UID,
            "image": m.IMAGE,
            "server_version_num": 160013,
            "database": "rehearsal_bootstrap",
        },
        "backup_uid": backup_uid,
        "backup_id": "20261001T100000",
        "primary_pod_uid": pod_uid,
        "timeline": 1,
        "targets": {"A": "2026-10-01T10:03:00Z", "B": "2026-10-01T10:05:00Z"},
        "markers": {},
    }
    for index, name in enumerate(("A", "B", "C")):
        minute = 2 + index * 2
        custody["markers"][name] = {
            "transaction_started_at": f"2026-10-01T10:{minute:02d}:00Z",
            "acknowledged_at": f"2026-10-01T10:{minute:02d}:01Z",
            "primary_pod_uid": pod_uid,
            "timeline": 1,
            "xid": str(123 + index),
            "marker_id": f"fixture-{name.lower()}",
            "acknowledged_flush_lsn": f"0/{3000000 + index:07X}",
            "capture_sha256": "a" * 64,
        }
    return cluster, backup, custody


def test_pair_has_distinct_new_storage_and_only_reader_archive_refs():
    objects = m.render(*fixture())
    store, a, b = objects
    assert store["kind"] == "ObjectStore" and "retentionPolicy" not in store["spec"]
    text = str(objects)
    assert "s3-writer" not in text and "isWALArchiver" not in text
    assert "s3-reader" in text and "s3-region" in text
    assert {c["metadata"]["name"] for c in (a, b)} == {"verdify-cnpg-pitr-a", "verdify-cnpg-pitr-b"}
    assert all(c["metadata"]["namespace"] == m.NS for c in (a, b))
    for c in (a, b):
        assert c["spec"]["externalClusters"][0]["plugin"]["parameters"]["serverName"] == m.SOURCE
        assert "plugins" not in c["spec"]
        assert c["spec"]["storage"] == {"size": "30Gi", "storageClass": "longhorn-v1-rwo"}
        assert c["spec"]["instances"] == 3
        assert c["spec"]["affinity"]["podAntiAffinityType"] == "required"
        assert c["spec"]["bootstrap"]["recovery"]["recoveryTarget"]["targetTLI"] == "1"
    assert a["spec"]["bootstrap"] != b["spec"]["bootstrap"]
    assert all(o["kind"] not in {"Secret", "Namespace", "Job", "ScheduledBackup"} for o in objects)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c, b, s: c["metadata"].update(uid="12345678-1234-4234-8234-123456789abc"),
        lambda c, b, s: c["spec"].update(imageName="wrong:latest"),
        lambda c, b, s: b["status"].update(phase="running"),
        lambda c, b, s: b["status"].pop("backupId"),
        lambda c, b, s: b["metadata"].update(ownerReferences=[]),
        lambda c, b, s: b["status"].update(stoppedAt="2026-10-01T10:08:00Z"),
        lambda c, b, s: s["markers"].pop("C"),
        lambda c, b, s: s["targets"].update(A="2026-10-01T10:04:00Z"),
        lambda c, b, s: s["targets"].update(B="2026-10-01T10:08:00Z"),
        lambda c, b, s: s.update(timeline="latest"),
        lambda c, b, s: s["markers"]["B"].update(timeline=2),
        lambda c, b, s: s["markers"]["B"].update(xid="123"),
        lambda c, b, s: s["markers"]["B"].update(acknowledged_flush_lsn="0/1"),
        lambda c, b, s: s["markers"]["B"].update(primary_pod_uid="12345678-1234-4234-8234-123456789abc"),
        lambda c, b, s: s["markers"]["B"].update(capture_sha256=""),
        lambda c, b, s: s["source"].update(database="verdify"),
        lambda c, b, s: s["targets"].update(A="2026-10-01T10:03:00"),
    ],
)
def test_refuses_missing_native_provenance_or_unreachable_target(mutation):
    args = copy.deepcopy(fixture())
    mutation(*args)
    with pytest.raises((ValueError, KeyError)):
        m.render(*args)


def test_native_capture_hashes_and_content_are_required(tmp_path):
    import hashlib
    import json

    custody = fixture()[2]
    for name, marker in custody["markers"].items():
        raw = json.dumps(
            {
                "source": custody["source"],
                "marker": {key: value for key, value in marker.items() if key != "capture_sha256"},
            }
        ).encode()
        (tmp_path / (name + ".json")).write_bytes(raw)
        marker["capture_sha256"] = hashlib.sha256(raw).hexdigest()
    raw = json.dumps(
        {
            "source": custody["source"],
            "primary_pod_uid": custody["primary_pod_uid"],
            "timeline": custody["timeline"],
            "targets": custody["targets"],
        }
    ).encode()
    (tmp_path / "target-boundaries.json").write_bytes(raw)
    custody["target_capture_sha256"] = hashlib.sha256(raw).hexdigest()
    m.validate_captures(custody, tmp_path)
    (tmp_path / "A.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        m.validate_captures(custody, tmp_path)
    custody["markers"]["A"]["capture_sha256"] = hashlib.sha256(b"{}").hexdigest()
    with pytest.raises(ValueError, match="content mismatch"):
        m.validate_captures(custody, tmp_path)


def test_natural_scheduled_backup_requires_exact_owner_lineage():
    cluster, backup, custody = fixture()
    cluster["metadata"]["creationTimestamp"] = "2026-10-01T09:00:00Z"
    scheduled = {
        "kind": "ScheduledBackup",
        "metadata": {"namespace": m.NS, "name": m.SOURCE + "-daily", "uid": "12345678-1234-4234-8234-123456789abe"},
        "spec": copy.deepcopy(backup["spec"]),
    }
    backup["metadata"]["ownerReferences"] = [
        {"kind": "ScheduledBackup", "name": scheduled["metadata"]["name"], "uid": scheduled["metadata"]["uid"]}
    ]
    m.render(cluster, backup, custody, scheduled)
    scheduled["metadata"]["uid"] = "12345678-1234-4234-8234-123456789abf"
    with pytest.raises(ValueError, match="owner UID mismatch"):
        m.render(cluster, backup, custody, scheduled)
