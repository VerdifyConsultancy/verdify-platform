"""Guard the public-view/admin-write boundary of the product Grafana."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_admin_oauth_is_confidential_and_group_scoped_with_password_clients_disabled():
    resources = list(yaml.safe_load_all((ROOT / "deploy/k8s/components/grafana/grafana.yaml").read_text()))
    deployment = next(r for r in resources if r and r["kind"] == "Deployment")
    container = next(c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "grafana")
    env = {e["name"]: e for e in container["env"]}
    for name, value in {
        "GF_AUTH_BASIC_ENABLED": "false",
        "GF_AUTH_DISABLE_LOGIN_FORM": "true",
        "GF_AUTH_GENERIC_OAUTH_ENABLED": "true",
        "GF_AUTH_GENERIC_OAUTH_USE_PKCE": "true",
        "GF_AUTH_GENERIC_OAUTH_ALLOWED_GROUPS": "admins",
        "GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_STRICT": "true",
        "GF_AUTH_GENERIC_OAUTH_SKIP_ORG_ROLE_SYNC": "false",
        "GF_AUTH_GENERIC_OAUTH_ALLOW_ASSIGN_GRAFANA_ADMIN": "false",
    }.items():
        assert env[name]["value"] == value
    assert env["GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"] == {
        "name": "verdify-grafana-secrets",
        "key": "GRAFANA_OAUTH_CLIENT_SECRET",
    }
    assert env["GF_AUTH_GENERIC_OAUTH_AUTH_URL"]["value"] == "https://auth.vallery.net/application/o/authorize/"
    assert env["GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_PATH"]["value"] == "contains(groups || `[]`, 'admins') && 'Admin'"
    assert env["GF_AUTH_ANONYMOUS_ENABLED"]["value"] == "true"
    assert env["GF_AUTH_ANONYMOUS_ORG_ROLE"]["value"] == "Viewer"
    assert env["GF_USERS_VIEWERS_CAN_EDIT"]["value"] == "false"
    assert env["GF_AUTH_GENERIC_OAUTH_ALLOW_SIGN_UP"]["value"] == "true"


def test_public_graph_paths_do_not_require_admin_forward_auth():
    route = yaml.safe_load((ROOT / "deploy/k8s/components/grafana/graphs-ingressroute.yaml").read_text())
    for row in route["spec"]["routes"]:
        assert row["middlewares"] == [{"name": "verdify-strip-identity-headers"}]
