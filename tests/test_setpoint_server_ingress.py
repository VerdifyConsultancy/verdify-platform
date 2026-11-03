"""The operator API reaches verdify-setpoint-server on 8200 and nothing else does (VD-57, #1170; API-20).

default-deny-ingress blocks every pod in verdify-prod; the setpoint server takes no token, so this policy is its guard.
Parsed from the component YAML: a selector typo would otherwise show only as API-20 answering ``timeout`` live.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "deploy/k8s/components/setpoint-server"
OPERATOR = ROOT / "deploy/k8s/components/operator-api"
POLICY = "allow-setpoint-from-operator-api"


def documents(path: Path) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def one(docs: list[dict], kind: str) -> dict:
    (found,) = [doc for doc in docs if doc["kind"] == kind]
    return found


def policy() -> dict:
    return one(documents(COMPONENT / f"{POLICY}.yaml"), "NetworkPolicy")


def selects(selector: dict, labels: dict) -> bool:
    return selector.items() <= labels.items()


def test_the_component_lists_the_policy():
    assert f"{POLICY}.yaml" in yaml.safe_load((COMPONENT / "kustomization.yaml").read_text())["resources"]


def test_it_selects_the_setpoint_server_pods_for_ingress_only():
    spec = policy()["spec"]
    assert policy()["metadata"]["name"] == POLICY
    assert "namespace" not in policy()["metadata"]  # the overlay sets verdify-prod
    assert spec["policyTypes"] == ["Ingress"]
    assert spec["podSelector"] == {"matchLabels": {"app.kubernetes.io/component": "setpoint-server"}}
    server = one(documents(COMPONENT / "setpoint-server.yaml"), "Deployment")
    assert selects(spec["podSelector"]["matchLabels"], server["spec"]["template"]["metadata"]["labels"])


def test_it_admits_only_the_operator_api_pods_in_their_own_namespace_on_8200():
    (rule,) = policy()["spec"]["ingress"]
    assert rule["from"] == [{"podSelector": {"matchLabels": {"app.kubernetes.io/component": "operator-api"}}}]
    assert rule["ports"] == [{"protocol": "TCP", "port": 8200}]
    operator = one(documents(OPERATOR / "deployment.yaml"), "Deployment")
    assert selects(rule["from"][0]["podSelector"]["matchLabels"], operator["spec"]["template"]["metadata"]["labels"])


def test_8200_is_the_port_the_service_the_url_names_forwards_to():
    docs = documents(COMPONENT / "setpoint-server.yaml")
    service = one(docs, "Service")
    (port,) = service["spec"]["ports"]
    (container,) = one(docs, "Deployment")["spec"]["template"]["spec"]["containers"]
    assert port["port"] == 8200 and {"name": "http", "containerPort": 8200}.items() <= container["ports"][0].items()
    url = one(documents(OPERATOR / "configmap.yaml"), "ConfigMap")["data"]["SETPOINT_SERVER_URL"]
    assert url == f"http://{service['metadata']['name']}.verdify-prod.svc:8200"
