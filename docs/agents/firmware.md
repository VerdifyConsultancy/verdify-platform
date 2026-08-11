# Agent: `firmware`

ESP32 controller code, build pipeline, replay validation, OTA, sensor health.

## Owns

- `firmware/**/*.h`, `firmware/**/*.cpp`, `firmware/**/*.yaml`, `firmware/test/**`
- Firmware build + compile (`make firmware-check`)
- Replay corpus validation (`firmware/test/test_greenhouse_logic.cpp` + golden CSV)
- OTA auto-rollback (Sprint 17 work)
- Firmware versioning, heating diagnostics, override flag emission
- Sensor staleness exclusion + probe health

## Does not own

- The DB tables firmware writes to (that's `ingestor` — ESP32 → ingestor → DB)
- The planner tunables firmware applies (that's `genai` — planner emits, firmware enforces)
- The drift guards that assert firmware ↔ schema alignment (shared
  `verdify_schemas/`; update both sides together when required)

## Handshakes

| With agent | When | Protocol |
|---|---|---|
| `ingestor` | Adding a new sensor or override flag that needs a DB column | Land schema + migration first, then firmware emits, then ingestor reads |
| `genai` | Changing which tunables the planner controls | Planner agent owns the tunables list (`verdify_schemas/tunables.py`); firmware reads it — don't add tunables unilaterally |
| shared schemas | Any change to `ALL_TUNABLES`, `EquipmentId`, or `override_events` | Update and validate the schema and firmware consumers in one compatible sequence |

## Required checks

Any structural change to `greenhouse_logic.h` must pass
`firmware/test/test_greenhouse_logic.cpp` and replay against 8 months of real
telemetry. Use `make test-firmware`, `make firmware-invariants`, and
`make firmware-replay-worktree` while the candidate is still uncommitted; use
`make firmware-replay OLD=<ref> NEW=<ref>` once both sides are committed refs.
See `CLAUDE.md` for the deploy protocol and OTA safety rules.

## Cross-component checks

- Add new override flags to `firmware/lib/greenhouse_types.h` and every consumer.
- Prove state-machine transition changes with physics invariants and replay.
- Update ingestor and planner consumers with firmware-version contract changes.
- Update `entity_map.py` with any `controls.yaml` entity rename or removal.

## Recent arc

The pre-agent-org operational stream (Sprints 15–23) retires with the agent split (see `CLAUDE.md`). Highlights of work now visible in the code:

- ESP32 reboot resilience and global-validation on boot (`greenhouse.yaml` `on_boot`)
- OBS-1e silent-override event emission (`evaluate_overrides()` in `greenhouse_logic.h`, `gh_overrides` text sensor, `override_events` DB table)
- Sensor fault resilience, per-probe staleness exclusion (`average_*` lambdas in `sensors.yaml`), OTA auto-rollback via `make firmware-deploy`
- OBS-3 control-state exposure: `ctl_relief_cycle_count`, `ctl_vent_latch_timer_s`
- 8-month historical replay gate (`make test-firmware`)

Sprint counters are retired. Use GitHub issues on
`VerdifyConsultancy/verdify-platform` for the current queue; the old
`docs/backlog/firmware.md` file is archived in
`/Users/jason/Orbit/context_dump/verdify-platform/`.
