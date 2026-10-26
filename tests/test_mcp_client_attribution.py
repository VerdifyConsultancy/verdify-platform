"""Source-only security boundary checks for the canonical MCP tunnel path (#1010)."""

from __future__ import annotations

import copy

import pytest
from test_verdify_traefik_file import MODULE
from test_verdify_traefik_file import documents as documents


def resource(documents, kind, name):
    return next(d for d in documents if d["kind"] == kind and d["metadata"]["name"] == name)


def test_tunnel_chain_is_mcp_only_and_ordinary_path_is_preserved(documents):
    outer = resource(documents, "IngressRoute", "verdify-mcp-tunnel-forward")["spec"]
    assert outer == {
        "entryPoints": ["tunnelclient"],
        "routes": [
            {
                "kind": "Rule",
                "match": "Host(`mcp.verdify.ai`) && PathPrefix(`/mcp`)",
                "services": [{"name": "verdify-traefik", "port": 81}],
            }
        ],
        "tls": {},
    }
    old_outer = resource(documents, "IngressRoute", "verdify-tier1-forward")["spec"]
    assert old_outer["entryPoints"] == ["websecure", "web"]
    assert old_outer["routes"][0]["services"] == [{"name": "verdify-traefik", "port": 80}]
    service = resource(documents, "Service", "verdify-traefik")["spec"]
    assert service["type"] == "ClusterIP"
    assert service["ports"] == [
        {"name": "web", "port": 80, "targetPort": "verdify-web", "protocol": "TCP"},
        {"name": "mcp-tunnel", "port": 81, "targetPort": "mcp-tunnel", "protocol": "TCP"},
    ]
    generated = MODULE.compile_routes(documents)["http"]
    trusted = generated["routers"]["verdify-t2-mcp-tunnel-0"]
    assert trusted["entryPoints"] == ["verdify-tunnel"]
    assert trusted["middlewares"] == ["verdify-strip-identity-headers"]
    assert generated["routers"]["verdify-t2-mcp-0"]["entryPoints"] == ["verdify-web"]
    assert generated["routers"]["verdify-t2-mcp-0"]["middlewares"] == [
        "verdify-strip-identity-headers",
        "verdify-strip-client-headers",
    ]
    assert generated["middlewares"]["verdify-strip-client-headers"] == {
        "headers": {"customRequestHeaders": {"Cf-Connecting-Ip": "", "Cf-Ray": ""}}
    }


@pytest.mark.parametrize("mutation", ["host", "path", "backend", "middleware", "ordinary_listener", "extra_route"])
def test_trusted_route_cannot_expand_silently(documents, mutation):
    candidate = copy.deepcopy(documents)
    spec = resource(candidate, "IngressRoute", "verdify-t2-mcp-tunnel")["spec"]
    route = spec["routes"][0]
    if mutation == "host":
        route["match"] = "Host(`api.verdify.ai`) && PathPrefix(`/mcp`)"
    elif mutation == "path":
        route["match"] = "Host(`mcp.verdify.ai`)"
    elif mutation == "backend":
        route["services"] = [{"name": "verdify-api", "port": 8080}]
    elif mutation == "middleware":
        route["middlewares"] = []
    elif mutation == "ordinary_listener":
        spec["entryPoints"] = ["verdify-web"]
    else:
        spec["routes"].append(copy.deepcopy(route))
    with pytest.raises(ValueError, match="trusted MCP route contract changed"):
        MODULE.compile_routes(candidate)


@pytest.mark.parametrize("name", ["verdify-t2-api", "verdify-t2-lab", "verdify-t2-mcp"])
def test_other_routes_cannot_attach_to_trusted_listener(documents, name):
    candidate = copy.deepcopy(documents)
    resource(candidate, "IngressRoute", name)["spec"]["entryPoints"] = ["verdify-tunnel"]
    with pytest.raises(ValueError, match="outside the Verdify HTTP boundary"):
        MODULE.compile_routes(candidate)


def tunnel_admissions(documents):
    # NetworkPolicies are additive: inspect every rendered policy selecting the
    # proxy, including a possible broad namespace default, not only the new one.
    proxy = resource(documents, "Deployment", "verdify-traefik")["spec"]["template"]["metadata"]["labels"]
    admissions = []
    for policy in [d for d in documents if d["kind"] == "NetworkPolicy"]:
        selector = policy["spec"]["podSelector"]
        if any(proxy.get(k) != v for k, v in selector.get("matchLabels", {}).items()):
            continue
        if any(
            (e["operator"] == "In" and proxy.get(e["key"]) not in e["values"])
            or (e["operator"] == "NotIn" and proxy.get(e["key"]) in e["values"])
            or (e["operator"] == "Exists" and e["key"] not in proxy)
            or (e["operator"] == "DoesNotExist" and e["key"] in proxy)
            for e in selector.get("matchExpressions", [])
        ):
            continue
        for rule in policy["spec"].get("ingress", []):
            if not rule.get("ports") or any(
                p.get("protocol", "TCP") == "TCP"
                and (
                    "port" not in p
                    or p["port"] == "mcp-tunnel"
                    or isinstance(p["port"], int)
                    and p["port"] <= 8001 <= p.get("endPort", p["port"])
                )
                for p in rule["ports"]
            ):
                admissions.append((policy["metadata"]["name"], rule))
    return admissions


def test_new_port_has_only_one_namespace_and_workload_scoped_ingress_grant(documents):
    admissions = tunnel_admissions(documents)
    assert admissions == [
        (
            "allow-verdify-mcp-tunnel-from-apps-ingress",
            {
                "from": [
                    {
                        "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "traefik-apps"}},
                        "podSelector": {
                            "matchLabels": {
                                "app.kubernetes.io/name": "traefik",
                                "app.kubernetes.io/instance": "traefik-apps-traefik-apps",
                            }
                        },
                    }
                ],
                "ports": [{"port": 8001, "protocol": "TCP"}],
            },
        )
    ]


@pytest.mark.parametrize("mutation", ["namespace_only", "pod_only", "or_peers", "ip_block", "all_ports", "port_range"])
def test_broad_policy_regressions_are_detected(documents, mutation):
    candidate = copy.deepcopy(documents)
    rule = resource(candidate, "NetworkPolicy", "allow-verdify-mcp-tunnel-from-apps-ingress")["spec"]["ingress"][0]
    peer = rule["from"][0]
    if mutation == "namespace_only":
        del peer["podSelector"]
    elif mutation == "pod_only":
        del peer["namespaceSelector"]
    elif mutation == "or_peers":
        rule["from"] = [{"namespaceSelector": peer["namespaceSelector"]}, {"podSelector": peer["podSelector"]}]
    elif mutation == "ip_block":
        rule["from"] = [{"ipBlock": {"cidr": "10.42.0.0/16"}}]
    elif mutation == "all_ports":
        del rule["ports"]
    else:
        rule["ports"] = [{"port": 8000, "endPort": 9000, "protocol": "TCP"}]
    assert tunnel_admissions(candidate) != tunnel_admissions(documents)
    assert len(tunnel_admissions(candidate)) == 1


def test_logs_keep_only_routing_peer_status_and_bounded_provenance_headers(documents):
    args = resource(documents, "Deployment", "verdify-traefik")["spec"]["template"]["spec"]["containers"][0]["args"]
    settings = dict(arg[2:].split("=", 1) for arg in args)
    assert settings["accesslog.format"] == "json"
    assert settings["accesslog.fields.defaultmode"] == "drop"
    assert settings["accesslog.fields.headers.defaultmode"] == "drop"
    assert {
        k.removeprefix("accesslog.fields.names."): v
        for k, v in settings.items()
        if k.startswith("accesslog.fields.names.")
    } == {
        "StartUTC": "keep",
        "Duration": "keep",
        "ClientHost": "keep",
        "RequestHost": "keep",
        "RequestMethod": "keep",
        "RouterName": "keep",
        "entryPointName": "keep",
        "DownstreamStatus": "keep",
    }
    assert {
        k.removeprefix("accesslog.fields.headers.names."): v
        for k, v in settings.items()
        if k.startswith("accesslog.fields.headers.names.")
    } == {"Cf-Connecting-Ip": "keep", "Cf-Ray": "keep"}
    assert not any("forwardedheaders" in arg.lower() or "proxyprotocol" in arg.lower() for arg in args)
    assert settings["entryPoints.verdify-tunnel.address"] == ":8001/tcp"
    assert settings["entryPoints.verdify-tunnel.asDefault"] == "false"
    assert settings["entryPoints.verdify-web.asDefault"] == "true"
