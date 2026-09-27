# #433 bounded writer reconciliation

Jason selected source-owned desired policy for Iris, crop VPD, lighting,
activity/direct-wet, and safety. This procedure has no facility inspection
precondition. It keeps one device writer and the 12-command batch limit.

## Delivery boundary

Merge and deploy the source and ingestor digest through the normal Verdify
full-sync path. The new code is inert until an approval file is placed in the
**running ingestor pod**. The state volume is `emptyDir`; a replacement pod
loses its approval and stops. Never open another ESPHome session.

The dispatcher writes `/srv/verdify/state/writer-stage-preview.json` on a
broad-restore hold. That file binds the full ordered candidate, all
current-generation cfg readbacks, one atomic 48-field canonical DB snapshot,
the active plan rows and their earliest expiry, image source revision, pod,
session, and connection generation. An approval applies to that exact preview
and expires in at most 30 minutes, or five minutes before the first
plan/one-shot expiry. A changed
plan, readback, session, or generation stops the run before another stage.

## Arm once from a fresh preview

Run from Jason's Mac after the exact intended image is Synced + Healthy and
the sole ingestor is Ready. `device-monitor` must report exactly one ESP32
connection. No physical walkthrough is required.

```bash
scripts/k3s-smoke.sh device-monitor
umask 077
kubectl -n verdify-prod exec deploy/verdify-ingestor -c ingestor -- \
  cat /srv/verdify/state/writer-stage-preview.json \
  > /private/tmp/verdify-writer-stage-preview.json
python scripts/prepare-writer-stage.py \
  /private/tmp/verdify-writer-stage-preview.json \
  /private/tmp/verdify-writer-stage-approval.json
kubectl -n verdify-prod exec -i deploy/verdify-ingestor -c ingestor -- \
  sh -c 'umask 077; cat > /srv/verdify/state/.writer-stage-approval.tmp && mv /srv/verdify/state/.writer-stage-approval.tmp /srv/verdify/state/writer-stage-approval.json' \
  < /private/tmp/verdify-writer-stage-approval.json
```

The ingestor itself selects at most 12 values in dispatcher order, writes a
durable `inflight` state **before** any request row or physical call, then
uses its existing `requested` → `sent` → `confirmed` lifecycle and the same
ESPHome connection. The next stage waits for every exact DB row to be
`confirmed` with `confirmed_at` and matching current-generation cfg readback.
It rechecks through the existing scheduler every 20 seconds while awaiting
confirmation; the eight-minute deadline is a stop condition, not a sleep.
The generation stays unreconciled until all approved values are confirmed.

Inspect progress without modifying the device:

```bash
kubectl -n verdify-prod logs deploy/verdify-ingestor -c ingestor --since=35m \
  | rg 'action=bounded_stage|action=stage_awaiting_confirmation|action=blocked_stage|action=blocked_broad_restore'
kubectl -n verdify-prod exec deploy/verdify-ingestor -c ingestor -- \
  cat /srv/verdify/state/writer-stage-state.json
scripts/k3s-smoke.sh device-monitor
```

Successful completion is `status: complete`, no residual candidate, one
connection, and 48 fresh readbacks. Preserve the preview, approval, state,
and prior private baseline outside the pod for the recovery record.

## Stop and bounded rollback

The run halts on a changed active plan or one-shot, changed untouched
readback, lost generation/lease, failed or missing lifecycle row, missing cfg
confirmation, or uncertain process outcome. Removing the approval file stops
future stages; an already sent batch must still be checked by its lifecycle.
The global 12-command cap remains in force. Do not reset state or retry an
unknown `inflight` stage.

For a halted run in the same pod, the tool can restore **only the last stage**
to its captured baseline, again through the sole ingestor in a batch of at
most 12. It skips fields already at baseline and requires a fresh 48-field
snapshot, a new ten-minute rollback approval, request/sent/confirmed rows,
and matching cfg readbacks. Earlier confirmed stages remain desired. A
replacement pod or a failed rollback requires a new read-only capture and
reviewed recovery; never use a broad blind restore.

```bash
umask 077
kubectl -n verdify-prod exec deploy/verdify-ingestor -c ingestor -- \
  cat /srv/verdify/state/writer-stage-preview.json \
  > /private/tmp/verdify-writer-rollback-preview.json
kubectl -n verdify-prod exec deploy/verdify-ingestor -c ingestor -- \
  cat /srv/verdify/state/writer-stage-state.json \
  > /private/tmp/verdify-writer-halted-state.json
python scripts/prepare-writer-stage.py \
  /private/tmp/verdify-writer-rollback-preview.json \
  /private/tmp/verdify-writer-stage-recovery.json \
  --rollback-state /private/tmp/verdify-writer-halted-state.json
kubectl -n verdify-prod exec -i deploy/verdify-ingestor -c ingestor -- \
  sh -c 'umask 077; cat > /srv/verdify/state/.writer-stage-recovery.tmp && mv /srv/verdify/state/.writer-stage-recovery.tmp /srv/verdify/state/writer-stage-recovery.json' \
  < /private/tmp/verdify-writer-stage-recovery.json
```

The recovered run remains stopped at `rollback_complete`; it does not resume
the prior desired vector. A new desired-policy run requires a fresh preview
and separate approval after resolving the mismatch.
