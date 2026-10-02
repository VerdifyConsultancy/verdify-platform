"""Offline current-k3s delivery contracts (#322); never contacts a service/device.

Live desired/running identity and health use scripts/k3s-smoke.sh with explicit
release receipt arguments after Argo delivery. Source render proves configuration,
not live health. The destroyed VM's Docker/systemd suite is retired in Git history.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def production_documents():
    command = ["kustomize", "build"] if shutil.which("kustomize") else ["kubectl", "kustomize"]
    result = subprocess.run(
        [*command, str(ROOT / "deploy/k8s/overlays/prod")], capture_output=True, text=True, check=True, timeout=60
    )
    return [x for x in yaml.safe_load_all(result.stdout) if x]


def validate_delivery(documents):
    """Assert the same contract on genuine renders and deliberately broken fixtures."""
    assert not any(x["kind"] == "Secret" for x in documents), "production must not render Secret values"
    objects = {(x["kind"], x["metadata"]["name"]): x for x in documents}
    writer = objects[("Deployment", "verdify-ingestor")]
    assert writer["spec"]["replicas"] == 1, "exactly one ingestor replica"
    assert writer["spec"]["strategy"]["type"] == "Recreate", "writer uses Recreate"
    config = objects[("ConfigMap", "verdify-config")]["data"]
    assert config["VERDIFY_DEVICE_WRITE_ENABLED"] == "1", "only explicit prod device gate enables writes"
    assert config["VERDIFY_WRITER_LEASE_ENABLED"] == "1", "writer Lease fence enabled"
    for name in ["verdify-api", "verdify-mcp", "verdify-ingestor"]:
        deployment = objects[("Deployment", name)]
        revision = deployment["spec"]["template"]["metadata"]["annotations"]["verdify.io/config-revision"]
        assert re.fullmatch(r"[a-f0-9]{12}", revision), "explicit config identity"
        for container in deployment["spec"]["template"]["spec"]["containers"]:
            assert re.fullmatch(r"registry\.vallery\.net/.+@sha256:[a-f0-9]{64}", container["image"]), (
                "immutable origin image"
            )
    api = objects[("Deployment", "verdify-api")]["spec"]["template"]["spec"]["containers"][0]
    mcp = objects[("Deployment", "verdify-mcp")]["spec"]["template"]["spec"]["containers"][0]
    assert api["readinessProbe"]["httpGet"]["path"] == "/health/detailed", "API readiness contract"
    assert mcp["readinessProbe"]["httpGet"]["path"] == "/readyz", "MCP authenticated readiness surface"
    assert objects[("StatefulSet", "verdify-db")]["spec"]["replicas"] == 1, "single product database"


@pytest.fixture(scope="module")
def production_render():
    return production_documents()


def test_current_k3s_delivery_source(production_render):
    validate_delivery(production_render)
