"""Source-owned bounded Jobs keep database-only authority and honest lifecycle."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "render_endpoints", Path(__file__).parents[1] / "scripts/render-runtime-endpoint-reversal.py"
)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def binding():
    ends = {
        c: dict(
            cluster_uid=u,
            database_oid=16447,
            host=c + "-rw.verdify-db-rehearsal.svc.cluster.local",
            new275_admission_installed=True,
            full_data_sequence_seal=True,
            admission_sha256="a" * 64,
            installation_sha256="b" * 64,
        )
        for c, u in [
            ("verdify-cnpg-s2", "4f697776-df25-4e22-b930-0b76cf35496e"),
            (m.B, "3981d0f2-4c72-48ac-9e09-826fe6bd4ed6"),
        ]
    }
    roles = (
        "api",
        "ingestor",
        "mcp",
        "planner",
        "setpoint_server",
        "ha_backfill",
        "vision",
        "lab_publisher",
        "grafana",
    )
    profiles = {
        r: dict(
            image="registry/owning@sha256:" + "c" * 64,
            identity_kind="baked-image",
            module_sha256="d" * 64,
            module_path="/app/owning.py",
            image_source_sha="e" * 40,
        )
        for r in roles
    }
    profiles["grafana"].update(
        identity_kind="actual-grafana-server", qualifier_image="registry/qualifier@sha256:" + "f" * 64
    )
    return dict(schema="cnpg-current275-nine-endpoint-reversal-v1", endpoints=ends, profiles=profiles)


def test_all_nine_clients_suspended_with_no_provider_device_or_storage_authority():
    objects = m.render(binding(), {"probe.py": "source"})
    jobs = [o for o in objects if o["kind"] == "Job"]
    assert len(jobs) == 11
    for j in jobs:
        s = j["spec"]
        assert s["suspend"] and s["backoffLimit"] == 0 and s["activeDeadlineSeconds"] == 240
        pod = s["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        assert pod["nodeSelector"] == {"kubernetes.io/hostname": "node4"}
        assert not any(k in v for v in pod["volumes"] for k in ("persistentVolumeClaim", "hostPath"))
        for c in pod["containers"]:
            assert c["securityContext"]["capabilities"]["drop"] == ["ALL"]
            for e in c.get("env", []):
                if "valueFrom" in e:
                    assert e["valueFrom"]["secretKeyRef"]["name"].startswith("verdify-cnpg-s2")
    grafana = [j for j in jobs if "grafana" in j["metadata"]["name"]]
    assert all(len(j["spec"]["template"]["spec"]["containers"]) == 2 for j in grafana)
    assert all("/run.sh" in j["spec"]["template"]["spec"]["containers"][0]["command"][-1] for j in grafana)


def test_policies_add_only_exact_b5432_and_dns_without_device_or_api_routes():
    policies = [o for o in m.render(binding(), {}) if o["kind"] == "NetworkPolicy"]
    assert len(policies) == 2
    e = next(o["spec"] for o in policies if o["spec"]["policyTypes"] == ["Egress"])
    assert e["egress"][0] == {
        "to": [{"podSelector": {"matchLabels": {"cnpg.io/cluster": m.B}}}],
        "ports": [{"protocol": "TCP", "port": 5432}],
    }
    assert {p["port"] for rule in e["egress"] for p in rule["ports"]} == {5432, 53}


@pytest.mark.parametrize("bad", ("floating", "missing-role", "source-substitution"))
def test_refuse_incomplete_or_changed_owned_sources(bad):
    b = binding()
    if bad == "floating":
        b["profiles"]["api"]["image"] = "registry/owning:latest"
    elif bad == "missing-role":
        del b["profiles"]["vision"]
    else:
        b["profiles"]["vision"]["source_mounts"] = [
            dict(key="script.py", path="/vsrc/analyze-greenhouse-snapshot.py", sha256="a" * 64)
        ]
    with pytest.raises(ValueError):
        m.render(b, {"script.py": "changed-source"})
