"""Only single-label product hosts reach the Verdify tier-2 forwarder."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "deploy/k8s/overlays/prod/traefik/verdify-tier1-apps-forward.yaml"


def test_outside_owner_exclusions_preserve_existing_forwarder_contract():
    route = yaml.safe_load(SOURCE.read_text())
    spec = route["spec"]
    match = spec["routes"][0]["match"]
    assert set(re.findall(r"!Host\(`([^`]+)`\)", match)) == {
        name + ".verdify.ai"
        for name in (
            "www",
            "www-dev",
            "proposals-hello",
            "auth",
            "analytics",
            "logs",
            "botauth",
            "crm",
            "mqtt",
            "traefik",
        )
    }
    assert spec["entryPoints"] == ["websecure", "web"]
    assert spec["routes"][0]["priority"] == 50
    assert spec["routes"][0]["services"] == [{"name": "verdify-traefik", "port": 80}]
    assert spec["tls"] == {}


def test_product_and_unknown_single_label_hosts_keep_forwarding():
    match = yaml.safe_load(SOURCE.read_text())["spec"]["routes"][0]["match"]
    expression = re.search(r"HostRegexp\(`([^`]+)`\)", match).group(1)
    excluded = set(re.findall(r"!Host\(`([^`]+)`\)", match))

    def forwards(host):
        return re.fullmatch(expression, host) is not None and host not in excluded

    assert all(forwards(name + ".verdify.ai") for name in ("api", "lab", "labs", "graphs", "mcp", "unclaimed"))
    assert not any(
        forwards(host) for host in ("www-dev.verdify.ai", "proposals-hello.verdify.ai", "verdify.ai", "a.b.verdify.ai")
    )
