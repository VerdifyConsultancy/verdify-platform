#!/usr/bin/env python3
"""Compile Verdify's existing HTTP route contracts into a tokenless file provider."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "deploy/k8s/overlays/prod/traefik/generated-routes.yaml"
NAMESPACE = "verdify-prod"
TRUSTED_MCP_ROUTE = "verdify-t2-mcp-tunnel"
TRUSTED_MCP_SPEC = {
    "entryPoints": ["verdify-tunnel"],
    "routes": [
        {
            "kind": "Rule",
            "match": "Host(`mcp.verdify.ai`) && PathPrefix(`/mcp`)",
            "middlewares": [{"name": "verdify-strip-identity-headers"}],
            "services": [{"name": "verdify-mcp", "port": 8000}],
        }
    ],
}


def compile_routes(documents: list[dict]) -> dict:
    """Fail closed on unimplemented routing semantics; never silently drop a guard."""
    selected = [d for d in documents if d.get("metadata", {}).get("labels", {}).get("traefik-tier") == "verdify"]
    middlewares = {}
    for document in selected:
        if document["kind"] == "Middleware":
            metadata = document["metadata"]
            if metadata.get("namespace") != NAMESPACE:
                raise ValueError("middleware outside verdify-prod")
            spec = document["spec"]
            if not spec or set(spec) - {"headers", "rateLimit", "inFlightReq"}:
                raise ValueError("unsupported middleware semantics")
            middlewares[metadata["name"]] = spec
    kubernetes_services = {
        d["metadata"]["name"]: d
        for d in documents
        if d.get("kind") == "Service" and d.get("metadata", {}).get("namespace") == NAMESPACE
    }
    routers, services = {}, {}
    for document in selected:
        if document["kind"] != "IngressRoute":
            continue
        metadata, spec = document["metadata"], document["spec"]
        if metadata.get("namespace") != NAMESPACE:
            raise ValueError("route outside the Verdify HTTP boundary")
        # The isolated listener carries canonical Cloudflare provenance. Never
        # expand it to another host, path, backend or middleware implicitly.
        if metadata["name"] == TRUSTED_MCP_ROUTE:
            if spec != TRUSTED_MCP_SPEC:
                raise ValueError("trusted MCP route contract changed")
        elif spec.get("entryPoints") != ["verdify-web"]:
            raise ValueError("route outside the Verdify HTTP boundary")
        if set(spec) - {"entryPoints", "routes"}:
            raise ValueError("unsupported route-level semantics")
        for index, route in enumerate(spec["routes"]):
            if route.get("kind") != "Rule" or set(route) - {"kind", "match", "priority", "middlewares", "services"}:
                raise ValueError("unsupported HTTP rule semantics")
            if len(route["services"]) != 1:
                raise ValueError("exactly one Kubernetes Service is required")
            backend = route["services"][0]
            if set(backend) - {"name", "port", "namespace", "kind", "sticky"}:
                raise ValueError("unsupported backend semantics")
            if backend.get("namespace", NAMESPACE) != NAMESPACE or backend.get("kind", "Service") != "Service":
                raise ValueError("backend must be a local Kubernetes Service")
            name, port = backend["name"], backend["port"]
            target = kubernetes_services.get(name)
            if not target or not any(p["port"] == port for p in target["spec"]["ports"]):
                raise ValueError(f"unknown backend service/port: {name}:{port}")
            key = f"{metadata['name']}-{index}"
            references = []
            for middleware in route.get("middlewares", []):
                if set(middleware) - {"name", "namespace"} or middleware.get("namespace", NAMESPACE) != NAMESPACE:
                    raise ValueError("middleware must be local")
                if middleware["name"] not in middlewares:
                    raise ValueError("unknown middleware reference")
                references.append(middleware["name"])
            router = {
                "entryPoints": spec["entryPoints"],
                "rule": route["match"],
                "service": key,
                "middlewares": references,
            }
            if "priority" in route:
                router["priority"] = route["priority"]
            routers[key] = router
            load_balancer = {
                "servers": [{"url": f"http://{name}.{NAMESPACE}.svc.cluster.local:{port}"}],
                "passHostHeader": True,
            }
            if "sticky" in backend:
                load_balancer["sticky"] = backend["sticky"]
            services[key] = {"loadBalancer": load_balancer}
    if not routers:
        raise ValueError("no Verdify routes compiled")
    # The readiness/liveness endpoint exists only if this dynamic file loads.
    # manualRouting disables the otherwise unconditional built-in ping router.
    routers["file-routing-ready"] = {"entryPoints": ["traefik"], "rule": "Path(`/ping`)", "service": "ping@internal"}
    return {"http": {"routers": routers, "services": services, "middlewares": middlewares}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = subprocess.check_output(["kustomize", "build", "deploy/k8s/overlays/prod"], cwd=ROOT, text=True)
    documents = [document for document in yaml.safe_load_all(rendered) if document]
    result = "# Generated by scripts/generate-verdify-traefik-file.py; edit route/Middleware sources.\n"
    result += yaml.safe_dump(compile_routes(documents), sort_keys=True)
    if args.check:
        if OUTPUT.read_text() != result:
            raise SystemExit("stale Verdify file routing; run scripts/generate-verdify-traefik-file.py")
    else:
        OUTPUT.write_text(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
