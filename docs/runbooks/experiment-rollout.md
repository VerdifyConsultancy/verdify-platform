# Controlled planner experiment — rollout and rollback runbook

Scope: the GitOps side of the controlled policy experiment (#581 / #587).
Source of truth: audit §8.10 in
[`docs/research/planner-efficacy-current-firmware-2026-08-14.md`](../research/planner-efficacy-current-firmware-2026-08-14.md)
and the program plan `docs/plans/planner-experiment-program.md`. This runbook
captures the ORDER of operations; the audit defines the acceptance detail.

Flags (base defaults are the feature-OFF shape, `deploy/k8s/base/configmap.yaml`):

```text
VERDIFY_POLICY_VECTOR_MODE=off|shadow|live        (default off)
VERDIFY_ACTIVE_EXPERIMENT_ID=<explicit UUID>       (default empty)
VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED=0|1    (default 1)
```

Overlay reality check (§8.10): the production Argo application is historically
named `verdify-prod-dark`, but its **source is `deploy/k8s/overlays/prod`** —
that overlay is the ONLY study target. Never sync the actual
`overlays/prod-dark` device-dark shape as if it were production, and never set
an experiment mode there (prod-dark stays non-actuating).

"Deployed" means the exact revision is ArgoCD **Synced + Healthy**, the
expected image digests and config revisions are running, and the baseline /
vector hash is confirmed on-device — not merely "manifest present".

## Config-revision bump procedure (do this on EVERY verdify-config edit)

`verdify-config` is consumed via `envFrom` by api / mcp / ingestor /
migration-job / planner / setpoint-server / lab-publisher / ha-gap-backfill,
so a ConfigMap edit alone does **not** restart any pod. The GitOps-owned
rollout trigger is the `verdify.io/config-revision` pod-template annotation on
verdify-api, verdify-mcp, verdify-ingestor, verdify-planner, and
verdify-setpoint-server, maintained by `scripts/gen-config-revision.sh`:

1. Edit the ConfigMap source (base `configmap.yaml` and/or an overlay
   `verdify-config` patch such as `device-write-configmap.yaml`).
2. Run `scripts/gen-config-revision.sh` (no args). It hashes the base
   ConfigMap plus every overlay `verdify-config` patch and rewrites the five
   annotations in place. Commit the flag change and the annotation bump
   together.
3. CI (`tests/test_21_config_revision.py`, also run by `scripts/ci-local.sh`)
   recomputes the hash and FAILS the build if an edit shipped without the
   bump.
4. After the gated Argo sync, verify every env-consuming pod actually
   restarted onto the new revision:
   `kubectl get pods -o jsonpath='{range .items[*]}{.metadata.name} {.metadata.annotations.verdify\.io/config-revision}{"\n"}{end}'`
   and compare with `scripts/gen-config-revision.sh --print`.

Note the deliberately conservative blast radius: the revision is ONE hash over
base + all overlay config patches, so a config edit for either overlay rolls
the consumers of both at their next sync. Restarts are sync-gated (ArgoCD is
manual-sync for this app), and the ingestor restart remains the gated
single-writer Recreate it always was.

## Rollout order (§8.10 steps 1–9)

1. **Foundations, everything OFF.** Verify a restorable database snapshot; run
   the ledgered migration-207 PreSync path; pass post-schema assertions; roll
   contracts, APIs, the frozen outcome view, dashboards, digest-pinned images,
   and flags **off**; verify every config consumer restarted onto the intended
   config revision.
2. **Shadow.** `VERDIFY_POLICY_VECTOR_MODE=shadow` (prod overlay patch +
   revision bump): proposal / arbiter / outbox run and persist; **no device
   actuation**.
3. **Firmware artifacts.** Build the staged-vector OTA image AND a separate
   recovery image through
   `deploy/k8s/components/firmware-builder/firmware-builder.yaml`. The
   recovery image must be the prior proven control logic **plus** the exact
   immutable baseline/vector schema compiled in (the old binary alone boots
   defaults — many legacy globals are not restored). Pin source, toolchain,
   binary, baseline, and schema hashes; test both images before OTA.
4. **Live twin.** Build `twin/Dockerfile` in-cluster (twin-builder →
   zot origin), digest-pin, deploy the completed live twin
   (`components/firmware-twin`), and collect 7–14 days of shadow action/hash
   agreement.
5. **Qualification.** Create and lock the non-efficacy `qualification` UUID
   (hashed pretrial spec, three canonical templates, six content-changing
   edges, 24 FIFO cell queues, four slots per cell). Commit `mode=live`, that
   exact ID, legacy writes `0` through GitOps; sync, restart, verify every
   config/image/audience hash; stage and echo the qualification manifest; arm
   the DB record; run the §8.3 protocol. On completion: confirm baseline via
   the ledgered path, close exposure, complete the UUID, declaratively return
   to `mode=shadow` + empty ID, verify the reset rollout.
6. **A/A.** Separate non-efficacy `aa` UUID (seven fixed local-day
   assignments, both lanes resolving to exact baseline content). Commit that
   ID in `mode=live`; sync/restart/verify; stage its baseline-only manifest;
   arm; run the seven-day A/A gate. Then confirm baseline, complete, return
   declaratively to shadow/empty ID, verify reset.
7. **Randomized arm-up.** Freeze the randomized protocol + new UUID; bind the
   passing qualification and A/A result hashes; name the future beacon round;
   draw and commit the witnessed mapping secret BEFORE that beacon publishes;
   generate the schedule. Commit `mode=live`, the randomized ID, legacy
   writes `0`, frozen Hermes/context hashes; sync/restart/verify; stage and
   echo the randomized manifest; arm the DB record; pre-stage day 1.
8. **Blinded run.** 30 days blinded, no efficacy peeking; then freeze exports
   and endpoint/fidelity/deviation tables BEFORE revealing the arm mapping.
9. **Completion.** Confirm baseline on-device; complete the experiment; then
   declaratively set mode off / empty ID; restore legacy writes only after the
   confirmed baseline; sync; restart; verify the final state.

## Immediate rollback (ORDER MATTERS — device truth before GitOps truth)

Do these strictly in sequence; the GitOps flip is deliberately LAST among the
software steps because flipping mode off first would strand an unconfirmed
vector on the device with no owner:

1. **Pause admission** — stop the arbiter admitting new proposals/assignments
   (experiment pause transition; no new outbox work).
2. **Baseline via the outbox** — enqueue the frozen baseline vector through
   the SAME durable outbox/staged-commit path as any other vector. Never
   hand-push, never bypass the delivery ledger.
3. **Confirm the device hash** — wait for the device to echo the exact
   baseline generation/activation hash (readback), proving the baseline is
   what is actually running.
4. **Close exposure** — close the open exposure interval as `fallback` so the
   analysis window has an honest boundary.
5. **THEN GitOps mode off** — set `VERDIFY_POLICY_VECTOR_MODE=off` and clear
   `VERDIFY_ACTIVE_EXPERIMENT_ID` in the prod overlay patch.
6. **Restore legacy writes** — `VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED=1`
   (only now — after the confirmed baseline).
7. **Bump + sync** — run `scripts/gen-config-revision.sh`, commit, ArgoCD
   sync.
8. **Verify restarts** — confirm every env-consuming pod restarted onto the
   rollback config revision (procedure above) and reports mode off / empty ID.

Independent paths when software rollback is unavailable (§8.10): with no
network, firmware expiry to the immutable ROM baseline is the independent
rollback; if the new firmware itself is bad, flash the **verified recovery
image** — not the unmodified legacy binary.

## Never `--prune`

Never run `argocd app sync --prune` (or enable automated pruning) on the
verdify app during any experiment phase. The app is deliberately
`prune:false`: a removed workload ORPHANS rather than deletes, and a prune
could tear down the single-writer ingestor, the DB, or delivery machinery
mid-experiment. Removal of a resource is its own reviewed change followed by
an explicit, targeted deletion — never a sync-time side effect.
