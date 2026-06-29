# Greenhouse Recording Quiet Mode

Recording quiet mode is a timed operator overlay for keeping the greenhouse as quiet as practical while someone is recording inside or nearby.

It is not a firmware safety override and it does not flash the ESP32. The dispatcher writes a manual overlay through the normal `setpoint_changes` path, then restores the captured pre-quiet setpoints when the timer expires or when the operator disables it.

## Commands

```bash
make greenhouse-quiet-on QUIET_MINUTES=30
make greenhouse-quiet-status
make greenhouse-quiet-off
```

`QUIET_MINUTES` defaults to 30 and is capped at 240 by the command-line tool.

If the recording also needs the grow lights turned off:

```bash
python3 scripts/greenhouse-quiet-mode.py enable --minutes 30 --lights-off
```

## What It Suppresses

- Routine fan/vent cycling by temporarily widening the normal temperature band.
- Routine mist/fog behavior by temporarily widening the VPD band and delaying moisture escalation.
- Grow-light automation by turning off `sw_gl_auto_mode`. Current light state is left unchanged unless `--lights-off` is passed.
- Irrigation by turning off the wall and center irrigation switches.

## What Still Runs

Firmware safety rails still run. If the greenhouse hits hard safety heat or cooling conditions, the ESP32 can still start protective equipment. Quiet mode is meant for recording convenience, not plant-risk acceptance.

## State

The tool stores state in `system_state`:

- `recording_quiet_mode`
- `recording_quiet_until`
- `recording_quiet_restore`
- `recording_quiet_reason`

The ingestor dispatcher checks those rows every cycle. While active, it keeps the quiet overlay from being undone by normal planner or crop-band refreshes. When expired, it restores the captured values once and marks the mode `expired_restored`.

The compatibility `/setpoints` endpoints also apply the quiet overlay while the mode is active. Current production firmware receives quiet-mode changes through direct ESPHome pushes and readbacks, but the compatibility surface stays aligned for diagnostics and recovery.
