from __future__ import annotations

import subprocess

import yaml

EXPECTED_SELECTOR = {"agentfleet.vallery.net/node-class": "general"}


def rendered_deployments() -> dict[str, dict]:
    rendered = subprocess.run(
        ["kustomize", "build", "deploy/k8s/overlays/prod"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {
        resource["metadata"]["name"]: resource
        for resource in yaml.safe_load_all(rendered)
        if isinstance(resource, dict) and resource.get("kind") == "Deployment"
    }


def test_prod_device_writers_require_exact_general_worker_selector() -> None:
    deployments = rendered_deployments()

    for name in ("verdify-ingestor", "verdify-setpoint-server"):
        pod_spec = deployments[name]["spec"]["template"]["spec"]
        assert pod_spec["nodeSelector"] == EXPECTED_SELECTOR


def test_ingestor_retains_soft_node6_avoidance_and_single_writer_strategy() -> None:
    ingestor = rendered_deployments()["verdify-ingestor"]

    assert ingestor["spec"]["strategy"] == {"type": "Recreate"}
    node_affinity = ingestor["spec"]["template"]["spec"]["affinity"]["nodeAffinity"]
    assert node_affinity == {
        "preferredDuringSchedulingIgnoredDuringExecution": [
            {
                "weight": 100,
                "preference": {
                    "matchExpressions": [
                        {
                            "key": "kubernetes.io/hostname",
                            "operator": "NotIn",
                            "values": ["vm-k3s-node6"],
                        }
                    ]
                },
            }
        ]
    }


def test_setpoint_server_retains_single_writer_strategy() -> None:
    setpoint = rendered_deployments()["verdify-setpoint-server"]

    assert setpoint["spec"]["strategy"] == {"type": "Recreate"}
