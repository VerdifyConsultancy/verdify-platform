# Runbook — Device-VLAN reachability spike (k3s cutover prep, handoff §3.4)

**Status:** PREP / design-only. This runbook describes a *gated* diagnostic. The
diagnostic itself is read-only, but the prerequisite it depends on — a route
from the k3s pod network to the device VLANs — is a **STOP-and-ask-Jason**
networking change that is **NOT performed here** (see [Gate 0](#gate-0--the-route-itself-stop-and-ask-jason)).

**Owner of this prep:** firmware agent (design only).
**Manifest:** `deploy/k8s/diagnostics/device-vlan-spike.yaml` (throwaway Pod,
`restartPolicy: Never`, namespace `verdify-staging`).
**Track A (greenhouse stays alive) outranks Track B (this refactor), always.**

---

## 1. Why this exists

Moving the **ingestor / setpoint dispatcher** (the only device-touching
workload, handoff §3.3) onto k3s hinges on one unknown: **can a pod reach the
device-VLAN endpoints the control loop needs, fast enough for the 5-10s
occupancy→light SLA?**

The occupancy→light path is `Frigate → MQTT → ingestor → ESPHome API push → 5s
firmware tick → HA Lutron`. The audited baseline for the band-change latency is
**p50 37s / p95 81s**; the occupancy→light reaction must land in **~5-10s**.
Adding network hops is a *functional regression risk*, so we measure pod→device
RTT before any cutover.

This spike proves **bare reachability + RTT only**. It is the safe predecessor
to the real, gated steps:
1. (gated) enable/confirm the route — Jason / laptop-root.
2. (this spike) prove TCP reachability + latency, read-only.
3. (gated) add the egress NetworkPolicy — reviewed PR + Jason.
4. (gated) first real ESPHome session + first live setpoint push from a pod —
   explicit Jason confirmation (handoff §6, P7).

> **Already proven once (2026-05-30, laptop-root):** a throwaway pod on the
> flannel pod-net reached the live ESP32 at `192.168.10.111:6053` over **plain
> L3 routing** (`via 192.168.30.1`, no VLAN-64 NIC / macvlan / Multus, **no
> firewall change**) at **~8 ms** — ~1,000× under the SLA. HA `:8123` also
> reachable. This runbook + manifest let the firmware agent / operator
> **reproduce** that result from the Verdify staging namespace on demand and
> capture it as a repeatable artifact, without re-establishing the route by hand.

---

## 2. What the probe does (and refuses to do)

The Pod runs a stdlib-only `python:3.12-alpine` container that does a **bare TCP
connect + RTT timing** to each target, then closes the socket. No payload, no
protocol bytes.

| Target | Host:port | Required? | What it is |
|---|---|---|---|
| esp32-native-api | `192.168.10.111:6053` | **yes** | ESPHome native API — setpoint/occupancy push + telemetry (SLA-critical) |
| home-assistant | `192.168.30.107:8123` | **yes** | HA REST — Shelly/Tempest/hydro/Lutron service calls + occupancy |
| local-mqtt | `192.168.30.107:1883` | **yes** | Sentinel occupancy bridge + ESP32 state |
| frigate-api | `192.168.30.142:5000` | no (info) | go2rtc/Frigate occupancy + camera |
| frigate-go2rtc | `192.168.30.142:1984` | no (info) | go2rtc stream API |

**It MUST NOT and CANNOT (by image + by design):**
- open an `aioesphomeapi` / ESPHome native-API **session** or authenticate
  (no Noise PSK is mounted; `aioesphomeapi` is not in the image);
- subscribe to or publish on MQTT (`paho-mqtt` is not in the image);
- push any setpoint, occupancy, or OTA — it never sends a byte after the TCP
  handshake;
- mount any secret or service-account token (`automountServiceAccountToken:
  false`, no `secretKeyRef`).

**Tempest weather** is a direct UDP LAN broadcast to the ESP32 (L2-local). It
terminates at the ESP32, is out of the pod's path entirely (handoff §3.4 option
3), and is **deliberately not probed**.

The first real API session or setpoint push from a pod is a **separate gated
step** (Jason, P7) — not this runbook.

---

## 3. Gates and owners

### Gate 0 — the route itself (STOP and ask Jason)

A normal k3s pod cannot reach the greenhouse VLAN (`192.168.10.0/24`) or the
services VLAN by default. **Any change to enable/alter that route — firewall,
router, VLAN, MetalLB, or CNI posture — is device-network-affecting and is a
hard STOP boundary (handoff §3.4 / §6).**

- **This runbook and manifest do NOT make that change.** No `kubectl`, no
  firewall edit, no route add is performed by the firmware agent.
- **Owner of the route decision:** **Jason** (network posture sign-off) and
  **laptop-root** (any cluster/UniFi-side change).
- Per the 2026-05-30 spike, plain L3 routing **already** reaches the ESP32 with
  no firewall change, so in practice Gate 0 may already be satisfied —
  **confirm with Jason that the route is in place and that running this probe is
  authorized** before applying the Pod. Do not assume.

### Gate 1 — apply / run authorization (laptop-root applies)

`kubectl apply` of any manifest to the live cluster is a **laptop-root** action
in this program (the firmware agent does design/PR only; no cluster apply). The
operator who runs the probe confirms with Jason first (Gate 0) and applies in
the `verdify-staging` namespace, which already exists.

### Gate 2 — the PASS/FAIL cutover gate (read the result, decide)

See [§5](#5--passfail-gate--what-the-result-means). PASS does **not** authorize
a setpoint push; it only clears the *reachability* unknown.

---

## 4. How to run it (operator / laptop-root)

Prerequisites: Gate 0 confirmed with Jason; `verdify-staging` namespace exists;
`kubectl` context points at the live k3s cluster.

```sh
# 1. Validate the manifest locally first (matches the k8s-manifests CI gate).
kubeconform -strict -ignore-missing-schemas deploy/k8s/diagnostics/device-vlan-spike.yaml

# 2. Apply the throwaway Pod (laptop-root; Gate 1). It is NOT in any
#    kustomization, so ArgoCD will never see or re-create it.
kubectl apply -f deploy/k8s/diagnostics/device-vlan-spike.yaml

# 3. Watch it run to completion (it exits within a few seconds; the
#    activeDeadlineSeconds:180 backstop kills it if a target hangs).
kubectl -n verdify-staging wait --for=jsonpath='{.status.phase}'=Succeeded \
  pod/device-vlan-spike --timeout=200s \
  || kubectl -n verdify-staging get pod device-vlan-spike -o wide

# 4. Read the per-target + summary output.
kubectl -n verdify-staging logs device-vlan-spike

# 5. ALWAYS delete it — it is throwaway. (restartPolicy:Never means it won't
#    restart, but the Pod object lingers until deleted.)
kubectl -n verdify-staging delete pod device-vlan-spike
```

If a default-deny-egress NetworkPolicy is later added to the namespace, the
probe Pod will be blocked at L3. In that case the operator adds a **scoped,
temporary** egress allow for the `verdify.ai/diagnostic: device-vlan-spike`
selector as a separate reviewed step — mirroring (but not enabling) the
`gated-§3.4` `allow-ingestor-device-egress` placeholder in
`deploy/k8s/base/networkpolicy.yaml`. Today the namespace has only a
default-deny-**ingress** policy, so egress is open and no extra allow is needed.

---

## 5. PASS/FAIL gate — what the result means

The container prints one line per target and a `RESULT: PASS|FAIL` summary, and
sets its exit code accordingly (0 = PASS, 1 = FAIL).

**PASS** — every **required** target (`esp32-native-api`, `home-assistant`,
`local-mqtt`) is `reachable=y` **and** the worst observed TCP RTT is far under
the SLA (expect single-digit to low-tens of ms; the watch threshold is 250 ms).

- **Meaning:** the ingestor *can* move to k3s — handoff §3.4 **Option 1** is
  viable. What remains is **not** a routing unknown; it is (a) the egress
  NetworkPolicy (Gate, reviewed PR) and (b) the single-writer cutover
  choreography (Gate, Jason): `replicas:1` + `strategy:Recreate`, ingestor
  pinned OFF in staging via `overlays/local-staging/ingestor-replicas-zero.yaml`
  until the gated flip.
- **PASS still does NOT authorize a live setpoint push.** That is the next,
  separate gated step (P7, Jason).

**FAIL on a required target** (most importantly `esp32-native-api`) —

- **Meaning:** the ingestor **stays VM-side** — handoff §3.4 **Option 2** (the
  device loop is the last thing to move, or never moves). This is fully
  consistent with "additive + reversible" and "Track A wins."
- **Do NOT** attempt to "fix" reachability by changing the route/firewall here.
  A FAIL is a **finding to hand to Jason / laptop-root**, not a change to make.
- The stateless API / MCP / site / DB-read services still move to k3s; only the
  device-touching ingestor is deferred.

A high but non-failing RTT (over the 250 ms watch, under the SLA) is an
**investigate** signal — capture it, note it on the cutover gate, and discuss
the headroom with Jason before relying on the path for the 5-10s loop.

---

## 6. What this runbook explicitly does NOT do

- It does **not** enable, add, or modify any route / firewall / VLAN / MetalLB /
  CNI configuration. That is a **STOP-and-ask-Jason** boundary owned by Jason
  (posture) and laptop-root (cluster/UniFi). The probe only *measures* a path
  that is already (or is confirmed by Jason to be) in place.
- It does **not** open an ESPHome session, authenticate, subscribe/publish MQTT,
  or push any setpoint / occupancy / OTA. First live device write from k3s is a
  separate gated step (Jason, P7).
- It does **not** deploy the ingestor, flip `replicas:0→1`, or get added to any
  kustomization / ArgoCD Application. The single-writer cutover is gated (P9).
- It does **not** touch the live VM stack, which remains authoritative.

---

## 7. Record the result

Capture the probe output (the per-target lines + `RESULT:` summary) as an
artifact alongside the cutover-gate evidence (the §3.4 row in
`docs/runbooks/verdify-cicd-golden-path-status-2026-05-30.md`). A reproduced
PASS from `verdify-staging` is the artifact that lets the cutover gate cite "k3s
pod proven to reach the ESP32 + HA + MQTT within latency budget" with a date and
a namespace, rather than relying solely on the laptop-root one-off.
