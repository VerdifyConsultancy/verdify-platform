# Verdify DNS / TLS / firewall matrix — Iris → Nexus contract

> **Obsolete under the 2026-06-19 single-environment model.** This matrix was
> derived from retired dev/staging overlays and should not be used as the current
> networking source of truth. Current live routing should be verified from
> `deploy/k8s/overlays/prod`, `deploy/k8s/overlays/prod-dark`, and the cluster.

> The single source of truth Nexus implements against for Verdify edge DNS,
> certificates, and cross-zone flow. Derived strictly from the live
> `IngressRoute` manifests under `deploy/k8s/overlays/{dev,staging,prod}/` and
> `deploy/k8s/components/www/`. Every hostname below is cross-checked against an
> actual manifest (cited inline as `file:Host`).

**Owner:** Iris (Verdify platform). **Implements against:** Nexus (DNS / cert /
firewall flow). **Status:** authoring complete; Nexus dependency validation tracked on #87
(out of scope for this doc).

Last updated: 2026-06-01

> [!IMPORTANT]
> This is a documentation artifact. It contains NO secrets, tokens, API keys, or
> private URLs. Where a credential is required (e.g. the verdify.ai Cloudflare
> DNS-01 token), it is referenced by name only — never pasted. The device-route
> `:6053` flow (§4) is documented as CONTEXT ONLY and is CUTOVER-GATED; this doc
> does NOT specify opening it.

## 1. Conventions and confirmed architecture

- **Domains.** PROD owns the bare `*.verdify.ai` product hosts
  (`www`/`lab`/`api`/`graphs` + the `verdify.ai` apex). DEV and STAGE live on
  `*.k3s.verdify.ai` (k3s sub-zone). The legacy `*.vallery.net` edge hosts are
  the current live staging surface and remain until the product domain cuts over.
- **One front door.** Every routed host enters through the shared apps Traefik
  (`traefik-apps`) on the apps VIP **`192.168.7.10`** (the ADR-15 / Model B′ "one
  conformant front door"). There is no per-app LoadBalancer in the target shape
  (the `.7.21` per-app `verdify-api` LB is the documented ADD-THEN-DROP
  anti-pattern). Backends are `ClusterIP` Services resolved by Traefik's
  `kubernetescrd` provider against `Service .spec.ports[].port`.
- **WAN vs LAN reach (split-horizon).**
  - **WAN:** the cloudflared `vallery-edge` tunnel forwards each public host to
    `https://192.168.7.10` (Host-preserving, `noTLSVerify`); Cloudflare
    terminates real public TLS at the proxied edge.
  - **LAN:** a UniFi (UDM) split-horizon DNS record points the host straight at
    `.7.10`, bypassing the tunnel.
- **TLS today.** Every `IngressRoute` is `tls: {}` — the Traefik default
  certificate (the `wildcard-vallery-net` cert via the `traefik-apps` TLSStore
  `default`). Real public TLS is currently provided by Cloudflare at the tunnel
  edge. The target wildcard certs are the residual gate in §3.
- **Forward-auth.** The `api` route attaches a `verdify-strip-identity-headers`
  Middleware that clears any inbound forward-auth identity headers
  (`X-Authentik-*`, `X-Forwarded-User`, `X-Forwarded-Email`) so the api never
  trusts spoofed identity at the edge. The marketing site (`www`/apex) attaches
  NO such Middleware — it is fully public and trusts no identity.

## 2. Hostname matrix

Each row's `Source` column cites the manifest and the literal `Host()` rule it
was derived from. "INERT-ON-MERGE" = the manifest is the reviewable target shape
but is not reconciled today (no `verdify-dev` / `verdify-prod` ArgoCD App CR
exists yet; the live `verdify-local-staging` App syncs only `overlays/staging`).

| Host | Env / namespace | Origin (apps VIP) | Backend Service:port | Required cert (target) | Split-horizon direction | Forward-auth | State | Source (`file:Host`) |
|---|---|---|---|---|---|---|---|---|
| `www.verdify.ai` | prod / `verdify-prod` | `192.168.7.10` (traefik-apps) | `verdify-www`:8080 | `*.verdify.ai` wildcard | LAN → `.7.10`; WAN → Cloudflare tunnel | none (public site) | INERT-ON-MERGE (no prod App CR) | `overlays/prod/www-ingressroute.yaml:Host(www.verdify.ai)` |
| `verdify.ai` (apex) | prod / `verdify-prod` | `192.168.7.10` (traefik-apps) | `verdify-www`:8080 | `*.verdify.ai` wildcard (apex SAN) | LAN → `.7.10`; WAN → Cloudflare tunnel | none (public site) | INERT-ON-MERGE; app owns its own `/` redirects | `overlays/prod/www-ingressroute.yaml:Host(verdify.ai)` |
| `api.verdify.ai` | staging / `verdify-staging` | `192.168.7.10` (traefik-apps) | `verdify-api`:80 (staging LB Service port) | `*.verdify.ai` wildcard | LAN → `.7.10`; WAN → Cloudflare tunnel | YES (strip-identity) | LIVE host-route (synced by staging App) | `overlays/staging/ingressroute.yaml:Host(api.verdify.ai)` |
| `api.k3s.verdify.ai` | dev / `verdify-dev` | `192.168.7.10` (traefik-apps) | `verdify-api`:8080 (base ClusterIP) | `*.k3s.verdify.ai` wildcard | LAN → `.7.10`; WAN → Cloudflare tunnel | YES (strip-identity) | INERT-ON-MERGE (no dev App CR) | `overlays/dev/ingressroute.yaml:Host(api.k3s.verdify.ai)` |
| `www.k3s.verdify.ai` | dev / `verdify-dev` | `192.168.7.10` (traefik-apps) | `verdify-www`:8080 | `*.k3s.verdify.ai` wildcard | LAN → `.7.10`; WAN → Cloudflare tunnel | none (public site) | INERT-ON-MERGE (no dev App CR) | `overlays/dev/www-ingressroute.yaml:Host(www.k3s.verdify.ai)` |
| `verdify.vallery.net` | staging / `verdify-staging` | `192.168.7.10` (traefik-apps) | `verdify-api`:80 (staging LB Service port) | `*.vallery.net` (default cert, live) | LAN → `.7.10`; WAN → Cloudflare tunnel | YES (strip-identity) | LIVE host-route (bare host -> staging for now) | `overlays/staging/ingressroute.yaml:Host(verdify.vallery.net)` |
| `verdify-staging.vallery.net` | staging / `verdify-staging` | `192.168.7.10` (traefik-apps) | `verdify-api`:80 (staging LB Service port) | `*.vallery.net` (default cert, live) | LAN → `.7.10`; WAN → Cloudflare tunnel | YES (strip-identity) | LIVE host-route | `overlays/staging/ingressroute.yaml:Host(verdify-staging.vallery.net)` |
| `www-staging.vallery.net` | staging / `verdify-staging` | `192.168.7.10` (traefik-apps) | `verdify-www`:8080 | `*.vallery.net` (default cert, live) | LAN → `.7.10`; WAN → Cloudflare tunnel | none (public site) | LIVE in-cluster route; tunnel host-forward + DNS are platform-layer prereqs | `overlays/staging/www-ingressroute.yaml:Host(www-staging.vallery.net)` |

### 2.1 Backend port note (load-bearing for Nexus)

The same `verdify-api` Service is exposed on different ports per env, and Traefik
resolves against the **Service** port (not the container port):

- **staging** patches in the apps-pool LoadBalancer Service (`api-loadbalancer.yaml`)
  exposing `:80` (`name: http -> targetPort http/8080`), so the staging routes
  use `port: 80`. Using `8080` there yields Traefik `service port not found: 8080`
  → 404 at the edge.
- **dev** has NO LoadBalancer overlay; the base `verdify-api` Service is
  `ClusterIP :8080`, so the dev route uses `port: 8080`.
- **www** (all envs) is `ClusterIP :8080` (`components/www/www.yaml` Service
  `port: 8080`), so every `verdify-www` route uses `port: 8080`.

### 2.2 Blocked-on-backend hosts (NOT yet routed)

These `*.verdify.ai` hosts are staged as commented rules in
`overlays/staging/ingressroute.yaml` but are intentionally NOT active — they
still run as docker-compose on `vm-docker-web` with no k8s Service yet. Nexus
should NOT pre-provision routing for them; un-comment each only once its k8s
Service exists, else `.7.10` would route to a missing Service (502/404).

| Host | Intended backend | Status | Source |
|---|---|---|---|
| `lab.verdify.ai`, `labs.verdify.ai` | `verdify-site`:80 | commented (NEEDS k8s Service) | `overlays/staging/ingressroute.yaml` (commented rule) |
| `graphs.verdify.ai` | `grafana-proxy`:80 | commented (NEEDS k8s Service) | `overlays/staging/ingressroute.yaml` (commented rule) |
| `www.verdify.ai` + `verdify.ai` apex | RedirectRegex → `lab.verdify.ai` (staging variant) | commented; prod variant is the LIVE `www-ingressroute.yaml` route above | `overlays/staging/ingressroute.yaml` (commented rule) |

### 2.3 Deliberately NEVER on the WAN edge

The following are explicitly excluded from the public edge and must NOT be added
by Nexus:

- `traefik.verdify.ai` — Traefik ops dashboard.
- `mqtt.verdify.ai` — Jason-owned greenhouse lane (`192.168.30.150`); part of the
  device path, never WAN-exposed.

## 3. TLS / certificate matrix

| Scope | Today | Target | Mechanism |
|---|---|---|---|
| `*.vallery.net` hosts | `tls: {}` Traefik default cert = `wildcard-vallery-net` (live) | unchanged (already covered) | existing cert-manager letsencrypt-dns01, `dnsZones:[vallery.net]` |
| `*.verdify.ai` hosts (`www`, apex, `api`) | `tls: {}` Traefik default cert (NO valid public `*.verdify.ai` cert exists) | `wildcard-verdify-ai` Certificate | cert-manager letsencrypt-dns01 — **needs new verdify.ai zone solver (see blocker)** |
| `*.k3s.verdify.ai` hosts (dev `api`/`www`) | `tls: {}` Traefik default cert | `*.k3s.verdify.ai` wildcard Certificate | cert-manager letsencrypt-dns01 — same verdify.ai-zone DNS-01 dependency |

> [!WARNING]
> **BLOCKER for Nexus / James — verdify.ai DNS-01 solver gap.**
> The live cert-manager letsencrypt-dns01 ClusterIssuer solver selector is
> `dnsZones:[vallery.net]` only. There is no `verdify.ai` Cloudflare DNS-01
> credential and no `verdify.ai` zone solver, so neither `*.verdify.ai` nor
> `*.k3s.verdify.ai` wildcard Certificates can be issued today. To close it,
> Nexus needs to provide, in this order:
>
> 1. A **verdify.ai Cloudflare DNS-01 API token** (referenced by name only —
>    e.g. a Secret `cloudflare-verdify-ai-dns01`; **do NOT paste the token
>    anywhere, including this doc**).
> 2. A **verdify.ai zone solver** added to the letsencrypt-dns01 ClusterIssuer
>    (`dnsZones:[verdify.ai]` alongside the existing `vallery.net` solver).
> 3. A **`wildcard-verdify-ai` Certificate** (SANs `*.verdify.ai`, `verdify.ai`)
>    and a `*.k3s.verdify.ai` Certificate, served via the `traefik-apps`
>    TLSStore.
>
> Until then, `*.verdify.ai` HTTP routing is live but public TLS is provided only
> by Cloudflare at the tunnel edge (the tunnel's `noTLSVerify` accepts the
> default cert upstream). Source of the gap:
> `overlays/staging/ingressroute.yaml` (the `tls: {}` block comment on the
> `api.verdify.ai` rule) and `overlays/dev/ingressroute.yaml`.

## 4. Cross-zone / cross-VLAN flow table

Two distinct planes. The **web/edge plane** (rows W-*) is what this contract asks
Nexus to wire. The **device plane** (row D-1) is CONTEXT ONLY.

| ID | Flow | Source → Dest | Port(s) | Action | Cutover gate | Source manifest |
|---|---|---|---|---|---|---|
| W-1 | Public WAN → edge | Internet → cloudflared `vallery-edge` → `192.168.7.10` (traefik-apps) | 443 (Host-preserving, `noTLSVerify`) | ALLOW | live for `*.vallery.net`; `*.verdify.ai` needs matching tunnel ingress rules | `overlays/staging/ingressroute.yaml` (header) |
| W-2 | LAN → edge | LAN client → split-horizon DNS → `192.168.7.10` | 443 | ALLOW | needs UDM split-horizon records `*.verdify.ai`/`*.k3s.verdify.ai` → `.7.10` | `overlays/staging/ingressroute.yaml` (header) |
| W-3 | Edge → api backend | `traefik` ns → `verdify-api` pod | 8080 (target) | ALLOW (Traefik ingress ns only) | live (staging) | `base/networkpolicy.yaml` (allow-api-from-ingress) |
| W-4 | App → DB | `api`/`mcp`/`ingestor`/`migrate` → `db` pod | 5432 | ALLOW (in-namespace only) | live | `base/networkpolicy.yaml` (allow-db-from-app) |
| W-5 | App → cluster DNS | any verdify pod → CoreDNS | 53 (UDP/TCP) | ALLOW | live | `overlays/*/(deny-esp32-egress\|allow-ingestor-device-egress).yaml` |
| D-1 | Ingestor → ESP32 (device VLAN) | `verdify-ingestor` pod → `192.168.10.111:6053` | 6053 (ESPHome native API) | **CONTEXT ONLY — CUTOVER-GATED, do NOT open now** | M5 cutover + single-writer choreography + explicit recorded task authorization | `overlays/prod/allow-ingestor-device-egress.yaml` (port 6053) |

### 4.1 Device-route (`:6053`) — CONTEXT ONLY, do NOT open

This row is included so Nexus understands the eventual device-plane shape; it is
**not** part of what this contract authorizes opening.

- **prod** carries the SINGLE network-layer allow to the device VLAN
  (`allow-ingestor-device-egress.yaml`: egress to `192.168.10.0/24:6053`, plus
  Home Assistant `192.168.30.107:8123`/`:1883` and Frigate/go2rtc
  `192.168.30.142:5000`/`:1984`). It is the network counterpart of
  `VERDIFY_DEVICE_WRITE_ENABLED=1` — the one real single writer.
- **staging** carries the inverse interlock (`deny-esp32-egress.yaml`: allow all
  egress EXCEPT `192.168.10.0/24`, expressed via `ipBlock.except`), so a staging
  ingestor physically cannot open the `:6053` ESPHome session. Staging also pins
  `ingestor` to `replicas: 0` and `VERDIFY_DEVICE_WRITE_ENABLED=0`. Only ONE of
  the two overlays ever grants the device allow — never both, never the base.
- **Gate.** Declaring the prod policy does NOT move the control loop to k3s. The
  device-VLAN route is a STOP-and-ask-Jason / laptop-root networking step; the
  cutover also requires the single-writer choreography and exact-target preflight
  confirmation (handoff §3.4 / §6, the gated P7/P9 cutover). This doc documents
  the shape; it does NOT instruct opening `:6053` pre-M5.

## 5. Nexus implementation checklist (out of scope to execute here)

1. Tunnel ingress rules on `vallery-edge` for the `*.verdify.ai` hosts in §2
   (no new connector; same Host-preserving `noTLSVerify` → `.7.10`).
2. UDM split-horizon DNS records: `*.verdify.ai` and `*.k3s.verdify.ai` → `.7.10`
   (LAN), plus the WAN public records routed through Cloudflare.
3. The §3 cert chain: verdify.ai Cloudflare DNS-01 token (by name), zone solver,
   `wildcard-verdify-ai` + `*.k3s.verdify.ai` Certificates.
4. Do NOT touch the device plane (row D-1) — cutover-gated.
5. Acknowledge implementability on #87.

## 6. Source manifests (authoritative inputs)

- `deploy/k8s/overlays/prod/www-ingressroute.yaml`
- `deploy/k8s/overlays/staging/ingressroute.yaml`
- `deploy/k8s/overlays/staging/www-ingressroute.yaml`
- `deploy/k8s/overlays/dev/ingressroute.yaml`
- `deploy/k8s/overlays/dev/www-ingressroute.yaml`
- `deploy/k8s/components/www/www.yaml`
- `deploy/k8s/base/networkpolicy.yaml`
- `deploy/k8s/overlays/prod/allow-ingestor-device-egress.yaml` (device plane, context only)
- `deploy/k8s/overlays/staging/deny-esp32-egress.yaml` (device-safety interlock)
