# #433 one-field cfg drift proof

This source-only probe is for the **sole running ingestor** after Gate R/C1
recovery and the two-hour #433 quiet observation. Deploying a new ingestor
image restarts its writer-stability clock, so do not promote it during that
window. The feature is inert without an approval file in that pod's `emptyDir`.
It creates no second ESPHome connection and never edits an active plan.

## Field and bounded effect

The only eligible field is `cool_stage2_exit_hysteresis_f`. The September 27
#433 readback and active plan both held **1.0°F**, with no pending command for
that field. Recheck a fresh preview before use; historical values are not
authority. Firmware and registry allow 0.3–3.0°F in 0.1°F Number steps. The
probe sends **1.1°F**, waits for durable confirmation and cfg readback, then
requires the only desired candidate to be **1.0°F** before one corrective
command. Both phases use one attempt through the existing dispatcher queue
and `requested` → `sent` → `confirmed` lifecycle.

For the brief interval at 1.1°F, an already latched cooling fan2 clears at
`temp_high + cool_stage2_over_high_f − 1.1°F` instead of the same threshold
minus 1.0°F. Fan2 can remain on for an additional 0.1°F of cooling near that
exit threshold. Fan2 entry, safety limits, direct-wet controls, and crop-band
anchors are not changed.

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
