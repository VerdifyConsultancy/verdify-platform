from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "deploy/k8s/components/grafana"


def _documents(name: str) -> list[dict]:
    return [
        document
        for document in yaml.safe_load_all((COMPONENT / name).read_text(encoding="utf-8"))
        if isinstance(document, dict)
    ]


def test_public_render_path_uses_cache_without_becoming_a_second_grafana_front_door():
    ingress = _documents("graphs-ingressroute.yaml")[0]
    routes = ingress["spec"]["routes"]

    assert routes[0]["match"] == "Host(`graphs.verdify.ai`) && PathPrefix(`/render/`)"
    assert routes[0]["priority"] == 100
    assert routes[0]["services"] == [{"name": "verdify-grafana-render-cache", "port": 8080}]
    assert routes[1]["match"] == "Host(`graphs.verdify.ai`)"
    assert routes[1]["services"] == [{"name": "verdify-grafana", "port": 3000}]

    config = (COMPONENT / "nginx-render-cache.conf").read_text(encoding="utf-8")
    assert "location /render/" in config
    assert "location / {\n        return 404;" in config
    assert "proxy_pass http://verdify-grafana:3000;" in config


def test_render_cache_coalesces_cold_renders_without_unbounded_server_stale():
    config = (COMPONENT / "nginx-render-cache.conf").read_text(encoding="utf-8")

    for contract in (
        "max_size=200m",
        "inactive=24h",
        "proxy_cache_lock on;",
        "proxy_cache_lock_age 65s;",
        'if ($cache_key_uri ~ "^(.*)([?&])_qts=[^&]*&(.*)$")',
        'if ($cache_key_uri ~ "^(.*)[?&]_qts=[^&]*$")',
        "proxy_cache_valid 200 5m;",
        'Cache-Control "public, max-age=60, stale-while-revalidate=300"',
        "X-Cache-Status $upstream_cache_status",
        'proxy_set_header Authorization "";',
        'proxy_set_header Cookie "";',
        "proxy_hide_header Set-Cookie;",
    ):
        assert contract in config

    assert 'Cache-Control "public, max-age=60, stale-while-revalidate=300" always' not in config
    assert "proxy_cache_use_stale" not in config
    assert "proxy_cache_background_update" not in config


def test_render_cache_runtime_is_pinned_unprivileged_and_credential_free():
    documents = _documents("grafana-render-cache.yaml")
    deployment = next(document for document in documents if document["kind"] == "Deployment")
    disruption_budget = next(document for document in documents if document["kind"] == "PodDisruptionBudget")
    service = next(document for document in documents if document["kind"] == "Service")
    policy = next(document for document in documents if document["kind"] == "NetworkPolicy")
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]

    assert deployment["spec"]["replicas"] == 2
    assert pod["topologySpreadConstraints"][0]["topologyKey"] == "kubernetes.io/hostname"
    assert pod["topologySpreadConstraints"][0]["minDomains"] == 2
    assert pod["topologySpreadConstraints"][0]["whenUnsatisfiable"] == "DoNotSchedule"
    assert pod["automountServiceAccountToken"] is False
    assert disruption_budget["spec"]["minAvailable"] == 1
    assert container["image"].startswith("nginxinc/nginx-unprivileged:1.29-alpine@sha256:")
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert "env" not in container and "envFrom" not in container
    assert service["spec"]["ports"] == [{"name": "http", "port": 8080, "targetPort": "http"}]
    assert policy["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/component": "edge-traefik"
    }

    kustomization = yaml.safe_load((COMPONENT / "kustomization.yaml").read_text(encoding="utf-8"))
    assert "grafana-render-cache.yaml" in kustomization["resources"]
    assert kustomization["configMapGenerator"] == [
        {
            "name": "verdify-grafana-render-cache-config",
            "files": ["default.conf=nginx-render-cache.conf"],
        }
    ]
