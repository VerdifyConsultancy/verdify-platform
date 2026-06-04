# M5 — Single-Writer Cutover Runbook (atomic ESP32-writer handoff, iris VM → k3s prod)

**Status:** DRAFT — PAPER ONLY. **Nothing in this document auto-executes.** No
command here is run by authoring or reviewing it. No `kubectl`/ArgoCD apply/sync,
no `systemctl` stop/start, no `ConfigMap`/replica/NetworkPolicy edit, no setpoint
push, no ESP32 native-API session, no firmware flash, no secret read/seal. Every
step is teed up for **Jason** to execute by hand at the gated M5 moment. The doc
stays marked **DRAFT** until its two preconditions land (IRIS-W012 setpoint
coverage gate + IRIS-W013 `:6053` ESTAB==1 monitor); Jason executes at M5.

**Handle:** IRIS-W014 (the W014 deliverable). **Issue:** #132 (DRAFT runbook),
child of **#73** (EPIC prod cutover / G9 atomic single-writer handoff), member of
**#15**. Companion monitor spec: **#89** (G10 smoke + `:6053` ESTAB monitor) — see
[`./k3s-smoke-postgreen.md`](./k3s-smoke-postgreen.md) and §7 below.

**Authored by:** Iris (`iris/cutover-runbook-and-monitor`). **requested-by: iris.**

> **The rule above everything: Track A (the greenhouse stays alive) > Track B
> (this refactor), always.** The live VM stack is the source of truth and remains
> authoritative right up to the atomic handoff instant. The ingestor — the ONLY
> workload that holds the live ESP32 native-API connection — moves LAST and only
> here. No firmware OTA is EVER part of cutover. **migrate-as-is:** the native-API
> dispatcher just changes which host dials out to the ESP32; no firmware change,
> no controller logic moves cloud-side. The ESP32 keeps its deterministic 8-state
> FSM + 5 s loop + relay safety regardless of who is connected.

---

## 0. The one invariant this entire runbook exists to protect

**SINGLE WRITER.** Exactly one process may ever hold the ESP32 ESPHome native-API
connection (`192.168.10.111:6053`) and own setpoint pushes. The
network-observable form of that invariant is the **count of ESTABLISHED TCP
connections to `192.168.10.111:6053`**, summed across the iris VM and every k3s
pod:

```
ESTAB(:6053) == 1     ← steady state, before AND after cutover
```

The cutover deliberately drives that count through a **stop-first, zero-gap**
transition — never a make-before-break overlap:

```
   1            iris VM verdify-ingestor.service holds the one connection  (BEFORE)
   │
   ▼  (Jason stops the VM unit; its aioesphomeapi socket releases)
   0            ATOMIC zero-writer window — proven empty before any k3s start  (PROVE)
   │
   ▼  (Jason flips the 3-part prod posture + scales k3s ingestor 0→1)
   1            exactly ONE k3s pod holds the connection, iris socket empty  (AFTER)
```

**The count must go 1 → 0 → 1. It must NEVER pass through 2.** A "2" at any
instant means two writers are double-pushing the live ESP32 — the exact
heap-thrash / setpoint-fight failure this whole posture (replicas:1+Recreate,
the device-write gate, the egress NetworkPolicy) was built to prevent.

This is why cutover is **break-before-make**, not make-before-break: we stop the
old writer and *prove the count is 0* before we let the new writer connect. A
brief zero-writer window is acceptable and bounded (the ESP32 owns relay safety
on-device during it); a two-writer window is never acceptable.

---

## 1. Roles & the single executor

| Owner | Does |
|---|---|
| **Jason** | executes **every** step in §3–§6 by hand. The atomic 3-part posture flip (§4) is HIS single action. Every stop/start/scale/flip is a Jason gate. |
| **laptop-root** | only if a cluster-side apply is needed to land the prod posture (e.g. `argocd app sync verdify-prod` after Jason approves) — prod is a **manual-sync** Application on purpose (no `syncPolicy.automated`), so nothing reconciles without an explicit operator sync. |

No agent executes any step. This runbook is the script Jason reads from.

---

## 2. Preconditions — ALL must hold before §3 begins

Cutover does not start until every box is checked. Several are themselves gated
deliverables; the runbook stays DRAFT until W012 + W013 land.

- [ ] **IRIS-W012 — setpoint coverage gate GREEN.** The k3s prod ingestor image
  has proven setpoint-dispatch parity coverage (every setpoint path the VM
  ingestor exercises is exercised by the pod build). Until W012 is green the pod
  is not trusted to be the writer.
- [ ] **IRIS-W013 — `:6053` ESTAB==1 monitor LIVE.** The device-route monitor of
  §7 / #89 is deployed and alerting on `ESTAB(:6053) != 1`. We do NOT perform an
  unobservable cutover: the monitor must be live so the 1→0→1 transition is
  watched in real time, and so a silent second writer would page.
- [ ] **DB parity G-DB-4 PASS.** `scripts/db-parity.sh` reports full parity
  (all 9 dimensions) between the iris VM TimescaleDB and the prod in-cluster
  `verdify-db`, including the final incremental catch-up. The k3s writer must
  write into a DB that already matches the VM, or telemetry/setpoint history
  forks. (DB write-ownership handoff is the Stage-5 quiescence in
  [`./k3s-cutover-sequence.md`](./k3s-cutover-sequence.md); it precedes this step
  and is re-verified here.)
- [ ] **Data-loss gates closed:** #102, #104, #105 all CLOSED. (Restore-recency,
  client-version skew G1, hypertable/compression repair — the prod DB must be a
  faithful, durable copy, not a schema-only placeholder.)
- [ ] **§3.4 device-VLAN reachability spike = PASS, re-run under live load.** A
  prod-namespace pod demonstrably reaches `192.168.10.111:6053`, HA
  `192.168.30.107:8123/1883`, local MQTT, and Frigate `192.168.30.142:5000/1984`
  inside the 5–10 s occupancy→light SLA. If the spike does NOT clear, the
  ingestor STAYS VM-side and this runbook is not executed.
- [ ] **ESP32 PSK reconciled at source** (canonical value in the VM ingestor
  runtime env, sha `127f85d0`) and synced to the prod `verdify-app-secrets`
  `ESP32_API_KEY`. **NEVER re-flash.** Sealing the PSK must never trigger an OTA.
  (Note: `ingestor.py:2224-2230` lets the `greenhouses` DB-row `esp32_host`/
  `esp32_port`/`esp32_api_key` OVERRIDE the env — confirm the prepared registry
  repoint, §4.1, carries the correct host/port/key.)
- [ ] **prod ingestor manifest pinned `replicas:0` for the cutover start.** The
  prod overlay inherits the base `replicas:1` (Recreate) and does NOT carry a
  `replicas:0` pin today. **Before cutover, the prod overlay must start the k3s
  ingestor at 0** so the manual ArgoCD sync brings up the rest of prod WITHOUT a
  device writer — then the 0→1 scale is the deliberate, gated act in §4. Either
  pin `replicas:0` in `overlays/prod` for the pre-cutover sync, or sync prod with
  the ingestor scaled to 0 and only patch to 1 at §4. Confirm the live
  `spec.replicas` is `0` at the start of §3. (Manifest base:
  `deploy/k8s/base/ingestor-deployment.yaml`; prod kustomization:
  `deploy/k8s/overlays/prod/kustomization.yaml`.)
- [ ] **No open `severity='critical'` alert.** (And no legacy `high` row.) Abort
  trigger — see §5.
- [ ] **Not inside a stress window.** If `outdoor_temp > 85°F` is forecast for the
  next 24 h, defer (operator-context warning, not a hard block by itself, but M5
  is a high-consequence handoff — prefer a calm window). See §5.
- [ ] **rollback path pre-walked.** Jason has confirmed the iris
  `verdify-ingestor.service` unit is present and startable (it carries an
  `ExecStartPre` pkill guard, `systemd/verdify-ingestor.service:11`, that
  guarantees a clean single aioesphomeapi connection on restart).

---

## 3. Step-by-step — phase 1: stop the VM writer, prove ZERO

> The count starts at **1** (the iris VM holds the connection). This phase drives
> it to **0** and proves the zero before anything k3s-side connects.

**3.1 Snapshot the BEFORE state (proves count == 1, VM-owned).** `[GATE: Jason]`
On the iris VM, observe the single ESTABLISHED `:6053` socket and record the
owning PID:

```sh
# iris VM (.150) — READ ONLY, opens no connection
ss -tnp | grep '192.168.10.111:6053'
# expect: exactly ONE ESTAB line, owned by the ingestor python (ingestor.py)
```

Confirm `ESTAB(:6053) == 1` and it is the VM ingestor. If it is already 0 or 2,
**STOP** (abort — §5: a zero-writer or pre-existing multi-writer state is not the
expected BEFORE).

**3.2 Stop the VM ingestor (the writer releases).** `[GATE: Jason]`

```sh
# iris VM (.150)
sudo systemctl stop verdify-ingestor.service
```

This releases the VM's one aioesphomeapi connection. (The unit's `ExecStartPre`
pkill guard means a later `start` re-establishes exactly one clean connection —
that is the rollback in §6.)

> **Note on the grow-light writer.** `verdify-setpoint-server.service` (`:8200`,
> grow lights via HA) is a SEPARATE device-affecting writer but it does NOT hold
> a `:6053` ESP32 native-API socket (it reaches HA `192.168.30.107:8123`). It is
> NOT part of the `:6053` ESTAB count. Its handoff is §4 step 6 (bring up the k3s
> `verdify-setpoint-server`); leave the VM `verdify-setpoint-server.service`
> running until then so grow-light control never gaps, and stop it at the same
> gated instant the k3s setpoint-server proves a green cycle.

**3.3 PROVE the zero-writer window (count == 0 everywhere).** `[GATE: Jason]`
The count must be 0 on BOTH sides before any k3s writer starts:

```sh
# iris VM (.150) — the VM socket must be GONE
ss -tnp | grep '192.168.10.111:6053' || echo "iris: ZERO :6053 ESTAB  ✓"

# k3s prod — the device-route monitor must read ZERO writers
KUBECONFIG=/home/jason/.kube/verdify-agent.config \
  scripts/k3s-smoke.sh device-monitor --namespace verdify-prod
# in this window the monitor SHOULD report ZERO writers (it exits non-zero on 0 —
# that non-zero is EXPECTED and correct here: zero is the intended transient).
```

Both must read **0**. If the iris socket has not released, wait/retry; if it will
not release, **STOP** and start the VM unit back (§6) — do not proceed into a
flip while the old writer is still connected (that path risks a 2).

---

## 4. Step-by-step — phase 2: the ATOMIC 3-part posture flip + scale 0→1

> This is the single M5 action. The **3-part posture** —
> (a) `VERDIFY_DEVICE_WRITE_ENABLED=1`, (b) prod ingestor `replicas 0→1`, and
> (c) the device-egress allow (`allow-ingestor-device-egress`, replacing the
> deny posture) — changes **together, as one atomic Jason action**. **No part may
> change earlier than this moment.** The runbook intentionally does not let
> DEVICE_WRITE, the replica count, or the egress policy flip during preconditions
> or phase 1; they are all here, in one gated block, executed only after §3.3
> proved the count is 0.

The prod overlay already declares the *target* shape of all three parts in git
(reviewable, inert until synced):

| Part | What changes | Where it lives (declared, NOT yet live) |
|---|---|---|
| (a) DEVICE_WRITE | `VERDIFY_DEVICE_WRITE_ENABLED: "1"` | `deploy/k8s/overlays/prod/device-write-configmap.yaml` (prod ONLY) |
| (b) replicas | ingestor `0 → 1` (Recreate, single-writer) | base `ingestor-deployment.yaml` (`replicas:1`); the pre-cutover `replicas:0` pin is removed/patched here |
| (c) egress | allow `:6053` + HA + Frigate; replaces deny | `deploy/k8s/overlays/prod/allow-ingestor-device-egress.yaml` (prod ONLY; the inverse `deny-esp32-egress` never co-applies) |

**4.1 Apply the prepared `greenhouses` registry repoint.** `[GATE: Jason]` Apply
the STAGED `greenhouses` DB-row repoint so the prod ingestor resolves the live
ESP32 (`esp32_host`/`esp32_port` = `192.168.10.111:6053`) and the correct
`esp32_api_key`. This is a prepared, reviewed SQL/registry change — applied now,
not improvised. (Recall `ingestor.py:2224-2230`: the DB row overrides the env
host/port/PSK; the repoint and the synced `ESP32_API_KEY` must agree.)

**4.2 Flip the 3-part posture + scale 0→1 (ONE atomic act).** `[GATE: Jason]`
With the count proven at 0 (§3.3), Jason lands all three parts together via the
prod manual sync. Concretely: ensure `device-write-configmap.yaml` (a) and
`allow-ingestor-device-egress.yaml` (c) are in the synced `overlays/prod`, remove
the pre-cutover `replicas:0` pin so the ingestor goes to `replicas:1` (b), then:

```sh
# laptop-root, AFTER Jason approves — prod is manual-sync by design
argocd app sync verdify-prod        # no automated selfHeal exists for prod
```

The base `strategy: Recreate` + `replicas:1` guarantees k3s brings up **exactly
one** ingestor pod (RollingUpdate is forbidden precisely so a second pod can
never connect mid-rollout). The pod opens the one aioesphomeapi connection from
the pinned greenhouse-VLAN-reachable node.

**4.3 PROVE exactly ONE writer (count == 1, k3s-owned).** `[GATE: Jason]`

```sh
# k3s prod — the device-route monitor must now read EXACTLY ONE writer
KUBECONFIG=/home/jason/.kube/verdify-agent.config \
  scripts/k3s-smoke.sh device-monitor --namespace verdify-prod
# expect: "EXACTLY ONE pod holds the ESP32 writer connection" → exit 0

# cross-check the iris socket is STILL empty (no resurrected VM writer)
# iris VM (.150):
ss -tnp | grep '192.168.10.111:6053' || echo "iris: still ZERO :6053 ESTAB  ✓"
```

Assert: monitor reports **exactly 1**, owned by a prod ingestor pod (k3s node
IP), AND the iris socket is empty. If the monitor reports **2+** at any read,
**ABORT IMMEDIATELY** (§5 + §6): there is a second writer. If it reports **0**
after the scale, the pod failed to connect — investigate before retrying; do not
restart the VM writer while a k3s pod might still come up (that is the 2-risk).

---

## 5. Abort criteria (any one ⇒ stop and roll back per §6)

Abort the moment ANY of these is observed — do not "fix forward" mid-cutover:

1. **ESTAB count doubles (`:6053` ESTAB ≥ 2)** at any observation — the
   single-writer invariant is breached; a second writer is live. Highest-severity
   abort.
2. **Zero-writer window does not close** — after §4.2 the monitor still reads 0
   writers (the k3s pod never connected) beyond a short bounded wait. The
   greenhouse is running open-loop on the ESP32 FSM; roll back to the VM writer
   rather than leave it writerless.
3. **The iris socket will not release** in §3.3 (old writer stuck connected) —
   never flip the posture on top of a live VM writer.
4. **An open `severity='critical'` alert** (or legacy `high` row) appears at any
   point — same freeze logic as firmware deploy; do not hand off the writer with
   a critical condition open.
5. **Stress window opens** (`outdoor_temp > 85°F` forecast for the next 24 h)
   before the flip — defer; M5 is high-consequence, run it in a calm window.
6. **DB parity regresses** (G-DB-4 no longer green) or a continuity gap appears in
   `max(ts) FROM climate` across the window — roll back so the VM keeps writing
   the authoritative DB.
7. **Setpoint dispatch does not confirm** within the audited baseline after the
   flip (see §6 green-cycle proof failing) — roll back; the writer moved but the
   loop is not healthy.

---

## 6. Rollback (reverse the count: k3s 1 → 0, iris 0 → 1) + green-cycle proof

**Rollback is always available and is the default response to any §5 abort.** It
restores the BEFORE state (the VM as the single writer) with a bounded zero-gap.

**6.1 Scale the k3s prod ingestor to 0** `[GATE: Jason]` (release the pod's
connection first — same break-before-make discipline):

```sh
# laptop-root / Jason — prod manual control
kubectl --kubeconfig /home/jason/.kube/verdify-agent.config \
  -n verdify-prod scale deploy/verdify-ingestor --replicas=0
```

Confirm the device-monitor reads **0** writers (k3s socket released).

**6.2 Restart the iris VM writer** `[GATE: Jason]`:

```sh
# iris VM (.150)
sudo systemctl start verdify-ingestor.service
```

The unit's `ExecStartPre` pkill guard ensures exactly one clean aioesphomeapi
connection. Confirm on the VM: `ss -tnp | grep '192.168.10.111:6053'` shows
exactly one ESTAB owned by the VM ingestor — count back to **1**, VM-owned.

> Rollback also reverts the 3-part posture: re-pin prod ingestor `replicas:0` (or
> leave it scaled 0), and the DEVICE_WRITE/egress posture is moot while the pod is
> down. The VM stack was never deleted; the iris TimescaleDB never stopped being
> writable. Copy-not-move means nothing was destroyed. Fully reversible.

**6.3 Green-cycle proof (the cutover is DONE only after this passes).** Once §4.3
shows exactly one k3s writer, prove the loop is healthy before declaring success:

- [ ] **Bring up the k3s `verdify-setpoint-server`** (grow-light writer, prod
  component) and stop the VM `verdify-setpoint-server.service` at the same gated
  instant so grow-light control hands over without a gap.
- [ ] **Two consecutive green control cycles.** Confirm-rate and band-change
  latency within the audited baseline (~95% confirm, p50 37 s / p95 81 s);
  occupancy→light path completes in the 5–10 s SLA. Two clean cycles in a row.
- [ ] **One content cycle.** A full planner→setpoint→confirm→telemetry round-trip
  lands in the prod DB (continuity probe `max(ts) FROM climate` advances with no
  gap across the handoff).
- [ ] **G10 post-deploy smoke GREEN** (`scripts/k3s-smoke.sh smoke
  --namespace verdify-prod` — api `/health/detailed` provenance + mcp surface +
  DB reachable) and the **device-route monitor steady at exactly 1**.
- [ ] **ESP32 still owns relay safety deterministically** (8-state FSM, 5 s loop)
  — confirm no controller logic moved cloud-side (migrate-as-is). Tempest UDP
  broadcast (L2-local, direct to the ESP32) confirmed unaffected — never relayed
  through the pod.

If any green-cycle check fails, roll back (§6.1–6.2) — the handoff is not done
until two green cycles + a content cycle pass with the monitor steady at 1.

---

## 7. Companion: the #89 device-route monitor + G10 smoke spec

This runbook DEPENDS on the monitor being live (precondition IRIS-W013). The
monitor and the post-deploy smoke are specified here and implemented by the
already-authored, validated, read-only `scripts/k3s-smoke.sh`
([`./k3s-smoke-postgreen.md`](./k3s-smoke-postgreen.md)). Wiring it into
alerting (Prometheus/Loki in the observability namespace) is the remaining #89
task.

### 7.1 Device-route monitor — `:6053` ESTAB==1 (the W013 / #89 guard)

**What it observes (network-observable single-writer invariant):** the count of
distinct prod-namespace pods holding an ESTABLISHED TCP connection to
`192.168.10.111:6053`. Implemented today as `scripts/k3s-smoke.sh
device-monitor --namespace verdify-prod` (read-only `ss`/`netstat` inside each
Running pod — inspects existing sockets, opens none).

**The three-state alert contract:**

| `ESTAB(:6053)` count | Meaning | Monitor verdict | Alert |
|---|---|---|---|
| **0** | NO writer — device loop down (or the intended transient zero-window in §3.3) | exit non-zero, "ZERO pods hold the ESP32 writer connection (no writer — device loop down?)" | **page** (`writer-down`) — EXCEPT suppressed during a declared M5 cutover window where 0 is the expected transient |
| **1** | exactly one writer — invariant HELD | exit 0, "EXACTLY ONE pod holds the ESP32 writer connection" | none (green) |
| **2+** | MULTI-WRITER — double-push / device-thrash | exit non-zero, "N pods hold the ESP32 writer connection — MULTI-WRITER, device-thrash risk" | **page immediately** (`multi-writer`) — highest severity |

The monitor MUST also account for the **summed** count across the iris VM and
k3s: during the steady state only ONE side should be non-zero, and their sum must
be 1. The k3s-side `device-monitor` covers the pod side; the iris-side check is
the `ss -tnp | grep :6053` of §3.1/§3.3 (and, post-decommission, the iris unit is
stopped so its side is structurally 0). For continuous alerting, wire BOTH:

- **k3s side:** an exporter/sidecar that runs `ss` where the app image lacks it,
  exporting `verdify_esp32_writers{ns="verdify-prod"}` as a gauge; alert on
  `!= 1`. (The app image is non-root, read-only-rootfs, may lack `ss` — the
  smoke script already reports "skipped, not a false pass" in that case; a
  sidecar with `ss` is the production-grade observation path.)
- **iris side (until decommission):** a blackbox/`ss` check that the VM holds 0
  once cutover is done; `> 0` post-cutover means a resurrected VM writer →
  combined with a k3s `1` that is a **2** → page.
- **Independent blackbox TCP probe to `:6053`** (reachability), separate from the
  ESTAB-owner count, so "device unreachable" and "wrong number of writers" alert
  distinctly.

### 7.2 G10 post-deploy smoke (the #89 smoke gate)

Run AFTER ArgoCD reports the prod instance green, as the post-deploy verifier
(`scripts/k3s-smoke.sh smoke --namespace verdify-prod`). It asserts:

1. **api `/health/detailed`** reachable and the baked `VERDIFY_GIT_SHA` matches
   the deployed image's `sha-<gitsha>` tag (image==source provenance; depends on
   #58 implementing `/health/detailed`).
2. **mcp** Deployment Ready and the FastMCP `/mcp` streamable-http surface
   responds to a `tools/list` JSON-RPC POST.
3. **DB reachable** (folded into [1]: `checks.db_reachable=true`).
4. **device-route monitor steady at exactly 1** (§7.1) — wired as a smoke
   sub-check on prod, the post-cutover steady-state assertion.
5. (staging variant only: ingestor `replicas==0` + ZERO device-VLAN writes — the
   device-dark interlock; prod is the inverse and asserts exactly-one instead.)

**Wire it as a cutover gate:** the green-cycle proof (§6.3) requires the G10
smoke GREEN before the handoff is declared done. The smoke is read-only and
idempotent — re-running it has no side effects and yields the same verdict for
the same cluster state.

---

## 8. What this runbook explicitly does NOT do

- No command auto-executes. Authoring/reviewing this doc touches nothing.
- No `systemctl stop/start`, no `kubectl scale`, no `argocd app sync`, no
  `ConfigMap`/replica/NetworkPolicy edit — every such command above is a Jason
  step, shown for him to run by hand at M5.
- No part of the 3-part posture (DEVICE_WRITE / replicas / egress) changes before
  the gated §4 moment. The preconditions and phase 1 are all read-only or
  VM-stop-only.
- No firmware flash/OTA. migrate-as-is: only which host dials `:6053` changes.
- No second writer is ever introduced — the count goes 1 → 0 (atomic, proven) → 1
  and never passes through 2.
- No data destroyed — copy-not-move; the iris TimescaleDB and VM stack remain the
  intact, instantly-restartable rollback target.
- DRAFT until IRIS-W012 + IRIS-W013 land; Jason executes at M5; #177/M7 work is
  DEFERRED (design-only) and out of scope here.
