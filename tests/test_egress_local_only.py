"""#1351/#1363 (Jason 2026-11-05: Verdify runs local-only): egress-local-only bounds every
verdify-prod component without its own allowlist to private ranges, and only the declared
exceptions keep public egress."""

import ipaddress
import subprocess
from pathlib import Path

import pytest
import yaml
from test_egress_deny_management import pod_labels, policies, selected

ROOT = Path(__file__).resolve().parents[1]
LOCAL = "egress-local-only"
DENY = "egress-deny-management"
PRIVATE = {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"}
# Workloads whose own policy may reach the public internet, with the decision that allows it.
PUBLIC_BY_DECISION = {
    "verdify-outbound",  # Open-Meteo (accepted, #1351 row 6)
    "verdify-firmware-builder",  # GitHub/PlatformIO/PyPI at build time (accepted, #1351 D3)
    "verdify-api",  # Cloudflare Turnstile siteverify (accepted for now, #1351 row 7)
    "verdify-hermes-iris",  # api.openai.com, retired by #1350
}


@pytest.fixture(scope="module")
def rendered():
    return [
        x
        for x in yaml.safe_load_all(
            subprocess.check_output(["kustomize", "build", "deploy/k8s/overlays/prod"], cwd=ROOT, text=True)
        )
        if x
    ]


def reaches_public(policy):
    for rule in policy["spec"].get("egress", []):
        for peer in rule.get("to", [{}]):
            block = peer.get("ipBlock")
            if block is None:
                continue  # pod/namespace selectors stay in-cluster
            if not ipaddress.ip_network(block["cidr"]).is_private:
                return True
    return False


def test_local_only_allows_private_ranges_minus_the_vendored_management_set(rendered):
    by_name = policies(rendered)
    local, deny = by_name[LOCAL], by_name[DENY]
    assert local["spec"]["policyTypes"] == ["Egress"]
    (rule,) = local["spec"]["egress"]
    assert "ports" not in rule
    blocks = {peer["ipBlock"]["cidr"]: peer["ipBlock"].get("except", []) for peer in rule["to"]}
    assert set(blocks) == PRIVATE
    # A network-infra re-vendor of the deny must move this copy too.
    assert blocks["192.168.0.0/16"] == deny["spec"]["egress"][0]["to"][0]["ipBlock"]["except"]
    assert blocks["10.0.0.0/8"] == blocks["172.16.0.0/12"] == []
    assert not reaches_public(local)


def test_every_pod_is_bounded_and_only_declared_exceptions_reach_the_internet(rendered):
    egress = [p for p in policies(rendered).values() if "Egress" in p["spec"].get("policyTypes", [])]
    public = set()
    for workload, labels in pod_labels(rendered):
        mine = [p for p in egress if selected(p["spec"]["podSelector"], labels)]
        # A pod no Egress policy selects has unrestricted egress.
        assert mine, workload
        if any(reaches_public(p) for p in mine):
            public.add(workload)
    assert public <= PUBLIC_BY_DECISION, public - PUBLIC_BY_DECISION
    assert {"verdify-grafana", "verdify-mqtt", "verdify-traefik", "verdify-db"}.isdisjoint(public)


def test_an_unlabeled_pod_is_local_only(rendered):
    by_name = policies(rendered)
    assert selected(by_name[LOCAL]["spec"]["podSelector"], {})
    assert not selected(by_name[DENY]["spec"]["podSelector"], {})
