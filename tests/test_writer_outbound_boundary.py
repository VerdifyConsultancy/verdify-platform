"""The complete rendered additive policy set must preserve the sole writer boundary."""

# Imported pytest fixture is intentionally reused by the tests below.
# ruff: noqa: F811

import ipaddress

import pytest
from test_mcp_egress_boundary import allowed, rendered, selected  # noqa: F401

# VD-70: the YINMIK hydro meter's reserved IoT address (jvallery/network-infra).
HYDRO_METER = "192.168.10.105"


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
    # #1370: the Frigate occupancy subscriber reads the HAOS broker again.
    assert allowed(p, "192.168.30.107", 1883)
    assert allowed(p, "10.42.1.2", 1883, pod_labels={"app.kubernetes.io/component": "mqtt-broker"})
    assert allowed(p, HYDRO_METER, 6668)


def test_hydro_reader_dials_the_admitted_meter_and_its_secret_is_optional(rendered):
    writer = next(
        x for x in rendered if x and x["kind"] == "Deployment" and x["metadata"]["name"] == "verdify-ingestor"
    )
    pod = writer["spec"]["template"]["spec"]
    container = next(c for c in pod["containers"] if c["name"] == "ingestor")
    assert {e["name"]: e.get("value") for e in container["env"]}["VERDIFY_HYDRO_PROBE_HOST"] == HYDRO_METER
    # A missing Secret must leave the reader idle, never block the sole writer's start.
    volume = next(v for v in pod["volumes"] if v["name"] == "hydro-probe")
    assert volume["secret"]["secretName"] == "verdify-hydro-probe" and volume["secret"]["optional"] is True
    mount = next(m for m in container["volumeMounts"] if m["name"] == "hydro-probe")
    assert mount == {"name": "hydro-probe", "mountPath": "/etc/verdify/hydro-probe", "readOnly": True}


def test_hydro_meter_is_one_exact_tcp_6668_rule(rendered):
    rules = [
        rule
        for rule in policy(rendered, "allow-ingestor-device-egress")["spec"]["egress"]
        if any(target.get("ipBlock", {}).get("cidr") == f"{HYDRO_METER}/32" for target in rule["to"])
    ]
    assert rules == [{"to": [{"ipBlock": {"cidr": f"{HYDRO_METER}/32"}}], "ports": [{"protocol": "TCP", "port": 6668}]}]


@pytest.mark.parametrize(
    "address,port,protocol",
    [
        (HYDRO_METER, 6667, "TCP"),
        (HYDRO_METER, 6053, "TCP"),
        ("192.168.10.111", 6668, "TCP"),
        (HYDRO_METER, 6666, "UDP"),
        (HYDRO_METER, 6667, "UDP"),
        ("192.168.10.255", 6667, "UDP"),
    ],
)
def test_hydro_meter_admits_nothing_beyond_tuya_tcp(rendered, address, port, protocol):
    assert not allowed(policy(rendered, "allow-ingestor-device-egress"), address, port, protocol)


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
        ("192.168.30.107", 22),
        ("192.168.30.107", 8883),
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
    # The cross-signed Open-Meteo chain requires two intermediates.
    assert "proxy_ssl_verify_depth 3;" in conf
    assert "access_log off;" in conf and "error_log /dev/stderr crit;" in conf
    for host, path in [
        ("api.open-meteo.com", "/v1/forecast"),
    ]:
        assert f"proxy_ssl_name {host};" in conf
        assert f"set $fixed_host {host};" in conf
        assert f"location = {path}" in conf
    assert "$http_host" not in conf and "$arg_" not in conf
    assert conf.count("location / { return 404; }") == 2
    assert "slack.com" not in conf and "listen 8082" not in conf  # #1352: no Slack destination
    # #1351: no OpenAI route (embeddings are Cortex embed.main since migration 296); Open-Meteo is the only one.
    assert "openai" not in conf and "listen 8081" not in conf
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
