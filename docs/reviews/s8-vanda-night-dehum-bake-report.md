# S8 vanda-night-dehum — bake report (LIVE DOCUMENT)

Wave `wave-s8-night-dehum` activation evidence. Canonical night window: **02:00–06:00
local (08:00–12:00Z)**, `climate` table, greenhouse `vallery`. Durability gate applies:
every claim carries probe timestamps; canaries re-probed ≥60 min apart.

## Configuration states (recorded per RELEASE-CHECKLIST §B)

| State | Value | Since | Evidence |
|---|---|---|---|
| Firmware | `2026.7.3.1931.ab18fe8` (flag-OFF build, #418) | 2026-07-04T01:33:20Z | diagnostics.firmware_version; sensor-health 27/0/0 |
| `band_track_fraction` | **0.0 (float, ADR-0004)** | 01:33Z reboot (g-377: accept float, joint #377 trial) | setpoint_snapshot continuous |
| `sw_dehum_vent_hold_enabled` | **0 (OFF)** — flip pending PR #425 promote (Jason directed early activation 2026-07-04, superseding the 48h soak; freeze rules exempt tunable pushes) | — | cfg readback 0.0 via wire route |
| Night band anchors | 60.71 / 65.71 / 70.71 °F, vpd_target 0.83 (migration 188, g-411 dry-roots) | 2026-07-03T23:00Z | prod re-probes 23:00Z + 02:38Z identical |
| Envelope | **door screen-window OPEN** (#412; open since ~06-19, until fall; NEVER change mid-bake) | — | Jason 2026-07-03 |
| Known bake-floor caveat | #424: device vpd_low/high ≈0.3/0.2 kPa BELOW served curve (pre-existing; dehum entries arm later than intended) | under investigation | v_band_device_divergence |

## Baselines

### 21-day pre-S8 baseline (02–06h, to 2026-07-03)
median VPD **0.618** · RH **72.6%** · house ~66.7 °F · outdoor 61.1 °F ·
priority_axis=vpd 61.8% · DEHUM_VENT 2.5% (old band: target 62, pinch 0.25, pre-telemetry)

### Reference night 2026-07-04 (NEW config, flag OFF) — the soak baseline
- median VPD **0.700** · RH **68.8%** · night_min **66.5 °F** · mean 67.8 °F · outdoor 63.2 °F (n=234)
- DIF **19.4 °F** (day max 85.9)
- Actions: IDLE 92%, HEAT 29, DEHUM_VENT 28 (no heat1 co-run — flag-OFF proof), VENT_COOL 22
- Actuators: vent 3 open-cycles / 5.7% duty · heat1 3.0% · **heat2 0%**
- Attribution note: the 0.618→0.700 move is the raised corridor + float + a warm dry
  outdoor night — NOT the hold path (flag was OFF). The hold path's target is the
  remaining gap to ≥0.78.

## Canary thresholds (blocking per wave plan)
`night_min ≥ 64 °F` (breach ⇒ immediate flag-off) · vent ≤ ~10 open-cycles/night ·
heat2 never without heat1 · 0 critical alerts · watch: heat1 runtime delta, re-entry-gap
idle dwell, morning hold episodes, mx JSON truncation.

## Flag-ON nights (to be appended)

_(pending: flip via `set_tunable('sw_dehum_vent_hold_enabled', 1, ...)` after PR #425
promote + bounces; then nightly rows here with median VPD / RH / night_min / DIF /
vent cycles / heat1 duty / mx_reason episode counts / canary verdicts)_

## Verdict

_(pending 48h of flag-ON nights + re-probe)_
