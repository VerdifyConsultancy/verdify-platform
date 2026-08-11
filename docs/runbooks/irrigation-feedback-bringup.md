# Irrigation Feedback Bring-Up

This runbook is for closing the remaining irrigation acceptance gap: south soil probe 1 must report credible nonzero moisture/EC, and the center path must have root-zone moisture plus runoff pH/EC feedback.

## Acceptance Gate and Live Proofs

Current proof artifacts are authoritative. Run `make irrigation-field-diagnostics` to refresh `/srv/verdify/state/irrigation-completion-audit.json`, `/srv/verdify/state/irrigation-work-order.txt`, `/srv/verdify/state/irrigation-discovery-proof.txt`, `/srv/verdify/state/irrigation-field-sensor-health-proof.txt`, and `/srv/verdify/state/irrigation-finalizer-dry-run-proof.txt`; do not treat the static snapshot below as fresher than those files.

Representative point-in-time snapshot, 2026-05-22 14:55 UTC:

- Software, dashboard, public site, retired-table write guards, canonical data-trust-ledger logging, and live Grafana render checks pass. `make test` passes with `485 passed, 2 skipped, 1 xfailed`; `/srv/greenhouse/.venv/bin/python scripts/irrigation-completion-audit.py --json --live-site --mqtt-live-timeout-s 0` proves objective items 1, 2, 3, 4, 6, and 7 and stops only at the physical feedback gate. `make irrigation-field-diagnostics IRRIGATION_MQTT_LIVE_TIMEOUT=5 IRRIGATION_FIELD_WATCH_MQTT_TIMEOUT=0` refreshed the field proof files in `/srv/verdify/state/`.
- The retired `irrigation_schedule` and `irrigation_log` tables are DB-level read-only compatibility surfaces. The current audit verifies `legacy_log_rows_since_retirement=0`, `canonical_log_view_ok=True`, `retired_view_deps=-`, and `write_guard_behavior_ok=True`. The live relay trace render passes the dense timeline visual check with `relay_visual_gray_pct=0.612`, `relay_visual_white_pct=0.370`, blue relay-state pixels present, and no ON/OFF text labels.
- `south_soil_probe_1` remains `stuck_zero`: DB moisture and EC are `0`, ESPHome/HA moisture is `0.0`, ESPHome/HA EC is `0.0`, while south-1 temperature is live around `69-70°F`. The status view reports `positive_samples_24h=0` for south-1, `south_2_reference_positive_samples_24h=880`, south-2 last positive at `2026-05-22T07:34:13Z`, south-1 moisture last positive at `2026-05-16T17:31:03Z`, and south-1 EC last positive at `2026-05-16T16:00:49Z`. DB source history shows south-1 moisture/EC have `77,576` lifetime samples but `0` valid samples in the last 24 hours, while south-1 temperature has `1,423` valid samples in the last 24 hours. `make irrigation-field-sensor-health-proof` reports no Modbus timeouts and 4/4 active zone probes, so prioritize south-1 probe/media contact or channel failure over shared ingestion or bus failure.
- Center feedback remains physically absent/unmapped: `moisture_center`, `ph_runoff_center`, and `ec_runoff_center` have zero lifetime DB samples, no accepted HA entities, no accepted MQTT retained/live topics, and no accepted ESPHome object IDs. `make irrigation-field-diagnostics IRRIGATION_MQTT_LIVE_TIMEOUT=5 IRRIGATION_FIELD_WATCH_MQTT_TIMEOUT=0` found no unmapped center-like HA, MQTT, or ESPHome sources to add to ingestion; it only found the known south-1 zero readings, nearby south/west references, and hydroponic/reservoir chemistry near-misses that must not be used as center runoff feedback.
- `make irrigation-feedback-finalize-dry-run-proof` correctly refuses to mutate closure rows with `not_ok=south_soil_probe_1:stuck_zero,center_root_zone_moisture:missing,center_runoff_ph:missing,center_runoff_ec:missing`.
- The completion gate remains intentionally blocked by four open `irrigation_feedback_gap` alerts plus the two `instrumentation_requirements` rows: `south_soil_probe_1_repair` and `center_root_zone_runoff_feedback`.

Do not run `make irrigation-feedback-finalize` until `make irrigation-feedback-check` exits 0 or `make irrigation-feedback-watch-field-proof` shows all four feedback keys as `ok`.

Run:

```bash
make irrigation-field-diagnostics
make irrigation-feedback-discover
```

Use this output to check the ESP32/Modbus sensor path and catch ESPHome, Home Assistant, or MQTT entities that look relevant but are not yet accepted by ingestion. The diagnostics and discovery targets tolerate known missing hardware so they can be used mid-install, and both include DB source-column history for the required feedback columns.
MQTT discovery checks both the accepted candidate topics and a broader `greenhouse/sensor/#` near-match scan using the configured MQTT credentials; this catches MQTT-only center feedback sensors that are present but named differently than the accepted list.

The same output also prints the live `instrumentation_requirements`, `maintenance_log`, and `sensor_registry` records for the blocked feedback channels. Use those rows as the field work order: they distinguish inactive hardware-not-installed targets from active sensors that are present but unhealthy.

If a PR changes accepted feedback aliases in `ingestor/entity_map.py`, `ingestor/tasks.py`, or the legacy `scripts/ha-sensor-sync.py`, merge/deploy it before treating discovery as authoritative. The in-process HA and MQTT feedback mappings are loaded by `verdify-ingestor`, so the post-merge service bounce is:

```bash
sudo systemctl restart verdify-ingestor
```

Alias-only irrigation feedback changes do not require `verdify-mcp` to restart unless the PR also touches schemas or `mcp/server.py`. Do not run this restart from a dirty shared worktree that contains unrelated changes; use the normal validated deploy path, then rerun `make irrigation-feedback-discover` and `make irrigation-feedback-check`.

Final acceptance is a post-deploy proof, not a deploy target. Run it only after the branch is merged, any required services are restarted, the generated public site is live, and the Grafana dashboard artifacts being validated are the deployed artifacts.

For a concise repair/install checklist instead of full diagnostics:

```bash
make irrigation-feedback-work-order
```

This uses the same live DB, HA, MQTT, and ESPHome evidence but formats it as field actions and pass criteria for the south probe repair and center feedback install. It also includes DB source-column history for the six feedback columns, so the handoff shows whether a channel has lifetime samples, valid samples in the last 24 hours, and last sample/valid timestamps. It includes a discovery sweep for feedback-like HA entities, MQTT topics, and ESPHome objects so newly installed or misnamed center sensors are captured in the handoff. It writes the transcript to `/srv/verdify/state/irrigation-work-order.txt` by default so the field handoff is not only terminal output. Override the proof path with `IRRIGATION_WORK_ORDER_PROOF=/path/to/work-order.txt`.

When checking whether the gap is an unmapped source or truly missing hardware, use the DB history query below. As of 2026-05-22 07:39 UTC, the center columns have no lifetime samples; south probe 1 last had positive moisture on 2026-05-16 17:31 UTC and positive EC on 2026-05-16 16:00 UTC.

```bash
docker exec verdify-timescaledb psql -U verdify -d verdify -c "
SELECT max(ts) FILTER (WHERE moisture_center IS NOT NULL) AS last_center_moisture,
       max(ts) FILTER (WHERE ph_runoff_center IS NOT NULL) AS last_center_ph,
       max(ts) FILTER (WHERE ec_runoff_center IS NOT NULL) AS last_center_ec,
       max(ts) FILTER (WHERE soil_moisture_south_1 > 0) AS last_positive_south1_moisture,
       max(ts) FILTER (WHERE soil_ec_south_1 > 0) AS last_positive_south1_ec
  FROM climate
 WHERE greenhouse_id = 'vallery';"
```

MQTT output separates retained broker values from non-retained live updates. Treat `stale_retained_only=true` as historical broker state, not a healthy current sensor. A retained positive south value does not clear the gate unless the DB status row changes to `ok`.
If broad MQTT discovery lists stale retained near-miss soil topics for east, south-2, or west with no live updates, clear that diagnostic noise after confirming `live=-`:

```bash
make irrigation-feedback-clear-stale-near-misses CONFIRM_CLEAR_RETAINED=1
```

This only clears known non-accepted near-match soil topics and does not affect accepted feedback ingestion or the DB gate.

ESPHome discovery lists native controller entities by `object_id`. If center sensors are added in firmware, `make irrigation-feedback-discover` should show the expected center `object_id` before HA or DB ingestion can prove the full path.
For present ESPHome sensor entities, discovery also prints the native API state value and `missing_state` flag, so field repair can distinguish a controller-native zero from a Home Assistant translation issue.

The acceptance gate is:

```bash
make irrigation-feedback-check
```

The gate passes only when all rows in `v_irrigation_sensor_feedback_status` are `ok` and there are no open `irrigation_feedback_gap` alerts. The target includes DB source-column history by default so a failed gate preserves lifetime/24-hour sample context for the south probe and center feedback channels.

The full irrigation/fertigation completion audit is:

```bash
make irrigation-stack-check
```

While hardware is still pending, this proves the software/dashboard side without passing the physical gate:

```bash
make irrigation-stack-software-check
```

That target runs `make site-doctor` before the software audit, so it verifies the published irrigation page, Grafana iframe inventory, source dashboard branding, and live Grafana branding while the physical feedback gate remains blocked.

For unattended validation after hardware work:

```bash
make irrigation-feedback-watch
```

This watches only the physical feedback status rows. Once it exits 0, run the finalizer or the one-command acceptance target so the pre-existing feedback alerts are resolved.

For hands-on repair/install work, use the field watch instead:

```bash
make irrigation-feedback-watch-field IRRIGATION_FEEDBACK_TIMEOUT=900 IRRIGATION_FEEDBACK_INTERVAL=30
```

This prints the same DB gate plus HA, MQTT, and ESPHome evidence on each poll. Use it while reseating/replacing south probe 1 or adding center feedback hardware; south moisture/EC must become nonzero at ESPHome/HA/DB, and center moisture/pH/EC must appear in at least one accepted source and flow into the DB status rows.
The field watch delegates to `make irrigation-feedback-watch-field-proof`, which writes the transcript to `/srv/verdify/state/irrigation-field-watch-proof.txt` by default. The field-watch proof includes DB source-column history on each poll so the repair transcript preserves both live state and whether each channel had prior/lifetime valid samples. Override the proof path with `IRRIGATION_FIELD_WATCH_PROOF=/path/to/field-watch.txt`.

After the sensors are healthy, resolve any system-owned irrigation feedback alerts and run the feedback gate:

```bash
make irrigation-feedback-finalize-dry-run
make irrigation-feedback-finalize
make irrigation-feedback-proof-json
```

The standalone dry run uses the same feedback and alert preconditions but reports the planned closure counts without mutating rows. A successful dry run must include `expected_open_feedback_alerts_after_finalize=0`; otherwise do not run the mutating finalizer. During field diagnostics, `make irrigation-feedback-finalize-dry-run-proof` writes the non-mutating dry-run transcript to `/srv/verdify/state/irrigation-finalizer-dry-run-proof.txt` and exits 0 only for the known `Irrigation feedback still blocked: ... not_ok=` physical-blocker refusal; other finalizer safety failures still fail the target. Override with `IRRIGATION_FINALIZER_DRY_RUN_PROOF=/path/to/finalizer-dry-run-proof.txt`. The finalizer also refuses to run if either required `instrumentation_requirements` row or any required `sensor_registry` target row is missing. The `irrigation-feedback-finalize` target first records DB, HA, MQTT, and ESPHome feedback source evidence, then runs the dry run before the mutating finalizer, and writes the source-evidence, dry-run, mutation, and feedback-check transcript to `/srv/verdify/state/irrigation-finalizer-proof.txt` by default. Override the proof path with `IRRIGATION_FINALIZER_PROOF=/path/to/finalizer-proof.txt`. The finalizer then closes the software side of the field-work loop: it marks the two irrigation `instrumentation_requirements` rows `complete`, activates the validated `sensor_registry` targets, and writes one idempotent `maintenance_log` validation row per field-work item.
`make irrigation-feedback-proof-json` then emits the post-finalizer DB, HA, MQTT, and ESPHome evidence as machine-readable JSON, writes the same payload to `/srv/verdify/state/irrigation-feedback-proof.json` by default, and exits nonzero if any feedback alert is still open. Override the proof path with `IRRIGATION_FEEDBACK_PROOF=/path/to/proof.json`.

One-command final acceptance after hardware work:

```bash
make irrigation-acceptance
```

This runs the persisted field watch first via `make irrigation-feedback-watch-field-proof`, then persists the full discovery sweep via `make irrigation-feedback-discovery-proof`, then runs a fresh sensor-health proof via `make irrigation-sensor-health-proof`, then calls `make irrigation-feedback-finalize`, emits `make irrigation-feedback-proof-json`, runs the strict live stack proof via `make irrigation-stack-proof`, persists `make irrigation-completion-audit-proof`, and finally runs the strict completion audit via `make irrigation-completion-audit`. Final acceptance captures DB status plus HA, MQTT, ESPHome, ESP32/Modbus health, finalizer closure counts, and site/Grafana evidence before and after the dry-run/finalizer closure; the direct finalizer proof also captures DB, HA, MQTT, and ESPHome source evidence before its dry-run and mutation steps. The stack proof runs `make site-doctor` before the strict live stack audit so the site, iframe inventory, and source/live Grafana brand checks are still proven if the final stack audit stops on a later gate.
In short, final acceptance captures DB status plus HA, MQTT, ESPHome, and site/Grafana evidence in one command.

`make irrigation-field-sensor-health-proof` runs `make sensor-health SINCE='2 minutes'`, writes `/srv/verdify/state/irrigation-field-sensor-health-proof.txt`, and exits nonzero if the ESP32/Modbus health sweep fails. Override the proof path with `IRRIGATION_FIELD_SENSOR_HEALTH_PROOF=/path/to/field-sensor-health.txt`.

`make irrigation-feedback-work-order-proof` is the explicit field-checklist artifact target used by `make irrigation-field-diagnostics`; it writes `/srv/verdify/state/irrigation-work-order.txt` and exits 0 even while the physical gate is blocked. Field diagnostics runs `make irrigation-field-sensor-health-proof` first so the handoff preserves the short-window bus/ESP32 health evidence, then runs `make irrigation-completion-audit-proof` after the work-order proof so the current seven-item objective audit is refreshed alongside the field handoff. It then runs `make irrigation-feedback-discovery-proof`, which writes the full HA/MQTT/ESPHome discovery sweep to `/srv/verdify/state/irrigation-discovery-proof.txt` by default, and `make irrigation-feedback-finalize-dry-run-proof`, which persists the closure preflight refusal while the physical gate is blocked. Override the discovery proof path with `IRRIGATION_DISCOVERY_PROOF=/path/to/discovery-proof.txt`.

`make irrigation-feedback-watch-field-proof` is the strict field-watch artifact used by `make irrigation-acceptance`; it writes `/srv/verdify/state/irrigation-field-watch-proof.txt` and exits nonzero until all four physical feedback rows are healthy.

`make irrigation-sensor-health-proof` runs `make sensor-health SINCE='5 minutes'`, writes the transcript to `/srv/verdify/state/irrigation-sensor-health-proof.txt` by default, and exits nonzero if the ESP32/Modbus health sweep fails. Override the proof path with `IRRIGATION_SENSOR_HEALTH_PROOF=/path/to/sensor-health.txt`.

`make irrigation-stack-proof` runs `make site-doctor` and `/srv/greenhouse/.venv/bin/python scripts/validate-irrigation-stack.py --live-site`, writes the transcript to `/srv/verdify/state/irrigation-stack-proof.txt` by default, and exits nonzero if any strict live stack gate fails. Override the proof path with `IRRIGATION_STACK_PROOF=/path/to/stack-proof.txt`.

Full final proof for this irrigation objective:

```bash
make irrigation-post-deploy-acceptance-plan
make irrigation-post-deploy-acceptance
```

Run this after merge/deploy on the production host; it proves the deployed state, it does not deploy the state. `make irrigation-post-deploy-acceptance-plan` is a print-only preview and does not run checks, wait on sensors, or invoke the finalizer. `make irrigation-post-deploy-acceptance` is an explicit post-deploy alias for `make irrigation-full-acceptance`. It adds `make lint`, `make test`, and `make irrigation-migration-proof` before the same field watch, finalizer, persisted proof artifacts, and strict live stack audit sequence. `make irrigation-migration-proof` replays migration 134 inside a rollback transaction, writes the transcript to `/srv/verdify/state/irrigation-migration-proof.txt` by default, and exits nonzero if the migration no longer replays cleanly. Override the proof path with `IRRIGATION_MIGRATION_PROOF=/path/to/migration-proof.txt`.

## South Soil Probe 1

Authoritative entity IDs:

```text
sensor.greenhouse_south_1_soil_moisture
sensor.greenhouse_south_1_soil_temp_degf
sensor.greenhouse_south_1_soil_ec_ms_cm
```

Expected outcome:

- `sensor.greenhouse_south_1_soil_moisture` reports a credible nonzero value.
- `sensor.greenhouse_south_1_soil_ec_ms_cm` reports a credible nonzero value when the SEN0601 is in conductive media.
- The DB status row for `south_soil_probe_1` changes from `stuck_zero` to `ok`.

Physical checks:

- Run `make sensor-health SINCE='2 minutes'`. If address 7 has Modbus timeouts, troubleshoot the RS485 bus, power, address, and wiring first.
- If there are no address-7 timeouts, `soil_temp_south_1` changes, and nearby `soil_moisture_south_2` reports positive moisture while south_1 moisture and EC remain zero, treat shared ingestion and the Modbus bus as healthy. Inspect south_1 probe/media contact or replace the SEN0601.
- The same status row reports `last_positive_ts`, `soil_ec_south_1_last_positive_ts`, and `south_2_reference_last_positive_ts` in `details`; use those timestamps to separate recent physical failures from ingestion or firmware changes.
- If MQTT discovery shows positive retained `greenhouse/sensor/south_1_soil_*` or legacy `greenhouse/sensor/south_soil_*` values with no live non-retained updates, ignore them for acceptance. Those are stale retained broker messages, not current ESPHome API/DB readings.
- To remove those stale broker breadcrumbs after confirming `live=-` for the retained south topics, run:

  ```bash
  make irrigation-feedback-clear-stale-retained CONFIRM_CLEAR_RETAINED=1
  ```

  This only clears the known south-1/legacy south retained feedback topics. It uses the same configured MQTT credentials as discovery, does not change the DB gate, and does not replace the physical repair requirement.
- Confirm the RS485 address-7 SEN0601 is powered and still wired to the ESP32 Modbus chain.
- Confirm moisture and EC change after removing/reseating the probe or after an irrigation response.
- Replace the probe if temperature changes but moisture and EC stay at zero.

## Center Feedback Sensors

Accepted Home Assistant entity IDs:

```text
sensor.greenhouse_center_soil_moisture
sensor.greenhouse_center_root_zone_moisture
sensor.greenhouse_center_root_zone_soil_moisture
sensor.greenhouse_center_rootzone_moisture
sensor.greenhouse_center_moisture
sensor.greenhouse_center_vwc
sensor.greenhouse_center_substrate_vwc
sensor.greenhouse_center_substrate_moisture
sensor.greenhouse_center_root_zone_vwc
sensor.greenhouse_middle_substrate_vwc
sensor.greenhouse_middle_substrate_moisture
sensor.greenhouse_center_runoff_ph
sensor.greenhouse_center_runoff_p_h
sensor.greenhouse_center_run_off_ph
sensor.greenhouse_center_run_off_p_h
sensor.greenhouse_center_drain_ph
sensor.greenhouse_center_drain_p_h
sensor.greenhouse_center_drainage_ph
sensor.greenhouse_center_leachate_ph
sensor.greenhouse_center_effluent_ph
sensor.greenhouse_center_tray_ph
sensor.greenhouse_center_runoff_ec
sensor.greenhouse_center_runoff_ec_ms_cm
sensor.greenhouse_center_runoff_ec_us_cm
sensor.greenhouse_center_runoff_ec_u_s_cm
sensor.greenhouse_center_run_off_ec
sensor.greenhouse_center_run_off_ec_ms_cm
sensor.greenhouse_center_run_off_ec_us_cm
sensor.greenhouse_center_runoff_conductivity
sensor.greenhouse_center_runoff_electrical_conductivity
sensor.greenhouse_center_drain_ec
sensor.greenhouse_center_drain_ec_ms_cm
sensor.greenhouse_center_drain_ec_us_cm
sensor.greenhouse_center_drain_ec_u_s_cm
sensor.greenhouse_center_drainage_ec
sensor.greenhouse_center_leachate_ec
sensor.greenhouse_center_effluent_ec
sensor.greenhouse_center_tray_ec
```

Expected ingestion columns:

```text
moisture_center
ph_runoff_center
ec_runoff_center
```

Accepted MQTT topics, if the feedback sensors are installed outside Home Assistant:

```text
greenhouse/sensor/center_soil_moisture____/state
greenhouse/sensor/center_root_zone_moisture____/state
greenhouse/sensor/center_root_zone_soil_moisture____/state
greenhouse/sensor/center_rootzone_moisture____/state
greenhouse/sensor/center_moisture____/state
greenhouse/sensor/center_vwc/state
greenhouse/sensor/center_substrate_vwc/state
greenhouse/sensor/center_substrate_moisture/state
greenhouse/sensor/center_substrate_moisture____/state
greenhouse/sensor/center_root_zone_vwc/state
greenhouse/sensor/middle_substrate_vwc/state
greenhouse/sensor/middle_substrate_moisture/state
greenhouse/sensor/middle_substrate_moisture____/state
greenhouse/sensor/center_runoff_ph/state
greenhouse/sensor/center_runoff_p_h/state
greenhouse/sensor/center_run_off_ph/state
greenhouse/sensor/center_run_off_p_h/state
greenhouse/sensor/center_drain_ph/state
greenhouse/sensor/center_drain_p_h/state
greenhouse/sensor/center_drainage_ph/state
greenhouse/sensor/center_leachate_ph/state
greenhouse/sensor/center_effluent_ph/state
greenhouse/sensor/center_tray_ph/state
greenhouse/sensor/center_runoff_ec/state
greenhouse/sensor/center_runoff_ec_ms_cm/state
greenhouse/sensor/center_runoff_ec_us_cm/state
greenhouse/sensor/center_runoff_ec_u_s_cm/state
greenhouse/sensor/center_runoff_ec___s_cm_/state
greenhouse/sensor/center_runoff_ec____s___cm_/state
greenhouse/sensor/center_run_off_ec/state
greenhouse/sensor/center_run_off_ec_ms_cm/state
greenhouse/sensor/center_run_off_ec_us_cm/state
greenhouse/sensor/center_run_off_ec_u_s_cm/state
greenhouse/sensor/center_run_off_ec___s_cm_/state
greenhouse/sensor/center_run_off_ec____s___cm_/state
greenhouse/sensor/center_runoff_conductivity/state
greenhouse/sensor/center_runoff_electrical_conductivity/state
greenhouse/sensor/center_drain_ec/state
greenhouse/sensor/center_drain_ec_ms_cm/state
greenhouse/sensor/center_drain_ec_us_cm/state
greenhouse/sensor/center_drain_ec_u_s_cm/state
greenhouse/sensor/center_drain_ec___s_cm_/state
greenhouse/sensor/center_drain_ec____s___cm_/state
greenhouse/sensor/center_drainage_ec/state
greenhouse/sensor/center_leachate_ec/state
greenhouse/sensor/center_effluent_ec/state
greenhouse/sensor/center_tray_ec/state
```

The ingestor ignores retained MQTT feedback messages. After installing MQTT-only feedback hardware, `make irrigation-feedback-discover` must show a live value, and the corresponding DB status row must change to `ok`.
MQTT and HA feedback ingestion reject non-finite or out-of-range values before DB write: moisture must be 0-100%, pH must be 0-14, and EC must be nonnegative. The DB feedback gate uses the same valid-range semantics, so an impossible reading remains `invalid`, `missing`, or `stale` rather than satisfying acceptance.

Accepted ESPHome object IDs, if center feedback is added to the greenhouse controller:

```text
center_soil_moisture____
center_root_zone_moisture____
center_root_zone_soil_moisture____
center_rootzone_moisture____
center_moisture____
center_vwc
center_substrate_vwc
center_substrate_moisture
center_substrate_moisture____
center_root_zone_vwc
middle_substrate_vwc
middle_substrate_moisture
middle_substrate_moisture____
center_runoff_ph
center_runoff_p_h
center_run_off_ph
center_run_off_p_h
center_drain_ph
center_drain_p_h
center_drainage_ph
center_leachate_ph
center_effluent_ph
center_tray_ph
center_runoff_ec
center_runoff_ec_ms_cm
center_runoff_ec_us_cm
center_runoff_ec_u_s_cm
center_runoff_ec___s_cm_
center_runoff_ec____s___cm_
center_run_off_ec
center_run_off_ec_ms_cm
center_run_off_ec_us_cm
center_run_off_ec_u_s_cm
center_run_off_ec___s_cm_
center_run_off_ec____s___cm_
center_runoff_conductivity
center_runoff_electrical_conductivity
center_drain_ec
center_drain_ec_ms_cm
center_drain_ec_us_cm
center_drain_ec_u_s_cm
center_drain_ec___s_cm_
center_drain_ec____s___cm_
center_drainage_ec
center_leachate_ec
center_effluent_ec
center_tray_ec
```

`middle_substrate_vwc` and substrate-moisture aliases are accepted as center root-zone moisture aliases because they are the same 0-100% moisture signal expected by `moisture_center`. Center drain, drainage, leachate, effluent, and tray pH/EC aliases are accepted as runoff feedback equivalents. Drain/runoff TDS remains discovery-only; do not map TDS/ppm into `ec_runoff_center` without an explicit conversion/calibration step. Hydroponic/reservoir pH, EC, and TDS are also discovery-only near-misses; they describe reservoir chemistry, not center runoff.

Expected outcome:

- One center moisture entity exists and updates within the freshness window.
- One center runoff pH entity exists and updates within the freshness window.
- One center runoff EC entity exists and updates within the freshness window.
- The DB status rows for `center_root_zone_moisture`, `center_runoff_ph`, and `center_runoff_ec` change from `missing` to `ok`.

## Firmware Boundary

The current ESP32 Modbus soil definitions cover south_1, south_2, and west only. Center feedback is currently prepared through Home Assistant ingestion. Adding center probes directly to the ESP32 Modbus chain requires a firmware PR and the firmware freeze gates, including replay diff, invariants, firmware tests, and OTA validation.

## Final Proof

After physical work and at least one ingestion cycle:

```bash
make irrigation-feedback-discover
make irrigation-field-diagnostics
make irrigation-feedback-work-order-proof
make irrigation-feedback-discovery-proof
make irrigation-feedback-watch-field-proof
make irrigation-feedback-finalize
make irrigation-feedback-proof-json
make irrigation-feedback-check
make irrigation-migration-proof
make irrigation-stack-check
make irrigation-stack-proof
make irrigation-completion-audit-proof
make site-doctor
make irrigation-acceptance
make irrigation-full-acceptance
docker exec verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT feedback_key,status,latest_value,last_sample_ts FROM v_irrigation_sensor_feedback_status ORDER BY feedback_key;"
docker exec verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT sensor_id,severity,disposition FROM alert_log WHERE alert_type='irrigation_feedback_gap' AND resolved_at IS NULL ORDER BY sensor_id;"
```

Completion requires `make irrigation-feedback-check` to exit 0, every status row to be `ok`, and the open-alert query to return no rows. `make irrigation-feedback-finalize` also verifies that the live `v_irrigation_sensor_feedback_status` definition still contains the valid-range gate before it marks requirements complete or resolves feedback alerts.

`make irrigation-feedback-work-order-proof` writes the current field handoff to `/srv/verdify/state/irrigation-work-order.txt`. That proof includes the valid-value gate, DB source-column history, live DB/HA/MQTT/ESPHome evidence, and the `instrumentation_requirements` plus `sensor_registry` rows that must close after the repair/install.
`make irrigation-field-sensor-health-proof` writes the current 2-minute ESP32/Modbus health sweep to `/srv/verdify/state/irrigation-field-sensor-health-proof.txt`. That proof distinguishes bus/controller faults from a south-1 probe/media failure during the field repair.
`make irrigation-feedback-discovery-proof` writes the full discovery sweep to `/srv/verdify/state/irrigation-discovery-proof.txt`. That proof is non-gating for known missing hardware but persists DB source-column history plus the HA entities, MQTT topics, and ESPHome object IDs seen during field diagnostics.
`make irrigation-feedback-finalize-dry-run-proof` writes the non-mutating finalizer preflight to `/srv/verdify/state/irrigation-finalizer-dry-run-proof.txt`. Before hardware repair it should show the physical-blocker refusal; after hardware repair it should show `expected_open_feedback_alerts_after_finalize=0`.
`make irrigation-feedback-watch-field-proof` writes the final repair/install watch transcript to `/srv/verdify/state/irrigation-field-watch-proof.txt`. That proof must show all four physical feedback rows healthy before the finalizer and JSON feedback proof are allowed to run, and it includes the DB source-column history used to distinguish repaired channels from newly installed channels.
`make irrigation-feedback-finalize` writes the DB source-column history, dry-run, mutating finalizer, and post-finalizer feedback-check transcript to `/srv/verdify/state/irrigation-finalizer-proof.txt`. That proof must show `expected_open_feedback_alerts_after_finalize=0`, the closed alert count, registry activation count, and validation-log count.
`make irrigation-completion-audit-proof` writes `/srv/verdify/state/irrigation-completion-audit.json`, mapping the original seven objective items to current evidence and blockers. Its feedback item includes DB status, DB source-column history, open alerts, field requirements, registry targets, accepted HA/MQTT/ESPHome source evidence, and discovered HA/MQTT/ESPHome near-misses for the south probe plus center feedback channels. The proof target exits 0 only when the stack is complete or item 5 is the sole blocked objective; software, site, dashboard, schema, or accounting regressions still fail the proof target. Final acceptance persists that JSON proof after the live stack proof, then runs the strict completion audit.
