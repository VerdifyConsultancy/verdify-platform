# Outer-loop handoff checkpoint — 2026-07-24 (node7 maintenance)

Written at a safe boundary. No in-flight mutations; last actions were read-only
probes. Resume by re-reading this, `docs/plans/lab-astro-migration.md`, and
`COORDINATION_REQUESTS.md` on main.

## Role
Claude = outer planning/execution loop. codex (tmux `agent-verdifyconsulta-f6ee`,
gpt-5.6 xhigh) = primary executor, currently **idle-but-alive** (verified: ran a
fresh audit, concluded nothing autonomously actionable). Background Claude agents
handle work codex's policy layer refuses.

## Single blocking gate (everything parks here)
**storage-infra#53 / jvallery/storage-infra#53.** Create private Garage bucket
`verdify-lab-occurrences` + distinct bucket-scoped reader/writer identities
(anon denied, reader-write denied, prefix-isolated); deliver to contracted Secret
NAMES/keys in ns `verdify-platform` out of band (values never in git/chat/logs).
Plus #534 reporting-tier read-only credential (distinct from Track A roles).

**Gate probe:** `kubectl get secrets -n verdify-platform | grep -cv NAME`.
- Current = **4** (`agent-ssh`, `ci-dbg-git`, `sops-age`, `zot-origin-cluster-pull`) → gate CLOSED.
- When count **> 4** → creds landed → drive codex's approved 2-pass activation
  (Pass 1: reporting+producer+two-pod runtime, unrouted → 143+2/both-cache
  convergence → Pass 2 route switch → 24h/96-sample freshness soak). Then update
  tracker + #351 as gates flip.

## Live state (verified 2026-07-24)
- `verdify-lab-astro-stage`: 2/2, https://lab-stage.verdify.ai → 200.
- `verdify-lab-release-runtime`: **0/0 replicas**, unrouted.
- 143 graphs + 2 cameras: **zero materialized occurrence files** (symptom of the
  missing bucket/creds).
- PR #550 (agent/nginx/exporter pins) merged but INERT (runtime at 0). No open PRs.
- Live & healthy: Astro stage build + scroll fix; migrations 203–206; Grafana
  sweep through c3caad1; legacy Quartz publisher still rebuilding every 10 min.

## Jason-gated queue (in COORDINATION_REQUESTS.md — do NOT act, escalate only)
1. **storage-infra#53** — see above (unblocks entire lab lane).
2. **Gated prod release (one attended window):** gated `argocd app sync
   verdify-prod-dark` (rolls verdify-mcp/api/ingestor/setpoint-server/vision pins)
   + apply migrations **201 then 202** (rollback-safety tooling) + bounce
   `verdify-mcp`. Unlocks ~62-91x scorecard speedup + fixes outcome_kpi cycle axis.
3. **Wedged vision job (quickest win):** `kubectl delete job
   verdify-vision-29730060 -n verdify-prod` — vision frozen since 2026-07-11
   (Forbid concurrency silently skipping every tick).
4. Infra P0s (storage-infra/Jason): iSCSI target-count exhaustion; DB PITR gap.

## Safety constraints (persist)
Never print/commit/log secret VALUES (names/paths only). No prod ArgoCD sync,
DNS/edge, Quartz retirement, device/VLAN/OTA, prod-destructive DB, or credential
provisioning without Jason's explicit go-ahead — queue in COORDINATION_REQUESTS.md
+ PushNotification. No destructive git. Never wrap self-committing migrations in
an outer txn. Do NOT push/reset the pod's local main. `rg` unreliable here — use grep.

## Next cheap-probe trio on resume
```
tmux capture-pane -t agent-verdifyconsulta-f6ee -p -S -6 | grep -E "Working|Goal" | tail -1
kubectl get secrets -n verdify-platform 2>/dev/null | grep -cv NAME   # >4 = act
curl -sS -o /dev/null -w "stage %{http_code}\n" --max-time 10 https://lab-stage.verdify.ai
```
Act only on: secret count > 4, a new open PR, codex leaving idle / policy refusal,
or a failing stage/prod probe. Otherwise steady state.
