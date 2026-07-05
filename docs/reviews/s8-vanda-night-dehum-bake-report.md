# S8 vanda-night-dehum — bake report (LIVE DOCUMENT)

Wave `wave-s8-night-dehum` activation evidence. Canonical night window: **02:00–06:00
local (08:00–12:00Z)**, `climate` table, greenhouse `vallery`. Durability gate applies:
every claim carries probe timestamps; canaries re-probed ≥60 min apart.

## Configuration states (recorded per RELEASE-CHECKLIST §B)

| State | Value | Since | Evidence |
|---|---|---|---|
| Firmware | `2026.7.3.1931.ab18fe8` (flag-OFF build, #418) | 2026-07-04T01:33:20Z | diagnostics.firmware_version; sensor-health 27/0/0 |
| `band_track_fraction` | **0.0 (float, ADR-0004)** | 01:33Z reboot (g-377: accept float, joint #377 trial) | setpoint_snapshot continuous |
| `sw_dehum_vent_hold_enabled` | **1 (ON)** — flipped via setpoint_changes INSERT (critic-F procedure) under Jason's early-activation directive | 2026-07-04T14:49:07Z (RT push <1s; snapshot 0->1 @14:49:37Z) | ingestor log + setpoint_snapshot |
| Night band anchors | 60.71 / 65.71 / 70.71 °F, vpd_target 0.83 (migration 188, g-411 dry-roots) | 2026-07-03T23:00Z | prod re-probes 23:00Z + 02:38Z identical |
| Envelope | **door screen-window OPEN** (#412; open since ~06-19, until fall; NEVER change mid-bake) | — | Jason 2026-07-03 |
| Known bake-floor caveat | #424 RESOLVED as view artifact — device is CORRECT and enforces the control envelope (`fn_house_vpd_control_band`: night floor ≈0.5 kPa, not the crop curve ≈0.74 the design assumed). CONSEQUENCE: hold episodes arm only on quite-wet excursions; a ~0.70 night may see few/none. Tuning lever if under-delivery: `night_vpd_bias_kpa` (planner-pushable 0–0.25, no OTA). View fix = migration 189 (queued) | 2026-07-04 trace | #424 comment |

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

**ACTIVATED 2026-07-04T14:49Z** (chain: #425 -> #426 -> sync -> bounces -> INSERT flip; estimator
alive within minutes). Reboot caveat: `restore_value:no` — any device reboot reverts OFF and
nothing re-pushes; re-check readback after reboots. Canary sentinels: night auto-flag-off cron
(night_min<64F / vent>10 cycles / heat2 breach) + daily 08:23 report cron.

| night (02-06h local) | med VPD | RH | night_min | DIF | vent cycles | heat1 | hold episodes | verdict |
|---|---|---|---|---|---|---|---|---|
| 2026-07-05 (night 1) | **0.723** | 66.4% | **65.6 °F** ✓ | **20.6 °F** ✓ | 2 ✓ | 10.7% (heat2 0 ✓) | 20 co-run rows / 311 `vent_plus_heat_hold` decisions | **ALL CANARIES PASS** |

Night-1 notes: the hold engaged in the final ~40 min of the window (first-ever episodes) and
drove VPD to 0.77–0.80 immediately after 06:00 with temp RISING under reheat (67.2→67.6 °F) —
the in-window median 0.723 understates the settled behavior. Trend: 0.618 baseline → 0.700
ref → 0.723 night-1 (late engage). heat1 cost of the hold: 10.7% duty (~26 min; electric).
night_min dipped to 65.6 during the first episodes then recovered — the duty-cycle+floor
design behaving as specified.

## Verdict

_(pending 48h of flag-ON nights + re-probe)_
