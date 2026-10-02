#!/usr/bin/env python3
"""Render alongside-only CNPG resources after an actual operand digest is qualified."""

from __future__ import annotations

import argparse
import re

import yaml

NS = "verdify-db-rehearsal"
CLUSTER = "verdify-cnpg-rehearsal"
STORE = "verdify-cnpg-rehearsal-backup"
SECRET = "verdify-cnpg-rehearsal-s3-writer"
REGION_SECRET = "verdify-cnpg-rehearsal-s3-region"


def render(image: str) -> list[dict]:
    if not re.fullmatch(
        r"registry\.vallery\.net/verdifyconsultancy/verdify-timescaledb-cnpg:16\.13-ts2\.25\.2@sha256:[0-9a-f]{64}",
        image,
    ):
        raise ValueError("expected qualified primary-origin operand digest, not a tag or placeholder")
    labels = {"app.kubernetes.io/part-of": "verdify", "app.kubernetes.io/component": "cnpg-rehearsal"}

    def resource(api, kind, name, spec):
        return {
            "apiVersion": api,
            "kind": kind,
            "metadata": {"name": name, "namespace": NS, "labels": labels},
            "spec": spec,
        }

    objects = [
        resource(
            "postgresql.cnpg.io/v1",
            "Cluster",
            CLUSTER,
            {
                "instances": 3,
                "imageName": image,
                "env": [
                    {"name": "VERDIFY_REHEARSAL_POD_UID", "valueFrom": {"fieldRef": {"fieldPath": "metadata.uid"}}}
                ],
                "imagePullSecrets": [{"name": "zot-origin-cluster-pull"}],
                "enableSuperuserAccess": False,
                "inheritedMetadata": {"labels": labels},
                "postgresql": {
                    "shared_preload_libraries": ["timescaledb", "pg_stat_statements"],
                    "parameters": {
                        "shared_buffers": "256MB",
                        "max_worker_processes": "32",
                        "synchronous_commit": "on",
                        "timescaledb.telemetry_level": "off",
                    },
                    "synchronous": {"method": "any", "number": 1, "dataDurability": "required", "failoverQuorum": True},
                },
                "storage": {"size": "30Gi", "storageClass": "longhorn-v1-rwo"},
                "walStorage": {"size": "10Gi", "storageClass": "longhorn-v1-rwo"},
                "affinity": {
                    "enablePodAntiAffinity": True,
                    "podAntiAffinityType": "required",
                    "topologyKey": "topology.vallery.net/proxmox-host",
                    "nodeSelector": {
                        "agentfleet.vallery.net/control-plane": "false",
                        "storage.vallery.net/longhorn": "true",
                    },
                },
                "resources": {"requests": {"cpu": "500m", "memory": "2Gi"}, "limits": {"memory": "6Gi"}},
                "bootstrap": {"initdb": {"database": "rehearsal_bootstrap", "owner": "rehearsal_bootstrap"}},
                "plugins": [
                    {
                        "name": "barman-cloud.cloudnative-pg.io",
                        "isWALArchiver": True,
                        "parameters": {"barmanObjectName": STORE},
                    }
                ],
            },
        )
    ]
    objects.append(
        resource(
            "barmancloud.cnpg.io/v1",
            "ObjectStore",
            STORE,
            {
                "configuration": {
                    "destinationPath": "s3://verdify-cnpg-rehearsal/postgresql",
                    "endpointURL": "https://s3-hdd.vallery.net",
                    "s3Credentials": {
                        "accessKeyId": {"name": SECRET, "key": "AWS_ACCESS_KEY_ID"},
                        "secretAccessKey": {"name": SECRET, "key": "AWS_SECRET_ACCESS_KEY"},
                        "region": {"name": REGION_SECRET, "key": "AWS_DEFAULT_REGION"},
                    },
                    "wal": {"compression": "gzip"},
                    "data": {"compression": "gzip"},
                },
                "retentionPolicy": "14d",
            },
        )
    )
    objects.append(
        resource(
            "postgresql.cnpg.io/v1",
            "ScheduledBackup",
            CLUSTER + "-daily",
            {
                "cluster": {"name": CLUSTER},
                "schedule": "0 30 3 * * *",
                "backupOwnerReference": "self",
                "method": "plugin",
                "pluginConfiguration": {"name": "barman-cloud.cloudnative-pg.io"},
            },
        )
    )
    objects.append(
        resource(
            "networking.k8s.io/v1",
            "NetworkPolicy",
            CLUSTER + "-isolation",
            {
                "podSelector": {"matchLabels": labels},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [
                    {
                        "from": [
                            {"podSelector": {"matchLabels": labels}},
                            {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "cnpg-system"}}},
                        ],
                        "ports": [{"protocol": "TCP", "port": p} for p in (5432, 8000, 9187)],
                    }
                ],
                "egress": [
                    {
                        "to": [{"podSelector": {"matchLabels": labels}}],
                        "ports": [{"protocol": "TCP", "port": p} for p in (5432, 8000)],
                    },
                    {
                        "to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}],
                        "ports": [{"protocol": proto, "port": 53} for proto in ("TCP", "UDP")],
                    },
                    {
                        "to": [{"ipBlock": {"cidr": "192.168.7.10/32"}}, {"ipBlock": {"cidr": "10.43.0.1/32"}}],
                        "ports": [{"protocol": "TCP", "port": 443}],
                    },
                    # Garage HTTPS traverses the apps VIP then Traefik websecure8443.
                    # Keep namespace and pod selectors in one peer (AND), never OR.
                    {
                        "to": [
                            {
                                "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "traefik-apps"}},
                                "podSelector": {
                                    "matchLabels": {
                                        "app.kubernetes.io/name": "traefik",
                                        "app.kubernetes.io/instance": "traefik-apps-traefik-apps",
                                    }
                                },
                            }
                        ],
                        "ports": [{"protocol": "TCP", "port": 8443}],
                    },
                    # Include observed API backend endpoints as well as the VIP; policy
                    # implementations may evaluate service traffic after DNAT.
                    {
                        "to": [
                            {"ipBlock": {"cidr": ip + "/32"}}
                            for ip in ("192.168.30.240", "192.168.30.31", "192.168.30.32", "192.168.30.33")
                        ],
                        "ports": [{"protocol": "TCP", "port": 6443}],
                    },
                ],
            },
        )
    )
    objects.insert(
        0,
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": NS,
                "labels": {
                    "verdify.ai/purpose": "disposable-db-qualification",
                    "pod-security.kubernetes.io/enforce": "restricted",
                },
            },
        },
    )
    objects.append(
        resource(
            "networking.k8s.io/v1",
            "NetworkPolicy",
            "deny-all-rehearsal",
            {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]},
        )
    )
    return objects


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    print(yaml.safe_dump_all(render(args.image), sort_keys=False), end="")


if __name__ == "__main__":
    main()
