"""Closed S2 profile refuses incomplete baseline and retains reader-only lineage."""

import copy
import importlib.util
import subprocess
from pathlib import Path

import pytest
import yaml
from test_cnpg_pitr_pair import fixture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("s2_pitr", ROOT / "scripts/render-cnpg-s2-pitr-pair.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def s2_fixture():
    cluster, backup, custody = fixture()
    cluster = yaml.safe_load((ROOT / "deploy/k8s/cnpg/rehearsal/cluster/s2-cluster.yaml").read_text())
    cluster["metadata"]["uid"] = m.SOURCE_UID
    backup["spec"]["cluster"]["name"] = m.SOURCE
    backup["metadata"]["ownerReferences"] = [{"kind": "Cluster", "name": m.SOURCE, "uid": m.SOURCE_UID}]
    custody["source"].update(cluster_name=m.SOURCE, cluster_uid=m.SOURCE_UID)
    admission = {
        "schema": "cnpg-s2-native-admission-v1",
        "cluster": m.SOURCE,
        "binding": {"cluster_uid": m.SOURCE_UID},
        "database_oid": 16447,
        "installation_sha256": m.INSTALL_SHA,
        "full_data_internal_catalog_accounting_complete": True,
        "historical_seals_and_ledger_retained": True,
    }
    return cluster, backup, custody, admission


def test_only_two_new_s2_reader_only_clusters_and_original_profile_unchanged():
    original = fixture()
    objects = m.render(*s2_fixture())
    assert {o["metadata"]["name"] for o in objects} == {"verdify-cnpg-s2-pitr-a", "verdify-cnpg-s2-pitr-b"}
    for o in objects:
        assert o["kind"] == "Cluster" and o["spec"]["instances"] == 3
        assert o["spec"]["storage"]["size"] == "20Gi"
        assert o["spec"]["walStorage"]["size"] == "5Gi"
        assert "plugins" not in o["spec"]
        assert o["spec"]["externalClusters"][0]["plugin"]["parameters"] == {
            "barmanObjectName": "verdify-cnpg-pitr-reader",
            "serverName": m.SOURCE,
        }
    assert fixture() == original


def test_frozen_recovery_disables_scheduler_before_start_without_catalog_rewrites():
    original = m.render(*s2_fixture())
    frozen = m.render(*s2_fixture(), freeze_background_workers=True)
    for before, after in zip(original, frozen, strict=True):
        assert after["metadata"]["name"] == before["metadata"]["name"] + "-frozen"
        assert after["spec"]["postgresql"]["parameters"].pop("timescaledb.max_background_workers") == "0"
        after["metadata"]["name"] = before["metadata"]["name"]
        assert after == before


def test_owning_render_adopts_frozen_candidates_with_reader_only_failover_policy():
    directory = ROOT / "deploy/k8s/cnpg/rehearsal/cluster"
    objects = list(yaml.safe_load_all(subprocess.check_output(["kustomize", "build", str(directory)])))
    identities = [(o["kind"], o["metadata"]["name"]) for o in objects]
    assert len(identities) == len(set(identities))
    assert all(o["metadata"]["namespace"] == "verdify-db-rehearsal" for o in objects)
    assert not any(o["kind"] in {"Secret", "Job", "Deployment", "StatefulSet", "PersistentVolume"} for o in objects)
    frozen = [o for o in objects if o["kind"] == "Cluster" and o["metadata"]["name"].endswith("-frozen")]
    assert {o["metadata"]["name"] for o in frozen} == {
        "verdify-cnpg-s2-pitr-a-frozen",
        "verdify-cnpg-s2-pitr-b-frozen",
    }
    for o in frozen:
        assert o["spec"]["postgresql"]["parameters"]["timescaledb.max_background_workers"] == "0"
        assert o["spec"]["instances"] == 3
        assert o["spec"]["affinity"]["podAntiAffinityType"] == "required"
        assert "plugins" not in o["spec"]
        assert (
            o["spec"]["externalClusters"][0]["plugin"]["parameters"]["barmanObjectName"] == "verdify-cnpg-pitr-reader"
        )
    reader = next(
        o for o in objects if o["kind"] == "ObjectStore" and o["metadata"]["name"] == "verdify-cnpg-pitr-reader"
    )
    credentials = reader["spec"]["configuration"]["s3Credentials"]
    assert (
        credentials["accessKeyId"]["name"]
        == credentials["secretAccessKey"]["name"]
        == "verdify-cnpg-rehearsal-s3-reader"
    )
    assert "retentionPolicy" not in reader["spec"]
    backup = next(o for o in objects if o["kind"] == "Backup")
    assert backup["metadata"]["name"] == "verdify-cnpg-s2-current274-20261006"
    assert backup["metadata"]["ownerReferences"][0]["uid"] == m.SOURCE_UID


@pytest.mark.parametrize(
    "part,key,value",
    [
        (3, "full_data_internal_catalog_accounting_complete", False),
        (3, "installation_sha256", "a" * 64),
        (3, "database_oid", 123),
        (0, "storage", {"size": "30Gi", "storageClass": "longhorn-v1-rwo"}),
        (0, "affinity", {}),
    ],
)
def test_refuses_admission_and_capacity_drift(part, key, value):
    args = copy.deepcopy(s2_fixture())
    target = args[part]["spec"] if part == 0 else args[part]
    target[key] = value
    with pytest.raises(ValueError):
        m.render(*args)
