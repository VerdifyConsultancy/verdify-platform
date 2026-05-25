# Firmware V2 Post-Deploy Validation and Reboot Forensics - 2026-04-27

This note captures the first post-deploy readout for the v2 greenhouse controller and the read-only reboot forensics run after the directory/build cleanup. All timestamps are UTC unless noted.

## Deployment Boundary

- Behavior-changing firmware: `2026.4.27.2009.2b5f2a5`
- Behavior-changing OTA diagnostic boundary: `2026-04-28 02:12:25.045923+00`
- Latest live firmware after wrapper-only redeploy: `2026.4.27.2040.c1a6403`
- Latest live firmware diagnostic boundary: `2026-04-28 02:41:54.144875+00`
- Git source alignment: current `origin/main` advanced after the behavior-changing deploy. The only firmware-adjacent delta from `2b5f2a5..origin/main` is the executable-bit fix on `scripts/firmware-esphome-worktree.sh`; there are no C++/YAML control-logic diffs. The running binary is behavior-aligned with current main; its embedded SHA is the wrapper-fix commit.
- ESPHome deploy path now compiles directly from the active git worktree through `scripts/firmware-esphome-worktree.sh`; `/srv/greenhouse/esphome` is no longer a symlink farm.

## Live Health Snapshot

Collected after the wrapper-only redeploy at `2026-04-28 02:49:01+00`.

| Metric | Value |
|---|---:|
| Controller state | `IDLE` |
| Temp avg | `60.2 F` |
| RH avg | `82.0%` |
| VPD avg | `0.32 kPa` |
| WiFi RSSI | `-55 dBm` |
| Heap | `42.00 KB` |
| Uptime | `422 s` |
| Open critical/high alerts | `0` |
| Open warning alerts | `0` |

During the wrapper-only redeploy, `14` transient `setpoint_unconfirmed` warnings opened even though their details already showed matching `last_cfg_readback` values. They auto-resolved by `2026-04-28 02:48:40+00`; the current open-alert count is back to `0`.

Sensor-health at `2026-04-27 20:48 MDT`:

```text
PASS: 27  FAIL: 0  WARN: 0
firmware_version = 2026.4.27.2040.c1a6403
reset_reason = Software reset
active probes = 4/4
No new sensor_offline / esp32_reboot / push / band alerts opened in 15 minutes
```

Active setpoint plan readback:

| Parameter | Value | Active Since |
|---|---:|---|
| `temp_low` | `55` | `2026-04-18 07:30:28+00` |
| `temp_high` | `80` | `2026-04-25 22:34:37+00` |
| `vpd_high` | `1.8` | `2026-04-25 22:34:37+00` |

There is no current `vpd_low` row in `setpoint_plan`; firmware fallback/safety behavior still governs the low-VPD side.

## Post-OTA Stability

Diagnostics after the behavior-changing `2b5f2a5` OTA and before the wrapper-only `c1a6403` redeploy:

| Metric | Value |
|---|---:|
| Diagnostic rows | `30` |
| First row | `2026-04-28 02:12:25+00` |
| Last row in sample | `2026-04-28 02:40:54+00` |
| Uptime range | `61.4 s` to `1741.5 s` |
| Unexpected resets | `0` |

Diagnostics after the wrapper-only `c1a6403` redeploy:

| Metric | Value |
|---|---:|
| Diagnostic rows | `8` |
| First row | `2026-04-28 02:41:54+00` |
| Last row in sample | `2026-04-28 02:48:55+00` |
| Uptime range | `1.8 s` to `421.8 s` |
| Unexpected resets | `0` |

Definition used here: an unexpected reboot event is an uptime drop greater than 120 seconds with `reset_reason in ('Guru/Panic', 'Task WDT')`.

## Reboot Forensics

The old backlog item called this a "midday crash-loop." The current evidence says that label is too narrow. Heat and high VPD can contribute, but the reset distribution is not only a hot-midday actuator problem.

### 60-Day Reboot Events

Using the uptime-drop detector over diagnostics:

| Reset reason | Events | First | Last |
|---|---:|---|---|
| `Software reset` | `391` | `2026-03-23 04:51:19+00` | `2026-04-28 02:12:25+00` |
| `Guru/Panic` | `154` | `2026-03-23 14:19:26+00` | `2026-04-27 20:53:41+00` |
| `Task WDT` | `127` | `2026-03-26 16:38:11+00` | `2026-04-27 19:44:11+00` |
| unknown | `21` | `2026-03-10 16:58:00+00` | `2026-03-22 17:28:00+00` |
| `Power-on` | `3` | `2026-03-28 23:33:29+00` | `2026-04-08 00:08:09+00` |

Top 60-day local-hour buckets for unexpected resets:

| Local hour | Reset reason | Events |
|---:|---|---:|
| `08` | `Guru/Panic` | `42` |
| `09` | `Guru/Panic` | `26` |
| `01` | `Task WDT` | `13` |
| `10` | `Guru/Panic` | `10` |
| `13` | `Task WDT` | `10` |
| `00` | `Task WDT` | `9` |

### 14-Day Unexpected Events

Unexpected events in the last 14 days:

| Reset reason | Events | First | Last |
|---|---:|---|---|
| `Task WDT` | `106` | `2026-04-15 01:49:58+00` | `2026-04-27 19:44:11+00` |
| `Guru/Panic` | `37` | `2026-04-15 23:23:00+00` | `2026-04-27 20:53:41+00` |

Recent local-day concentration:

| Local date | Guru/Panic | Task WDT |
|---|---:|---:|
| `2026-04-27` | `9` | `22` |
| `2026-04-26` | `7` | `14` |
| `2026-04-25` | `8` | `35` |
| `2026-04-24` | `2` | `3` |
| `2026-04-21` | `5` | `22` |

Environment immediately before those 143 events:

| Reset reason | Events | Avg temp F | Max temp F | Avg VPD | Max VPD | Avg RH % | Avg prior heap KB | Min prior heap KB | Avg RSSI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `Guru/Panic` | `37` | `69.8` | `96.6` | `0.99` | `4.83` | `66.4` | `40.4` | `4.6` | `-51.0` |
| `Task WDT` | `106` | `70.4` | `97.5` | `1.14` | `5.13` | `64.9` | `41.1` | `24.6` | `-51.5` |

Envelope counts across the same 143 events:

- `29` occurred with `temp_avg > 80 F`.
- `9` occurred with `temp_avg > 90 F`.
- `22` occurred with `vpd_avg > 1.8 kPa`.
- `9` occurred with `vpd_avg > 3.0 kPa`.
- `32` had at least one `override_events` row within the prior 10 minutes.

## Crash-Window Evidence

The clearest recent `Guru/Panic` window was `2026-04-27 20:48 UTC`.

Before the reset, the ESP32 was ventilating at about `81.2 F` and `2.57 kPa` VPD with Tempest solar around `950 W/m2`. The final pre-crash error was:

```text
2026-04-27 20:48:04.995363+00 [E][json:064]: Parse error: IncompleteInput
```

On the next boot, ESPHome reported:

```text
*** CRASH DETECTED ON PREVIOUS BOOT ***
Reason: Fault - IllegalInstruction
PC: 0x40088364
api took a long time for an operation (82 ms), max is 30 ms
Average sensors NaN - using case probe (77.7 F)
```

The log emitted an `addr2line` command, but this crash predates the final deployed build. If the exact matching `firmware.elf` for that older firmware is not retained, symbolization may be unreliable.

## Interpretation

What is working:

- The latest v2 firmware build is running, reporting current version, and passing the sensor-health sweep.
- Critical/high alerts are clean after the alert lifecycle fixes and deploy-path cleanup.
- The controller is staying inside the broad active temp band at the current night condition, with all four probes active.
- There have been no `Guru/Panic` or `Task WDT` resets since the final OTA boundary in the sampled window.

What is still unresolved:

- The historical reset problem is real, but it is not explained by midday heat alone.
- The strongest concrete crash clue is an ESPHome JSON parse failure followed by an illegal-instruction crash, plus slow API work and one NaN fallback.
- Heap exhaustion is plausible for some panics because the 14-day minimum prior heap for `Guru/Panic` was `4.6 KB`, but most `Task WDT` events had much healthier prior heap.
- The active plan currently has no `vpd_low` row, so low-VPD behavior is still partly firmware-default-driven rather than fully planner-explicit.
- `setpoint_unconfirmed` warnings briefly opened after the wrapper-only redeploy even though `last_cfg_readback` matched the requested value; they did auto-resolve on the next monitoring pass.

## Recommended Next Work

1. Continue the 48-hour bake with no OTA unless a critical operational issue appears.
2. Add crash artifact retention: every OTA should retain `firmware.elf`, `firmware.bin`, build commit, and `addr2line` command under a versioned artifact path.
3. Add a crash-symbolization runbook or script that maps recent ESP32 crash logs to the retained ELF for the firmware version in diagnostics.
4. Instrument JSON/API failure bursts as first-class observability: count `json:064` parse errors, slow API operations, and crash-log detections into DB-visible diagnostics or alert rows.
5. Tighten the `setpoint_unconfirmed` warning lifecycle if the transient matching-readback warnings recur after future redeploys.
6. Make `vpd_low` explicit in the active planner/setpoint contract or document why firmware fallback owns that boundary.
7. After the bake, start the next firmware PR with replay-backed diagnostics and normal OTA evidence; do not add a runtime alternate controller path.
