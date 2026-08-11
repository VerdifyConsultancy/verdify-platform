# STAGED — HA-3 single-writer Lease fence + fast-failover: GATED live-arm runbook

**Status:** STAGED. **Gate:** root executor. **NEVER run unattended.**
**Tracks:** #239 (fast-failover) · #240 (Lease fence) · #241 (split-brain alarm — already LIVE).
**Design:** `~/Agents/root/docs/verdify-ha-architecture-2026-06-07.md` §2.3 / §3 / §5.

This runbook arms, on the **live prod device-writer**, the exactly-one Lease fence
and the fast unpinned failover that are merged but **INERT** until this procedure.
It restarts the sole live ESP32 writer, so it runs ONLY inside a preflight-validated
maintenance window with the single-writer interlock preserved end-to-end.

---

## 0. Pre-flight (all must be GREEN before any change)

```bash
K="ssh jason@192.168.30.32 sudo k3s kubectl"
# a) Exactly one writer right now (the oracle):
$K -n observability exec deploy/prometheus -c prometheus -- \
  wget -qO- 'http://localhost:9090/api/v1/query?query=sum(verdify_esp32_writer_estab)'
#    EXPECT: result == "1"
# b) Split-brain alarm is LIVE (#241) and inactive:
$K -n observability exec deploy/prometheus -c prometheus -- \
  wget -qO- 'http://localhost:9090/api/v1/rules' | grep -o 'VerdifyESP32SplitBrain'
#    EXPECT: present;  alert state inactive
# c) The PriorityClass exists (created by ha-1):
$K get priorityclass verdify-device-writer
# d) The fresh DB dump is < 26h (RPO floor) — VerdifyDBBackupStale NOT firing.
# e) Live ingestor healthy, NOT on cordoned node7:
$K -n verdify-prod get pod -l app.kubernetes.io/component=ingestor -o wide
```

A split-brain (`sum>=2`) or a NoWriter (`sum==0`) condition at pre-flight is a
**STOP** — resolve before arming.

---

## Step A — Land the fence image + RBAC (additive, NO live-writer restart)

The lease code ships in the `verdify-ingestor` image and is INERT
(`VERDIFY_WRITER_LEASE_ENABLED=0`). The SA + Role/RoleBinding + PDB
(`ingestor-rbac.yaml`, `ingestor-pdb.yaml`) and the `POD_NAME/POD_NAMESPACE`
downward-API env are additive — applying them does **not** restart the running
pod (env/SA changes on the PodSpec DO trigger a Recreate, so stage this as part
of the SAME window as Step C, or accept one controlled restart here).

```bash
# Verify the SA can manage ONLY the one lease (least-privilege):
$K -n verdify-prod auth can-i update leases/verdify-ingestor-writer \
  --as=system:serviceaccount:verdify-prod:verdify-ingestor      # EXPECT yes
$K -n verdify-prod auth can-i update leases/anything-else \
  --as=system:serviceaccount:verdify-prod:verdify-ingestor      # EXPECT no
```

ArgoCD syncs the merged base → the SA/Role/RoleBinding/PDB + downward-API env
land. This is the reviewable target shape; the flag stays `"0"` so behaviour is
unchanged (always-held no-op).

## Step B — Confirm INERT on the live writer

```bash
$K -n verdify-prod get deploy verdify-ingestor -o yaml | grep -A2 WRITER_LEASE
#   EXPECT VERDIFY_WRITER_LEASE_ENABLED: "0"
$K -n verdify-prod logs deploy/verdify-ingestor | grep writer_lease
#   EXPECT: enabled=False  (fence inert; is_held()→True; pre-fence behaviour)
$K -n verdify-prod get lease verdify-ingestor-writer 2>&1
#   EXPECT: NotFound (no lease created while disabled)
```

## Step C — ARM the fence (the gated flip) — verify the exact target and prerequisites

Flip the flag in the prod overlay (NOT base):

```yaml
# overlays/prod/device-write-configmap.yaml  (or a sibling writer-fence patch)
data:
  VERDIFY_WRITER_LEASE_ENABLED: "1"
```

Commit → ArgoCD syncs → Recreate rollout (old pod fully down before new starts —
the single-writer invariant during the rollout). Watch the oracle THROUGHOUT:

```bash
# In a separate shell, 2s oracle watch — must NEVER read >=2 during the flip:
while true; do $K -n observability exec deploy/prometheus -c prometheus -- \
  wget -qO- 'http://localhost:9090/api/v1/query?query=sum(verdify_esp32_writer_estab)' \
  | grep -o '"[01]"'; sleep 2; done
```

Post-arm checks:
```bash
$K -n verdify-prod logs deploy/verdify-ingestor | grep writer_lease
#   EXPECT: enabled=True can_fence=True ; "ACQUIRED verdify-ingestor-writer as <pod>"
$K -n verdify-prod get lease verdify-ingestor-writer \
  -o jsonpath='{.spec.holderIdentity} {.spec.leaseDurationSeconds}'
#   EXPECT: <the live ingestor pod name> 15
$K -n observability exec deploy/prometheus -c prometheus -- \
  wget -qO- 'http://localhost:9090/api/v1/query?query=sum(verdify_esp32_writer_estab)'
#   EXPECT: "1"  (the armed writer reconnected and holds the lease)
```

## Step D — Apply the fast-failover podSpec patch (#239) — pairs with the fence

ONLY after Step C is green. Append the staged patch to the prod kustomization:

```yaml
# overlays/prod/kustomization.yaml  → patches:
  - path: STAGED-ha3-fast-failover-patch.yaml
```

This adds `priorityClassName: verdify-device-writer`, 20s not-ready/unreachable
tolerations, `cpu:500m` request (no cpu limit), `terminationGracePeriodSeconds:15`.
Commit → ArgoCD Recreate rollout (one more controlled writer restart). Watch the
same 2s oracle; confirm post-rollout `sum==1` and the lease holder is the new pod.

## Step E — Chaos validation (gated window; design §5 Tests A–D)

With the fence armed, in the maintenance window only:
- **Test C (graceful):** `kubectl -n verdify-prod rollout restart deploy/verdify-ingestor`
  → SIGTERM releases the lease → new pod acquires in **seconds** (proven in
  dev: 2.5s) → oracle `1→0→1`, **never 2**.
- **Test A (hard node loss):** `qm stop` the writer's Proxmox VM → new pod
  reschedules (20s toleration) + reacquires lease after ≤15s expiry → `sum→1`
  within ≤60s (≤5min ceiling), **never ≥2**.
- **Test B (partition — the important one):** partition the writer's node from
  the API server but keep its device-VLAN path → the OLD pod **self-fences**
  (drops the ESP32 ESTAB within 15s of lost renewal, logged "SELF-FENCING")
  *before* the replacement acquires the lease and connects → oracle **never 2**.
- **Test D (node pressure):** stress the node → the writer (priority + cpu
  request) is never the pod preempted/evicted; lower-priority pods go first.

**A1 (never-two) is the hard life-safety gate.** Pass = the oracle (and the
VerdifyESP32SplitBrain alarm) NEVER report `>=2` in any test, re-probed ≥60 min.

## Rollback (instant, at any step)

```bash
# Disarm the fence — flip back to inert (Recreate rollout, single-writer held):
#   overlays/prod ... VERDIFY_WRITER_LEASE_ENABLED: "0"   → commit → sync
# Remove the fast-failover patch line from the kustomization → commit → sync.
# The image is unchanged; disabling the flag returns is_held()→True (pre-fence).
$K -n verdify-prod delete lease verdify-ingestor-writer   # clear the stale lease
```

The fence and the failover patch are independently reversible; neither touches
firmware, the DB, or the device. The split-brain alarm (#241) stays live
throughout as the out-of-band backstop.
