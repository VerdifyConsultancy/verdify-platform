"""Closed physical renderer refusal/source reuse; no actual auth or recovery proof."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from test_cnpg_pitr_pair import fixture as pitr_fixture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("physical_client", ROOT / "scripts/cnpg-physical-client-qualification.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def fixture(profile):
    source, backup, markers = pitr_fixture()
    rendered = m.physical.pitr.render(source, backup, markers)
    cluster = copy.deepcopy(next(o for o in rendered if o["kind"] == "Cluster" and o["metadata"]["name"] == profile))
    cluster["metadata"]["uid"] = uid(1)
    cluster["status"] = {"currentPrimary": profile + "-1"}
    pods, nodes, claims, volumes = [], [], [], []
    for n in range(1, 4):
        name = profile + "-" + str(n)
        owner = [{"kind": "Cluster", "uid": uid(1)}]
        claim_names = [name, name + "-wal"]
        pods.append(
            {
                "metadata": {
                    "name": name,
                    "namespace": m.physical.pitr.NS,
                    "uid": uid(10 + n),
                    "labels": {"cnpg.io/cluster": profile},
                    "ownerReferences": owner,
                },
                "spec": {
                    "nodeName": "node" + str(n),
                    "containers": [{"name": "postgres", "image": m.physical.pitr.IMAGE}],
                    "volumes": [{"persistentVolumeClaim": {"claimName": cn}} for cn in claim_names],
                },
                "status": {
                    "podIP": "10.42.0." + str(n),
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "containerStatuses": [{"name": "postgres", "ready": True, "imageID": m.physical.pitr.IMAGE}],
                },
            }
        )
        nodes.append(
            {"metadata": {"name": "node" + str(n), "uid": uid(20 + n), "labels": {m.HOST_LABEL: "host" + str(n)}}}
        )
        for offset, cn in enumerate(claim_names):
            cuid = uid(100 + n * 2 + offset)
            claims.append(
                {
                    "metadata": {
                        "name": cn,
                        "namespace": m.physical.pitr.NS,
                        "uid": cuid,
                        "labels": {"cnpg.io/cluster": profile},
                        "ownerReferences": owner,
                    },
                    "spec": {"storageClassName": "longhorn-v1-rwo", "volumeName": cn},
                    "status": {"phase": "Bound"},
                }
            )
            volumes.append(
                {
                    "metadata": {"name": cn, "uid": uid(200 + n * 2 + offset)},
                    "spec": {
                        "claimRef": {"name": cn, "namespace": m.physical.pitr.NS, "uid": cuid},
                        "csi": {"driver": "driver.longhorn.io", "volumeHandle": cn},
                    },
                }
            )
    service = {
        "metadata": {
            "name": profile + "-rw",
            "namespace": m.physical.pitr.NS,
            "uid": uid(30),
            "ownerReferences": [{"kind": "Cluster", "uid": uid(1)}],
        },
        "spec": {
            "selector": {"cnpg.io/cluster": profile, "cnpg.io/instanceRole": "primary"},
            "ports": [{"port": 5432, "targetPort": 5432}],
        },
    }
    store = copy.deepcopy(rendered[0])
    store["metadata"]["uid"] = uid(40)
    binding = {
        "namespace": m.physical.pitr.NS,
        "cluster": profile,
        "cluster_uid": uid(1),
        "pod": pods[0]["metadata"]["name"],
        "pod_uid": pods[0]["metadata"]["uid"],
        "operand_digest": m.t.operator.DIGEST,
    }
    data = {
        "binding": binding,
        "source_cluster": source,
        "backup": backup,
        "markers": markers,
        "recovery": {
            "scheduled_backup": None,
            "native_archive_inventory_sha256": "a" * 64,
            "native_target_reached_sha256": "b" * 64,
            "target_time": markers["targets"][profile[-1].upper()],
        },
    }
    native = {
        "cluster": cluster,
        "pod": pods[0],
        "pods": {"items": pods},
        "nodes": {"items": nodes},
        "pvcs": {"items": claims},
        "pvs": {"items": volumes},
        "service": service,
        "object_store": store,
    }
    return native, data


@pytest.mark.parametrize("profile", m.t.PHYSICAL_TARGETS)
@pytest.mark.parametrize("consumer", ("api", "ingestor", "mcp"))
def test_closed_render_reuses_baked_pools_and_hot_sql(profile, consumer, monkeypatch):
    native, data = fixture(profile)
    monkeypatch.setattr(
        m.physical,
        "client_bindings",
        lambda *args: {
            c: {"login": m.ordinary.ROLES[c], "host": profile + "-rw." + m.physical.pitr.NS + ".svc.cluster.local"}
            for c in m.ordinary.ROLES
        },
    )
    path = {"api": "api/main.py", "ingestor": "ingestor/ingestor.py", "mcp": "mcp/server.py"}[consumer]
    module_sha = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
    objects = m.render(
        profile,
        native,
        data,
        {},
        {},
        "c" * 64,
        consumer,
        "registry.vallery.net/verdifyconsultancy/verdify-" + consumer + "@sha256:" + "d" * 64,
        "e" * 40,
        module_sha,
        "finite123",
    )
    job = objects[-1]
    container = job["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e for e in container["env"]}
    assert env["DB_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
        "name": profile + "-" + consumer + "-client-auth",
        "key": "password",
    }
    assert job["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    assert job["spec"]["backoffLimit"] == 0
    assert m.ordinary.CLUSTER not in container["command"][3]
    assert profile in container["command"][3]
    assert (
        "consumer._init_db_connection" in container["command"][3]
        and "consumer.attest_ordinary_ingestor_runtime_role" in container["command"][3]
    )
    assert (
        "consumer._kpi_fanout_pool_get" in container["command"][3] and "asyncpg.create_pool" in container["command"][3]
    )
    assert (
        "service_process_started" in container["command"][3]
        and "inherited_authentication_credit" in container["command"][3]
    )
    facts = json.loads(env["QUALIFICATION_BINDING"]["value"])
    assert (
        facts["hot_sql"] == m.ordinary.hot_query(consumer) and facts["backup_uid"] == data["backup"]["metadata"]["uid"]
    )
    assert facts["physical_cluster_uid"] == data["binding"]["cluster_uid"]
    assert objects[0]["spec"]["egress"][0]["to"][0]["podSelector"]["matchLabels"] == {"cnpg.io/cluster": profile}
    assert set(objects[0]["spec"]["egress"][1]["to"][0]) == {"namespaceSelector", "podSelector"}
    assert "RoleBinding" not in json.dumps(objects) and "verdify-prod" not in json.dumps(objects)


@pytest.mark.parametrize(
    "case",
    ("source_uid", "pod_uid", "reader_writer", "archive_writer", "pv_uid", "domain", "image", "target_time", "service"),
)
def test_native_physical_refuses_identity_storage_or_recovery_drift(case):
    profile = m.t.PHYSICAL_TARGETS[0]
    native, data = fixture(profile)
    assert m.validate_binding(profile, native, data)
    if case == "source_uid":
        native["cluster"]["metadata"]["uid"] = m.physical.pitr.SOURCE_UID
    elif case == "pod_uid":
        native["pod"]["metadata"]["uid"] = uid(999)
    elif case == "reader_writer":
        native["object_store"]["spec"]["configuration"]["s3Credentials"]["accessKeyId"]["name"] = "writer"
    elif case == "archive_writer":
        native["cluster"]["spec"]["plugins"] = [{"name": "archive-writer"}]
    elif case == "pv_uid":
        native["pvs"]["items"][0]["spec"]["claimRef"]["uid"] = uid(999)
    elif case == "domain":
        native["nodes"]["items"][1]["metadata"]["labels"][m.HOST_LABEL] = "host1"
    elif case == "image":
        native["pod"]["status"]["containerStatuses"][0]["imageID"] = "wrong"
    elif case == "target_time":
        native["cluster"]["spec"]["bootstrap"]["recovery"]["recoveryTarget"]["targetTime"] = "wrong"
    else:
        native["service"]["spec"]["selector"]["cnpg.io/cluster"] = m.ordinary.CLUSTER
    with pytest.raises(ValueError):
        m.validate_binding(profile, native, data)


def test_original_adapter_stays_closed_and_physical_probe_has_no_generic_override():
    assert m.ordinary.CLUSTER == "verdify-cnpg-rehearsal"
    assert "assert identity['cluster_name'] == 'verdify-cnpg-rehearsal'" in m.ordinary.PROBE
    for profile in ("verdify-prod", "verdify-db", m.ordinary.CLUSTER, "other"):
        with pytest.raises(ValueError):
            m.wrapped_probe(profile)


@pytest.mark.parametrize("profile", m.t.PHYSICAL_TARGETS)
@pytest.mark.parametrize(
    "bad",
    [
        None,
        "login",
        "database",
        "version",
        "cluster",
        "replica",
        "backend",
        "readonly",
        "readonly_reset",
        "password",
        "close",
    ],
)
def test_physical_probe_reuses_full_883_driver_refusals(profile, bad, monkeypatch, capsys):
    from test_cnpg_runtime_client_qualification import (
        client,
        isolated_probe_process_state,
        test_probe_pool_startup_and_identity_fail_closed_without_secret_output,
    )

    wrapped = m.wrapped_probe(profile)
    monkeypatch.setattr(client, "CLUSTER", profile)
    monkeypatch.setattr(client, "HOST", profile + "-rw." + m.physical.pitr.NS + ".svc.cluster.local")
    monkeypatch.setattr(client, "WRAPPED_PROBE", wrapped)
    setenv = monkeypatch.setenv

    def inject_fixture_custody(name, value, **kwargs):
        if name == "QUALIFICATION_BINDING":
            facts = json.loads(value)
            facts.update(
                {
                    "physical_cluster_uid": uid(1),
                    "backup_uid": uid(2),
                    "backup_id": "fixture-id",
                    "native_archive_inventory_sha256": "a" * 64,
                    "native_target_reached_sha256": "b" * 64,
                    "target_time": "2026-10-01T12:00:00Z",
                }
            )
            value = json.dumps(facts)
        return setenv(name, value, **kwargs)

    monkeypatch.setattr(monkeypatch, "setenv", inject_fixture_custody)
    with isolated_probe_process_state():
        test_probe_pool_startup_and_identity_fail_closed_without_secret_output(monkeypatch, capsys, bad)


def test_native_identity_accepts_metadata_refresh_but_not_primary_address_drift():
    profile = m.t.PHYSICAL_TARGETS[0]
    native, data = fixture(profile)
    native["pod"] = copy.deepcopy(native["pod"])
    native["pod"]["metadata"]["resourceVersion"] = "new-native-read"
    assert m.validate_binding(profile, native, data)
    native["pod"]["status"]["podIP"] = "10.42.99.99"
    with pytest.raises(ValueError, match="primary address"):
        m.validate_binding(profile, native, data)
