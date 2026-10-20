"""Routing fidelity and privilege regression for the tokenless Verdify edge (#1006)."""

from __future__ import annotations

import copy
import importlib.util
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verdify_file_routes", ROOT / "scripts/generate-verdify-traefik-file.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture(scope="module")
def documents():
    rendered = subprocess.check_output(["kustomize", "build", "deploy/k8s/overlays/prod"], cwd=ROOT, text=True)
    return [d for d in yaml.safe_load_all(rendered) if d]


def test_generated_projection_and_all_routes_are_current(documents):
    actual = MODULE.compile_routes(documents)
    assert yaml.safe_load(MODULE.OUTPUT.read_text()) == actual
    routes = [
        d for d in documents if d["kind"] == "IngressRoute" and d["metadata"]["labels"].get("traefik-tier") == "verdify"
    ]
    assert len(actual["http"]["routers"]) == 1 + sum(len(d["spec"]["routes"]) for d in routes)
    for route in routes:
        for index, source in enumerate(route["spec"]["routes"]):
            key = f"{route['metadata']['name']}-{index}"
            projected = actual["http"]["routers"][key]
            assert projected["rule"] == source["match"]
            assert projected.get("priority") == source.get("priority")
            assert projected["middlewares"] == [m["name"] for m in source.get("middlewares", [])]
            backend = source["services"][0]
            server = actual["http"]["services"][key]["loadBalancer"]["servers"][0]
            assert server["url"] == f"http://{backend['name']}.verdify-prod.svc.cluster.local:{backend['port']}"
    assert actual["http"]["routers"]["verdify-t2-graphs-0"]["priority"] == 200
    assert actual["http"]["routers"]["verdify-t2-graphs-1"]["priority"] == 100
    assert "PathPrefix(`/mcp`)" in actual["http"]["routers"]["verdify-t2-mcp-0"]["rule"]
    stripped = actual["http"]["middlewares"]["verdify-strip-identity-headers"]["headers"]["customRequestHeaders"]
    assert stripped and all(value == "" for value in stripped.values())


@pytest.mark.parametrize(
    "mutation", ["tls", "cross_namespace", "missing_service", "wrong_port", "middleware", "weighted", "backend_tls"]
)
def test_unsupported_or_unsafe_route_contracts_fail_closed(documents, mutation):
    candidate = copy.deepcopy(documents)
    document = next(d for d in candidate if d["kind"] == "IngressRoute" and d["metadata"]["name"] == "verdify-t2-api")
    route = document["spec"]["routes"][0]
    if mutation == "tls":
        document["spec"]["tls"] = {"secretName": "not-for-this-proxy"}
    elif mutation == "cross_namespace":
        route["services"][0]["namespace"] = "auth"
    elif mutation == "missing_service":
        route["services"][0]["name"] = "nonexistent"
    elif mutation == "wrong_port":
        route["services"][0]["port"] = 9876
    elif mutation == "middleware":
        route["middlewares"][0]["name"] = "unknown"
    elif mutation == "weighted":
        route["services"].append(copy.deepcopy(route["services"][0]))
    elif mutation == "backend_tls":
        route["services"][0]["scheme"] = "https"
    with pytest.raises(ValueError):
        MODULE.compile_routes(candidate)


def secret_list_watch_violations(documents):
    return [
        d["metadata"]["name"]
        for d in documents
        if d["kind"] in {"Role", "ClusterRole"}
        for rule in d.get("rules", [])
        if set(rule.get("apiGroups", [])) & {"", "*"}
        and set(rule.get("resources", [])) & {"secrets", "*"}
        and set(rule.get("verbs", [])) & {"list", "watch", "*"}
    ]


def test_no_secret_listing_and_traefik_has_no_kubernetes_authority(documents):
    assert not secret_list_watch_violations(documents)
    assert not any(
        d["kind"] in {"Role", "ClusterRole", "RoleBinding", "ClusterRoleBinding"}
        and d["metadata"]["name"] == "verdify-traefik"
        for d in documents
    )
    account = next(d for d in documents if d["kind"] == "ServiceAccount" and d["metadata"]["name"] == "verdify-traefik")
    assert account["automountServiceAccountToken"] is False
    workload = next(d for d in documents if d["kind"] == "Deployment" and d["metadata"]["name"] == "verdify-traefik")
    pod = workload["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    args = pod["containers"][0]["args"]
    assert not any("kubernetes" in arg.lower() for arg in args)
    assert "--ping.manualrouting=true" in args
    assert MODULE.compile_routes(documents)["http"]["routers"]["file-routing-ready"] == {
        "entryPoints": ["traefik"],
        "rule": "Path(`/ping`)",
        "service": "ping@internal",
    }
    assert "--providers.file.filename=/etc/traefik/dynamic/routes.yaml" in args
    assert any(v.get("configMap", {}).get("name", "").startswith("verdify-traefik-routing-") for v in pod["volumes"])


@pytest.mark.parametrize(
    "kind,resource,verb", [("ClusterRole", "secrets", "list"), ("Role", "secrets", "watch"), ("ClusterRole", "*", "*")]
)
def test_secret_privilege_regression_is_detected(kind, resource, verb):
    assert secret_list_watch_violations(
        [
            {
                "kind": kind,
                "metadata": {"name": "unsafe"},
                "rules": [{"apiGroups": [""], "resources": [resource], "verbs": [verb]}],
            }
        ]
    ) == ["unsafe"]
