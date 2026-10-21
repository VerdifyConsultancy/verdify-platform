"""Security regressions for the suspended builder; never invoke a build or device."""

from __future__ import annotations

import ipaddress
import os
import subprocess
from pathlib import Path

import pytest
import yaml

MANIFEST = Path(__file__).resolve().parents[1] / "deploy/k8s/components/firmware-builder/firmware-builder.yaml"


def documents():
    return list(yaml.safe_load_all(MANIFEST.read_text()))


def builder():
    job = next(d for d in documents() if d["kind"] == "CronJob")
    return job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]


@pytest.mark.parametrize("revision", ["", "main", "v2026.1", "a" * 12, "A" * 40, "a" * 39 + ";", "-help"])
def test_mutable_or_malformed_revision_fails_before_clone(revision):
    # YAML removes the block's indentation; execute only admission, never git.
    prefix = builder()["args"][0].split('echo "[builder] cloning', 1)[0]
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", prefix], env={**os.environ, "FW_REVISION": revision}, capture_output=True
    )
    assert result.returncode == 64


def test_exact_commit_passes_admission_and_checkout_is_verified():
    script = builder()["args"][0]
    prefix = script.split('echo "[builder] cloning', 1)[0]
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", prefix],
        env={**os.environ, "FW_REVISION": "0123456789abcdef" * 2 + "01234567"},
        capture_output=True,
    )
    assert result.returncode == 0
    assert 'git checkout --detach -q "${FW_REVISION}"' in script
    assert 'test "$(git rev-parse HEAD)" = "${FW_REVISION}"' in script
    assert next(e for e in builder()["env"] if e["name"] == "FW_REVISION")["value"] == ""


def test_builder_private_networks_are_excluded_except_ota():
    policy = next(d for d in documents() if d["kind"] == "NetworkPolicy")
    public, ota, dns = policy["spec"]["egress"]
    block = public["to"][0]["ipBlock"]
    exceptions = [ipaddress.ip_network(n) for n in block["except"]]
    assert block["cidr"] == "0.0.0.0/0"
    assert public["ports"] == [{"port": 443, "protocol": "TCP"}]
    for address in [
        "10.42.0.1",
        "172.16.0.1",
        "192.168.30.240",
        "192.168.10.111",
        "169.254.169.254",
        "100.64.0.1",
        "127.0.0.1",
        "224.0.0.1",
    ]:
        assert any(ipaddress.ip_address(address) in network for network in exceptions)
    assert ota == {"to": [{"ipBlock": {"cidr": "192.168.10.111/32"}}], "ports": [{"port": 3232, "protocol": "TCP"}]}
    assert dns["to"] == [
        {
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
            "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
        }
    ]
    assert sorted((p["port"], p["protocol"]) for p in dns["ports"]) == [(53, "TCP"), (53, "UDP")]
