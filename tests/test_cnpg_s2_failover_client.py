"""Failover custody refuses stale target authority and ambiguous write replay."""

import copy
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
from test_cnpg_target_runtime_transition import private_pg  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(m):
    cluster = {
        "metadata": {"name": m.CLUSTER, "namespace": m.NS, "uid": m.CLUSTER_UID},
        "status": {"currentPrimary": m.CLUSTER + "-1", "readyInstances": 3},
        "spec": {
            "imageName": m.IMAGE,
            "instances": 3,
            "postgresql": {"parameters": {"timescaledb.max_background_workers": "0"}},
        },
    }
    pod = {
        "metadata": {
            "name": m.CLUSTER + "-1",
            "namespace": m.NS,
            "uid": "7c45cff0-5375-40ab-8cdd-ff7b638a53f1",
            "labels": {"cnpg.io/cluster": m.CLUSTER},
            "ownerReferences": [{"kind": "Cluster", "uid": m.CLUSTER_UID}],
        },
        "status": {"podIP": "10.42.3.107", "phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]},
    }
    return cluster, pod


def test_failover_job_only_routes_to_frozen_b_and_uses_actual_bootstrap_secret():
    m = load("render-cnpg-s2-failover-client")
    client, server, job = m.render(*fixture(m), "s2-failover-20261006-once")
    spec = job["spec"]["template"]["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert job["spec"]["activeDeadlineSeconds"] == 180 and job["spec"]["backoffLimit"] == 0
    env = {x["name"]: x for x in spec["containers"][0]["env"]}
    assert env["PGPASSWORD"] == {
        "name": "PGPASSWORD",
        "valueFrom": {"secretKeyRef": {"name": m.CLUSTER + "-app", "key": "password"}},
    }
    assert env["PGHOST"]["value"] == m.CLUSTER + "-rw." + m.NS + ".svc.cluster.local"
    assert client["spec"]["ingress"] == []
    assert client["spec"]["egress"][0] == {
        "to": [{"podSelector": {"matchLabels": {"cnpg.io/cluster": m.CLUSTER}}}],
        "ports": [{"protocol": "TCP", "port": 5432}],
    }
    assert server["spec"]["podSelector"] == {"matchLabels": {"cnpg.io/cluster": m.CLUSTER}}
    assert set(spec["containers"][0]) >= {"image", "command", "env", "securityContext"}


@pytest.mark.parametrize("mutation", ("uid", "namespace", "ready", "workers", "image", "primary", "pod_ready"))
def test_failover_refuses_stale_or_unqualified_native_binding(mutation):
    m = load("render-cnpg-s2-failover-client")
    c, p = copy.deepcopy(fixture(m))
    if mutation in ("uid", "namespace"):
        c["metadata"][mutation] = "other"
    elif mutation == "ready":
        c["status"]["readyInstances"] = 2
    elif mutation == "workers":
        c["spec"]["postgresql"]["parameters"]["timescaledb.max_background_workers"] = "16"
    elif mutation == "image":
        c["spec"]["imageName"] = "unqualified"
    elif mutation == "primary":
        p["metadata"]["name"] = "replacement"
    else:
        p["status"]["conditions"] = []
    with pytest.raises(ValueError, match="exact healthy"):
        m.render(c, p, "s2-failover-20261006-once")


@pytest.mark.parametrize("first_outcome", ("timeout", "warning"))
def test_ambiguous_first_commit_is_not_acknowledged_or_replayed(monkeypatch, capsys, first_outcome):
    m = load("cnpg-s2-failover-client")
    monkeypatch.setenv("FAILOVER_RUN_ID", "s2-failover-20261006-once")
    monkeypatch.setenv("FAILOVER_INITIAL_SERVER_ADDRESS", "10.42.3.107")
    times = iter((0, 1, 2, 3, 4, 5, 6, 7, 200, 201))
    monkeypatch.setattr(m.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    dispatched = []

    def run(command, **options):
        sql = options.get("input", "")
        if "INSERT INTO" not in sql:
            return subprocess.CompletedProcess(command, 0, "", "")
        dispatched.append(sql)
        if len(dispatched) == 1 and first_outcome == "timeout":
            raise subprocess.TimeoutExpired(command, options["timeout"])
        ack = json.dumps(
            {"kind": "ack", "sequence": len(dispatched), "marker_id": f"s2-failover-20261006-once-{len(dispatched)}"}
        )
        return subprocess.CompletedProcess(command, 0, ack + "\n", "sync wait canceled" if len(dispatched) == 1 else "")

    monkeypatch.setattr(m.subprocess, "run", run)
    m.main()
    records = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    attempts = [x for x in records if x["kind"] == "attempt"]
    assert len(dispatched) == 2
    assert "once-1'" in dispatched[0] and "once-2'" not in dispatched[0]
    assert "once-2'" in dispatched[1] and "once-1'" not in dispatched[1]
    assert "ack" not in attempts[0]
    assert attempts[1]["outcome"] == "acknowledged" and attempts[1]["ack"]["sequence"] == 2


def test_marker_protocol_requires_guarded_commit_before_ack():
    m = load("cnpg-s2-failover-client")
    sql = m.statement("s2-failover-20261006-once", 1)
    assert sql.index("COMMIT;") < sql.index("'kind','ack'")
    assert "inet_client_addr() IS NULL" in sql
    assert "timescaledb.max_background_workers')<>'0'" in sql
    for run_id, sequence in (
        ("unsafe';DROP TABLE x;--", 1),
        ("s2-failover-20261006-once", True),
        ("s2-failover-20261006-once", 0),
    ):
        with pytest.raises(ValueError):
            m.statement(run_id, sequence)


def test_bootstrap_absent_extension_guc_and_present_restoring_native_refusal(private_pg):  # noqa: F811
    m = load("cnpg-s2-failover-client")
    expression = "coalesce" + m.GUARD.split("OR coalesce", 1)[1].split("\n", 1)[0]
    assert private_pg("SELECT " + expression) == "f"
    for value, expected in (("off", "f"), ("on", "t"), ("", "t")):
        assert (
            private_pg("BEGIN; SET LOCAL timescaledb.restoring='" + value + "'; SELECT " + expression + "; ROLLBACK;")
            == expected
        )
    ddl = (ROOT / "db/qualification/cnpg-s2-failover-sentinel.sql").read_text()
    assert expression in ddl
    assert "EXISTS(SELECT 1 FROM pg_extension WHERE extname='timescaledb')" in m.GUARD
