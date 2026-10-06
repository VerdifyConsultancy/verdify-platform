"""Render only the fixed frozen-B bootstrap failover client and two policies."""

import argparse
import ipaddress
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
NS = "verdify-db-rehearsal"
CLUSTER = "verdify-cnpg-s2-pitr-b-frozen"
CLUSTER_UID = "3981d0f2-4c72-48ac-9e09-826fe6bd4ed6"
IMAGE = "registry.vallery.net/verdifyconsultancy/verdify-timescaledb-cnpg:16.13-ts2.25.2@sha256:8b461e37d18aa049704eb6a9cde2ba9af0f955f8d3725e921070d2450bb2f137"
LABELS = {
    "app.kubernetes.io/part-of": "verdify",
    "app.kubernetes.io/component": "cnpg-s2-failover-client",
    "verdify.ai/qualification-target": CLUSTER,
}


def render(cluster, primary, run_id):
    if (
        cluster["metadata"]["name"] != CLUSTER
        or cluster["metadata"]["namespace"] != NS
        or cluster["metadata"]["uid"] != CLUSTER_UID
        or cluster["status"].get("readyInstances") != 3
        or cluster["spec"]["instances"] != 3
        or cluster["spec"]["imageName"] != IMAGE
        or cluster["spec"]["postgresql"]["parameters"].get("timescaledb.max_background_workers") != "0"
        or primary["metadata"]["name"] != cluster["status"]["currentPrimary"]
        or primary["metadata"]["namespace"] != NS
        or primary["metadata"].get("labels", {}).get("cnpg.io/cluster") != CLUSTER
        or primary["status"].get("phase") != "Running"
        or not any(
            x.get("type") == "Ready" and x.get("status") == "True" for x in primary["status"].get("conditions", [])
        )
        or not re.fullmatch(r"[0-9a-f-]{36}", primary["metadata"]["uid"])
        or not any(
            x.get("kind") == "Cluster" and x.get("uid") == CLUSTER_UID for x in primary["metadata"]["ownerReferences"]
        )
        or not re.fullmatch(r"s2-failover-[a-z0-9-]{8,48}", run_id)
    ):
        raise ValueError("failover requires exact healthy frozen-B native binding")
    address = str(ipaddress.IPv4Address(primary["status"]["podIP"]))
    if ipaddress.IPv4Address(address) not in ipaddress.IPv4Network("10.42.0.0/16"):
        raise ValueError("failover requires native cluster Pod address")
    pod_selector = {"cnpg.io/cluster": CLUSTER}
    policies = [
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "cnpg-s2-frozen-b-failover-client-only", "namespace": NS},
            "spec": {
                "podSelector": {"matchLabels": LABELS},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": [
                    {
                        "to": [{"podSelector": {"matchLabels": pod_selector}}],
                        "ports": [{"protocol": "TCP", "port": 5432}],
                    },
                    {
                        "to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}],
                        "ports": [{"protocol": "TCP", "port": 53}, {"protocol": "UDP", "port": 53}],
                    },
                ],
            },
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "cnpg-s2-frozen-b-failover-client-ingress", "namespace": NS},
            "spec": {
                "podSelector": {"matchLabels": pod_selector},
                "policyTypes": ["Ingress"],
                "ingress": [
                    {"from": [{"podSelector": {"matchLabels": LABELS}}], "ports": [{"protocol": "TCP", "port": 5432}]}
                ],
            },
        },
    ]
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": run_id,
            "namespace": NS,
            "labels": LABELS,
            "annotations": {
                "verdify.ai/cluster-uid": CLUSTER_UID,
                "verdify.ai/initial-primary-uid": primary["metadata"]["uid"],
            },
        },
        "spec": {
            "activeDeadlineSeconds": 180,
            "backoffLimit": 0,
            "template": {
                "metadata": {"labels": LABELS},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "imagePullSecrets": [{"name": "zot-origin-cluster-pull"}],
                    "securityContext": {
                        "runAsUser": 26,
                        "runAsGroup": 26,
                        "runAsNonRoot": True,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "client",
                            "image": IMAGE,
                            "command": ["python3", "-c", (ROOT / "scripts/cnpg-s2-failover-client.py").read_text()],
                            "env": [
                                {"name": "PGHOST", "value": CLUSTER + "-rw." + NS + ".svc.cluster.local"},
                                {"name": "PGPORT", "value": "5432"},
                                {"name": "PGDATABASE", "value": "rehearsal_bootstrap"},
                                {"name": "PGUSER", "value": "rehearsal_bootstrap"},
                                {"name": "PGCONNECT_TIMEOUT", "value": "2"},
                                {
                                    "name": "PGPASSWORD",
                                    "valueFrom": {"secretKeyRef": {"name": CLUSTER + "-app", "key": "password"}},
                                },
                                {"name": "FAILOVER_RUN_ID", "value": run_id},
                                {"name": "FAILOVER_INITIAL_SERVER_ADDRESS", "value": address},
                            ],
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "64Mi"},
                                "limits": {"cpu": "500m", "memory": "128Mi"},
                            },
                            "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                        }
                    ],
                },
            },
        },
    }
    return [*policies, job]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("cluster", "primary", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    objects = render(json.loads(args.cluster.read_text()), json.loads(args.primary.read_text()), args.run_id)
    with args.output.open("x") as stream:
        yaml.safe_dump_all(objects, stream, sort_keys=False)


if __name__ == "__main__":
    main()
