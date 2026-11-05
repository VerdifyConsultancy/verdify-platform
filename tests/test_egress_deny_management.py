"""network-infra#839: the management-set egress deny covers every verdify-prod component
that has no egress allowlist of its own, and never selects one that does."""

import ipaddress
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DENY = "egress-deny-management"
HA_ALLOW = "egress-allow-management-home-assistant"
MANAGEMENT = [
    "192.168.30.31",
    "192.168.30.240",
    "192.168.30.211",
    "192.168.30.50",
    "192.168.30.126",
    "192.168.30.107",
    "192.168.1.1",
]


def selected(selector, labels):
    for expression in selector.get("matchExpressions", []):
        key, operator, values = expression["key"], expression["operator"], expression.get("values", [])
        matched = {
            "In": lambda: labels.get(key) in values,
            "NotIn": lambda: labels.get(key) not in values,
            "Exists": lambda: key in labels,
            "DoesNotExist": lambda: key not in labels,
        }[operator]()
        if not matched:
            return False
    return all(labels.get(key) == value for key, value in selector.get("matchLabels", {}).items())


def allowed(policy, address, port):
    address = ipaddress.ip_address(address)
    for rule in policy["spec"]["egress"]:
        if not any(p["port"] == port and p.get("protocol", "TCP") == "TCP" for p in rule["ports"]):
            continue
        for target in rule["to"]:
            block = target.get("ipBlock")
            if (
                block
                and address in ipaddress.ip_network(block["cidr"])
                and not any(address in ipaddress.ip_network(x) for x in block.get("except", []))
            ):
                return True
    return False


@pytest.fixture(scope="module")
def rendered():
    return [
        x
        for x in yaml.safe_load_all(
            subprocess.check_output(["kustomize", "build", "deploy/k8s/overlays/prod"], cwd=ROOT, text=True)
        )
        if x
    ]


def policies(rendered):
    return {x["metadata"]["name"]: x for x in rendered if x["kind"] == "NetworkPolicy"}


def pod_labels(rendered):
    for x in rendered:
        if x["kind"] in {"Deployment", "StatefulSet", "Job", "DaemonSet"}:
            yield x["metadata"]["name"], x["spec"]["template"]["metadata"].get("labels", {})
        elif x["kind"] == "CronJob":
            yield x["metadata"]["name"], x["spec"]["jobTemplate"]["spec"]["template"]["metadata"].get("labels", {})


def test_deny_never_selects_a_pod_with_its_own_egress_allowlist(rendered):
    by_name = policies(rendered)
    deny = by_name[DENY]
    own = [
        p
        for name, p in by_name.items()
        if name not in {DENY, HA_ALLOW} and "Egress" in p["spec"].get("policyTypes", [])
    ]
    assert own, "expected the existing egress allowlists in the render"
    covered = set()
    for workload, labels in pod_labels(rendered):
        bounded = any(selected(p["spec"]["podSelector"], labels) for p in own)
        assert selected(deny["spec"]["podSelector"], labels) is not bounded, workload
        if not bounded:
            covered.add(workload)
    # #1351/#1363: egress-local-only bounds everything else; only these two keep public egress.
    assert covered == {"verdify-api", "verdify-hermes-iris"}


def test_deny_keeps_public_egress_and_refuses_the_management_set(rendered):
    deny = policies(rendered)[DENY]
    assert deny["spec"]["policyTypes"] == ["Egress"]
    (rule,) = deny["spec"]["egress"]
    (peer,) = rule["to"]
    block = peer["ipBlock"]
    assert block["cidr"] == "0.0.0.0/0" and "ports" not in rule
    for address in MANAGEMENT:
        assert any(ipaddress.ip_address(address) in ipaddress.ip_network(x) for x in block["except"]), address
    for address in ["1.1.1.1", "192.168.7.10", "192.168.10.111", "10.43.0.10"]:
        assert not any(ipaddress.ip_address(address) in ipaddress.ip_network(x) for x in block["except"]), address


def test_home_assistant_allow_is_exact(rendered):
    allow = policies(rendered)[HA_ALLOW]
    assert allow["spec"]["policyTypes"] == ["Egress"]
    workloads = {name for name, labels in pod_labels(rendered) if selected(allow["spec"]["podSelector"], labels)}
    # VD-48 retired the HA gap backfill CronJob; only the setpoint server calls HA.
    assert workloads == {"verdify-setpoint-server"}
    assert allowed(allow, "192.168.30.107", 8123)
    for address, port in [
        ("192.168.30.107", 1883),
        ("192.168.30.107", 22),
        ("192.168.30.126", 8123),
        ("192.168.30.31", 6443),
        ("1.1.1.1", 443),
    ]:
        assert not allowed(allow, address, port), (address, port)
