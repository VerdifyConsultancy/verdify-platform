# Descheduler ownership and attended adoption (#802)

The source runs in enforce mode, matching the live CronJob since #234 GATE 6
(2026-06-08). It runs hourly at `15 * * * *`, with exactly these arguments:

```text
--policy-config-file=/policy-dir/policy.yaml
--descheduling-interval=0
--v=4
```

This adoption changes ownership only. It preserves the pinned image, policy,
RBAC, resource limits, cadence and pod-template `evict: "true"` annotation.
The policy restricts candidates to labelled pods in `verdify-prod`; existing
PDBs, priority exclusions and the per-node eviction limit remain in force.
Do not relabel product workloads or force an eviction to prove adoption.

## Source and ownership boundary

`VerdifyConsultancy/verdify-platform/deploy/k8s/descheduler` renders seven objects:

| Kind | Name | Namespace |
| --- | --- | --- |
| CronJob | descheduler | verdify-descheduler |
| ConfigMap | descheduler-policy | verdify-descheduler |
| ServiceAccount | descheduler | verdify-descheduler |
| Role | descheduler | verdify-descheduler |
| RoleBinding | descheduler | verdify-descheduler |
| ClusterRole | verdify-descheduler | cluster |
| ClusterRoleBinding | verdify-descheduler | cluster |

`jvallery/agents` declares the `verdify-descheduler` AppProject in
`platform/gitops/projects/` and manual Application in
`platform/gitops/applications/local-prod/`. The Application uses an accepted
immutable product revision, has no resource-deletion finalizer, and cannot create
the namespace. Its AppProject allows only the seven resource kinds above.
`Namespace`, Secret, Job, Pod and Deployment are outside that allowlist.

The existing namespace and its external labels are not adopted. Neither
`namespace.yaml` nor `STAGED-rebalanceable-labels-patch.yaml` is rendered; both are
historical material, not apply instructions. The previous dry-run/relabel/arm
procedure is preserved in Git history and #234.

The live `spec.suspend` flag remains an operator pause. Git deliberately omits
it; the Application ignores that exact CronJob field and uses
`RespectIgnoreDifferences=true` so a manual reconciliation preserves the pause.
Keep automation and pruning off. Normal operation must not use `kubectl apply -k`
or the old staged patches as a second desired-state writer.

## Attended adoption

1. Confirm context `vallery`, accepted product and Agents commits, and current
   ownership. Export the seven full objects and the Namespace separately; retain
   UIDs, server-defaulted specs, labels and tracking annotations before applying
   anything. Read the CronJob's suspend flag and active Jobs. Confirm the exact
   Application and AppProject are absent or still match the reviewed plan.
2. Render this source at the accepted product commit. Require seven objects and
   no Namespace. Compare every source leaf, including array members, with the
   export; review API defaults separately. The final server-defaulted comparison
   must show no spec, policy, RBAC or namespace-label change. Stop for any drift
   beyond the intended Argo ownership metadata.
3. Deliver only the reviewed Agents AppProject and Application through their
   owning catalogs. The catalog roots are independently pinned and automated;
   advancing either root requires review of its entire pending delta. Do not
   advance an unrelated root or reconcile the whole estate for this adoption.
   Bind the Application to the accepted product commit before its first sync.
4. Diff the exact `verdify-descheduler` Application and sync that revision with
   pruning disabled. The resource set must be exactly the seven objects above.
   Do not use replace/force or delete/recreate. No new Job is part of the sync.
5. Read back all seven UIDs, specs and ownership metadata, plus Namespace labels.
   Require unchanged UIDs and specs, unchanged namespace labels, the expected
   tracking owner and `Synced`/`Healthy` at the accepted product revision. Confirm
   the live suspend value is unchanged. Check namespace access: the descheduler
   identity owns its existing scoped rules; unrelated observers must remain
   read-only and retired coding-pod writers absent.
6. Observe the next natural hourly Job to terminal completion. Record its
   CronJob owner UID, pod image/arguments, timestamps and logs. Check actual
   eviction outcomes and exclusion-policy behavior; an empty candidate set is
   valid but does not prove a forced relocation. Verify the product's single
   ingestor connection remains one and no singleton is evicted. Re-read the
   seven objects and Application after that run for reconciliation durability.
7. Refresh the Agents live ring snapshot and assign the now-existing Application
   to its manual ring using actual evidence. Do not put a prospective Application
   into the live snapshot. Update #802 only with the observed acceptance results.

## Pause and inverse

For an operational pause, set only the live CronJob's `spec.suspend: true` under
the current operator's authority. This prevents new Jobs; it does not stop an
already-running Job. The ignored field remains paused across manual Argo syncs.
Do not change enforce arguments as an ownership rollback.

To reverse adoption while preserving service, first remove the Application and
AppProject from their owning Agents declarations/catalogs (and their sync-policy
entry/generated classification), or otherwise restore the reviewed prior catalog
revision without unrelated changes. Ensure the automated parents cannot recreate
them. Delete only the `verdify-descheduler` Application **without cascading**;
verify it has no resources finalizer before deletion. Preserve all seven objects,
Jobs, the Namespace and product workloads. Remove only that Application's Argo
tracking metadata from the seven objects, restoring the pre-adoption metadata,
then remove the dedicated AppProject if no Application references it. Re-read
UIDs, specs, suspend state and namespace labels against the export. The CronJob
continues its existing schedule. Never use `kubectl delete -k` as this inverse.

Source-only preparation, a successful sync, or a pre-adoption Job does not by
itself complete #802: the unchanged-object proof and natural post-adoption Job
remain required.
