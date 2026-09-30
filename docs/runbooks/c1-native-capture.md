# C1 original callback capture

The source collector uses the ingestor's existing authenticated subscription.
It creates no client, subscription, SubscribeStates replay, device command or
DB mutation. It arms only after current-generation initial readback readiness
and raw entity-grid attestation. Every one of the 48 firmware cfg template
sensors must publish a new original callback; every per-field timestamp must
advance beyond the prior complete epoch. Flushes cannot create source epochs.
Any cached state replay requested by either existing same-socket source worker
invalidates the entire armed request before the replay is sent. Its cached cfg
callbacks cannot create C1 epochs; a distinct fresh request is required.
Reconnect or process identity changes discard partial collection. Uptime
regression discards completed evidence and marks subsequent raw epochs as reset.

The independent native packet retains off-grid raw values rather than projecting
them. A packet is evidence, not qualification. ToolA still rejects off-grid cfg
values, missing routes, old generations and unequal served/control bands.

Consumed band edges and house targets come from the actual firmware Setpoints
publication. The text device sample epoch is published last in the same band
batch. This is the computation time; receive timestamps remain separately
preserved. The served resolver is queried read-only at that integer control
minute, matching the firmware's minute-level control clock. The control and
observed layers share the actual consumed-member publication; the control layer
is not an independent recomputation. Any difference from served values is
reported honestly by ToolA's exact binary32 comparison.

After the intended source has been released and its additive firmware surfaces
are running, prepare one request from fresh runtime status:

```bash
umask 077
kubectl -n verdify-prod exec deploy/verdify-ingestor -c ingestor -- \
  cat /srv/verdify/state/c1-capture-status.json > /private/tmp/c1-status.json
python scripts/prepare-c1-native-capture.py /private/tmp/c1-status.json /private/tmp/c1-request.json
kubectl -n verdify-prod exec -i deploy/verdify-ingestor -c ingestor -- \
  sh -c 'umask 077; cat > /srv/verdify/state/.c1-request.tmp && mv /srv/verdify/state/.c1-request.tmp /srv/verdify/state/c1-capture-request.json' \
  < /private/tmp/c1-request.json
```

For ten minutes, the collector records only natural callbacks. Full-48 source
UUID packets and ToolA inputs are stored beneath the request UUID in
`/srv/verdify/state/c1-capture/`. The passive band publication keeps its existing
five-minute cadence. Each `.native.json` preserves source epoch identity,
original callback timestamps, runtime generation, metadata and device sample
clock. The corresponding `.input.json` is accepted directly by
`scripts/component_grid_capture.py`; it does not itself claim qualification.
Preserve both artifacts and hashes. Two complete distinct natural source epochs
must have strictly advancing times, matching identity and no reset. Retain
failed and off-grid evidence; an explicit grid proposal belongs to the separate
source-derived desired-policy path.
