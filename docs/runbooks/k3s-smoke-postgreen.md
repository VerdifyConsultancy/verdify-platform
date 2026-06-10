# k3s post-green smoke + device-route monitor (#89, G10)

**Status:** RUNBOOK + READY-TO-RUN SCRIPT. The script `scripts/k3s-smoke.sh` is
authored and validated (`bash -n` clean) but has **NOT** been executed against
the cluster. It is **READ-ONLY**: it never scales/patches/applies/syncs, never
writes the `argocd` namespace, never triggers an ArgoCD sync, never touches the
live VM / docker-compose / ESP32, and never pushes a setpoint.

**When to run:** AFTER ArgoCD reports the instance green (Synced/Healthy on the
new `overlays/staging` digests), as the G10 post-deploy verifier. It is a
post-green check, not a liveness poke during an in-progress reconcile.

## What it checks (mode `smoke`, default ns `verdify-staging`)

1. **api /health/detailed** is reachable (via a localhost `kubectl port-forward`
   to `svc/verdify-api:8080`) and the baked `VERDIFY_GIT_SHA` it reports matches
   the `sha-<gitsha>` tag of the api image actually deployed on the Deployment.
   If the image is pinned only by `@sha256` digest with no readable `sha-` tag,
   it asserts a real (non-`unknown`) baked SHA is present.
2. **mcp** Deployment is Ready and the FastMCP streamable-http `/mcp` surface
   responds to a `tools/list` JSON-RPC POST (a non-empty tool list is the strong
   pass; a live-but-handshake-gated HTTP response still confirms the surface).
   Note: mcp serves `/mcp`, not `GET /health` — the base probe is `tcpSocket`.
3. **db reachable** — folded into [1]: api `/health/detailed` reports
   `checks.db_reachable=true`.
4. **STAGING ONLY: ingestor `spec.replicas == 0`** — the single-writer pin.
5. **STAGING ONLY: ZERO device-VLAN writes** — execs `ss`/`netstat` (read-only,
   lists each pod's own sockets, opens nothing) in every Running pod and asserts
   none holds an ESTABLISHED connection into `192.168.10.0/24` (the greenhouse
   device VLAN, ESP32 at `.111:6053`). If a pod has no `ss`/`netstat`, the
   manifest interlocks (deny-esp32-egress NetPol + replicas:0 +
   `VERDIFY_DEVICE_WRITE_ENABLED=0`) remain the authoritative guarantee and the
   live socket cross-check is reported as skipped, not a false pass.

## Mode `device-monitor` (prod exactly-one-writer STUB)

`scripts/k3s-smoke.sh device-monitor [--namespace verdify-prod]` counts how many
Running pods hold an ESTABLISHED connection to `192.168.10.111:6053` and asserts
the count is **exactly one** (the network-observable single-writer invariant).
It is a STUB: read-only, prints what it sees, exits non-zero on 0 or >1 writers.
Wire it into alerting / an exporter separately (e.g. a sidecar that runs `ss`
where the app image lacks it). Do NOT run `device-monitor` against prod as part
of staging cutover — it is the production single-writer guard for Stage 6.

## Usage

```sh
KUBECONFIG=/home/jason/.kube/verdify-agent.config \
  scripts/k3s-smoke.sh smoke                       # staging post-green smoke
KUBECONFIG=/home/jason/.kube/verdify-agent.config \
  scripts/k3s-smoke.sh device-monitor              # prod single-writer stub (READ in prod)
```

Flags: `--namespace NS`, `--api-port`, `--mcp-port`, `--timeout SECS`, `--help`.
Exit 0 = GREEN (all checks passed); non-zero lists the FAIL lines. Idempotent —
re-running has no side effects and yields the same verdict for the same state.

## Safety boundaries (matches CLAUDE.md + k3s-cutover-sequence.md)

- Read-only kubectl (`get`/`exec`-read) + a localhost `port-forward` tunnel
  (mutates nothing server-side). The only background processes it owns are its
  own `kubectl port-forward` PIDs, cleaned up on exit.
- The `ss`/`netstat` checks only INSPECT a pod's existing sockets; they open no
  device connection.
- Owned by verdify-agent (scripts + manifests on `live/platform-main`). It does
  not assume any write to the `argocd` ns or any ArgoCD sync.
