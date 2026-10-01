"""Scope and durability contracts for the isolated #396 candidate."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cnpg_rehearsal", ROOT / "scripts/render-cnpg-rehearsal.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_candidate_requires_origin_digest_and_never_acquires_live_authority():
    with pytest.raises(ValueError):
        module.render("localhost/cnpg:latest")
    with pytest.raises(ValueError):
        module.render("registry.vallery.net/verdifyconsultancy/verdify-timescaledb-cnpg:latest")
    objects = module.render(
        "registry.vallery.net/verdifyconsultancy/verdify-timescaledb-cnpg:16.13-ts2.25.2@sha256:" + "a" * 64
    )
    assert {o["metadata"]["namespace"] for o in objects if o["kind"] != "Namespace"} == {"verdify-db-rehearsal"}
    assert not any(o["kind"] in {"Secret", "Deployment", "StatefulSet"} for o in objects)
    assert objects[0]["kind"] == "Namespace" and objects[0]["metadata"]["name"] == "verdify-db-rehearsal"
    cluster = objects[1]["spec"]
    assert cluster["instances"] == 3 and cluster["postgresql"]["synchronous"]["dataDurability"] == "required"
    assert cluster["affinity"]["podAntiAffinityType"] == "required"
    assert cluster["affinity"]["topologyKey"] == "topology.vallery.net/proxmox-host"
    assert cluster["storage"]["storageClass"] == "longhorn-v1-rwo"
    assert cluster["bootstrap"]["initdb"]["database"] != "verdify"
    text = str(objects)
    assert "DB_HOST" not in text and "verdify-prod" not in text
    assert all(o["metadata"]["name"] != "verdify-db" for o in objects)
    assert objects[-1]["spec"]["policyTypes"] == ["Ingress", "Egress"]
    isolation = next(
        o for o in objects if o["kind"] == "NetworkPolicy" and o["metadata"]["name"].endswith("-isolation")
    )
    api_rules = [r for r in isolation["spec"]["egress"] if any(port["port"] == 6443 for port in r["ports"])]
    assert len(api_rules) == 1
    assert {t["ipBlock"]["cidr"] for t in api_rules[0]["to"]} == {
        "192.168.30.240/32",
        "192.168.30.31/32",
        "192.168.30.32/32",
        "192.168.30.33/32",
    }
    # A database port may reach only isolated same-namespace peers, never prod/IP blocks.
    assert all(
        "ipBlock" not in t and "namespaceSelector" not in t
        for r in isolation["spec"]["egress"]
        if any(port["port"] == 5432 for port in r["ports"])
        for t in r["to"]
    )


def test_operand_restores_required_vector_version_from_verified_source():
    source = (ROOT / "deploy/k8s/cnpg/image/Dockerfile").read_text()
    assert "ARG PGVECTOR_VERSION=0.8.1" in source
    assert "sha256sum -c -" in source
    assert "standard-bookworm@sha256:" in source
