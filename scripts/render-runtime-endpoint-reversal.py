"""Render suspended owning-client endpoint reversals and B-only network additions."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import yaml

NS = "verdify-db-rehearsal"
CM = "verdify-s2-endpoint-reversal"
B = "verdify-cnpg-s2-pitr-b-frozen"
S2 = "verdify-cnpg-s2"
LABELS = {
    "app.kubernetes.io/part-of": "verdify",
    "app.kubernetes.io/component": "cnpg-s2-runtime-qualification",
    "verdify.ai/qualification-target": S2,
    "verdify.ai/endpoint-reversal": "true",
}
SEC = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def ref(name, key, secret):
    return {"name": name, "valueFrom": {"secretKeyRef": {"name": secret, "key": key}}}


def render(binding, sources):
    spec = importlib.util.spec_from_file_location(
        "endpoints", Path(__file__).with_name("qualify-runtime-endpoint-reversal.py")
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    ends = m.checked_endpoints(binding)
    require(set(binding["profiles"]) == set(m.ROLES) | {"grafana"}, "all nine genuine owning-image profiles required")
    data = {"binding.json": json.dumps(binding, sort_keys=True), **sources}
    objects = [
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": CM, "namespace": NS},
            "immutable": True,
            "data": data,
        }
    ]
    for role in (*m.ROLES, "grafana"):
        profile = binding["profiles"][role]
        require(re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", profile["image"]), "immutable owning image required")
        require(
            profile["identity_kind"] in {"baked-image", "mounted-source", "actual-grafana-server"},
            "explicit actual source identity required",
        )
        if role != "grafana":
            require(profile["identity_kind"] in {"baked-image", "mounted-source"}, "ordinary source identity required")
            require(re.fullmatch(r"[0-9a-f]{64}", profile["module_sha256"]), "module hash required")
            if profile["identity_kind"] == "baked-image":
                require(re.fullmatch(r"[0-9a-f]{40}", profile["image_source_sha"]), "known baked source required")
            else:
                require(
                    re.fullmatch(r"[0-9a-f]{40}", profile["mounted_source_sha"]),
                    "known mounted source revision required",
                )
                require(
                    any(
                        v["path"] == profile["module_path"] and v["sha256"] == profile["module_sha256"]
                        for v in profile.get("source_mounts", [])
                    ),
                    "mounted source must be the hash-bound actual module",
                )
        else:
            require(profile["identity_kind"] == "actual-grafana-server", "actual Grafana server identity required")
        waves = ("before", "target", "restored") if role == "grafana" else ("all",)
        for wave in waves:
            name = "verdify-s2-endpoint-" + role.replace("_", "-") + "-" + wave
            env = [
                {"name": "VERDIFY_DEVICE_WRITE_ENABLED", "value": "0"},
                {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
                ref("SOURCE_DB_PASSWORD", role, "verdify-cnpg-s2-runtime-client-auth"),
                ref("TARGET_DB_PASSWORD", role, B + "-runtime-client-auth"),
            ]
            mounts = [
                {"name": "qualification", "mountPath": "/qualification", "readOnly": True},
                {"name": "tmp", "mountPath": "/tmp"},  # noqa: S108 - private per-Pod emptyDir
            ]
            volumes = [{"name": "qualification", "configMap": {"name": CM}}, {"name": "tmp", "emptyDir": {}}]
            container = {
                "name": "qualify",
                "image": profile["image"],
                "command": [
                    "python3",
                    "/qualification/qualify-runtime-endpoint-reversal.py",
                    "--binding",
                    "/qualification/binding.json",
                    "--duty",
                    role,
                ],
                "env": env,
                "volumeMounts": mounts,
                "securityContext": SEC,
                "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"cpu": "1", "memory": "1Gi"}},
            }
            # Every additional owning script mount is explicitly source-hash bound;
            # allowed only inside immutable qualification ConfigMap, never a PVC.
            for mount in profile.get("source_mounts", []):
                require(
                    mount["key"] in sources
                    and hashlib.sha256(sources[mount["key"]].encode()).hexdigest() == mount["sha256"],
                    "mounted owning source hash differs",
                )
                require(
                    mount["path"] in {profile["module_path"]} or mount["path"].startswith("/qualification/"),
                    "mounted path outside qualification source",
                )
                mounts.append(
                    {"name": "qualification", "mountPath": mount["path"], "subPath": mount["key"], "readOnly": True}
                )
            containers = [container]
            if role == "grafana":
                cluster = B if wave == "target" else S2
                datasource = {
                    "apiVersion": 1,
                    "datasources": [
                        {
                            "name": "Verdify TimescaleDB",
                            "uid": "verdify-tsdb",
                            "type": "grafana-postgresql-datasource",
                            "access": "proxy",
                            "url": ends[cluster]["host"] + ":5432",
                            "user": "verdify_grafana_runtime_login",
                            "editable": False,
                            "jsonData": {
                                "database": "verdify_rehearsal",
                                "sslmode": "require",
                                "postgresVersion": 1600,
                                "timescaledb": True,
                                "maxOpenConns": 10,
                                "maxIdleConns": 5,
                            },
                            "secureJsonData": {"password": "$GRAFANA_RUNTIME_DB_PASSWORD"},
                        }
                    ],
                }
                key = "datasource-" + wave + ".yaml"
                data[key] = yaml.safe_dump(datasource, sort_keys=False)
                volumes.extend(
                    [
                        {"name": "phase-state", "emptyDir": {}},
                        {"name": "grafana-data", "emptyDir": {}},
                        {"name": "grafana-logs", "emptyDir": {}},
                    ]
                )
                serverenv = [
                    {"name": k, "value": v}
                    for k, v in {
                        "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=30000",
                        "GF_AUTH_ANONYMOUS_ENABLED": "false",
                        "GF_SECURITY_ADMIN_USER": "admin",
                        "GF_ANALYTICS_REPORTING_ENABLED": "false",
                        "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
                        "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES": "false",
                        "GF_LOG_LEVEL": "warn",
                    }.items()
                ]
                serverenv.extend(
                    [
                        ref(
                            "GF_SECURITY_ADMIN_PASSWORD",
                            "grafana_admin_password",
                            "verdify-cnpg-s2-runtime-client-auth",
                        ),
                        ref(
                            "GRAFANA_RUNTIME_DB_PASSWORD",
                            "grafana",
                            B + "-runtime-client-auth" if wave == "target" else "verdify-cnpg-s2-runtime-client-auth",
                        ),
                    ]
                )
                server = {
                    "name": "grafana",
                    "image": profile["image"],
                    "command": [
                        "sh",
                        "-c",
                        '/run.sh & server_pid=$!; trap \'kill -TERM "$server_pid" 2>/dev/null; wait "$server_pid"\' EXIT TERM INT; while kill -0 "$server_pid" 2>/dev/null; do test ! -f /phase-state/done || exit 0; sleep 1; done; wait "$server_pid"; exit 1',
                    ],
                    "env": serverenv,
                    "securityContext": SEC,
                    "volumeMounts": [
                        {
                            "name": "qualification",
                            "mountPath": "/etc/grafana/provisioning/datasources/datasources.yaml",
                            "subPath": key,
                            "readOnly": True,
                        },
                        {"name": "phase-state", "mountPath": "/phase-state"},
                        {"name": "grafana-data", "mountPath": "/var/lib/grafana"},
                        {"name": "grafana-logs", "mountPath": "/var/log/grafana"},
                        {"name": "tmp", "mountPath": "/tmp"},  # noqa: S108 - private per-Pod emptyDir
                    ],
                    "resources": container["resources"],
                }
                require(
                    re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", profile["qualifier_image"]),
                    "pinned credential-free qualifier image required",
                )
                container.update(
                    image=profile["qualifier_image"],
                    command=[
                        "python3",
                        "/qualification/qualify-grafana-endpoint-phase.py",
                        "--binding",
                        "/qualification/binding.json",
                        "--wave",
                        wave,
                    ],
                    env=[
                        ref("GRAFANA_ADMIN_PASSWORD", "grafana_admin_password", "verdify-cnpg-s2-runtime-client-auth")
                    ],
                    volumeMounts=mounts + [{"name": "phase-state", "mountPath": "/phase-state"}],
                )
                containers = [server, container]
            pod = {
                "restartPolicy": "Never",
                "nodeSelector": {"kubernetes.io/hostname": "node4"},
                "automountServiceAccountToken": False,
                "enableServiceLinks": False,
                "imagePullSecrets": [{"name": "zot-origin-cluster-pull"}],
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 472 if role == "grafana" else 1000,
                    "runAsGroup": 472 if role == "grafana" else 1000,
                    "fsGroup": 472 if role == "grafana" else 1000,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": containers,
                "volumes": volumes,
            }
            objects.append(
                {
                    "apiVersion": "batch/v1",
                    "kind": "Job",
                    "metadata": {
                        "name": name,
                        "namespace": NS,
                        "labels": LABELS,
                        "annotations": {
                            "verdify.ai/proof-scope": "isolated actual owning client readonly S2-B-S2; not production cutover",
                            "verdify.ai/binding-sha256": hashlib.sha256(data["binding.json"].encode()).hexdigest(),
                        },
                    },
                    "spec": {
                        "suspend": True,
                        "backoffLimit": 0,
                        "activeDeadlineSeconds": 240,
                        "template": {"metadata": {"labels": LABELS}, "spec": pod},
                    },
                }
            )
    # Existing exact S2 policies remain; these additions permit only B5432 and DNS.
    clients = {"matchLabels": LABELS}
    server = {"matchLabels": {"cnpg.io/cluster": B}}
    objects.extend(
        [
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {"name": "verdify-s2-endpoint-b-egress", "namespace": NS},
                "spec": {
                    "podSelector": clients,
                    "policyTypes": ["Egress"],
                    "egress": [
                        {"to": [{"podSelector": server}], "ports": [{"protocol": "TCP", "port": 5432}]},
                        {
                            "to": [
                                {
                                    "namespaceSelector": {
                                        "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                                    },
                                    "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                                }
                            ],
                            "ports": [{"protocol": p, "port": 53} for p in ("UDP", "TCP")],
                        },
                    ],
                },
            },
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {"name": "verdify-s2-endpoint-b-ingress", "namespace": NS},
                "spec": {
                    "podSelector": server,
                    "policyTypes": ["Ingress"],
                    "ingress": [{"from": [{"podSelector": clients}], "ports": [{"protocol": "TCP", "port": 5432}]}],
                },
            },
        ]
    )
    return objects


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--binding", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument(
        "--owning-source",
        action="append",
        default=[],
        help="Explicit ConfigMap key=source-path, matched to reviewed per-role source SHA",
    )
    args = p.parse_args()
    root = Path(__file__).parent
    sources = {
        name: (root / name).read_text()
        for name in (
            "qualify-runtime-endpoint-reversal.py",
            "qualify-grafana-endpoint-phase.py",
            "qualify-ordinary-runtime-login.py",
        )
    }
    for entry in args.owning_source:
        key, path = entry.split("=", 1)
        require(re.fullmatch(r"[a-zA-Z0-9_.-]+", key) and key not in sources, "invalid or duplicate owning source key")
        sources[key] = Path(path).read_text()
    args.output.write_text(yaml.safe_dump_all(render(json.loads(args.binding.read_text()), sources), sort_keys=False))


if __name__ == "__main__":
    main()
