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

`VerdifyConsultancy/verdify-platform/deploy/k8s/descheduler` renders eight objects:

| Kind | Name | Namespace |
| --- | --- | --- |
| Namespace | verdify-descheduler | cluster |
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
immutable product revision and has no resource-deletion finalizer. Its AppProject
allows only the resource kinds above; Secret, Job, Pod and Deployment are outside
that allowlist. `CreateNamespace=false` remains; the explicit existing Namespace
manifest carries `Prune=false,Delete=false` and preserves its four labels.

The eight-object Namespace adoption completed under #995 on October 20, 2026,
at product `162e8fcbf51d0141cadb2517ba95d2ec7498a9f6`: all original UIDs,
functional payloads, Namespace labels/finalizers and current pause state were
unchanged, and all eight objects gained the expected tracking. The historical
`STAGED-rebalanceable-labels-patch.yaml` is not rendered or an apply instruction.

#1004 records `spec.suspend: false` in Git and removes the exact suspend ignore
from the owning Agents Application in its companion. Until that companion and
accepted child source are delivered, the prior ignore remains live; do not claim
source-only preparation removed it. Routine pauses go through source and an
attended manual, non-pruning sync. Keep automation off and do not use
`kubectl apply -k` as a second desired-state writer.

## Attended adoption

1. Confirm context `vallery`, accepted product and Agents commits, and current
   ownership. Export the eight full objects; retain
   UIDs, server-defaulted specs, labels and tracking annotations before applying
   anything. Read the CronJob's suspend flag and active Jobs. Confirm the exact
   Application and AppProject are absent or still match the reviewed plan.
2. Render this source at the accepted product commit. Require the eight objects above. Compare every source leaf, including array members, with the
   export; review API defaults separately. The final server-defaulted comparison
   must show no spec, policy, RBAC or namespace-label change. Stop for any drift
   beyond the intended Argo ownership metadata.
3. Deliver only the reviewed Agents AppProject and Application through their
   owning catalogs. The catalog roots are independently pinned and automated;
   advancing either root requires review of its entire pending delta. Do not
   advance an unrelated root or reconcile the whole estate for this adoption.
   Bind the Application to the accepted product commit before its first sync.
4. Diff the exact `verdify-descheduler` Application and sync that revision with
   pruning disabled. The resource set must be exactly the eight objects above.
   Do not use replace/force or delete/recreate. No new Job is part of the sync.
5. Read back all eight UIDs, specs and ownership metadata, plus Namespace labels.
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
   eight objects and Application after that run for reconciliation durability.
7. Refresh the Agents live ring snapshot and assign the now-existing Application
   to its manual ring using actual evidence. Do not put a prospective Application
   into the live snapshot. Update #802 only with the observed acceptance results.

## Pause and inverse

For a routine pause, commit `spec.suspend: true`, deliver its accepted child source
through the owning Agents catalog, and run the attended full non-pruning sync.
Verify the source, live pause and Application agree. Suspending prevents new Jobs;
it does not stop an already-running Job. Resume through the same source path.
An urgent containment pause must be recorded and reconciled into Git before the
next sync, so that an old `false` pin cannot silently resume it. Do not change
enforce arguments as an ownership rollback.

To reverse adoption while preserving service, first remove the Application and
AppProject from their owning Agents declarations/catalogs (and their sync-policy
entry/generated classification), or otherwise restore the reviewed prior catalog
revision without unrelated changes. Ensure the automated parents cannot recreate
them. Delete only the `verdify-descheduler` Application **without cascading**;
verify it has no resources finalizer before deletion. Preserve all eight objects,
Jobs, the Namespace and product workloads. Remove only that Application's Argo
tracking metadata from the eight objects, restoring the pre-adoption metadata,
then remove the dedicated AppProject if no Application references it. Re-read
UIDs, specs, suspend state and namespace labels against the export. The CronJob
continues its existing schedule. Never use `kubectl delete -k` as this inverse.

Source-only preparation, a successful sync, or a pre-adoption Job does not by
itself complete #802: the unchanged-object proof and natural post-adoption Job
remain required.
