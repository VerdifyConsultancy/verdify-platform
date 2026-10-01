"""Offline scope/refusal tests; no native fault, SQL, or Kubernetes execution."""

import copy
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "fault", Path(__file__).parents[1] / "scripts/cnpg-existing-target-fault.py"
)
f = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(f)


def uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def snapshot():
    nodes, pods, pvcs, pvs = [], [], [], []
    for n in range(1, 4):
        node = f"node{n}"
        nodes.append({"metadata": {"name": node, "uid": uid(n), "labels": {f.HOST: f"host{n}"}}})
        name = f.CLUSTER + f"-{n}"
        claims = []
        for offset in (0, 10):
            cn = name + ("-wal" if offset else "")
            claims.append({"persistentVolumeClaim": {"claimName": cn}})
            pvcs.append(
                {
                    "metadata": {
                        "name": cn,
                        "namespace": f.NS,
                        "uid": uid(100 + n + offset),
                        "labels": {"cnpg.io/cluster": f.CLUSTER},
                        "ownerReferences": [{"kind": "Cluster", "uid": f.UID}],
                    },
                    "spec": {"storageClassName": "longhorn-v1-rwo", "volumeName": cn},
                    "status": {"phase": "Bound"},
                }
            )
            pvs.append(
                {
                    "metadata": {"name": cn, "uid": uid(200 + n + offset)},
                    "spec": {
                        "claimRef": {"namespace": f.NS, "name": cn, "uid": uid(100 + n + offset)},
                        "csi": {"driver": "driver.longhorn.io", "volumeHandle": cn},
                    },
                }
            )
        pods.append(
            {
                "metadata": {
                    "name": name,
                    "namespace": f.NS,
                    "uid": uid(300 + n),
                    "labels": {"cnpg.io/cluster": f.CLUSTER},
                    "ownerReferences": [{"kind": "Cluster", "uid": f.UID}],
                },
                "spec": {"nodeName": node, "containers": [{"name": "postgres", "image": f.IMAGE}], "volumes": claims},
                "status": {"podIP": f"10.0.0.{n}", "conditions": [{"type": "Ready", "status": "True"}]},
            }
        )
    return {
        "cluster": {
            "metadata": {"name": f.CLUSTER, "namespace": f.NS, "uid": f.UID},
            "spec": {
                "imageName": f.IMAGE,
                "instances": 3,
                "postgresql": {
                    "synchronous": {"method": "any", "number": 1, "dataDurability": "required", "failoverQuorum": True}
                },
                "storage": {"size": "30Gi", "storageClass": "longhorn-v1-rwo", "resizeInUseVolumes": True},
                "walStorage": {"size": "10Gi", "storageClass": "longhorn-v1-rwo", "resizeInUseVolumes": True},
            },
            "status": {"currentPrimary": f.CLUSTER + "-1"},
        },
        "pods": {"items": pods},
        "nodes": {"items": nodes},
        "pvcs": {"items": pvcs},
        "pvs": {"items": pvs},
        "service": {
            "metadata": {
                "name": f.CLUSTER + "-rw",
                "namespace": f.NS,
                "uid": uid(400),
                "ownerReferences": [{"kind": "Cluster", "uid": f.UID}],
            },
            "spec": {
                "selector": {"cnpg.io/cluster": f.CLUSTER, "cnpg.io/instanceRole": "primary"},
                "ports": [{"port": 5432, "targetPort": 5432}],
            },
        },
    }


def facts(binding):
    return {
        name: {
            "server": 160013,
            "database": "postgres",
            "cluster": f.CLUSTER,
            "recovery": name != binding["primary"],
            "system_identifier": "1234",
            "timeline": 1,
            "synchronous_commit": "on",
            "fsync": "on",
            "full_page_writes": "on",
            "standby_names": "ANY 1 (" + ",".join('"' + x + '"' for x in binding["pods"]) + ")",
            "standbys": [
                {"name": x, "state": "streaming", "sync_state": "quorum", "flush_lsn": "0/A"}
                for x in binding["pods"]
                if x != binding["primary"]
            ],
        }
        for name in binding["pods"]
    }


def test_renderer_closed_scope_and_host():
    objects, b, ticket = f.render(snapshot(), "finite123")
    pod = objects[-1]
    assert pod["spec"]["nodeSelector"][f.HOST] != b["pods"][b["primary"]]["domain"]
    assert ticket == {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": uid(301)}}
    encoded = json.dumps(objects)
    assert "verdify-prod" not in encoded and "RoleBinding" not in encoded and '"kind": "Secret"' not in encoded
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert [
        e["valueFrom"]["secretKeyRef"]
        for e in pod["spec"]["containers"][0]["env"]
        if "secretKeyRef" in e.get("valueFrom", {})
    ] == [{"name": f.CLUSTER + "-app", "key": "password"}]
    assert objects[0]["spec"]["egress"][1]["to"][0].keys() == {"namespaceSelector", "podSelector"}


@pytest.mark.parametrize("case", ["uid", "image", "host", "storage", "service", "pv"])
def test_binding_refuses_drift(case):
    s = snapshot()
    if case == "uid":
        s["cluster"]["metadata"]["uid"] = uid(999)
    elif case == "image":
        s["cluster"]["spec"]["imageName"] = "wrong"
    elif case == "host":
        s["nodes"]["items"][1]["metadata"]["labels"][f.HOST] = "host1"
    elif case == "storage":
        s["cluster"]["spec"]["walStorage"]["size"] = "20Gi"
    elif case == "service":
        s["service"]["spec"]["selector"]["cnpg.io/instanceRole"] = "replica"
    else:
        s["pvs"]["items"][0]["spec"]["claimRef"]["uid"] = uid(999)
    with pytest.raises(ValueError):
        f.bind(s)


@pytest.mark.parametrize("case", ["writer", "fsync", "standby", "names"])
def test_quorum_native_refusals(case):
    b = f.bind(snapshot())
    data = facts(b)
    assert f.quorum(b, data)["system_identifier"] == "1234"
    if case == "writer":
        data[f.CLUSTER + "-2"]["recovery"] = False
    elif case == "fsync":
        data[b["primary"]]["fsync"] = "off"
    elif case == "standby":
        data[b["primary"]]["standbys"][0]["sync_state"] = "async"
    else:
        data[b["primary"]]["standby_names"] = "ANY 1 (*)"
    with pytest.raises(ValueError):
        f.quorum(b, data)


def test_promotion_requires_old_uid_absent_and_storage_unchanged():
    before = f.bind(snapshot())
    after = copy.deepcopy(before)
    after["primary"] = f.CLUSTER + "-2"
    with pytest.raises(ValueError, match="old primary"):
        f.promoted(before, after, facts(after))
    after["pods"][before["primary"]]["uid"] = uid(888)
    assert f.promoted(before, after, facts(after))["timeline"] == 1
    after["storage"][next(iter(after["storage"]))]["pv_uid"] = uid(777)
    with pytest.raises(ValueError, match="storage"):
        f.promoted(before, after, facts(after))


def test_unknown_commit_preserved_not_credited(monkeypatch, tmp_path):
    evidence = f.Evidence(tmp_path / "exclusive")
    calls = []

    def timeout(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, 25, output=b"COMMIT\n", stderr=b"lost response")

    monkeypatch.setattr(f.subprocess, "run", timeout)
    with pytest.raises(ValueError):
        evidence.command("commit-service-1", ["fixture"], "safe SQL")
    events = [json.loads(x) for x in evidence.events.read_text().splitlines()]
    assert len(calls) == 1 and events[0]["event"] == "attempt"
    assert events[1]["unknown_commit_status"] and events[1]["stdout"] == "COMMIT\n"
    assert events[1]["returncode"] is None
    with pytest.raises(FileExistsError):
        f.Evidence(tmp_path / "exclusive")


def test_native_ack_requires_server_and_real_clock():
    b = f.bind(snapshot())
    value = {
        "kind": "native-service-ack",
        "seq": 0,
        "marker": "finite123-0",
        "database": f.APP,
        "user": f.APP,
        "cluster": f.CLUSTER,
        "server": 160013,
        "recovery": False,
        "server_addr": "10.0.0.1",
        "flush_lsn": "0/A",
        "server_utc": "2026-10-01T12:00:00+00:00",
    }
    assert f.ack(value, "finite123", 0, b) == value
    value["server_addr"] = "10.0.0.2"
    with pytest.raises(ValueError):
        f.ack(value, "finite123", 0, b)
    with pytest.raises(ValueError):
        f.json_line("COMMIT\n", "native-service-ack")


def test_sql_product_boundary_and_idempotence():
    for sql in (f.init_sql("finite123"), f.commit_sql("finite123", 1), f.probe_sql("finite123")):
        assert "verdify_rehearsal" not in sql and "rehearsal_bootstrap" in sql
        assert "DROP " not in sql and "TRUNCATE" not in sql
    assert "ON CONFLICT(seq) DO NOTHING" in f.commit_sql("finite123", 1)
    assert "RAISE EXCEPTION 'idempotent sentinel conflicts" in f.commit_sql("finite123", 1)
    assert "READ ONLY" in f.probe_sql("finite123")


def test_network_policy_additive_selection_and_default_projection():
    assert f.selects({}, {"x": "y"})
    assert not f.selects({"matchExpressions": [{"key": "x", "operator": "NotIn", "values": ["y"]}]}, {"x": "y"})
    assert f.contains({"image": "fixed", "terminationMessagePolicy": "File"}, {"image": "fixed"})
    assert not f.contains([{"name": "a"}, {"name": "injected"}], [{"name": "a"}])


def test_private_pg_sentinel_conflict_and_shape(tmp_path, monkeypatch):
    """Synthetic PG16 socket exercises SQL only; actual TLS/160013 guard refuses it."""
    import os
    import tempfile

    pg = Path(os.environ.get("CNPG_TEST_PG_BIN", "/nonexistent"))
    if not (pg / "initdb").exists():
        pytest.skip("set CNPG_TEST_PG_BIN for private native SQL fixture")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "DB_", "POSTGRES_"))}
    env["LC_ALL"] = "C"
    fixture_dir = tempfile.TemporaryDirectory(prefix="c5fault-", dir="/tmp")
    socket = Path(fixture_dir.name)
    data = tmp_path / "data"

    def command(argv, **kwargs):
        return subprocess.run(argv, text=True, capture_output=True, env=env, timeout=30, **kwargs)

    def sql(query, *, role="rehearsal_bootstrap", database="rehearsal_bootstrap"):
        return command(
            [
                str(pg / "psql"),
                "-X",
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-h",
                str(socket),
                "-p",
                "55479",
                "-U",
                role,
                "-d",
                database,
            ],
            input=query,
        )

    assert (
        command(
            [
                str(pg / "initdb"),
                "-D",
                str(data),
                "-U",
                "fixture_owner",
                "--auth-local=trust",
                "--auth-host=reject",
                "--no-locale",
            ]
        ).returncode
        == 0
    )
    started = False
    try:
        start = command(
            [
                str(pg / "pg_ctl"),
                "-D",
                str(data),
                "-l",
                str(tmp_path / "server.log"),
                "-o",
                f"-k {socket} -p 55479 -c listen_addresses='' -c cluster_name={f.CLUSTER}",
                "-w",
                "start",
            ]
        )
        assert start.returncode == 0, start.stderr
        started = True
        setup = sql(
            "CREATE ROLE rehearsal_bootstrap LOGIN; CREATE DATABASE rehearsal_bootstrap OWNER rehearsal_bootstrap;",
            role="fixture_owner",
            database="postgres",
        )
        assert setup.returncode == 0, setup.stderr
        refused = sql(f.init_sql("finite123"))
        assert refused.returncode != 0 and "existing target client refuses" in refused.stderr
        role_guard = (
            f.client_guard()
            .replace("current_setting('server_version_num')::int<>160013", "false")
            .replace("inet_client_addr() IS NULL", "false")
            .replace("OR NOT coalesce((SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()),false)", "OR false")
        )
        assert sql(role_guard).returncode == 0
        for privilege, disabled in (
            ("SUPERUSER", "NOSUPERUSER"),
            ("CREATEDB", "NOCREATEDB"),
            ("CREATEROLE", "NOCREATEROLE"),
            ("BYPASSRLS", "NOBYPASSRLS"),
        ):
            assert (
                sql(
                    "ALTER ROLE rehearsal_bootstrap " + privilege + ";", role="fixture_owner", database="postgres"
                ).returncode
                == 0
            )
            denied = sql(role_guard)
            assert denied.returncode != 0 and "existing target client refuses" in denied.stderr
            assert (
                sql(
                    "ALTER ROLE rehearsal_bootstrap " + disabled + ";", role="fixture_owner", database="postgres"
                ).returncode
                == 0
            )
        monkeypatch.setattr(f, "client_guard", lambda: role_guard)
        assert sql(f.init_sql("finite123")).returncode == 0
        assert sql(f.init_sql("finite123")).returncode != 0
        for _ in range(2):
            result = sql(f.commit_sql("finite123", 1))
            assert result.returncode == 0, result.stderr
            assert f.json_line(result.stdout, "native-service-ack")["seq"] == 1
        result = sql(f.probe_sql("finite123"))
        assert result.returncode == 0, result.stderr
        assert f.json_line(result.stdout, "native-service-read")["rows"] == [[1, "finite123-1"]]
        assert sql("UPDATE public.cnpg_recovery_finite123 SET marker='unexpected';").returncode == 0
        conflict = sql(f.commit_sql("finite123", 1))
        assert conflict.returncode != 0 and "idempotent sentinel conflicts" in conflict.stderr
        assert sql("ALTER TABLE public.cnpg_recovery_finite123 ADD COLUMN changed integer;").returncode == 0
        assert sql(f.probe_sql("finite123")).returncode != 0
    finally:
        if started:
            command([str(pg / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"])
        fixture_dir.cleanup()


@pytest.mark.parametrize(
    "delete_error",
    [
        "unexpected EOF",
        "Error from server (Forbidden): forbidden",
        "Error from server (Conflict): UID mismatch",
        "Error from server (NotFound): gone",
    ],
)
def test_finite_runner_one_delete_unknown_commit_and_all_ack_rows(monkeypatch, tmp_path, delete_error):
    source = snapshot()
    recovery = copy.deepcopy(source)
    recovery["cluster"]["status"]["currentPrimary"] = f.CLUSTER + "-2"
    recovery["pods"]["items"][0]["metadata"]["uid"] = uid(888)
    state = {"deleted": False, "unknown": False, "rows": {}, "deletes": 0}

    class FakeTarget:
        def __init__(self, evidence):
            self.evidence = evidence
            self.base = ["fixture-kubectl"]

        def snapshot(self):
            return recovery if state["deleted"] else source

        def client(self, *args):
            pass

        def facts(self, binding):
            return facts(binding)

        def sql(self, name, poduid, query, *, client=False, label="native-read"):
            b = f.bind(self.snapshot())
            if label == "init-exclusive-sentinel":
                return ""
            if label.startswith("commit"):
                seq = 0 if label == "commit-baseline" else int(label.rsplit("-", 1)[1])
                state["rows"][seq] = "finite123-" + str(seq)
                if seq == 1 and not state["unknown"]:
                    state["unknown"] = True
                    self.evidence.record({"event": "fixture-unknown-commit", "seq": seq})
                    raise ValueError("lost response; commit remains unknown")
                return json.dumps(
                    {
                        "kind": "native-service-ack",
                        "seq": seq,
                        "marker": state["rows"][seq],
                        "database": f.APP,
                        "user": f.APP,
                        "cluster": f.CLUSTER,
                        "server": 160013,
                        "recovery": False,
                        "server_addr": b["pods"][b["primary"]]["pod_ip"],
                        "flush_lsn": "0/A",
                        "server_utc": "2026-10-01T12:00:00+00:00",
                    }
                )
            return json.dumps(
                {
                    "kind": "native-service-read",
                    "rows": list(state["rows"].items()),
                    "server_addr": b["pods"][b["primary"]]["pod_ip"],
                }
            )

    def rpc(argv, **kwargs):
        assert argv[1] == "delete" and "--raw" in argv
        assert json.loads(kwargs["input"])["preconditions"]["uid"] == uid(301)
        assert all(x not in argv for x in ("--force", "--now", "--grace-period=0"))
        state["deletes"] += 1
        state["deleted"] = True
        return subprocess.CompletedProcess(argv, 1, "", delete_error)

    monkeypatch.setattr(f, "ExistingTarget", FakeTarget)
    monkeypatch.setattr(f.subprocess, "run", rpc)
    monkeypatch.setattr(f.time, "sleep", lambda _: None)
    if delete_error != "unexpected EOF":
        with pytest.raises(ValueError, match="DELETE rejected"):
            f.run(source, "finite123", uid(999), tmp_path / "evidence")
        assert state["deletes"] == 1 and not state["unknown"]
        events = [json.loads(x) for x in (tmp_path / "evidence/events.jsonl").read_text().splitlines()]
        assert any(e["event"] == "delete-definitively-rejected" for e in events)
        assert not any(e.get("sentinel_fault") == "qualified" for e in events)
        return
    result = f.run(source, "finite123", uid(999), tmp_path / "evidence")
    assert state["deletes"] == 1 and state["unknown"]
    assert [a["seq"] for a in result["acknowledged"]] == [0, 1, 2, 3]
    assert result["rpo_acknowledged_rows_lost"] == 0 and not result["c5_complete"]
    events = [json.loads(x) for x in (tmp_path / "evidence/events.jsonl").read_text().splitlines()]
    assert any(e["event"] == "delete-result-unknown-or-failed" for e in events)
    assert any(e["event"] == "failed-intermediate-round" for e in events)
    assert result["rto_observer_upper_bound_seconds"] >= 0


@pytest.mark.parametrize(
    "message",
    [
        "Error from server (Forbidden): forbidden",
        "Error from server (Conflict): UID mismatch",
        "Error from server (NotFound): gone",
        "unknown local command failure",
    ],
)
def test_definitive_delete_error_cannot_be_unknown(message):
    assert not f.delete_unknown(f.CommandError("delete-primary-once", 1, "", message))


@pytest.mark.parametrize(
    "code,message", [(None, ""), (1, "unexpected EOF"), (1, "connection reset by peer"), (1, "i/o timeout")]
)
def test_transport_lost_delete_remains_unknown(code, message):
    assert f.delete_unknown(f.CommandError("delete-primary-once", code, "", message))


def test_delete_native_response_requires_exact_scope():
    name, exact = f.CLUSTER + "-1", uid(301)
    pod = {
        "kind": "Pod",
        "metadata": {"namespace": f.NS, "name": name, "uid": exact, "deletionTimestamp": "2026-10-01T12:00:00Z"},
    }
    f.validate_delete_response(json.dumps(pod), name, exact)
    status = {"kind": "Status", "status": "Success", "details": {"name": name, "uid": exact, "kind": "pods"}}
    f.validate_delete_response(json.dumps(status), name, exact)
    for bad in (
        {"kind": "Status", "status": "Success"},
        {"kind": "Status", "status": "Failure", "details": status["details"]},
        {"kind": "Pod", "metadata": {**pod["metadata"], "uid": uid(999)}},
        {"kind": "Pod", "metadata": {**pod["metadata"], "namespace": "verdify-prod"}},
    ):
        with pytest.raises(ValueError):
            f.validate_delete_response(json.dumps(bad), name, exact)


@pytest.mark.parametrize(
    "extra",
    [
        {"capabilities": {"drop": ["ALL"], "add": ["NET_ADMIN"]}},
        {"privileged": True},
        {"procMount": "Unmasked"},
        {"runAsUser": 0},
    ],
)
def test_live_client_extra_privilege_rejected(extra, monkeypatch):
    manifests, baseline, _ = f.render(snapshot(), "finite123")
    expected = manifests[-1]
    live = copy.deepcopy(expected)
    live["metadata"]["uid"] = uid(999)
    live["status"] = {"conditions": [{"type": "Ready", "status": "True"}]}
    live["spec"]["containers"][0]["securityContext"].update(extra)
    target = f.ExistingTarget(None)
    monkeypatch.setattr(target, "get", lambda *a, **kw: live)
    with pytest.raises(ValueError):
        target.client(expected, uid(999), baseline)
