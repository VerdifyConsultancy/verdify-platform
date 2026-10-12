"""Fresh recovery cannot inherit the background-job drift or broaden admission."""

import copy
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CLUSTERS = ROOT / "deploy/k8s/cnpg/rehearsal/cluster"


def load(filename, module_name):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "scripts" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


m = load("cnpg-s2-integration-current275-transition.py", "integration_current275")


def test_actual_rehearsal_render_cannot_recreate_original_initdb_or_archive_writer():
    rendered = subprocess.check_output(["kustomize", "build", str(CLUSTERS)], text=True, timeout=30)
    objects = list(yaml.safe_load_all(rendered))
    clusters = [obj for obj in objects if obj["kind"] == "Cluster"]
    assert [obj["metadata"]["name"] for obj in clusters] == [m.CLUSTER]
    assert "initdb" not in clusters[0]["spec"]["bootstrap"]
    assert "plugins" not in clusters[0]["spec"]
    assert not any(obj["kind"] in {"Secret", "Backup", "ScheduledBackup"} for obj in objects)
    stores = [obj for obj in objects if obj["kind"] == "ObjectStore"]
    assert len(stores) == 1 and stores[0]["metadata"]["name"] == "verdify-cnpg-pitr-reader"
    assert "retentionPolicy" not in stores[0]["spec"]
    credentials = stores[0]["spec"]["configuration"]["s3Credentials"]
    assert credentials["accessKeyId"] == {
        "name": "verdify-cnpg-rehearsal-s3-reader",
        "key": "AWS_ACCESS_KEY_ID",
    }
    assert credentials["secretAccessKey"] == {
        "name": "verdify-cnpg-rehearsal-s3-reader",
        "key": "AWS_SECRET_ACCESS_KEY",
    }
    assert credentials["region"] == {
        "name": "verdify-cnpg-rehearsal-s3-region",
        "key": "AWS_DEFAULT_REGION",
    }


def test_fresh_clone_keeps_exact_accepted_reader_lineage_and_stops_jobs_before_start():
    before = list(yaml.safe_load_all((CLUSTERS / "s2-current274-frozen-recovery-pair.yaml").read_text()))[1]
    after = yaml.safe_load((CLUSTERS / "s2-current274-integration-recovery.yaml").read_text())
    assert after["metadata"]["name"] == m.CLUSTER
    assert after["spec"]["postgresql"]["parameters"]["timescaledb.max_background_workers"] == "0"
    assert "plugins" not in after["spec"]
    assert after["spec"]["enableSuperuserAccess"] is False
    assert after["spec"]["bootstrap"]["recovery"]["recoveryTarget"] == {
        "backupID": "20261006T052358",
        "targetTime": "2026-10-06T05:38:19.219172+00:00",
        "targetTLI": "1",
        "exclusive": False,
    }
    normalized = copy.deepcopy(after)
    normalized["metadata"]["name"] = before["metadata"]["name"]
    del normalized["metadata"]["annotations"]["verdify.ai/recovery-purpose"]
    assert normalized == before


@pytest.mark.parametrize("target", ["verdify-db", "verdify-cnpg-s2", "verdify-cnpg-s2-pitr-b-frozen", "arbitrary"])
def test_integration_adapter_refuses_every_other_target(target):
    with pytest.raises(ValueError, match="closed275"):
        m.transition.configured(target)


def test_new_admission_retains_historical_source_and_custody_guards():
    old = load("cnpg-current275-transition.py", "historical_current275")
    targets = old.TARGETS
    m.transition.configured(m.CLUSTER)
    ddl, bodies = m.transition.t.ddl(True, physical_target=m.CLUSTER)
    assert "current_setting('cluster_name')<>'" + m.CLUSTER + "'" in ddl
    assert len(bodies) == 2
    assert old.TARGETS == targets == ("verdify-cnpg-s2", "verdify-cnpg-s2-pitr-b-frozen")
    assert old.VERSION == "cnpg-current275-additive-transition-v1"
    for key in (
        "NATIVE274_WITNESS_SHA",
        "NATIVE275_BRIDGE_WITNESS_SHA",
        "MIGRATION_SHA",
        "CANONICAL_BEFORE",
        "CANONICAL_AFTER",
    ):
        assert getattr(m.transition, key) == getattr(old, key)
    raw = (ROOT / old.MIGRATION).read_bytes()
    assert m.transition.remap_migration(raw) == old.remap_migration(raw)


def test_real_witness_emitter_preserves_c0_source_and_binds_only_integration_target():
    guards, select = m.transition.witness_parts(m.CLUSTER)
    assert m.transition.t.operator.CLUSTER == "verdify-cnpg-s2"
    assert "current_setting('cluster_name') <> '" + m.CLUSTER + "'" in guards
    assert "current_setting('cluster_name') <> 'verdify-cnpg-s2'" not in guards
    assert "C0 logical recovery witness refuses changed executable digest implementation" in guards
    assert select.startswith("SELECT jsonb_build_object(")
    with pytest.raises(ValueError, match="unsupported isolated cluster profile"):
        m.transition.t.c0.emit_sql(target=True, bootstrap_grantor_profile=True, cluster_name=m.CLUSTER)


def test_client_network_can_only_reach_the_new_target_and_dns():
    policies = list(yaml.safe_load_all((CLUSTERS / "cnpg-s2-integration-runtime-client-policy.yaml").read_text()))
    egress, ingress = policies
    assert egress["spec"]["podSelector"]["matchLabels"]["verdify.ai/qualification-target"] == m.CLUSTER
    assert egress["spec"]["egress"][0] == {
        "to": [{"podSelector": {"matchLabels": {"cnpg.io/cluster": m.CLUSTER}}}],
        "ports": [{"protocol": "TCP", "port": 5432}],
    }
    assert len(egress["spec"]["egress"]) == 2
    assert egress["spec"]["egress"][1]["ports"] == [{"protocol": "TCP", "port": 53}, {"protocol": "UDP", "port": 53}]
    assert "ingress" not in egress["spec"]
    assert ingress["spec"]["podSelector"]["matchLabels"] == {"cnpg.io/cluster": m.CLUSTER}
    assert ingress["spec"]["ingress"][0]["from"] == [{"podSelector": egress["spec"]["podSelector"]}]


def test_old_rejected_clone_is_parked_and_new_restore_remains_awake():
    old = yaml.safe_load((CLUSTERS / "s2-cluster.yaml").read_text())
    new = yaml.safe_load((CLUSTERS / "s2-current274-integration-recovery.yaml").read_text())
    assert old["metadata"]["annotations"]["cnpg.io/hibernation"] == "on"
    assert "cnpg.io/hibernation" not in new["metadata"].get("annotations", {})
    assert old["spec"]["instances"] == new["spec"]["instances"] == 3
    assert old["spec"]["resources"]["requests"] == new["spec"]["resources"]["requests"]


@pytest.mark.parametrize("wrong_target", ["verdify-db", "verdify-cnpg-s2", "verdify-cnpg-s2-pitr-b-frozen"])
def test_runtime_duty_runner_rejects_other_clusters_and_preserves_historical_target(wrong_target):
    old = load("qualify-restored-runtime-duties.py", "historical_runtime_duties")
    new = load("qualify-s2-integration-runtime-duties.py", "integration_runtime_duties").duties
    assert old.TARGET_CLUSTER == "verdify-cnpg-s2"
    assert new.TARGET_CLUSTER == m.CLUSTER
    uid = "11111111-2222-4333-8444-555555555555"

    def binding(cluster):
        meta = {"namespace": "verdify-db-rehearsal", "uid": uid}
        return {
            "cluster": {"metadata": {**meta, "name": cluster}, "status": {"currentPrimary": cluster + "-1"}},
            "pod": {
                "metadata": {**meta, "name": cluster + "-1", "labels": {"cnpg.io/cluster": cluster}},
                "status": {
                    "phase": "Running",
                    "podIP": "10.42.5.100",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            },
            "service": {
                "metadata": {**meta, "name": cluster + "-rw", "ownerReferences": [{"kind": "Cluster", "uid": uid}]},
                "spec": {"selector": {"cnpg.io/cluster": cluster, "role": "primary"}, "ports": [{"port": 5432}]},
            },
        }

    native = binding(m.CLUSTER)
    assert new.target_binding(json.dumps(native).encode(), uid)[0] == "10.42.5.100"
    with pytest.raises(AssertionError):
        new.target_binding(json.dumps(binding(wrong_target)).encode(), uid)
    native["service"]["metadata"]["ownerReferences"][0]["uid"] = "wrong-owner"
    with pytest.raises(AssertionError):
        new.target_binding(json.dumps(native).encode(), uid)
    identity = {
        "server_addr": "10.42.5.100",
        "database_oid": 16447,
        "cluster_name": wrong_target,
        "replica": False,
        "server_version_num": "160013",
    }
    with pytest.raises(AssertionError):
        new.check_target_identity(identity, "10.42.5.100", 16447)
