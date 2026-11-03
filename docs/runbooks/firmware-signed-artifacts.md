# Signed firmware artifacts

The suspended `verdify-firmware-builder` compiles and archives one exact Git
revision. It cannot reach the controller. Its build initContainer has the
existing compile-time Wi-Fi/API/OTA settings, but no signing key. A separate
cosign container signs the archived `release.json`; only that container mounts
`verdify-firmware-signing`, whose encrypted custody belongs to agents KSOPS.
The public key is pinned in this product's `cosign.pub` ConfigMap input.

The independent suspended `verdify-firmware-flasher` has no Git, Wi-Fi, API or
signing credential. It stages a private copy of the reviewed artifact, verifies
the cosign bundle with the pinned key, checks all binary hashes and the exact
reviewed manifest digest, and defaults to verification only. Its only permitted
network destination is controller TCP/3232. Neither template opens TCP/6053.

The builder writes `secrets.yaml` with real YAML escaping (`firmware_release.py
secrets-yaml`). After the compile it checks, value-blind, that each Wi-Fi and OTA
literal in the generated `main.cpp` equals its Secret and appears in
`firmware.ota.bin` (`check-credentials`). A failure stops the build before the
archive and the signature, because a Wi-Fi mismatch is the one failure an OTA
rollback cannot reach.

Prepare and inspect Jobs before submission:

```sh
python3 scripts/firmware-release-job.py build --revision FULL_SHA --name JOB_NAME
python3 scripts/firmware-release-job.py verify --revision FULL_SHA --expected MANIFEST_SHA256 --name JOB_NAME
```

Add `--submit` for an approved run. The helper binds the same full revision in
both build containers. Archives use the full source revision as their directory
name and cannot overwrite an existing directory. A failed partial archive stays
preserved for diagnosis; do not remove or overwrite it to manufacture a receipt.

After a successful build, review `/artifacts/FULL_SHA/release.json` and record
its SHA256. A verification-only Job exercises staging, signature and hash
verification without device contact. The manifest binds the source repository,
revision, firmware version, immutable compiler image and all four build
artifacts. `release.sigstore.json` is a standard cosign v3 bundle with a P-256
signature. Private builds use an empty signing-service configuration: nothing
is uploaded to a public transparency log or timestamp service. The trusted key
comes independently from Git, never from the bundle.

Only a separately authorized attended firmware release may select action
`flash`. Complete the existing firmware preflight and retain rollback/bake
evidence first. The flasher uploads the verified private binary without
recompiling; the operator then performs the existing database-backed version
and sensor-health checks. A software signature is not device-side enforcement:
signed-app verification/Secure Boot remains deferred under DR-48. The existing
OTA password, physical qualification and network-owner ACL acceptance remain
separate requirements. This pipeline does not rotate credentials or promote a
new last-good image automatically.

## Signed flash procedure

The order the engine release used on 2026-11-03. `make firmware-deploy`,
`make firmware-rollback` and `scripts/firmware-rollback.sh` are not part of it:
they need a host with `firmware/artifacts`, esphome and the OTA secret, and the
rollback target flashes `last-good`, which need not be the release that ran last.

Take every identity from the build and verify receipts. Never derive one from
local git or `date`: the archive's short SHA has 8 characters and a local one 9,
so a recomputed version can never match.

```sh
export VERDIFY_DB_BACKEND=kube
k() { kubectl --context vallery -n verdify-prod "$@"; }
CAND_REV=FULL_SHA; CAND_MANIFEST=MANIFEST_SHA256; V=FIRMWARE_VERSION
# The release running now, which is the rollback target.
RB_REV=FULL_SHA;   RB_MANIFEST=MANIFEST_SHA256;   RV=FIRMWARE_VERSION
RB_BIN=PRIVATE_DIR/rollback.ota.bin    # on the operator host
```

Choose the window first. A boot loop or a dead link can leave the house without
software heat, decaying toward outdoor air with a time constant of about 3 hours
(an estimate), and the hardware thermostat is the only backstop. Flash when the
house can ride that out, or with someone able to reach it. If the release has a
no-water rule, disable the lines and slots it covers first, and read them back:
the first program push carries every enabled line and slot within about a minute
of connect.

### 1. Verify both releases

```sh
python3 scripts/firmware-release-job.py verify --revision "$CAND_REV" --expected "$CAND_MANIFEST" --name JOB_NAME --submit
python3 scripts/firmware-release-job.py verify --revision "$RB_REV" --expected "$RB_MANIFEST" --name OTHER_JOB_NAME --submit
k logs job/JOB_NAME -c flash-reviewed-artifact
```

Each must print `verified_artifact_bytes: true`, `device_contact: false` and the
receipt's version and manifest. A difference is a stop. The artifacts PVC is RWO,
so no flash Job may start until these pods have finished.

### 2. Credential parity and rollback bytes

The builder's check proves an image matches the Secret on the day it was built.
It cannot show that the Secret still matches the image the controller runs. One
read-only Pod does both checks: it mounts the PVC read-only and holds no Secret
and no token, and its label selects the deny-all-egress NetworkPolicy.

```sh
python3 scripts/firmware-release-job.py inspect --name POD_NAME --submit
k wait pod/POD_NAME --for=condition=Ready --timeout=120s
k exec POD_NAME -- python3 /release-tools/firmware_release.py credential-parity --revision "$CAND_REV" --running "$RB_REV"
```

It prints only setter names and counts, for `set_ssid`, `set_password` and
`set_auth_password`, and exits nonzero unless every count passes.

| Output | Meaning | Action |
|---|---|---|
| `literals` 1, `candidate` and `running` at least 1, for all three | The candidate carries what the connected image carries | Pass |
| `running` 0 with `candidate` at least 1 | The candidate has a credential the connected image lacks | Stop. Do not flash |
| `candidate` 0, `literals` other than 1, or an error | Inconclusive | Not passed. If it cannot be settled, do not flash unattended |

The preflight wants the rollback binary as a local file, the exact bytes of the
release that is running. `firmware.ota.bin` contains the Wi-Fi password, the OTA
password and the API key: never send it through a pod log, keep the copy at mode
0600 in a private directory, and delete it after the soak. Do not use
`/artifacts/last-good.ota.bin`, which need not be the running release.

```sh
k exec POD_NAME -- sha256sum "/artifacts/$RB_REV/release.json"    # must equal $RB_MANIFEST
k exec POD_NAME -- python3 -c "import json;print(json.load(open('/artifacts/$RB_REV/release.json'))['files']['firmware.ota.bin'])"
umask 077; k exec POD_NAME -- cat "/artifacts/$RB_REV/firmware.ota.bin" > "$RB_BIN"
sha256sum "$RB_BIN"; wc -c "$RB_BIN"                              # must equal the manifest's sha256 and bytes
k delete pod POD_NAME --wait=true; k get pod POD_NAME             # must be NotFound: the PVC is RWO
```

### 3. Preflight

Run it unchanged, immediately before the wet gate:

```sh
FIRMWARE_ROLLBACK_BIN="$RB_BIN" FIRMWARE_ROLLBACK_SHA256=SHA256_FROM_THE_MANIFEST \
FIRMWARE_OTA_FREEZE_OVERRIDE_REASON='WHY THIS ROLLBACK RELEASE, NAMING ITS MANIFEST' \
bash scripts/firmware-deploy-preflight.sh
```

Required: exit 0, `Verified provisional rollback candidate (not accepted
last-good)`, `Weekly OTA limit clear`, and one new `deploy_override` row in
`alert_log`. If the weekly line reads "override accepted", stop and decide
explicitly: the reason variable also clears that gate. Do not set
`ALLOW_FIRMWARE_DEPLOY_GUARD_OVERRIDE` or `FIRMWARE_DEPLOY_OVERRIDE_REASON`, which
weaken the alert and telemetry guards.

### 4. Wet gate

The upload blocks the main loop and the firmware does not close relays first, so a
relay that is ON when the upload starts stays ON until the reboot or the abort.
Uploads have held about 10 s, and some up to about 6 minutes. Pulse timers do not
count during the hold. Re-read this every 30 s and submit on the first clean read
(`scripts/verdify-db.sh prod -c "$GATE"`):

```sql
WITH r AS (SELECT recorded_at, gap_requested FROM equipment_state_source_receipts ORDER BY source_observed_through DESC LIMIT 1),
a AS (SELECT ts, relay_truth rt, source_system_state->>'greenhouse_state' AS mode FROM climate_action_log ORDER BY ts DESC LIMIT 1),
c AS (SELECT vpd_avg, flow_gpm, temp_avg, water_total_gal FROM climate ORDER BY ts DESC LIMIT 1),
b AS (SELECT value FROM setpoint_snapshot WHERE parameter = 'band_house_vpd_high' AND ts > now() - interval '60 minutes' ORDER BY ts DESC LIMIT 1),
d AS (SELECT ts, uptime_s FROM diagnostics WHERE uptime_s IS NOT NULL AND ts <> date_trunc('minute', ts) ORDER BY ts DESC LIMIT 1),
st AS (SELECT value FROM system_state WHERE entity = 'dispatcher_stage' AND ts > now() - interval '30 days' ORDER BY ts DESC LIMIT 1),
w AS (SELECT count(*) n, coalesce(string_agg(equipment, ',') FILTER (WHERE state), 'none') wet_on FROM (SELECT DISTINCT ON (equipment) equipment, state FROM equipment_state WHERE equipment IN ('fog','drip_wall','drip_wall_fert','drip_center','drip_center_fert','fert_master_valve','mister_center','mister_south','mister_south_fert','mister_west','mister_west_fert') ORDER BY equipment, ts DESC) s)
SELECT round(extract(epoch FROM now() - r.recorded_at)) AS receipt_s, r.gap_requested AS gap, round(extract(epoch FROM now() - a.ts)) AS action_s, round(extract(epoch FROM now() - d.ts)) AS diag_s, d.uptime_s AS u0, w.n AS rows_seen, w.wet_on, c.flow_gpm AS flow, c.water_total_gal AS water_gal, round(c.temp_avg::numeric, 2) AS temp, round(c.vpd_avg::numeric, 3) AS vpd, round(b.value::numeric, 3) AS vpd_gate, a.mode, st.value AS stage, a.rt->>'heat2' AS heat2, a.rt->>'fan1' AS fan1, a.rt->>'fan2' AS fan2, a.rt->>'vent' AS vent,
(now() - r.recorded_at < interval '60 seconds' AND NOT r.gap_requested AND now() - a.ts < interval '120 seconds' AND now() - d.ts < interval '90 seconds' AND w.n = 11 AND w.wet_on = 'none' AND a.rt->>'fog' = 'false' AND a.rt->>'mister_center' = 'false' AND a.rt->>'mister_south' = 'false' AND a.rt->>'mister_west' = 'false' AND c.flow_gpm = 0 AND b.value - c.vpd_avg >= 0.20) AS hard_pass,
(a.mode = 'IDLE' AND st.value = 'ordinary' AND a.rt->>'heat2' = 'false' AND a.rt->>'fan1' = 'false' AND a.rt->>'fan2' = 'false' AND a.rt->>'vent' = 'false') AS soft_pass
FROM r, a, c, b, d, st, w;
```

| Result | Action |
|---|---|
| `hard_pass` and `soft_pass` true | Submit |
| `hard_pass` true, `soft_pass` false | Re-read for up to 20 minutes, then submit and record why. heat1 ON is normal and not in the check |
| `hard_pass` false from a relay, flow or the margin | Wait, pulses run 46-84 s. Still failing after 10 minutes is an anomaly: do not flash |
| `hard_pass` false from a stale `receipt_s`, `action_s` or `diag_s` | Telemetry fault. Do not flash until it clears |

The 0.20 kPa margin to the VPD gate is a judgement call, so that a mist pulse is
unlikely to start as the upload begins. Do not gate on `water_flowing`, which lags
flow by up to about 2 minutes. Record `u0` as U0, and the full row in the receipt.

### 5. Flash

```sh
python3 scripts/firmware-release-job.py flash --revision "$CAND_REV" --expected "$CAND_MANIFEST" --name JOB_NAME      # inspect only
python3 scripts/firmware-release-job.py flash --revision "$CAND_REV" --expected "$CAND_MANIFEST" --name JOB_NAME --submit
k logs -f job/JOB_NAME -c flash-reviewed-artifact
```

This is the only device write. If `--submit` errors, run `k get job JOB_NAME`
first: the request has a 15 s timeout and may have succeeded, and a manual Job
bypasses the CronJob's `concurrencyPolicy`, so never create a second one on a guess.
The Job has a 600 s deadline and a killed pod's log can vanish, so stream from the
start. `SuccessCriteriaMet` and `FailureTarget` are interim conditions: wait for
`Complete` or `Failed`, and record that transition as T.

### 6. The device decides, not the Job

Run this whatever the Job says, then read the newest diagnostics row after T:

```sh
bash scripts/wait-for-firmware-version.sh "$V" --timeout 300
```

| Row | Job | Meaning | Action |
|---|---|---|---|
| `V` | Either | Committed. A Failed Job here is a lost final ack | Continue. Never re-flash |
| `RV`, `uptime_s` below U0 | Either | A revert or a crash during the attempt | Record `reset_reason`. No re-flash |
| `RV`, `uptime_s` above U0, row later than T+180 s | Failed | Image unchanged: the device can commit and reboot up to 90 s after a failed Job | At most one retry under a new Job name, after the wet gate, and only for a transport error after auth. Never on `Authentication invalid` or a manifest or signature refusal |
| `RV`, `uptime_s` above U0, after T+180 s | Complete | Contradiction | Treat as unknown. Do not flash. Report |
| Another version, or no row by T+300 s with `ingestor_heartbeat` fresh | Either | Dark: a Wi-Fi mismatch, a crash loop or safe mode | Read the ingestor log for `ESP32 connection error`. Do not re-flash the candidate. Go to Rollback |

### 7. Post-flash checks

Set B, the boot, from the first row of the new version:
`to_timestamp(controller_time_epoch) - make_interval(secs => uptime_s)`. Do not
use `diagnostics.ts - uptime_s`, because rows lag by 33-37 s on OTA boots.

| When | Check | Pass |
|---|---|---|
| B+2 min | `system_state` `irr_engine` and `irr_program_id` | `irr_engine` true, newer than the flash. `src=rom`, then `src=nvs` with the expected program hash, normally within a minute of connect |
| B+2 min | Newest `irrigation_push` row | `confirmed_at` set, `reject_code` null |
| B+2 min | `bash scripts/k3s-smoke.sh device-monitor` | EXACTLY ONE. UNKNOWN naming a Job pod: re-run after 60-90 s. ZERO: re-sample 60 s later. More than one is a real failure that a firmware rollback does not fix |
| B+3 min | Ingestor log | `seeded=` and `writer_dispatch reason=transport_reconnect`. For 5 minutes, no `ValidationError` or `row failed schema validation` |
| B+5 min | `climate_action_log` `relay_truth` | heat1 true by about B+181 s (`min_heat_off_s`) if the house is under band low + 3.5 F. Do not use `v_equipment_now`: the reboot's OFF edge is dropped, so it keeps showing heat ON |
| B+5 to 10 min | `system_state` `dispatcher_stage` rows after B | `paced ...`, then `ordinary`. `held:readbacks` past 5 minutes: read `missing_params` in the ingestor log. An older `ordinary` row proves nothing |
| Uptime 360 s or more | `bash scripts/sensor-health-sweep.sh --since '10 minutes' --expected-fw "$V"`, with `--no-water` under a no-water rule | Zero FAIL. This is the decisive sweep: before 360 s a value from before the boot can still read fresh |
| B+10 min | Newest `setpoint_snapshot` against the desired values | Pushed values confirmed, none mismatched |
| B+20 min | `setpoint_delivery_stuck` | Not open |
| B+60 min | Close-out, below | |

Re-run a failing sweep once, after `max(diagnostics.ts)` has moved. Report water as
"no evidence of a valve opening, from telemetry": the ingestor can drop an ON edge
in the reconnect burst, the meter is coarse, and nothing here sees a stuck valve.
Relay-to-line mapping, the door and the hardware thermostat stay unobserved until
someone is on site, so a green soak is "flashed, software canaries only", not
acceptance. The bake and the last-good promotion stay separate decisions.

### 8. Close-out and the pin

Hold 60 minutes with no operator device-affecting change and no sync that restarts
the ingestor. Then re-run the sweep with `--since '65 minutes'` and pin the accepted
version:

```sh
python3 scripts/firmware-release-job.py pin --firmware-version "$V"            # refuses unless the controller reports it
python3 scripts/firmware-release-job.py pin --firmware-version "$V" --submit   # writes, then reads back
```

`alert_monitor` compares `diagnostics.firmware_version` with
`/srv/verdify/state/expected-firmware-version` in the ingestor pod every 300 s, and
this is the only supported path that writes that file. Until it moves, a healthy
flash opens `firmware_version_mismatch`; the sweep reports that as `Pin lags`, a
warning, only while live equals `--expected-fw`. After the pin, wait one alert cycle
and confirm no new row opens.

### Rollback

Roll back on evidence, never on a clock: an attempt marks the running image valid
and erases its fallback slot, so a speculative one gives up any automatic revert.

| Trigger | Threshold |
|---|---|
| Crash | `reset_reason` Task WDT, Guru/Panic, Interrupt WDT or Other WDT |
| Resets | Two or more further resets in 30 minutes, or a Software reset in the first 60 minutes without about 300 s of silence before it. One after that silence fits `reboot_timeout`: re-baseline B |
| Heap | `heap_bytes` under 30 KB on 3 consecutive rows, or largest block under 16 KB on 5, after uptime 300 s. Under 15 KB on any row in the first 60 minutes |
| Loop | Two consecutive `heap_profile` lines with `floor=FAIL`, `loop_15m_overruns` above 0 or `loop_15m_max` above 250 ms |
| Probes | `active_probe_count` below its pre-flash value for 10 minutes. `temp_avg` or `rh_avg` NULL for 5 minutes after B+10 |
| Temperature | `temp_avg` under 55 F, or under 58 F for 10 minutes. If both heaters read ON, get a person instead: a reboot will not help |
| Heat | Both heaters off for 10 minutes with `temp_target_delta_f` under -3 |
| Water | An ON edge on a drip, fertilizer, south or west relay under a no-water rule: send the Stop first (Stop all water, or a `stop` row in `irrigation_command` from an unattended session), then roll back |
| Log | `Blocked ungranted`, `Blocked center_mister`, `Mister watchdog`, `Heap-guard`, `ALL probes silent` or `No probe update` |
| Sweep | A FAIL that survives one re-run with the device connected |
| Dark | No diagnostics row for 10 minutes in the first hour, or 30 minutes after it, with the ingestor heartbeat fresh and the ingestor retrying |

Telemetry silence inside those limits is not a trigger. No attempt runs inside an
announced network window. Confirm no flasher Job is active, read the wet gate
(advisory here: down telemetry must not delay a needed rollback), then use the
flasher Job, under a new name per attempt:

```sh
k get jobs -l app.kubernetes.io/component=firmware-flasher
python3 scripts/firmware-release-job.py flash --revision "$RB_REV" --expected "$RB_MANIFEST" --name JOB_NAME --submit
bash scripts/wait-for-firmware-version.sh "$RV" --timeout 300
bash scripts/sensor-health-sweep.sh --since '5 minutes' --expected-fw "$RV"
bash scripts/k3s-smoke.sh device-monitor
python3 scripts/firmware-release-job.py pin --firmware-version "$RV" --submit
```

Read a failed attempt with `k logs job/JOB_NAME -c flash-reviewed-artifact`; the
final "OTA transport failed" is generic and the lines before it give the cause.

| Log line | Meaning | Action |
|---|---|---|
| `Connecting to ... port 3232 failed` | Listener not up: booting, crash loop or Wi-Fi | Retry after spacing |
| `receiving version response: timed out` | Accepted but not served: another client or a wedged loop | Retry after spacing |
| `Authentication invalid` | OTA password mismatch | Stop retrying. Report |

Space attempts at least 120 s after the previous Job is terminal, because each
authenticated attempt blocks the control loop. A failed attempt never writes the
running slot. Cap at 30 minutes, which spans a safe-mode window, then stop and tell
Jason. A Wi-Fi mismatch cannot be reached this way: the 2026-10-01 recovery needed a
temporary WLAN change under `NETWORK-WRITE`. Roll back at most once, and keep the
failed release's evidence and its PVC directory. Rolling back to pre-engine firmware
makes the ingestor write `irr_engine` false on the reconnect and fall back to the L01
bridge, so L01 runs and every wet line stops through it, while other runs, clear-fault
and the bursts answer `unavailable`.
