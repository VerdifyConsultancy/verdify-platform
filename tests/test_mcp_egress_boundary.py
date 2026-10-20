"""Rendered MCP identity and security-negative destination matrix (#1011)."""

import ipaddress
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def rendered():
    return list(
        yaml.safe_load_all(
            subprocess.check_output(["kustomize", "build", "deploy/k8s/overlays/prod"], cwd=ROOT, text=True)
        )
    )


def selected(selector, labels):
    # An unhandled selector must not silently disappear from the additive-policy check.
    assert not selector.get("matchExpressions"), "review any new MCP selector expression"
    return all(labels.get(key) == value for key, value in selector.get("matchLabels", {}).items())


def test_no_additive_escape_or_mounted_identity_token(rendered):
    labels = {"app.kubernetes.io/component": "mcp", "app.kubernetes.io/part-of": "verdify"}
    policies = [
        x
        for x in rendered
        if x
        and x["kind"] == "NetworkPolicy"
        and "Egress" in x["spec"].get("policyTypes", [])
        and selected(x["spec"]["podSelector"], labels)
    ]
    assert [x["metadata"]["name"] for x in policies] == ["verdify-mcp-egress"]
    deploy = next(x for x in rendered if x and x["kind"] == "Deployment" and x["metadata"]["name"] == "verdify-mcp")
    spec = deploy["spec"]["template"]["spec"]
    assert spec["serviceAccountName"] == "verdify-mcp" and spec["automountServiceAccountToken"] is False
    account = next(
        x for x in rendered if x and x["kind"] == "ServiceAccount" and x["metadata"]["name"] == "verdify-mcp"
    )
    assert account["automountServiceAccountToken"] is False and not account.get("secrets")
    assert not any(x and x["kind"] == "Secret" for x in rendered)
    assert not any(
        x
        and x["kind"] in {"RoleBinding", "ClusterRoleBinding"}
        and any(s.get("kind") == "ServiceAccount" and s.get("name") == "verdify-mcp" for s in x.get("subjects", []))
        for x in rendered
    )


def allowed(policy, address, port, protocol="TCP", pod_labels=None, namespace="verdify-prod"):
    address = ipaddress.ip_address(address)
    for rule in policy["spec"]["egress"]:
        if not any(p["port"] == port and p.get("protocol", "TCP") == protocol for p in rule["ports"]):
            continue
        for target in rule["to"]:
            if "ipBlock" in target:
                block = target["ipBlock"]
                if address in ipaddress.ip_network(block["cidr"]) and not any(
                    address in ipaddress.ip_network(x) for x in block.get("except", [])
                ):
                    return True
            elif pod_labels is not None:
                ns = (
                    target.get("namespaceSelector", {})
                    .get("matchLabels", {})
                    .get("kubernetes.io/metadata.name", "verdify-prod")
                )
                if namespace == ns and selected(target["podSelector"], pod_labels):
                    return True
    return False


@pytest.mark.parametrize(
    "address,port",
    [
        ("192.168.30.126", 443),
        ("192.168.30.126", 2049),
        ("192.168.10.111", 6053),
        ("192.168.30.240", 6443),
        ("10.43.0.1", 443),
        ("10.42.1.2", 443),
        ("172.16.1.2", 443),
        ("169.254.169.254", 443),
        ("100.64.1.2", 443),
        ("1.1.1.1", 80),
        ("1.1.1.1", 53),
        ("2606:4700:4700::1111", 443),
    ],
)
def test_lan_metadata_cluster_and_other_ports_denied(rendered, address, port):
    policy = next(
        x for x in rendered if x and x["kind"] == "NetworkPolicy" and x["metadata"]["name"] == "verdify-mcp-egress"
    )
    assert not allowed(policy, address, port)


def test_only_declared_dns_database_and_public_tls_paths(rendered):
    p = next(
        x for x in rendered if x and x["kind"] == "NetworkPolicy" and x["metadata"]["name"] == "verdify-mcp-egress"
    )
    assert allowed(p, "1.1.1.1", 443)
    assert allowed(p, "10.42.1.2", 5432, pod_labels={"app.kubernetes.io/component": "db"})
    assert not allowed(p, "10.42.1.2", 5432, pod_labels={"app.kubernetes.io/component": "api"})
    assert not allowed(p, "10.42.1.2", 5432, pod_labels={"app.kubernetes.io/component": "db"}, namespace="other")
    for protocol in ["TCP", "UDP"]:
        assert allowed(p, "10.42.1.3", 53, protocol, {"k8s-app": "kube-dns"}, "kube-system")
        assert not allowed(p, "10.42.1.3", 53, protocol, {"k8s-app": "kube-dns"}, "other")
