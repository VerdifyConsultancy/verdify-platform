# #433 one-field cfg drift proof

This source-only probe is for the **sole running ingestor** after Gate R/C1
recovery and the two-hour #433 quiet observation. Deploying a new ingestor
image restarts its writer-stability clock, so do not promote it during that
window. The feature is inert without an approval file in that pod's `emptyDir`.
It creates no second ESPHome connection and never edits an active plan.

## Field and bounded effect

The only eligible field is `outdoor_staleness_max_s`. The September 27 #433
readback and active plan both held **600 seconds**, with no pending command for
that field. Recheck a fresh preview before use; historical values are not
authority. Firmware and registry allow 120–1800 seconds in 30-second Number
steps. The probe sends **570 seconds**, waits for durable confirmation and cfg
readback, then requires the only desired candidate to be **600 seconds** before
one corrective command. Both phases use one attempt through the existing
dispatcher queue and `requested` → `sent` → `confirmed` lifecycle.

For the brief interval at 570 seconds, the summer vent preference gate treats
outdoor weather data aged 570–599 seconds as stale instead of fresh. Data
younger than 570 seconds or at least 600 seconds produces the same gate input.
Safety limits, direct-wet controls, and crop-band anchors are not changed.

## Arm from the exact running pod

Require `device-monitor` to show one ESP32 connection and use a preview from
that same Ready ingestor. The preview binds all 48 current-generation cfg
fields, the active plan, source revision, pod, process session, and generation.
It is valid for six minutes. A nonempty ordinary candidate, changed plan or
readback, lost lease, or changed connection stops the probe before a command.

```bash
scripts/k3s-smoke.sh device-monitor
umask 077
kubectl -n verdify-prod exec deploy/verdify-ingestor -c ingestor -- \
  cat /srv/verdify/state/writer-drift-preview.json \
  > /private/tmp/verdify-writer-drift-preview.json
python scripts/prepare-writer-drift-probe.py \
  /private/tmp/verdify-writer-drift-preview.json \
  /private/tmp/verdify-writer-drift-approval.json
kubectl -n verdify-prod exec -i deploy/verdify-ingestor -c ingestor -- \
  sh -c 'umask 077; cat > /srv/verdify/state/.writer-drift-approval.tmp && mv /srv/verdify/state/.writer-drift-approval.tmp /srv/verdify/state/writer-drift-approval.json' \
  < /private/tmp/verdify-writer-drift-approval.json
```

Inspect `writer-drift-state.json`, the two exact `setpoint_changes` timestamps
it records, current-generation cfg readback, and `writer_drift_probe` plus
`writer_delivery` logs. Completion requires `status: complete`, both rows
`confirmed` with `confirmed_at`, exactly one transport `sent` event for the
probe and one for the correction, no other command in between, one socket, and
zero residual desired candidate. Save preview, approval, and final state
outside the pod. If status is `halted` or `*_inflight`, preserve those receipts
and inspect the live cfg before any recovery; never blindly replay the file or
open a second client.
