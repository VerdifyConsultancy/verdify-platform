"""deploy/k8s/components/operator-api renders the operator API's verdify-prod manifests (#1148; architecture §10).

Rendered alone it holds one Deployment, a ClusterIP Service on 8090, the ConfigMap and one ingress NetworkPolicy that
admits only the dashboard web pods: no Secret, no IngressRoute and no egress policy. In the prod overlay (R1's wiring
PR, #1143), the operator API runs the verdify-api release digest, allow-db-from-app admits it and
egress-deny-management covers it, and the overlay still renders no Secret.
Renders with kustomize, as tests/test_egress_deny_management.py renders the prod overlay.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "deploy/k8s/components/operator-api"
PROD = ROOT / "deploy/k8s/overlays/prod"
DASHBOARD = ROOT / "deploy/k8s/dashboard"
API_DEPLOYMENT = ROOT / "deploy/k8s/base/api-deployment.yaml"
NAME = "verdify-operator-api"
POLICY = "allow-operator-api-from-dashboard-web"
LABELS = {"app.kubernetes.io/part-of": "verdify", "app.kubernetes.io/component": "operator-api"}
API_IMAGE = "ghcr.io/verdifyconsultancy/verdify-api"
WEB_POD = {"app.kubernetes.io/name": "verdify-dashboard-web"}


def render(path: Path) -> list[dict]:
    output = subprocess.run(["kustomize", "build", str(path)], check=True, capture_output=True, text=True).stdout
    return [document for document in yaml.safe_load_all(output) if document]


def one(documents: list[dict], kind: str, name: str | None = None) -> dict:
    found = [doc for doc in documents if doc["kind"] == kind and name in (None, doc["metadata"]["name"])]
    assert len(found) == 1, (kind, name, [doc["metadata"]["name"] for doc in found])
    return found[0]


def selects(selector: dict, labels: dict) -> bool:
    assert set(selector) <= {"matchLabels"}, "review a selector with expressions"
    return all(labels.get(key) == value for key, value in selector["matchLabels"].items())


@pytest.fixture(scope="module")
def rendered() -> list[dict]:
    return render(COMPONENT)


@pytest.fixture(scope="module")
def prod() -> list[dict]:
    return render(PROD)


def test_the_component_renders_its_four_objects_and_no_secret_route_or_egress_policy(rendered):
    assert sorted((doc["kind"], doc["metadata"]["name"]) for doc in rendered) == [
        ("ConfigMap", NAME),
        ("Deployment", NAME),
        ("NetworkPolicy", POLICY),
        ("Service", NAME),
    ]
    for document in rendered:
        assert document["metadata"]["labels"] == LABELS, document["kind"]
        assert "namespace" not in document["metadata"]  # the overlay sets verdify-prod
    assert yaml.safe_load((COMPONENT / "kustomization.yaml").read_text())["kind"] == "Component"


def test_the_deployment_runs_the_verdify_api_image_as_operator_api_on_8090(rendered):
    deployment = one(rendered, "Deployment", NAME)
    spec = deployment["spec"]
    assert spec["replicas"] == 1
    assert spec["strategy"] == {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}
    assert spec["selector"] == {"matchLabels": {"app.kubernetes.io/component": "operator-api"}}
    assert spec["template"]["metadata"]["labels"] == LABELS
    pod = spec["template"]["spec"]
    (container,) = pod["containers"]
    assert "initContainers" not in pod
    assert container["image"] == API_IMAGE  # the overlay's images: entry and release-pins.yaml pin it
    command = ["uvicorn", "operator_api:app", "--host", "0.0.0.0", "--port", "8090", "--no-access-log"]  # noqa: S104
    assert container["command"] == command
    assert container["ports"] == [{"name": "http", "containerPort": 8090}]
    assert container["envFrom"] == [{"configMapRef": {"name": NAME}}]
    assert container["env"] == [
        {"name": "DB_PASS", "valueFrom": {"secretKeyRef": {"name": "verdify-dashboard-runtime-db", "key": "password"}}},
        {
            "name": "OPERATOR_API_TOKEN",
            "valueFrom": {"secretKeyRef": {"name": NAME, "key": "operator-api-token"}},
        },
    ]
    assert container["resources"] == {"requests": {"cpu": "50m", "memory": "192Mi"}, "limits": {"memory": "512Mi"}}
    assert container["readinessProbe"]["httpGet"] == {"path": "/readyz", "port": "http"}
    assert container["livenessProbe"]["httpGet"] == {"path": "/healthz", "port": "http"}
    assert container["readinessProbe"]["timeoutSeconds"] > 3  # /readyz answers within 3 s (READY_TIMEOUT_S)
    assert pod["imagePullSecrets"] == [{"name": "zot-origin-cluster-pull"}]
    assert pod["automountServiceAccountToken"] is False


def test_the_deployment_keeps_the_verdify_api_security_context_with_a_writable_tmp(rendered):
    documents = yaml.safe_load_all(API_DEPLOYMENT.read_text())
    api = next(doc for doc in documents if doc and doc["kind"] == "Deployment")["spec"]["template"]["spec"]
    pod = one(rendered, "Deployment", NAME)["spec"]["template"]["spec"]
    assert pod["securityContext"] == api["securityContext"]
    assert pod["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 1000,
        "runAsGroup": 1000,
        "fsGroup": 1000,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    (container,) = pod["containers"]
    assert container["securityContext"] == api["containers"][0]["securityContext"]
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert container["volumeMounts"] == [{"name": "tmp", "mountPath": "/tmp"}]  # noqa: S108
    assert pod["volumes"] == [{"name": "tmp", "emptyDir": {}}]


def test_the_configmap_names_the_dashboard_login(rendered):
    data = one(rendered, "ConfigMap", NAME)["data"]
    assert data == {
        "DB_HOST": "verdify-db",
        "DB_NAME": "verdify",
        "DB_USER": "verdify_dashboard_runtime_login",
        "SETPOINT_SERVER_URL": "http://verdify-setpoint-server.verdify-prod.svc:8200",
    }


def test_the_service_is_clusterip_on_8090_with_no_route(rendered):
    service = one(rendered, "Service", NAME)["spec"]
    assert service == {
        "type": "ClusterIP",
        "selector": {"app.kubernetes.io/component": "operator-api"},
        "ports": [{"name": "http", "port": 8090, "targetPort": "http"}],
    }
    assert not [doc for doc in rendered if doc["kind"] in {"IngressRoute", "Ingress", "IngressRouteTCP"}]


def test_only_dashboard_web_pods_reach_8090(rendered):
    policy = one(rendered, "NetworkPolicy", POLICY)["spec"]
    assert policy["policyTypes"] == ["Ingress"]
    assert "egress" not in policy
    assert selects(policy["podSelector"], one(rendered, "Deployment", NAME)["spec"]["template"]["metadata"]["labels"])
    (rule,) = policy["ingress"]
    assert rule["ports"] == [{"protocol": "TCP", "port": 8090}]
    # One from entry holding both selectors: the pod must be the web pod AND be in verdify-dashboard.
    assert rule["from"] == [
        {
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "verdify-dashboard"}},
            "podSelector": {"matchLabels": WEB_POD},
        }
    ]
    egress = [doc for doc in rendered if doc["kind"] == "NetworkPolicy" and "Egress" in doc["spec"]["policyTypes"]]
    assert egress == []


def test_the_dashboard_web_pods_carry_the_labels_the_policy_admits():
    dashboard = render(DASHBOARD)
    web = one(dashboard, "Deployment", "verdify-dashboard-web")
    assert web["metadata"]["namespace"] == "verdify-dashboard"
    assert selects({"matchLabels": WEB_POD}, web["spec"]["template"]["metadata"]["labels"])
    namespace = one(dashboard, "Namespace", "verdify-dashboard")
    # Kubernetes sets kubernetes.io/metadata.name to the namespace name, which the namespaceSelector matches.
    assert namespace["metadata"]["name"] == "verdify-dashboard"
    # The BFF calls this Service by name and port.
    (config,) = [doc for doc in dashboard if doc["kind"] == "ConfigMap"]
    assert config["data"]["OPERATOR_API_URL"] == f"http://{NAME}.verdify-prod.svc:8090"


def test_the_api_image_carries_the_operator_api_and_every_route_module():
    lines = (ROOT / "api/Dockerfile").read_text().splitlines()
    copies = ["COPY api/operator_api.py ./operator_api.py", "COPY api/operator_routes/ ./operator_routes/"]
    check = 'RUN python -c "import operator_api"'  # every included route module imports at build
    for line in (*copies, check):
        assert lines.count(line) == 1, line
    packages = ["COPY verdify_schemas/ ./verdify_schemas/", "COPY verdify_services/ ./verdify_services/"]
    for line in (*copies, *packages, "COPY verdify_public/ ./verdify_public/"):
        assert lines.index(line) < lines.index(check), line
    # Plain COPY lines only, so path impact maps api/operator_api.py and api/operator_routes/** to this image.
    assert not [line for line in lines if line.startswith("ADD ") or ("COPY" in line and "--chown" in line)]


def test_the_prod_overlay_renders_the_component_and_no_secret(prod):
    assert "../../components/operator-api" in yaml.safe_load((PROD / "kustomization.yaml").read_text())["components"]
    assert not [doc for doc in prod if doc["kind"] == "Secret"]
    names = {(doc["kind"], doc["metadata"]["name"]) for doc in prod}
    assert names >= {("Deployment", NAME), ("Service", NAME), ("ConfigMap", NAME), ("NetworkPolicy", POLICY)}
    assert not [doc for doc in prod if doc["kind"] in {"Ingress", "IngressRoute"} and NAME in str(doc["spec"])]


def test_in_prod_it_runs_the_verdify_api_release_digest(prod):
    operator = one(prod, "Deployment", NAME)
    public = one(prod, "Deployment", "verdify-api")
    assert operator["metadata"]["namespace"] == public["metadata"]["namespace"] == "verdify-prod"
    (operator_container,) = operator["spec"]["template"]["spec"]["containers"]
    (public_container,) = public["spec"]["template"]["spec"]["containers"]
    pins = {
        doc["metadata"]["name"]: doc["imageTag"] for doc in yaml.safe_load_all((PROD / "release-pins.yaml").read_text())
    }
    release = pins["release-pin-verdify-api"]
    assert operator_container["image"] == public_container["image"] == f"{release['name']}@{release['digest']}"
    assert one(prod, "NetworkPolicy", POLICY)["metadata"]["namespace"] == "verdify-prod"


def test_in_prod_the_operator_api_syncs_after_everything_else_with_no_pod_template_churn(prod):
    # Argo stops at a wave that does not turn Healthy: an operator API that cannot start must hold nothing else.
    annotation = "argocd.argoproj.io/sync-wave"
    operator = one(prod, "Deployment", NAME)
    others = [
        int(annotations.get(annotation, "0"))
        for doc in prod
        if doc is not operator
        and "argocd.argoproj.io/hook" not in (annotations := doc["metadata"].get("annotations") or {})
    ]
    assert int(operator["metadata"]["annotations"][annotation]) > max(others)
    assert annotation not in (operator["spec"]["template"]["metadata"].get("annotations") or {})


def test_in_prod_allow_db_from_app_admits_the_operator_api(prod):
    labels = one(prod, "Deployment", NAME)["spec"]["template"]["metadata"]["labels"]
    policy = one(prod, "NetworkPolicy", "allow-db-from-app")["spec"]
    (rule,) = policy["ingress"]
    (expression,) = rule["from"][0]["podSelector"]["matchExpressions"]
    assert expression["key"] == "app.kubernetes.io/component" and expression["operator"] == "In"
    assert labels["app.kubernetes.io/component"] in expression["values"]
    assert rule["ports"] == [{"protocol": "TCP", "port": 5432}]


def test_in_prod_egress_local_only_covers_the_operator_api(prod):
    """#1351/#1363: the operator API gets private-range egress only (egress-local-only), not the
    vendored deny's public egress, and no allowlist of its own."""
    local = one(prod, "NetworkPolicy", "egress-local-only")["spec"]["podSelector"]
    deny = one(prod, "NetworkPolicy", "egress-deny-management")["spec"]["podSelector"]
    labels = one(prod, "Deployment", NAME)["spec"]["template"]["metadata"]["labels"]
    component = labels["app.kubernetes.io/component"]
    ((local_expression,), (deny_expression,)) = local["matchExpressions"], deny["matchExpressions"]
    assert local_expression["key"] == deny_expression["key"] == "app.kubernetes.io/component"
    assert local_expression["operator"] == "NotIn" and component not in local_expression["values"]
    assert deny_expression["operator"] == "In" and component not in deny_expression["values"]
    own_egress = [
        doc["metadata"]["name"]
        for doc in prod
        if doc["kind"] == "NetworkPolicy"
        and "Egress" in doc["spec"]["policyTypes"]
        and doc["metadata"]["name"] not in {"egress-deny-management", "egress-local-only"}
        and all(labels.get(key) == value for key, value in doc["spec"]["podSelector"].get("matchLabels", {}).items())
        and not doc["spec"]["podSelector"].get("matchExpressions")
    ]
    assert own_egress == []
