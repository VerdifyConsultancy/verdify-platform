# Exact Gate R recovery-only successor (migration 255)

**Research draft; do not merge, promote, or activate.** A read-only comparison
on 2026-09-27 found 17 encoded baseline mismatches, eight desired values the
experiment wire grid changes during encoding, and four crop VPD desired
commands outside the 48-field experiment vector. The selected all-five-layer
policy cannot be restored by this frozen study baseline. The compatible live
path is a separately authorized facility handoff followed by the bounded
ordinary writer stage; historical experiment recovery debt remains open.

This procedure is scoped to experiment `45039c86-c1d9-52f6-a0a9-d94a17bc4b14`,
the latest `db_outage` fault `725306a0-9b4b-4083-a3ba-8912308e1d55`, and
sealed Gate R resolution `ace2d26c-539d-4007-ad9c-d25ad812644f`. It restores
the frozen 48-field baseline through one new work row. It does not create an
aggressive work row, exposure, or Gate P proof. The 98 older failed recovery
rows and original fault stay in the ledger.

## Prepare and deploy with capability off

1. Merge migration 255 and the ingestor source, let CI build the five image
   candidates, and promote all five release pins per
   [laptop-operator.md](laptop-operator.md) §2. Diff the whole production
   render. Full sync at the exact revision, with prune false and no resource
   selector, so the migrate PreSync hook runs. Verify Synced + Healthy, migration
   255 stamped, the intended ingestor digest, one Ready Recreate ingestor pod,
   its sole ESP32 connection, `VERDIFY_POLICY_VECTOR_MODE=off`, component
   capability `off`, and an empty active experiment ID. Migration installation
   alone neither enables a writer nor begins a work window.
   Before arming the component, run the read-only machine preflight with a
   fresh #433 preview. It compares the frozen baseline's 178-byte vector with
   all 48 desired canonical values, rejects wire quantization of a desired
   value, and rejects desired commands outside the vector. A mismatch exits 1.
   It calls only `SELECT encode(wire_vector,'hex')`; no DB credential is printed.

   ```bash
   python scripts/check-recovery-baseline.py /private/tmp/fresh-writer-stage-preview.json
   ```
2. Prepare a separate attended GitOps commit that sets
   `VERDIFY_COMPONENT_EXPERIMENT_ENABLED=enabled` and
   `VERDIFY_ACTIVE_EXPERIMENT_ID=45039c86-c1d9-52f6-a0a9-d94a17bc4b14`
   in `deploy/k8s/base/configmap.yaml`. Keep vector mode `off`. Run
   `scripts/gen-config-revision.sh`, review the rendered diff, merge, then
   perform the same full, no-prune Argo sync. **Complete this potentially long
   full hook sync and the singleton ingestor restart before calling `begin`.**
   Verify the new pod has the exact config, one ESP32 connection, and its
   startup all-48 ordinary-writer hold. No recovery work exists yet.

## Begin and observe the exact baseline

Use a new immutable authorization reference for this attended run. The API pod
already carries the function-only lifecycle login; `kubectl exec -i` streams
the local script into Python without reading or printing its password. The
begin function itself checks sealed Gate R, latest fault, draft/shadow/closed,
lease 17, zero exposure, idle work, and the frozen 178-byte baseline. It opens
one 20-minute baseline-only work window and returns its UUID. Start the window
only after the rollout in step 2 is fully Ready.

```bash
kubectl -n verdify-prod exec -i deploy/verdify-api -- \
  python - begin --authorization-ref '<immutable-authorization-ref>' \
    --actor 'jason-recovery-operator' --minutes 20 \
  < scripts/recovery-only-lifecycle.py
```

Record the printed `recovery_only_work_id`. The enabled component executor on
the sole ingestor must claim that exact `baseline_recovery` work and deliver
the frozen full 48-field vector. Require every component outcome to be
confirmed and two distinct raw cfg epochs at least 30 seconds apart, each
with all 48 canonical observations and the exact frozen wire vector in the
current runtime/writer/connection generation. The real executor must record
the later `recovered` work event. Check those DB rows and the device cfg
readbacks before finishing. An expired/failed/uncertain work row or new runtime
fault requires stopping; a retry needs a separately reviewed successor.

## Finish, disable, and verify

`finish` refuses absent/incomplete recovery evidence, new work, new fault, or
open exposure. It closes admission and moves the study to draft/shadow/off in
one DB transaction, incrementing the lease to revoke component authority.

```bash
kubectl -n verdify-prod exec -i deploy/verdify-api -- \
  python - finish --work-id '<recovery_only_work_id>' \
    --authorization-ref '<same-immutable-authorization-ref>' \
    --actor 'jason-recovery-operator' \
  < scripts/recovery-only-lifecycle.py
```

Immediately make a GitOps commit returning component capability to `off` and
the active experiment ID to empty, run `scripts/gen-config-revision.sh`, merge,
diff, and full sync without prune. Verify the Recreate ingestor restarted, is
the sole ESP32 connection, and its ordinary writer hold is released only on
this clean off-mode startup. A process that acquired the hold does not release
it solely because the SQL attestation says recovery is complete.

Readbacks on the production DB:

```sql
SELECT status, execution_phase, admission_state, component_enabled,
       lease_generation FROM public.control_experiments
 WHERE experiment_id = '45039c86-c1d9-52f6-a0a9-d94a17bc4b14';
SELECT * FROM public.fn_experiment_v2_ops_status()
 WHERE experiment_id = '45039c86-c1d9-52f6-a0a9-d94a17bc4b14';
SELECT public.fn_experiment_v2_gate_p_recovery_handoff_ready(
    '45039c86-c1d9-52f6-a0a9-d94a17bc4b14');
SELECT * FROM public.fn_experiment_v2_safe_startup_after_recovery_only(
    'esp32-vallery', NULL);
```

Expected: draft/shadow/closed/off, lease 19, ops safety `shadow_closed` with
no current work or alert, recovery handoff readiness true, startup
`hold_required=false` with reason `recovery_only_baseline_confirmed`. The
startup `recovery_pending_count` still truthfully reports 98 historical rows
without recovered events; they are not rewritten or credited. The original
Gate P sealed-target predicate is a **pre-recovery** check and now returns
false. The new handoff predicate is read-only evidence for a future Gate P
successor, not Gate P activation. Any newer fault makes startup hold true and
handoff readiness false again.
