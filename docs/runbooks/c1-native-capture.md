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

### Measured consumed-evidence firmware successor

The source consumer then bound firmware `2026.10.2.0637.620a218a-wifi-bound`,
compiled from frozen source `620a218a93cf24ca1e1b49dfed617cc97de7d258`.
Its measured ELF SHA256 is
`39a237cfb044e72633d52646c203d27b1217ba9f9de3d4a8308798b1b7a96b5b`;
the exact OTA binary SHA256 is
`0f84ea9a06a8c96928d3675361515aa8fa1ab39528d256353babb59717bd9176`.
This source binding is preparation for that exact binary, not a deployment or
physical qualification claim. Historical2143 captures keep their original
firmware/source identity and outcomes.

The six genuinely consumed Setpoints, branch and sample marker publish in one
15-second controller batch, with the marker last. Solar, per-zone, wet and delta
diagnostics keep their five-minute cadence. This adds 30.4 publications/minute;
actual heap/API health must be verified after delivery. Control arithmetic,
strict binary32 equality, 30-second sample freshness, original callback fences
and authority expiry remain unchanged.

Actual Xtensa GCC 14.2 ELF inspection found the same float operand order,
loaded scalar constants and libm/division call targets for the solar/band
functions and phase helper as historical 2143. Function relocation annotations
and solar-phase alignment padding differ. The host 46,080-reference differential
checks this explicit instruction contract; it does not execute the Xtensa target
or replace two fresh native full 48-component epochs and source-owned restoration.

### Engine firmware

`SUPPORTED_FIRMWARE` now binds the engine release `2026.11.3.0435.5e24d07c`,
compiled from `5e24d07c3802958c5b7b09f560ab0e796a8f96bf` by the signed builder
with the same GCC 14.2 (manifest
`777bdb886a29791c4113ba98a67d81f13ac88db78ae3ce0ef156113524a037b0`, OTA binary
`8d7621bdfed03a729df4e164c97d038ba74578062273c75b2dd794bddd791e79`). The move
rests on source identity: `firmware/lib/greenhouse_solar.h` is byte-identical to
620a218a's, the solar and band expressions in `firmware/greenhouse/controls.yaml`
are unchanged, and the differential above still passes. It is not a physical
qualification claim.

Since #1277 the ingestor also accepts the accepted firmware pin, the version
`scripts/firmware-release-job.py pin` writes to
`/srv/verdify/state/expected-firmware-version` after a flash, so C1 no longer
writes `.unavailable.json` for every epoch after each release. That rests on the
same source identity: `algorithm_revision()` refuses any capture whose
`FIRMWARE_SOURCES` hashes (greenhouse_solar.h and controls.yaml) differ from the
ingestor's source, and a change to either file must restate in
`verdify_schemas/control_band_wire.py` that the solar and band expressions are
unchanged. Offline Tool A takes the same pin as `--accepted-firmware`. Without a
pin, the `SUPPORTED_FIRMWARE` fallback names the W6 flash
`2026.11.7.0133.9517acb6` (source `9517acb6`), whose two source files match
`FIRMWARE_SOURCES`. `TARGET_ELF_SHA256` still names 620a218a's ELF,
the last one whose instructions were inspected. To move it, read `elf_sha256`
from a verify Job's log (`docs/runbooks/firmware-signed-artifacts.md`), repeat
the instruction inspection on that ELF, then change the constant and this section.
