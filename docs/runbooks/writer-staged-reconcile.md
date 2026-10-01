# #433 bounded writer reconciliation

Jason selected source-owned desired policy for Iris, crop VPD, lighting,
activity/direct-wet, and safety. This procedure has no facility inspection
precondition. It keeps one device writer and the 12-command batch limit.

## Delivery boundary

Merge and deploy the source and ingestor digest through the normal Verdify
full-sync path. The new code is inert until an approval file is placed in the
**running ingestor pod**. The state volume is `emptyDir`; a replacement pod
loses its approval and stops. Never open another ESPHome session.

The dispatcher writes `/srv/verdify/state/writer-stage-preview.json` when any
desired candidate exceeds 12 commands, whether it follows a reconnect, cfg
drift, or a changed plan. It captures all current-generation cfg readbacks for audit.
The approval fingerprint binds the canonical 48 fields, every candidate and
prior-stage field, one atomic 48-field DB snapshot, the active plan rows and
their earliest expiry, image source revision, pod, session, and connection
generation. Unrelated changing sensor values do not invalidate the approval.
An approval expires in at most 30 minutes, or five minutes before the first
plan/one-shot expiry. A changed unrelated fixed desired value, bound readback,
plan, session, or generation stops the run before another stage. The four crop
VPD targets may move with their fresh source calculation; they are sent last.
The seven named VPD-high moisture guardrail fields may also change effective
value under the same unchanged plan when the live guardrail engages or releases.
The dispatcher verifies each against its freshly calculated effective value;
other planner fields stay fixed to approval. Already confirmed guardrail values
must retain their readback until a new bounded stage sends the changed value.

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
An atomic approval replacement wakes the existing dispatcher within its
one-second scheduler loop, subject to the existing 30-second retry throttle.
It rechecks through that scheduler every 20 seconds while awaiting
confirmation; the eight-minute deadline is a stop condition, not a sleep.
A reconnect generation stays unreconciled until all approved values are
confirmed. An ordinary desired-change run keeps its existing reconciled
generation and defers its dispatch trigger until the stage is confirmed.

Inspect progress without modifying the device:

```bash
kubectl -n verdify-prod logs deploy/verdify-ingestor -c ingestor --since=35m \
  | rg 'action=bounded_stage|action=stage_awaiting_confirmation|action=blocked_stage|action=blocked_broad_restore'
kubectl -n verdify-prod exec deploy/verdify-ingestor -c ingestor -- \
  cat /srv/verdify/state/writer-stage-state.json
scripts/k3s-smoke.sh device-monitor
```

The `writer_reconcile reason=` field names the actual trigger, including
`desired_change` for a refreshed plan and `transport_reconnect` for a new
socket. Both paths use the same stage approval and 12-command cap.

Successful completion is `status: complete`, no remaining desired candidate,
one connection, and 48 fresh readbacks. A newly moved crop VPD target may pass
from the final stage into the ordinary writer only when its fresh source
matches and the whole current candidate is at most 12 commands; verify that
ordinary lifecycle reaches `confirmed` and its cfg readback matches before
recording no residual candidate. Preserve the preview, approval, state, and
prior private baseline outside the pod for the recovery record.

After a completed stage, a later desired candidate above 12 commands gets a
new preview and requires a new approval. The ingestor first writes
`writer-stage-completed-<run_id>.json` with the old state and approval, then
removes their active names. Keep that archived receipt with the operator copy
before replacing the pod; its `emptyDir` is lost on restart. If archival cannot
be verified, the writer holds the broad candidate.

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

### Close an elapsed confirmation window after verified recovery

A sent request can remain physically uncertain after its eight-minute
confirmation deadline. Do not mark it failed or confirmed, replay it, or remove
its alert by hand. For a `rollback_complete` bounded run, prepare the native
custody companion with `scripts/prepare-recovered-confirmation-custody.py`.
This companion grants no device authority. It binds unchanged raw state,
original approval and recovery files, the preserved terminal recovery proof,
and a read-only export of the exact original and rollback request rows.

The request export has `original_requests` and `rollback_confirmations` arrays.
Original rows contain `ts`, `parameter`, `value`, `delivery_status`,
`source`, `confirmed_at` and `expired_at`; rollback rows contain the same fields except
`expired_at`. Select original rows only for the last stage's exact parameter
set, between `stage_started_at` and recovery `approved_at`, excluding ESP32
observation rows. Select rollback rows by the exact timestamp/parameter keys
in `rollback_records`. Preserve the original SQL export with the raw receipts.

```bash
python scripts/prepare-recovered-confirmation-custody.py \
  --state preserved-state.json --approval preserved-approval.json \
  --recovery preserved-recovery.json --terminal terminal-recovery-proof.json \
  --requests exact-request-export.json --output local-custody.json
```

ROOT reviews and installs that new companion as
`/srv/verdify/state/writer-stage-recovery-custody.json`, using exact file-hash
comparison and an exclusive, fsynced publication. Preserve all existing native
files. `/srv/verdify/state` is backed by the retained ingestor state PVC; a
normal Recreate carries these receipts to the new sole writer. A missing or
changed custody companion holds the run.

After a normal source delivery, the recovered-run archive path requires fresh
current-generation canonical 48 readbacks, the last stage at its original
baseline, earlier completed stages still matching, the same actual firmware,
a strict writer lease and the existing numeric heap guard (30 KiB free,
18 KiB largest block). It rechecks real confirmed rollback rows and later
request outcomes through the authorized writable projection under row locks.
Only original `sent`/unconfirmed requests whose confirmation window has
elapsed can transition atomically to `expired`. `expired_at` is the actual DB
clock at transition; the deadline is recorded separately. Confirmation remains
null, and the immutable prior-facts receipt retains the original sent status.
Attempt receipts do not assert commit. Actual committed DB rows, including
expiry times, become the recovered archive evidence. The existing confirmation
monitor then owns any terminal-alert resolution. This closes a confirmation
window; it proves neither earlier delivery nor failure and does not resume the
stopped desired-policy run.

### Forward from one fully confirmed halted stage

A redundant seconds candidate can halt a run after every command in its first
stage has already been confirmed. Preserve that stopped run; do not roll back
valid desired crop values, reset its state, or reuse its approval. Ordinary
candidate construction consistently omits durations already equivalent on the
current-generation cfg readback. The residual validator additionally permits
only an unapproved seconds candidate whose original plan value and original
baseline readback are unchanged and equivalent.

The explicit `confirmed_halt_forward` handoff applies only to a halted run with
one stage of 1–12 parameters, an empty pending-record array, exactly those
completed parameters/values, and no rollback history. Retain the raw active
files as a JSON object mapping filenames to their exact text. Retain a full
original Pod readback plus `current.source`, `current.pod`, and
`current.preview` in the operator receipt; its Pod UID/name and original
source/session/generation must agree. Export exactly the original native
request rows (`ts`, `parameter`, `value`, `source`, `delivery_status`,
`confirmed_at`, `expired_at`) read-only, excluding ESP32 observation rows.
Every row must be confirmed with a non-null confirmation and no expiry.

After source adoption, capture a fresh preview from the new sole writer. The
following command writes **only a local handoff manifest**:

```bash
python scripts/prepare-writer-stage.py current-preview.json local-forward.json \
  --confirmed-halt-custody original-raw-active-files.json \
  --native-requests original-native-requests.json \
  --original-writer-custody original-writer-before.json
```

The owning operator installs this manifest as `writer-stage-recovery.json`
with exact live Pod/file-custody checks and atomic publication. It grants no
setter authority. The writer rechecks the exact native rows, rejects any later
request on those fields, and verifies the unchanged plan, confirmed completed
readbacks, and exact untouched baseline. Its six-minute identity/baseline
binding governs initial archive creation. The immutable
`writer-stage-confirmed-halt-<original-run-id>.json` retains the old state,
approval, raw custody, Pod provenance, and native outcomes. The old active
state/approval remain unchanged; the writer holds for fresh authority.

Only after that archive exists, prepare a distinct ordinary approval from a
fresh current preview using the normal command above this section. The writer
rechecks archived native history and readbacks, then runs all normal fresh
source/session/generation, plan, expiry, lease, candidate and bounded-send
checks before replacing the active state. Archive-time identity is retained
as history, never relabelled as the new admission identity. A refused fresh
approval leaves the original stopped state and archive intact. No DB outcome
is edited or original request replayed. This path does not recover an unknown,
failed, partially confirmed, or multi-stage halted run.
