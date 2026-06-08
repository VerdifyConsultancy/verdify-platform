# Prod-promotion runbook — gated commit → stage → prod (#220)

**Owner:** laptop-root · **Tracker:** VerdifyConsultancy/verdify-platform#220 ·
**Status:** AUTHORED 2026-06-07. The CI/CD lower half (build → publish → staging
auto-deploy → guards) is LIVE on `live/platform-main`; the one-click prod-promote
entry point (`.github/workflows/prod-promote.yml`) lands with this runbook.

This is the durable "how an image gets from a merge to live prod" record. It exists
so Jason **stops hand-editing `overlays/prod` image digests** — promotion is now a
button plus a gated PR, with the live-device-write step still behind the manual
ArgoCD sync and the human gate.

---

## 0. The invariant this whole flow protects

There is **exactly ONE** live ESP32 writer (the `verdify-prod` ingestor on
`vm-k3s-node5`/`192.168.30.36`, the device-write path). Nothing in this pipeline
may stand up a second writer or restart the live writer without an explicit Jason
gate. Every automated step below is **device-dark** (staging/dev are
`ingestor replicas:0` + `VERDIFY_DEVICE_WRITE_ENABLED=0` + `deny-esp32-egress`);
only the final, manual `argocd app sync verdify-prod` touches the writer, and only
after the device-VLAN sign-off.

---

## 1. The env/gate flow (what runs where)

```
 merge to live/platform-main
        │
        ▼
 ┌─────────────────────────── verdify-platform CI (GitHub-hosted) ───────────────────────────┐
 │ ci.yml                 lint · site-guards · schemas+drift · firmware · firmware-logic ·    │
 │  (the gate ledger)     replay-diff · no-fire-and-forget · service-restart-drift  (≈12 gates)│
 │ container-publish.yml  image-impact → build+publish DIGEST-PINNED images to GHCR           │
 │                        → bump-staging-digests: IN-REPO write-back of overlays/staging       │
 │                          (+dev) @sha256 pins  [skip ci]  (LOCKED: no cross-repo PR,         │
 │                          no ArgoCD Image Updater)                                            │
 │ k8s-manifests.yml      kustomize build + kubeconform every overlay                          │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
        │  (staging overlay digests advanced in git)
        ▼
 ArgoCD  verdify-local-staging   automated{selfHeal:true, prune:false}  → AUTO-SYNCS staging
        │
        ▼
 ┌── OPERATOR GATE G10 (post-green smoke, device-dark) ──────────────────────────────────────┐
 │ scripts/k3s-smoke.sh smoke   (READ-ONLY; api /health/detailed, mcp tools/list, db,         │
 │                               ingestor replicas==0, ZERO device-VLAN sockets)               │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
        │  staging is GREEN + smoke-proven on the new digests
        ▼
 ┌── ONE-CLICK PROMOTE  (.github/workflows/prod-promote.yml, workflow_dispatch) ──────────────┐
 │ 1. read CURRENT staging digests (the proven-on-staging set)                                 │
 │ 2. compute delta = staging-tracked prod pins that LAG staging (api,mcp,ingestor,migrate,www)│
 │ 3. surgical digest bump of overlays/prod/kustomization.yaml (comment-preserving)            │
 │ 4. DEVICE-WRITE-SAFETY-GATE: render-equality — non-image render byte-identical;             │
 │    VERDIFY_DEVICE_WRITE_ENABLED + allow-ingestor-device-egress intact; all @sha256          │
 │ 5. open a `prod-promote`-labelled PR (mode=pull-request) — or just print (mode=dry-run)     │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌── REQUIRED CHECK on the PR ───────────────────────────────────────────────────────────────┐
 │ promote-diff-guard.yml   change-surface containment (prod overlay = digests/comments/       │
 │                          component-refs only) + STAGING-EQUALITY (a changed prod pin must    │
 │                          land on EXACTLY the current staging digest — no fabricated digest)  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
        │  human reviews + merges the PR  (= the "one approval")
        ▼
 ArgoCD  verdify-prod   MANUAL-SYNC (no automated block)  → prod sits OutOfSync on new digests
        │
        ▼
 ┌── JASON GATE (device-write) ──────────────────────────────────────────────────────────────┐
 │ device-VLAN reachability/latency sign-off + single-writer cutover approval, THEN:           │
 │   argocd app sync verdify-prod        (operator-initiated, the ONLY step that touches the   │
 │                                        live writer)                                          │
 │   scripts/k3s-smoke.sh device-monitor --namespace verdify-prod   (EXACTLY-ONE-writer guard) │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

**The "one-click / one-approval" of #220** = step "ONE-CLICK PROMOTE" (the button)
+ the human merge of the generated PR (the approval). The live-device sync stays a
**separate, explicit Jason gate** below it.

---

## 2. ArgoCD app wiring (the env tiers)

| App | Path | syncPolicy | Role |
|---|---|---|---|
| `verdify-dev` | `overlays/dev` | `automated{selfHeal,prune}` (intended) | device-dark telemetry SUBSCRIBER; auto. |
| `verdify-local-staging` | `overlays/staging` | `automated{selfHeal:true, prune:false}` | device-dark; **auto-syncs the digest write-back**. |
| `verdify-prod` | `overlays/prod` | **manual** (no `automated`) | the ONE writer; `prune:false`; operator-synced behind the Jason gate. |
| `verdify-prod-dark` | `overlays/prod-dark` | **manual** | device-DARK prod adoption (ingestor replicas:0); M4 proof. |

> Drift note (2026-06-07): the live `verdify-dev` Application is currently
> **manual-sync** (no `automated` block) even though the in-repo CR declares
> `automated{selfHeal,prune}`. Re-apply the CR to converge if dev should
> auto-sync. This does not affect the prod-promote flow.

The staging app already auto-deploys on the in-repo digest bump (verified
`automated{selfHeal:true}` syncing `overlays/staging` from `live/platform-main`).
No manual ArgoCD sync is needed to ADVANCE staging — satisfying #220's "no manual
ArgoCD sync to advance an env" for the dev/stage tier. Prod advancing is the gated
PR; prod SYNCING is the device gate.

---

## 3. Operator procedure

### 3.1 Promote (the button)

1. Confirm staging is green on the digests you want in prod:
   ```sh
   ssh jason@192.168.30.32 'sudo k3s kubectl -n argocd get application verdify-local-staging \
     -o custom-columns=SYNC:.status.sync.status,HEALTH:.status.health.status'
   # want: Synced / Healthy
   ```
2. Run the **post-green staging smoke** (device-dark gate G10):
   ```sh
   KUBECONFIG=/home/jason/.kube/verdify-agent.config scripts/k3s-smoke.sh smoke
   # exit 0 = GREEN; asserts ingestor replicas==0 + ZERO device-VLAN sockets in staging
   ```
3. **Dry-run** the promotion to see the delta + gate verdicts (opens no PR):
   - GitHub → Actions → **Prod Promote** → Run workflow → `mode=dry-run`.
   - Read the job summary: the staging→prod digest table + `Device-Write-Safety-Gate OK`.
4. **Open the PR**: re-run **Prod Promote** with `mode=pull-request`
   (optionally `images=api,mcp` to promote a subset). It opens a
   `prod-promote`-labelled PR that advances `overlays/prod` digests to the current
   staging digests, comment-preserving.

### 3.2 Approve (the merge)

5. On the PR, confirm the **required check** `Promote Diff Guard /
   promote-diff-guard` is green (change-surface + staging-equality), review the
   digest table, and **merge**. This is the human approval; it is a git change
   only — the cluster is untouched.

### 3.3 Sync prod (the Jason device gate — NOT automatic)

6. After merge, prod is **OutOfSync** on the new digests:
   ```sh
   ssh jason@192.168.30.32 'sudo k3s kubectl -n argocd get application verdify-prod \
     -o custom-columns=SYNC:.status.sync.status,HEALTH:.status.health.status'
   # expect: OutOfSync / Healthy  — this is correct; do NOT auto-sync.
   ```
7. **Only after** the device-VLAN reachability/latency sign-off + single-writer
   cutover are explicitly approved by Jason, an operator runs:
   ```sh
   argocd app sync verdify-prod          # operator-initiated; the ONLY device-touching step
   # NEVER pass --prune (verdify-db StatefulSet + iSCSI PVC must never be reaped)
   ```
8. Post-sync, prove the **single-writer invariant** holds:
   ```sh
   KUBECONFIG=/home/jason/.kube/verdify-agent.config \
     scripts/k3s-smoke.sh device-monitor --namespace verdify-prod
   # asserts EXACTLY ONE pod holds the ESP32 native-API connection (192.168.10.111:6053)
   ```

---

## 4. The gates, enumerated (#220 acceptance)

| Gate | Where | What it proves |
|---|---|---|
| ci.yml ledger (~12) | CI on every PR/push | lint, schemas+drift, firmware logic/replay-diff, fire-and-forget, restart hygiene |
| k8s-manifests | CI | every overlay renders + kubeconform-valid |
| container-publish smoke-import | CI, pre-push | the built image imports `verdify_schemas.alerts` before it can be pushed |
| **Device-Write-Safety-Gate (static)** | **prod-promote.yml** | promotion is digests-only; non-image render byte-identical; interlock + egress-allow intact; all @sha256 |
| promote-diff-guard | **required check on the PR** | change-surface containment + a changed prod pin lands on EXACTLY the current staging digest |
| **smoke (device-dark)** | **operator, post-staging-green (G10)** | api/mcp/db serve; staging ingestor replicas==0; ZERO device-VLAN sockets |
| **device-monitor (single-writer)** | **operator, post-prod-sync** | EXACTLY ONE pod holds the ESP32 writer connection |

CI runners are GitHub-hosted and cannot reach the cluster, so the two
`k3s-smoke.sh` gates are **operator-run** (read-only; never sync/scale/patch). The
static Device-Write-Safety-Gate that CAN run hosted (render-equality on the
promotion) runs in `prod-promote.yml` and is re-asserted by `promote-diff-guard`.

---

## 5. Safety properties (adversarially checked)

- **No second writer, ever.** The promote workflow only edits git; staging/dev are
  device-dark; prod stays manual-sync. The live writer changes only at step 3.3
  step 7, behind the Jason gate.
- **No fabricated digest.** A prod pin can only advance to a digest staging is
  *currently running* (promote-diff-guard staging-equality, render-derived).
- **No posture drift.** The Device-Write-Safety-Gate render-equality fails the PR
  if the bump changed ANY non-image manifest (NetworkPolicy, the
  `VERDIFY_DEVICE_WRITE_ENABLED` interlock, replicas, patches).
- **No comment/format churn.** The surgical digest edit changes only `digest:`
  lines, so the rationale comments survive and `promote-diff-guard`'s
  change-surface check passes (a `kustomize edit set image` would reflow the file
  and trip it — that is why this workflow does NOT use it).
- **Prod-only images are never promoted.** planner/setpoint-server/lab/mqtt/hermes
  are absent from the staging render, so the derived staging-tracked set excludes
  them; their prod pins advance only via a separate human-gated write-back.
- **No DB reaping.** `prune:false` on prod + the runbook ban on `--prune`.

---

## 6. Files

- `.github/workflows/prod-promote.yml` — the one-click promotion entry point (this PR).
- `.github/workflows/promote-diff-guard.yml` — the required PR gate (already live).
- `.github/workflows/container-publish.yml` — build/publish + staging digest write-back (already live).
- `.github/workflows/k8s-manifests.yml` — overlay render/validate (already live).
- `scripts/k3s-smoke.sh` — the operator smoke + single-writer monitor (already in repo).
- `deploy/k8s/argocd/apps/verdify-{dev,prod,prod-dark}.yaml` — the ArgoCD Application CRs.
- `docs/runbooks/k3s-smoke-postgreen.md` — the deeper smoke-script reference.
