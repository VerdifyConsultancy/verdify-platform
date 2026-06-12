# ADR 0001 — Device-write API surface and single-writer at the API tier (multi-greenhouse)

- **Status:** Proposed — **DESIGN ONLY (M7 / #177 DEFERRED).** No code in this ADR contacts a device; nothing here is scheduled for this cutover sprint.
- **Date:** 2026-06-04
- **Owner lane:** verdify-platform (Iris authors; firmware concurrence review on heap/protocol; Jason gates any future scheduling).
- **Refs:** #177 (M7 device-write API), #79 (`VERDIFY_DEVICE_WRITE_ENABLED` single-writer gate), #27/#87 (device-VLAN route + split-horizon), #73/#132 (M5.1 single-writer cutover), #113/#114 (MQTT fan-out publish/subscribe).
- **Supersedes / extends:** the LAN-only, native-ESPHome-API device plane documented in `docs/networking/verdify-dns-tls-matrix.md` (§4, row D-1) and `docs/design/verdify-final-migration-2026-05-31.md` (§A.1). Those describe the *as-is* single greenhouse on the device VLAN; this ADR describes the *future* internet-reachable, multi-device shape and is explicitly out of scope until M7 is scheduled.

> [!IMPORTANT]
> This is a documentation artifact. It contains NO secrets, tokens, API keys,
> device PSKs, or private URLs. Credentials are referenced **by name only**. The
> design herein is NOT authorized to be implemented: per the 2026-06-04 lane
> integration, **M7 / #177 is DEFERRED — Iris authors DESIGN only; no code
> touches the device this sprint**, and the migration ships **migrate-as-is**
> (native-API dispatcher → ESP32 unchanged) with the **single-writer invariant**
> intact. This ADR records the decision *shape* so the future build inherits a
> reviewed contract rather than improvising one.

---

## 1. Context

### 1.1 Where we are today (the thing this ADR must NOT break)

The greenhouse control loop is **pull/connect-out from a single trusted writer on a trusted LAN**:

- One process — the VM `verdify-ingestor` (today) / a single prod k3s ingestor pod (post-M5.1) — opens **one** ESPHome **native-API** session to `192.168.10.111:6053` (Noise PSK, `ESP32_API_KEY`) and is the **sole** setpoint/occupancy writer. The grow-light path is the inverse: the ESP32 **polls** `verdify-setpoint-server :8200` (`docs/design/verdify-final-migration-2026-05-31.md` §A.1).
- The single-writer guarantee is **defense-in-depth, three layers** (`docs/verdify-state-and-cutover-sprint-2026-06-03.md` §A4):
  1. **process/env gate** — `VERDIFY_DEVICE_WRITE_ENABLED == '1'`, exact-string, default-deny (`ingestor/esp32_push.py:30-32`);
  2. **network-policy gate** — exactly one overlay (prod) carries `allow-ingestor-device-egress` to `192.168.10.0/24:6053`; every other overlay carries `deny-esp32-egress` (`deploy/k8s/overlays/prod/allow-ingestor-device-egress.yaml`, `overlays/staging/deny-esp32-egress.yaml`);
  3. **replica pin** — non-prod ingestors are `replicas: 0` / SHADOW.
- The device VLAN is **not internet-reachable** and the matrix is explicit that `mqtt.verdify.ai` and the `:6053` device route are *deliberately never on the WAN edge* (`docs/networking/verdify-dns-tls-matrix.md` §2.3, §4.1).

That model is correct, safe, and **must remain the production path through the entire M0→M6 cutover.** This ADR changes nothing about it. It describes the *next* problem.

### 1.2 The problem M7 eventually solves

The as-is model assumes (a) exactly one greenhouse, (b) on a LAN the writer can reach, (c) with a connect-out integration the platform initiates. The product direction (multi-tenant Verdify, greenhouses Verdify does not share a LAN with) breaks all three:

- a device **behind a customer NAT/firewall** cannot be reached by a platform-initiated connect-out to `:6053`;
- multiple devices need **per-device identity and authorization**, not one shared `ESP32_API_KEY`;
- the "one writer process" invariant has to survive **horizontally scaled, restartable, possibly multi-region** API replicas, where "one process holds the socket" is no longer expressible as a `replicas: 1 Recreate` Deployment.

So M7 is: **define the device-facing ingest + setpoint API on `api.verdify.ai`, the per-device auth model, and how the single-writer invariant is preserved when the writer is no longer one process holding one socket but an API tier of N replicas serving M devices.**

### 1.3 Scope of this ADR

In scope (decided here, design-level): the API surface, the per-device auth model, and single-writer-**at-the-API-tier**. Captured as context: the transport trade-offs (MQTT vs edge-gateway vs WS/HTTPS) and the **TLS-heap fork** (ESP32-S3/PSRAM vs edge-offload) from the 2026-06-04 discussion.

Out of scope (NOT decided here): any firmware change, any concrete transport selection (that is a go/no-go for the future build with firmware concurrence), schema/`verdify_schemas` additions, billing/multi-tenant data partitioning, and the planner's interaction with multi-device setpoints. **No device is contacted by anything in this ADR.**

---

## 2. Decision

We adopt the following **design contract** for the eventual device-write API. (Decision, not deployment — see Status.)

### D1. Surface: a device-facing API on `api.verdify.ai`, versioned and physically separated from the dashboard API.

A small, append-only-friendly device contract under a distinct path prefix and (logically) a distinct backend deployment from the human/dashboard API:

| Direction | Method + path (illustrative) | Purpose | Idempotent? |
|---|---|---|---|
| device → platform | `POST /device/v1/{device_id}/telemetry` | batch telemetry ingest (climate/equipment_state/diagnostics rows) | yes (by `(device_id, sample_seq)`) |
| device → platform | `GET  /device/v1/{device_id}/commands?after={ack_seq}` | device pulls pending setpoint/occupancy commands it has not yet acked (NAT-friendly long-poll / poll) | yes (read) |
| device → platform | `POST /device/v1/{device_id}/commands/{command_seq}/ack` | device acks an applied command (with applied value + result) | yes |
| device → platform | `POST /device/v1/{device_id}/heartbeat` | liveness + firmware version + heap/health snapshot | yes |
| platform → device | *(no platform-initiated connect-out in the default shape)* | commands are **pulled by the device**, mirroring today's grow-light `:8200` poll | n/a |

Rationale for **device-pulls-commands** as the default: it is the **migrate-as-is-shaped** generalization of the existing pull patterns (ESP32 already polls `:8200`), it traverses customer NAT without inbound holes, and it keeps the platform from ever holding a fan-out of long-lived outbound device sockets. The transport under this contract (HTTPS poll vs WS vs MQTT vs edge-gateway) is deliberately left to §4 — the *contract* (idempotent, sequence-keyed, ack'd) is transport-independent.

Separation rationale: the device API has a different auth model (§3), a different threat surface, and a different scaling profile than the dashboard API; co-mingling them on one deployment couples a device-fleet DoS to the customer-facing dashboard. The edge already strips spoofed identity headers on the api route (`verdify-strip-identity-headers`, `docs/networking/verdify-dns-tls-matrix.md` §1) — the device path must NOT reuse that human-identity middleware; it authenticates devices, not users.

### D2. Per-device auth: mutual TLS with a per-device client certificate as the primary; per-device bearer credential as the constrained-device fallback.

- **Primary — mTLS, one client cert per device.** Each greenhouse controller is provisioned a unique X.509 client certificate (device identity = the cert subject / a `device_id` claim), signed by a Verdify device CA. The API tier validates the client cert at the TLS layer; `device_id` in the URL must match the cert identity or the request is rejected. This gives cryptographic per-device identity, natural revocation (CRL/OCSP or short-lived re-issued certs), and **no shared secret** — the failure mode of today's single shared `ESP32_API_KEY` (one key compromises the whole fleet) does not exist.
- **Fallback — per-device bearer token** (opaque, rotatable, stored hashed server-side, scoped to one `device_id`) for devices/transport stacks that cannot afford a client-cert handshake's heap (see §4.2 TLS-heap fork). Strictly per-device, never a fleet-wide key, never reused across devices.
- **Provisioning** is a separate, gated, human/automation flow (out of scope to specify here): a device gets its credential once, at manufacture/onboarding, never over the unauthenticated path. The canonical-key declaration discipline already used for the single device (`GATE-105` / #105) generalizes to "per-device credential issuance is a gated step, and rotating it must not silently re-flash or drop a device."
- **No anonymous device writes, ever.** An unauthenticated request to any `/device/v1/**` path is rejected before it can touch the DB or a command queue.

### D3. Single-writer **at the API tier**: per-device command ownership via lease + monotonic sequence + idempotency, gated by an env flag that is the fleet equivalent of `VERDIFY_DEVICE_WRITE_ENABLED`.

The crux. Today "single writer" = "one process holds the one socket." At scale that physical guarantee is gone, so we reconstruct it **logically, per device**, with four mechanisms that compose:

1. **Per-device command ownership / lease.** For any device, at most one writer may hold the *command-authority lease* for that `device_id` at a time. The lease is a short-TTL row/lock in the shared store (the DB is the natural lease authority; the existing `verdify-db` is already the in-namespace single source of truth). A replica must hold (and keep renewing) the lease for `device_id` before it may enqueue a command for that device. Lease expiry + takeover is well-defined so a crashed replica's device is reclaimed without two live owners. This makes "single writer" a **per-device** property that survives N API replicas: many replicas, but for any one device exactly one current command author.
2. **Monotonic per-device command sequence.** Every command carries a strictly increasing `command_seq` allocated under the lease. The device applies commands in order and refuses to go backwards; the platform refuses to issue a `command_seq` it did not allocate monotonically. This is what makes a stale/duplicate writer *detectable and rejectable* rather than silently corrupting state — the lesson from the as-is fan-out gate (`ingestor/mqtt_fanout.py`: subscribe mode can never write; modes are mutually exclusive and asserted at startup) generalized to per-device sequencing.
3. **Idempotency on both directions.** Telemetry ingest is idempotent on `(device_id, sample_seq)` (a retried POST after a flaky customer link writes the same row once). Command ack is idempotent on `(device_id, command_seq)`. This is mandatory because a NAT-traversing device WILL retry — at-least-once delivery with idempotent handlers gives effectively-once without distributed transactions.
4. **The fleet env gate.** A single platform-level flag — call it `VERDIFY_DEVICE_WRITE_ENABLED` extended to the fleet (or a successor `VERDIFY_FLEET_WRITE_ENABLED`) — default-deny, exact-string `'1'`, read at call time, that the *command-issuing* path checks before it may hand any device a non-empty command set. This preserves the existing #79 discipline (`esp32_push.py:30-32`): a misconfigured/forgotten env makes the whole command tier a **read+telemetry-only no-op against every device**, exactly as a staging ingestor is a no-op today. Telemetry ingest stays allowed (it is not a device write); only the command/setpoint direction is gated.

The composition guarantee: **lease** ⇒ at most one current command author per device; **sequence** ⇒ a stale author is rejected, not silently applied; **idempotency** ⇒ retries are safe; **env gate** ⇒ a whole-fleet kill switch with the same default-deny semantics the operators already trust. No two-writer window and no zero-writer-corruption window for any individual device, by construction, without requiring "one global process."

### D4. The as-is path is unchanged and remains the only production path until M7 is explicitly scheduled and gated by Jason.

This ADR adds a future contract; it removes nothing. The single greenhouse keeps running the native-API connect-out path through M0→M6. M7 does not begin at the M5.1 cutover and is not part of it.

---

## 3. Single-writer invariant — how the new contract preserves it (mapping table)

| Invariant property | As-is mechanism (today, 1 greenhouse) | At-API-tier mechanism (M7 design, N devices) |
|---|---|---|
| At most one writer per device | one process holds the one `:6053` socket; `replicas:1 Recreate` | per-device **command lease** (short TTL, single holder, defined takeover) |
| Stale/duplicate writer can't corrupt | only one socket exists | **monotonic `command_seq`**; device + platform reject out-of-order/duplicate |
| Retries are safe | n/a (single in-LAN session) | **idempotency** on `(device_id, sample_seq)` and `(device_id, command_seq)` |
| Default-deny kill switch | `VERDIFY_DEVICE_WRITE_ENABLED='1'` exact, default off (`esp32_push.py:32`) | fleet env gate, same exact-string default-deny semantics; command direction only |
| Network-layer single allow | one overlay carries `allow-ingestor-device-egress` (#80) | edge admits authenticated devices; **no platform-initiated device connect-out** in the default shape (devices pull) |
| Misconfigured non-prod = no-op | staging `replicas:0` + `deny-esp32-egress` | a replica without a held lease, or with the fleet gate off, issues empty command sets |

**Critical non-regression:** nothing in M7 may enable a *second* writer to the existing single greenhouse. During any transition, the device's command authority must be a single, explicit, lease-owned handoff — the same "stop the old writer in the same atomic window you start the new one" choreography as M5.1 (archived in `/Users/jason/Orbit/context_dump/verdify-platform/docs/handover/verdify-migration-lanes-2026-06-04.md` §M5.1), expressed per-device via the lease rather than per-host via systemd stop. The ESTAB-count proof generalizes to "exactly one current lease holder per device."

---

## 4. Transport trade-offs (context from the 2026-06-04 discussion)

The §2 contract (idempotent, sequence-keyed, ack'd, device-pulls-commands) is transport-independent. Three candidate transports were weighed; **none is selected here** — selection is a future go/no-go with firmware concurrence.

### 4.1 MQTT vs edge-gateway vs WS/HTTPS

| Dimension | **MQTT** (device ↔ broker) | **Edge-gateway** (local gateway ↔ device on LAN; gateway ↔ cloud) | **WS/HTTPS** (device ↔ `api.verdify.ai`) |
|---|---|---|---|
| NAT/firewall traversal | good (device opens outbound to broker) | excellent (only the gateway egresses) | good (device opens outbound HTTPS) |
| Reuses what we already run | partial — fan-out bus already MQTT (#113/#114), but that is *telemetry*, internal, not a device-auth broker | adds a new component class (a per-site gateway) | best — `api.verdify.ai` + Traefik + cloudflared edge already exist (`dns-tls-matrix` §1) |
| Per-device auth fit | per-device MQTT creds / mTLS at broker; broker ACLs per topic | gateway holds device trust on the LAN; cloud trusts the gateway (one hop of trust) | per-device mTLS / bearer at the API tier (D2) directly |
| Command delivery model | broker push to per-device topic (retained=last-command) | gateway mediates; can keep the as-is native-API path *locally* | device long-poll / WS pull (D1), mirrors `:8200` poll |
| Single-writer fit (D3) | topic ownership + broker ACL must encode the lease; sequence in payload | gateway is the natural per-site single writer — closest to as-is | lease+seq live in the API tier where we already gate writes |
| ESP32 heap cost | persistent TLS MQTT session (see §4.2) | **device stays on plaintext/Noise LAN native-API; gateway bears cloud TLS** | persistent TLS (WS) or repeated TLS handshakes (HTTPS poll) — heaviest on-device |
| New ops surface | broker auth/ACL/scaling, retained-msg hygiene | a fleet of gateways to provision/update (one per site) | least new infra; rides existing edge |
| Migrate-as-is alignment | medium | **highest** — the device firmware/native-API path is literally unchanged; only the gateway is new | low — device firmware must speak cloud TLS itself |

**Reading of the table (not a decision):**
- **WS/HTTPS** is the lowest *new-infra* path because `api.verdify.ai` and the edge exist, and it maps cleanest onto D1/D3 (lease + gate live in the API tier we already control). Its cost is **on-device TLS** (§4.2).
- **Edge-gateway** is the highest *migrate-as-is* path: the ESP32 keeps the unchanged native-API/Noise session on the local LAN, and a small per-site gateway is the one component that bears cloud TLS + holds the per-site command lease. It is the closest structural analog to "one writer per site" and the friendliest to the firmware-freeze posture — at the cost of a new device class to build/provision/update.
- **MQTT** reuses our protocol familiarity and the existing fan-out concept, but the fan-out bus today is *internal telemetry* (#113/#114), not a device-authenticating, per-device-ACL'd, command-bearing broker; turning it into one is real work, and topic-ownership has to encode the lease.

### 4.2 The TLS-heap fork (ESP32-S3 / PSRAM vs edge-offload)

The load-bearing on-device constraint surfaced 2026-06-04. The current controller already paces ESP32 writes specifically to avoid **transient heap-pressure alerts** post-OTA (`ingestor/esp32_push.py:47-51`). Adding a **persistent outbound cloud-TLS session** (WS/HTTPS-keepalive/MQTT-over-TLS) to the device is a materially larger and *sustained* heap commitment than the current LAN Noise session. That forks the design:

- **Fork A — TLS on-device (ESP32-S3 / PSRAM).** The device terminates cloud TLS itself (WS or MQTT-over-TLS or HTTPS poll). Requires a part with enough RAM to hold the TLS record buffers + cert chain + app state *concurrently with the control loop* — practically an **ESP32-S3 with PSRAM**, not the bare ESP32 class. This is a **hardware fork** (a device-revision / new BOM), and a firmware-stack fork (mbedTLS tuning, session reuse, fragment sizes). It keeps the device a first-class internet endpoint with per-device mTLS (D2 primary). Risk: heap regressions are exactly the failure class the firmware-freeze rules were written for; this path must clear firmware concurrence on a measured heap budget, not an estimate.
- **Fork B — edge-offload (local gateway / Forks toward §4.1 edge-gateway).** The device **never speaks cloud TLS.** It keeps the current LAN-side Noise/native-API session to a nearby **gateway** (which can be a Pi-class device or a small always-on box per site); the gateway bears the cloud-TLS, holds the per-site command lease, and speaks the §2 device API upstream. No ESP32 hardware change, no on-device TLS heap cost — the firmware-freeze posture is preserved end-to-end. Cost: the gateway is a new device class with its own provisioning, update, and failure story, and it is one more hop that can be the single point of failure for a site (mitigated by it being the per-site single writer, which is what we want).

**Framing for the future decision (NOT decided here):** Fork B (edge-offload) is the better fit for *migrate-as-is* + the firmware-freeze posture + the single-writer invariant (gateway = natural per-site single writer), at the cost of a new gateway component. Fork A (TLS on-device) is the better fit for a *true* fleet of self-contained internet devices with no per-site box, at the cost of a hardware revision (ESP32-S3/PSRAM) and a heap budget that must be proven, not assumed. The choice is a hardware + firmware + ops decision that belongs to the scheduled-M7 go/no-go with firmware concurrence and Jason's gate — this ADR only records that the fork exists and what each arm costs.

---

## 5. Consequences

**Positive**
- A reviewed contract exists before any code is written, so the eventual build inherits the single-writer discipline (#79) instead of reinventing it under deadline.
- Per-device mTLS removes the single-shared-key blast radius that the current `ESP32_API_KEY` has.
- The lease + monotonic-sequence + idempotency + fleet-env-gate composition reconstructs the single-writer invariant *per device* without requiring a single global process, so the API tier can scale.
- Device-pulls-commands keeps the platform free of a fan-out of outbound device sockets and traverses customer NAT — and is the natural generalization of the existing `:8200` poll, honoring migrate-as-is.

**Negative / risks**
- Either transport fork costs something real: Fork A is a hardware revision + a heap budget that has historically been our most dangerous regression class; Fork B is a whole new gateway device class to build, provision, and keep updated.
- A lease authority becomes single-writer-critical infrastructure: if the lease store is unavailable, command issuance must **fail closed** (no commands) rather than fail open (two writers) — which means a lease outage degrades to telemetry-only, consistent with the default-deny posture.
- mTLS at fleet scale brings real PKI ops (issuance, rotation, revocation, CRL/OCSP distribution to NAT'd devices) that the single-device shared-key model never had.

**Neutral / explicitly deferred**
- No `verdify_schemas/` change is proposed here; a future consumer PR would land schema first per the repo's schema-first rule.
- No transport is selected; no firmware is touched; no device is contacted.

---

## 6. Compliance with the governing invariants

- **migrate-as-is:** the production native-API dispatcher → ESP32 path is unchanged; this ADR is additive future design. ✔
- **single-writer:** nothing here enables a second writer to the live greenhouse; the existing #79 three-layer gate is untouched and the new contract *strengthens* the invariant per-device (lease + sequence + idempotency + fleet gate). ✔
- **#177 / M7 DEFERRED — design only:** zero code that contacts the device; this is a pure document; implementation is gated on a future Jason go/no-go. ✔
- **no secrets:** all credentials referenced by name only. ✔

## 7. Open questions for the future M7 go/no-go (NOT decided here)

1. Transport selection — Fork A (ESP32-S3/PSRAM, TLS on-device) vs Fork B (edge-gateway offload). Needs firmware concurrence on a *measured* heap budget.
2. Lease authority — reuse `verdify-db` advisory locks / a lease table, or a purpose-built coordination store? Fail-closed semantics on lease-store outage to be specified.
3. PKI ops at fleet scale — CA hierarchy, issuance/rotation/revocation, OCSP/CRL reachability for NAT'd devices.
4. How the planner addresses N greenhouses' setpoints, and how per-device command authority interacts with the planner's write path.
5. Multi-tenant data partitioning (per-greenhouse isolation in the DB) — out of scope here, a prerequisite for productizing.
