"""Render fixed A/B authenticated ordinary clients; ROOT owns credentials/Jobs.

Consume the owning physical admission's full frozen-source, logical-install,
Backup/WAL/marker/dataset custody before accepting either fixed new target.
Reuse the exact original consumer startup/pools/hot SQL; no service/device main.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import ipaddress
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(
        "physical_client_" + name.replace("-", "_"), ROOT / "scripts" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


physical = load("cnpg-physical-runtime-transition")
ordinary = load("cnpg-runtime-client-qualification")
t = physical.t
require = physical.require
HOST_LABEL = "topology.vallery.net/proxmox-host"


def contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(contains(a, b) for a, b in zip(actual, expected, strict=True))
        )
    return actual == expected


def validate_binding(profile, native, data):
    require(profile in t.PHYSICAL_TARGETS, "closed physical client profile required")
    require(
        set(native) == {"cluster", "pod", "pods", "nodes", "pvcs", "pvs", "service", "object_store"},
        "full physical native custody required",
    )
    cluster, pod, service = (native[k] for k in ("cluster", "pod", "service"))
    binding = data["binding"]
    for obj in (cluster, pod, service):
        physical.pitr.uid(obj["metadata"]["uid"])
        require(
            obj["metadata"]["namespace"] == physical.pitr.NS and not obj["metadata"].get("deletionTimestamp"),
            "physical native namespace/termination mismatch",
        )
    require(
        (cluster["metadata"]["name"], cluster["metadata"]["uid"], pod["metadata"]["name"], pod["metadata"]["uid"])
        == (profile, binding["cluster_uid"], binding["pod"], binding["pod_uid"]),
        "physical native UID/profile mismatch",
    )
    require(cluster["status"]["currentPrimary"] == pod["metadata"]["name"], "physical Pod not current primary")
    expected = next(
        o
        for o in physical.pitr.render(
            data["source_cluster"], data["backup"], data["markers"], data["recovery"].get("scheduled_backup")
        )
        if o["kind"] == "Cluster" and o["metadata"]["name"] == profile
    )
    for key in (
        "imageName",
        "instances",
        "storage",
        "walStorage",
        "postgresql",
        "bootstrap",
        "externalClusters",
        "affinity",
    ):
        require(contains(cluster["spec"][key], expected["spec"][key]), "physical recovery spec drift: " + key)
    require(not cluster["spec"].get("plugins"), "physical target archive writer forbidden")
    require(
        contains(cluster["metadata"].get("annotations", {}), expected["metadata"]["annotations"]),
        "physical backup/source annotation drift",
    )
    store = native["object_store"]
    expected_store = physical.pitr.render(
        data["source_cluster"], data["backup"], data["markers"], data["recovery"].get("scheduled_backup")
    )[0]
    require(
        store["metadata"]["namespace"] == physical.pitr.NS
        and store["metadata"]["name"] == physical.pitr.STORE
        and contains(store["spec"], expected_store["spec"])
        and not store["spec"].get("retentionPolicy"),
        "physical reader ObjectStore drift",
    )
    physical.pitr.uid(store["metadata"]["uid"])
    selector = service["spec"]["selector"]
    selectors = [selector[k] for k in ("role", "cnpg.io/instanceRole") if k in selector]
    require(
        service["metadata"]["name"] == profile + "-rw"
        and service["spec"].get("type", "ClusterIP") == "ClusterIP"
        and selector.get("cnpg.io/cluster") == profile
        and selectors
        and all(v == "primary" for v in selectors)
        and any(p["port"] == 5432 and p.get("targetPort", 5432) == 5432 for p in service["spec"]["ports"]),
        "wrong physical RW Service",
    )
    require(
        any(
            o.get("uid") == binding["cluster_uid"] and o.get("kind") == "Cluster"
            for o in service["metadata"].get("ownerReferences", [])
        ),
        "physical Service owner mismatch",
    )
    nodes = {n["metadata"]["name"]: n for n in native["nodes"]["items"]}
    domains, claims, addresses = set(), set(), set()
    pods = native["pods"]["items"]
    require(len(pods) == 3 and sum(p == pod for p in pods) == 1, "physical three-Pod/primary capture required")
    for member in pods:
        m, spec = member["metadata"], member["spec"]
        physical.pitr.uid(m["uid"])
        require(
            m["namespace"] == physical.pitr.NS
            and m["labels"].get("cnpg.io/cluster") == profile
            and re.fullmatch(re.escape(profile) + r"-[1-9]\d*", m["name"])
            and not m.get("deletionTimestamp")
            and any(c["type"] == "Ready" and c["status"] == "True" for c in member["status"]["conditions"])
            and any(
                o.get("uid") == binding["cluster_uid"] and o.get("kind") == "Cluster"
                for o in m.get("ownerReferences", [])
            )
            and any(c["name"] == "postgres" and c["image"] == physical.pitr.IMAGE for c in spec["containers"])
            and any(
                c["name"] == "postgres" and c.get("ready") and c.get("imageID", "").endswith(t.operator.DIGEST)
                for c in member["status"]["containerStatuses"]
            ),
            "physical Pod readiness/operand/owner drift",
        )
        node = nodes[spec["nodeName"]]
        physical.pitr.uid(node["metadata"]["uid"])
        domains.add(node["metadata"]["labels"][HOST_LABEL])
        address = ipaddress.ip_address(member["status"]["podIP"])
        require(address in ipaddress.ip_network("10.42.0.0/16"), "wrong physical Pod address")
        addresses.add(str(address))
        claims |= {v["persistentVolumeClaim"]["claimName"] for v in spec["volumes"] if "persistentVolumeClaim" in v}
    require(len(domains) == len(addresses) == 3, "physical domains/address custody incomplete")
    pvs = {v["metadata"]["name"]: v for v in native["pvs"]["items"]}
    pvcs = native["pvcs"]["items"]
    require(
        len(pvcs) == len(pvs) == len(claims) == 6 and {c["metadata"]["name"] for c in pvcs} == claims,
        "physical six-volume custody incomplete",
    )
    for claim in pvcs:
        m, spec = claim["metadata"], claim["spec"]
        physical.pitr.uid(m["uid"])
        require(
            m["namespace"] == physical.pitr.NS
            and m["labels"].get("cnpg.io/cluster") == profile
            and claim["status"]["phase"] == "Bound"
            and spec["storageClassName"] == "longhorn-v1-rwo"
            and any(
                o.get("uid") == binding["cluster_uid"] and o.get("kind") == "Cluster"
                for o in m.get("ownerReferences", [])
            ),
            "physical PVC owner/binding drift",
        )
        pv = pvs[spec["volumeName"]]
        physical.pitr.uid(pv["metadata"]["uid"])
        require(
            pv["spec"]["claimRef"]["uid"] == m["uid"]
            and pv["spec"]["claimRef"]["namespace"] == physical.pitr.NS
            and pv["spec"]["claimRef"]["name"] == m["name"]
            and pv["spec"]["csi"]["driver"] == "driver.longhorn.io"
            and pv["spec"]["csi"]["volumeHandle"],
            "physical PV/claim/driver custody mismatch",
        )
    return hashlib.sha256(json.dumps(native, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def wrapped_probe(profile):
    require(profile in t.PHYSICAL_TARGETS, "closed physical probe profile required")
    # Exactly the two source-owned identity literals differ. Startup helpers,
    # baked revision/module proofs, pools, readonly posture and hot SQL are reused.
    probe = ordinary.PROBE
    old_host = "assert os.environ['DB_HOST'] == '" + ordinary.HOST + "'"
    old_cluster = "assert identity['cluster_name'] == '" + ordinary.CLUSTER + "'"
    require(probe.count(old_host) == probe.count(old_cluster) == 1, "original ordinary probe identity shape changed")
    probe = probe.replace(
        old_host, "assert os.environ['DB_HOST'] == '" + profile + "-rw." + physical.pitr.NS + ".svc.cluster.local'", 1
    )
    probe = probe.replace(old_cluster, "assert identity['cluster_name'] == '" + profile + "'", 1)
    old_receipt = "'profile_sha256':binding['profile_sha256'],"
    require(probe.count(old_receipt) == 1, "original ordinary result custody changed")
    probe = probe.replace(
        old_receipt,
        old_receipt + " 'physical_cluster_uid':binding['physical_cluster_uid'],"
        " 'backup_uid':binding['backup_uid'], 'backup_id':binding['backup_id'],"
        " 'native_archive_inventory_sha256':binding['native_archive_inventory_sha256'],"
        " 'native_target_reached_sha256':binding['native_target_reached_sha256'],"
        " 'target_time':binding['target_time'], 'inherited_authentication_credit':False,",
        1,
    )
    require(ordinary.WRAPPED_PROBE.count(repr(ordinary.PROBE)) == 1, "original ordinary exception wrapper changed")
    return ordinary.WRAPPED_PROBE.replace(repr(ordinary.PROBE), repr(probe), 1)


def render(profile, native, data, installation, reviewed, reviewed_sha, consumer, image, source, module_sha, suffix):
    binding_sha = validate_binding(profile, native, data)
    bindings = physical.client_bindings(profile, data["binding"], installation, reviewed, reviewed_sha)
    require(consumer in ordinary.ROLES, "ordinary consumer required")
    require(
        re.fullmatch(r"registry\.vallery\.net/verdifyconsultancy/verdify-" + consumer + r"@sha256:[0-9a-f]{64}", image),
        "origin consumer digest required",
    )
    require(
        re.fullmatch(r"[0-9a-f]{40}", source) and re.fullmatch(r"[a-z0-9]{8,20}", suffix),
        "exact source/unique suffix required",
    )
    paths = {"api": "api/main.py", "ingestor": "ingestor/ingestor.py", "mcp": "mcp/server.py"}
    require(
        re.fullmatch(r"[0-9a-f]{64}", module_sha)
        and hashlib.sha256((ROOT / paths[consumer]).read_bytes()).hexdigest() == module_sha,
        "reviewed baked consumer module source mismatch",
    )
    login = bindings[consumer]["login"]
    host = bindings[consumer]["host"]
    facts = {
        "consumer": consumer,
        "login": login,
        "consumer_source": source,
        "consumer_image": image,
        "consumer_module_sha256": module_sha,
        "source_module_path": paths[consumer],
        "image_module_path": {
            "api": "/app/main.py",
            "ingestor": "/app/ingestor/ingestor.py",
            "mcp": "/app/mcp/server.py",
        }[consumer],
        "profile_sha256": reviewed_sha,
        "target_binding_sha256": binding_sha,
        "primary_address": native["pod"]["status"]["podIP"],
        "hot_sql": ordinary.hot_query(consumer),
        "physical_cluster_uid": data["binding"]["cluster_uid"],
        "physical_qualification_sha256": reviewed_sha,
        "backup_uid": data["backup"]["metadata"]["uid"],
        "backup_id": data["backup"]["status"]["backupId"],
        "native_archive_inventory_sha256": data["recovery"]["native_archive_inventory_sha256"],
        "native_target_reached_sha256": data["recovery"]["native_target_reached_sha256"],
        "target_time": data["recovery"]["target_time"],
        "credential_origin": "Agents fresh target-only password/SCRAM; no inherited auth credit",
    }
    component = "cnpg-physical-client-" + profile[-1]
    labels = {"app.kubernetes.io/part-of": "verdify", "app.kubernetes.io/component": component}
    env = [
        {"name": k, "value": v}
        for k, v in {
            "QUALIFICATION_BINDING": json.dumps(facts, sort_keys=True),
            "DB_HOST": host,
            "DB_PORT": "5432",
            "DB_NAME": t.DATABASE,
            "DB_USER": login,
            "VERDIFY_DEVICE_WRITE_ENABLED": "0",
            "VERDIFY_" + consumer.upper() + "_RUNTIME_DB_ROLE_REQUIRED": "1",
        }.items()
    ]
    env.append(
        {
            "name": "DB_PASSWORD",
            "valueFrom": {"secretKeyRef": {"name": profile + "-" + consumer + "-client-auth", "key": "password"}},
        }
    )
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": "cnpg-physical-" + profile[-1] + "-" + consumer + "-" + suffix,
            "namespace": physical.pitr.NS,
            "labels": labels,
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 180,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 10001,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "imagePullSecrets": [{"name": "zot-origin-cluster-pull"}],
                    "containers": [
                        {
                            "name": "client",
                            "image": image,
                            "command": ["python", "-I", "-c", wrapped_probe(profile)],
                            "env": env,
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"memory": "1Gi"}},
                        }
                    ],
                },
            },
        },
    }
    policies = copy.deepcopy(ordinary.policies())
    for policy in policies:
        policy["metadata"]["name"] = (
            "cnpg-physical-"
            + profile[-1]
            + ("-client-only" if "Egress" in policy["spec"]["policyTypes"] else "-client-ingress")
        )
    policies[0]["spec"]["podSelector"]["matchLabels"] = labels
    policies[0]["spec"]["egress"][0]["to"][0]["podSelector"]["matchLabels"] = {"cnpg.io/cluster": profile}
    policies[1]["spec"]["podSelector"]["matchLabels"] = {"cnpg.io/cluster": profile}
    policies[1]["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"] = labels
    return [*policies, job]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=t.PHYSICAL_TARGETS, required=True)
    physical.input_arguments(parser)
    for key in ("physical_install", "native_binding"):
        parser.add_argument("--" + key.replace("_", "-"), type=Path, required=True)
        parser.add_argument("--" + key.replace("_", "-") + "-sha256", required=True)
    parser.add_argument("--consumer", choices=ordinary.ROLES, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--module-sha256", required=True)
    parser.add_argument("--suffix", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data, post, reviewed_sha = physical.qualified_inputs(args)
    require(post is not None and reviewed_sha is not None, "actual physical rollback qualification required")
    installation, install_sha = t.c0.read_witness(args.physical_install)
    require(install_sha == args.physical_install_sha256, "physical install custody mismatch")
    native, native_sha = t.c0.read_witness(args.native_binding)
    require(native_sha == args.native_binding_sha256, "raw native physical custody mismatch")
    reviewed, sha = t.c0.read_witness(args.reviewed_physical)
    require(sha == reviewed_sha, "reviewed physical record changed")
    objects = render(
        args.profile,
        native,
        data,
        installation,
        reviewed,
        sha,
        args.consumer,
        args.image,
        args.source,
        args.module_sha256,
        args.suffix,
    )
    with args.output.open("x") as stream:
        json.dump({"apiVersion": "v1", "kind": "List", "items": objects}, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
