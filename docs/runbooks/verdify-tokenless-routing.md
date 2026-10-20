# Tokenless tier-2 Verdify routing (#1006)

Jason chose the file-provider migration on October 20, 2026. Traefik v3.7.1's [CRD client](https://github.com/traefik/traefik/blob/v3.7.1/pkg/provider/kubernetes/crd/client.go#L207-L250) always starts Secret and ConfigMap informers and waits for their caches. Removing Secret permissions from that provider cannot meet both no-Secret-access and working-route acceptance. The file provider needs no Kubernetes identity or RBAC.

The existing tier-2 `IngressRoute` and `Middleware` documents carrying `traefik-tier=verdify` remain the source contracts. `scripts/generate-verdify-traefik-file.py` renders prod and projects those HTTP rules into `deploy/k8s/overlays/prod/traefik/generated-routes.yaml`. It preserves match rules, priority, middleware order, identity-header stripping and backend Service names/ports; it refuses unsupported TLS, cross-namespace, multi-backend and unknown-service/middleware semantics. Keep adding route/Middleware changes to their existing owner source, regenerate, and run the routing tests. The shared tier-1 route stays unchanged.

```sh
python scripts/generate-verdify-traefik-file.py
python scripts/generate-verdify-traefik-file.py --check
python -m pytest -q tests/test_verdify_traefik_file.py tests/test_verdify_traefik_placement.py
```

The content-hashed ConfigMap is mounted as a read-only directory. Updating it changes the Deployment's referenced name and rolls the two stateless edge replicas with maxUnavailable=0. The proxy uses only the file provider, has token automount disabled on both Pod and ServiceAccount, and has no Role or Binding. The generated file owns the internal `/ping` router (`ping.manualRouting=true`), so a file that does not load cannot pass readiness/liveness. The internal ping entrypoint is not a public Service port.

Backends are stable in-namespace Service DNS names, resolved and load-balanced by Kubernetes. This removes endpoint discovery from the proxy. The Grafana render route still targets the existing cache Service and retains its cookie settings, but the proxy no longer selects individual cache Pod IPs: the cookie now selects the Service upstream, not an individual cache replica. Do not claim identical per-browser cache-Pod affinity. Verify real anonymous iframe/PNG rendering and repeated cache behavior after rollout; no durable cache data or storage is changed.

Deployment: inspect the full prod diff, merge the intended revision, and perform the documented exact-revision full nonpruning sync with fresh hooks. Verify new Pod UIDs, the generated ConfigMap mount, no Kubernetes arguments or projected API-token volume, loaded routes and all public paths before deleting the old ClusterRoleBinding and ClusterRole `verdify-traefik` by their exact UID. Update central ownership in the same delivery window. Verify all effective Kubernetes verbs for the ServiceAccount, including local/cluster Secret reads, are denied. Collect at least one hour of current-Pod logs and route checks before closing #1006.

Rollback is a scoped inverse of the file-provider source/config. Keep the old RBAC metadata receipt as recovery information; restoring cluster-wide Secret access is not the default rollback. If routing fails before the rolling deployment becomes Ready, retain the healthy old replicas and diagnose the exact parser/backend error. Never alter shared tier-1 routing or relax token/RBAC restrictions to hide a bad generated file.
