"""Static contract guards for the separately runtime-reviewed Grafana manifest."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "deploy/k8s/components/grafana/grafana.yaml"
SECRET_CONTRACT = ROOT / "deploy/k8s/SECRETS.md"
GRAFANA_IMAGE = "grafana/grafana:12.4.5@sha256:26b8f35a9e4e4431995cf64c3f396505a4faf17bcfc19f9ed84943ec6bfd5ecd"
RENDERER_IMAGE = (
    "grafana/grafana-image-renderer:v5.10.0@sha256:c0eb7b915a181c7bbe451718f9b633843678bef93703b5ed5fda2f28fa508986"
)
IMMUTABLE_IMAGE_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")


def _documents() -> list[dict]:
    return [document for document in yaml.safe_load_all(MANIFEST.read_text()) if document]


def _deployment() -> dict:
    return next(document for document in _documents() if document.get("kind") == "Deployment")


def _container(spec: dict, name: str) -> dict:
    return next(container for container in spec if container["name"] == name)


def _environment(container: dict) -> dict[str, dict]:
    return {entry["name"]: entry for entry in container.get("env", [])}


def test_grafana_and_renderer_use_reviewed_immutable_security_images():
    pod = _deployment()["spec"]["template"]["spec"]
    init = _container(pod["initContainers"], "inject-canvas-css")
    grafana = _container(pod["containers"], "grafana")
    renderer = _container(pod["containers"], "renderer")

    assert init["image"] == GRAFANA_IMAGE
    assert grafana["image"] == GRAFANA_IMAGE
    assert renderer["image"] == RENDERER_IMAGE
    assert all(IMMUTABLE_IMAGE_RE.fullmatch(item["image"]) for item in (init, grafana, renderer))

    text = MANIFEST.read_text()
    assert "grafana/grafana-oss:11.6.0" not in text
    assert "grafana/grafana-image-renderer:3.12.6" not in text


def test_manifest_wires_the_same_required_secret_sourced_renderer_token():
    containers = _deployment()["spec"]["template"]["spec"]["containers"]
    grafana_env = _environment(_container(containers, "grafana"))
    renderer_env = _environment(_container(containers, "renderer"))
    expected = {
        "valueFrom": {
            "secretKeyRef": {
                "name": "verdify-grafana-secrets",
                "key": "GRAFANA_RENDERER_TOKEN",
            }
        }
    }

    assert grafana_env["GF_RENDERING_RENDERER_TOKEN"] == {
        "name": "GF_RENDERING_RENDERER_TOKEN",
        **expected,
    }
    assert renderer_env["AUTH_TOKEN"] == {"name": "AUTH_TOKEN", **expected}
    assert grafana_env["GF_RENDERING_RENDERER_TOKEN"].get("value") is None
    assert renderer_env["AUTH_TOKEN"].get("value") is None


def test_manifest_locks_the_runtime_reviewed_renderer_v5_configuration_and_probes():
    pod = _deployment()["spec"]["template"]["spec"]
    containers = pod["containers"]
    grafana_env = _environment(_container(containers, "grafana"))
    renderer = _container(containers, "renderer")
    env = _environment(renderer)

    assert env["SERVER_ADDR"]["value"] == ":8081"
    assert env["RATE_LIMIT_MAX_LIMIT"]["value"] == "4"
    assert env["RATE_LIMIT_MIN_LIMIT"]["value"] == "1"
    assert grafana_env["GF_RENDERING_CONCURRENT_RENDER_REQUEST_LIMIT"]["value"] == "4"
    assert grafana_env["GF_RENDERING_CALLBACK_URL"]["value"] == "http://127.0.0.1:3000/"
    assert env["BROWSER_READINESS_TIMEOUT"]["value"] == "30s"
    assert not {
        "HTTP_PORT",
        "RENDERING_ARGS",
        "RENDERING_MODE",
        "RENDERING_CLUSTERING_MODE",
        "RENDERING_CLUSTERING_MAX_CONCURRENCY",
        "RENDERING_CLUSTERING_TIMEOUT",
    } & set(env)

    for probe_name in ("livenessProbe", "readinessProbe"):
        request = renderer[probe_name]["httpGet"]
        assert request == {"path": "/healthz", "port": "renderer"}

    # The pinned renderer OCI config declares USER 65532 and a 0770
    # /home/nonroot WORKDIR owned by that uid. Keep gid/fsGroup 472 for the
    # shared emptyDirs, but never inherit Grafana's uid 472 here.
    assert pod["securityContext"]["runAsUser"] == 472
    assert pod["securityContext"]["runAsGroup"] == 472
    assert pod["securityContext"]["fsGroup"] == 472
    assert renderer["securityContext"]["runAsUser"] == 65532


def test_renderer_port_is_not_exposed_by_the_public_service_or_network_policy():
    documents = _documents()
    service = next(document for document in documents if document.get("kind") == "Service")
    policy = next(document for document in documents if document.get("kind") == "NetworkPolicy")

    assert service["spec"]["ports"] == [{"name": "http", "port": 3000, "targetPort": "http"}]
    ingress_ports = [port["port"] for rule in policy["spec"]["ingress"] for port in rule.get("ports", [])]
    assert ingress_ports == [3000]


def test_surge_first_rollout_retains_the_old_pod_until_the_candidate_is_ready():
    strategy = _deployment()["spec"]["strategy"]

    assert strategy == {
        "type": "RollingUpdate",
        "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
    }


def test_required_runtime_secrets_fail_closed_and_sql_expressions_are_not_explicitly_enabled():
    containers = _deployment()["spec"]["template"]["spec"]["containers"]
    grafana_env = _environment(_container(containers, "grafana"))
    renderer_env = _environment(_container(containers, "renderer"))
    enabled = grafana_env.get("GF_FEATURE_TOGGLES_ENABLE", {}).get("value", "")

    for name in ("GF_SECURITY_ADMIN_PASSWORD", "POSTGRES_PASSWORD"):
        secret_ref = grafana_env[name]["valueFrom"]["secretKeyRef"]
        assert secret_ref.get("optional") is not True
    assert grafana_env["GF_RENDERING_RENDERER_TOKEN"]["valueFrom"]["secretKeyRef"].get("optional") is not True
    assert renderer_env["AUTH_TOKEN"]["valueFrom"]["secretKeyRef"].get("optional") is not True
    assert "sqlExpressions" not in enabled.split(",")


def test_required_grafana_secret_keys_and_manual_sync_gate_are_in_the_canonical_contract():
    contract = SECRET_CONTRACT.read_text()

    assert (
        "| `verdify-grafana-secrets` | `GRAFANA_ADMIN_PASSWORD` | "
        "grafana (`secretKeyRef`, required; pod fails closed when absent) | — | — | ✓ |"
    ) in contract
    assert (
        "| `verdify-grafana-secrets` | `GRAFANA_RENDERER_TOKEN` | "
        "grafana + image-renderer (`secretKeyRef`, required shared token; pod fails closed when absent) | "
        "— | — | ✓ |"
    ) in contract
    assert "there is deliberately no in-repo placeholder, default password, or" in contract
    assert "then obtain Jason's explicit approval" in contract
