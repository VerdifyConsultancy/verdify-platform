from __future__ import annotations

import subprocess

import yaml

# The ingestor is pinned to node4/node5 by hostname, not by node class (D-127):
# its ESP32/Shelly egress source set is the node4/node5 SNAT addresses (staged
# gateway rule GAP #11). The setpoint
# server's only off-cluster peer is Home Assistant on the services VLAN, so it
# may run on any non-control-plane worker.
INGESTOR_SELECTOR = {"kubernetes.io/os": "linux", "agentfleet.vallery.net/control-plane": "false"}
INGESTOR_HOSTS = ["vm-k3s-node4", "vm-k3s-node5"]
SETPOINT_SELECTOR = {"agentfleet.vallery.net/control-plane": "false"}


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


def test_prod_device_writers_require_exact_worker_selectors() -> None:
    deployments = rendered_deployments()

    ingestor = deployments["verdify-ingestor"]["spec"]["template"]["spec"]
    assert ingestor["nodeSelector"] == INGESTOR_SELECTOR
    setpoint = deployments["verdify-setpoint-server"]["spec"]["template"]["spec"]
    assert setpoint["nodeSelector"] == SETPOINT_SELECTOR
    assert setpoint["priorityClassName"] == "verdify-device-writer"


def test_ingestor_pins_egress_hosts_keeps_node6_avoidance_and_single_writer() -> None:
    ingestor = rendered_deployments()["verdify-ingestor"]

    assert ingestor["spec"]["strategy"] == {"type": "Recreate"}
    node_affinity = ingestor["spec"]["template"]["spec"]["affinity"]["nodeAffinity"]
    assert node_affinity == {
        "requiredDuringSchedulingIgnoredDuringExecution": {
            "nodeSelectorTerms": [
                {"matchExpressions": [{"key": "kubernetes.io/hostname", "operator": "In", "values": INGESTOR_HOSTS}]}
            ]
        },
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
        ],
    }


def test_setpoint_server_retains_single_writer_strategy() -> None:
    setpoint = rendered_deployments()["verdify-setpoint-server"]

    assert setpoint["spec"]["strategy"] == {"type": "Recreate"}
