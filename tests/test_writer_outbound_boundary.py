"""The complete rendered additive policy set must preserve the sole writer boundary."""

# Imported pytest fixture is intentionally reused by the tests below.
# ruff: noqa: F811

import ipaddress

import pytest
from test_mcp_egress_boundary import allowed, rendered, selected  # noqa: F401


def policy(rendered, name):
    return next(x for x in rendered if x and x["kind"] == "NetworkPolicy" and x["metadata"]["name"] == name)


def test_writer_has_one_policy_and_exact_greenhouse_exceptions(rendered):
    labels = {"app.kubernetes.io/component": "ingestor", "app.kubernetes.io/part-of": "verdify"}
    policies = [
        x
        for x in rendered
        if x
        and x["kind"] == "NetworkPolicy"
        and "Egress" in x["spec"].get("policyTypes", [])
        and selected(x["spec"]["podSelector"], labels)
    ]
    assert len(policies) == 1
    p = policies[0]
    assert p["metadata"]["name"] == "allow-ingestor-device-egress"
    for rule in p["spec"]["egress"]:
        for target in rule["to"]:
            if "ipBlock" in target:
                assert ipaddress.ip_network(target["ipBlock"]["cidr"]).prefixlen == 32
    assert allowed(p, "192.168.10.111", 6053)
    assert allowed(p, "192.168.10.36", 502)
    assert allowed(p, "10.43.0.1", 443)
    assert allowed(p, "192.168.30.31", 6443)
    assert allowed(p, "192.168.30.107", 8123)
    assert allowed(p, "10.42.1.2", 1883, pod_labels={"app.kubernetes.io/component": "mqtt-broker"})


@pytest.mark.parametrize(
    "address,port",
    [
        ("192.168.10.112", 6053),
        ("192.168.10.36", 6053),
        ("192.168.10.111", 3232),
        ("192.168.30.126", 2049),
        ("192.168.30.126", 443),
        ("1.1.1.1", 443),
        ("10.43.0.1", 6443),
        ("192.168.30.31", 443),
    ],
)
def test_writer_other_vlan_hosts_ports_nas_and_direct_internet_denied(rendered, address, port):
    assert not allowed(policy(rendered, "allow-ingestor-device-egress"), address, port)


def test_relay_has_fixed_tls_destinations_and_never_logs_credentials(rendered):
    cm = next(
        x for x in rendered if x and x["kind"] == "ConfigMap" and x["metadata"]["name"] == "verdify-outbound-config"
    )
    conf = cm["data"]["default.conf"]
    assert "proxy_ssl_verify on;" in conf and "proxy_ssl_server_name on;" in conf
    assert "access_log off;" in conf and "error_log /dev/stderr crit;" in conf
    for host, path in [
        ("api.openai.com", "/v1/embeddings"),
        ("slack.com", "/api/chat.postMessage"),
        ("api.open-meteo.com", "/v1/forecast"),
    ]:
        assert f"proxy_ssl_name {host};" in conf
        assert f"set $fixed_host {host};" in conf
        assert f"location = {path}" in conf
    assert "$http_host" not in conf and "$arg_" not in conf
    assert conf.count("location / { return 404; }") == 4
    relay = policy(rendered, "verdify-outbound-boundary")
    assert not allowed(relay, "192.168.30.126", 443)
    assert not allowed(relay, "169.254.169.254", 443)
    assert allowed(relay, "1.1.1.1", 443)


def test_delivery_orders_ready_relay_then_clients_then_restrictions(rendered):
    by_name = {x["metadata"]["name"]: x for x in rendered if x and x["kind"] == "Deployment"}
    for name, wave in [("verdify-outbound", "0"), ("verdify-mcp", "10"), ("verdify-ingestor", "30")]:
        assert by_name[name]["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] == wave
    assert (
        policy(rendered, "allow-ingestor-device-egress")["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"]
        == "40"
    )
    assert policy(rendered, "verdify-mcp-egress")["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] == "40"
    writer = by_name["verdify-ingestor"]
    assert writer["spec"]["replicas"] == 1 and writer["spec"]["strategy"]["type"] == "Recreate"
