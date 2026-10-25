"""Render the full overlay so later patches cannot reintroduce host pinning."""

import subprocess

import pytest
import yaml


@pytest.fixture(scope="module")
def workloads():
    rendered = subprocess.check_output(["kustomize", "build", "deploy/k8s/overlays/prod"], text=True)
    return {(item["kind"], item["metadata"]["name"]): item for item in yaml.safe_load_all(rendered) if item}


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        ("StatefulSet", "verdify-db"),
        ("Deployment", "verdify-grafana"),
        ("Deployment", "verdify-hermes-iris"),
    ],
)
def test_retained_state_has_two_worker_eligibility_without_hostname(workloads, kind, name):
    spec = workloads[kind, name]["spec"]["template"]["spec"]
    assert spec["nodeSelector"] == {
        "kubernetes.io/os": "linux",
        "agentfleet.vallery.net/node-class": "general",
        "storage.vallery.net/longhorn-attach": "true",
    }
    assert "nodeAffinity" not in spec.get("affinity", {})


def test_portability_keeps_singleton_storage_and_recreate_boundaries(workloads):
    db = workloads["StatefulSet", "verdify-db"]
    assert db["spec"]["replicas"] == 1
    assert db["spec"]["volumeClaimTemplates"][0]["apiVersion"] == "v1"
    assert db["spec"]["volumeClaimTemplates"][0]["kind"] == "PersistentVolumeClaim"
    assert db["spec"]["volumeClaimTemplates"][0]["metadata"]["name"] == "db-data"
    assert db["spec"]["volumeClaimTemplates"][0]["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert "ServerSideApply=true" in db["metadata"]["annotations"]["argocd.argoproj.io/sync-options"]
    for name in ("verdify-grafana", "verdify-hermes-iris", "verdify-ingestor"):
        assert workloads["Deployment", name]["spec"]["replicas"] == 1
        assert workloads["Deployment", name]["spec"]["strategy"]["type"] == "Recreate"


def test_lab_origin_recreates_without_losing_publisher_attachment_cohort(workloads):
    origin = workloads["Deployment", "verdify-lab-cache-origin"]
    assert origin["spec"]["replicas"] == 1
    assert origin["spec"]["strategy"] == {"type": "Recreate"}
    claim = next(v for v in origin["spec"]["template"]["spec"]["volumes"] if v["name"] == "origin")
    assert claim["persistentVolumeClaim"] == {"claimName": "verdify-lab-site-cache", "readOnly": True}
    publisher = workloads["CronJob", "verdify-lab-publisher"]["spec"]["jobTemplate"]["spec"]["template"]
    for template in (origin["spec"]["template"], publisher):
        assert template["metadata"]["labels"]["verdify.ai/lab-cache-cohort"] == "portable-rwo"
        affinity = template["spec"]["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]
        assert affinity == [
            {
                "labelSelector": {"matchLabels": {"verdify.ai/lab-cache-cohort": "portable-rwo"}},
                "topologyKey": "kubernetes.io/hostname",
            }
        ]
