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
- The drift guards that assert firmware ↔ schema alignment (shared `verdify_schemas/` — route to coordinator)

## Handshakes

| With agent | When | Protocol |
|---|---|---|
| `ingestor` | Adding a new sensor or override flag that needs a DB column | Schema + migration via coordinator first, then firmware emits, then ingestor reads |
| `genai` | Changing which tunables the planner controls | Planner agent owns the tunables list (`verdify_schemas/tunables.py`); firmware reads it — don't add tunables unilaterally |
| `coordinator` | Any change to `ALL_TUNABLES`, `EquipmentId`, `override_events` shape | Coordinator merges the schema change; firmware PR lands after |

## Gates

**Replay is a permanent gate for firmware changes.** Any structural change to `greenhouse_logic.h` must pass `firmware/test/test_greenhouse_logic.cpp` *and* replay against 8 months of real telemetry. Use `make test-firmware`, `make firmware-invariants`, and `make firmware-replay-worktree` while the candidate is still uncommitted; use `make firmware-replay OLD=<ref> NEW=<ref>` once both sides are committed refs. See `CLAUDE.md` at repo root for the deploy protocol and OTA freeze rules.

## Ask coordinator before

- Adding an override flag that isn't already in `firmware/lib/greenhouse_types.h`
- Changing the band-first state machine's transition logic (physics invariant territory)
- Bumping firmware version in a way that requires ingestor / planner code changes
- Touching `controls.yaml` in ways that rename or remove entities (entity_map.py depends on these)

## Recent arc

The pre-agent-org operational stream (Sprints 15–23) retires with the agent split (see `CLAUDE.md`). Highlights of work now visible in the code:

- ESP32 reboot resilience and global-validation on boot (`greenhouse.yaml` `on_boot`)
- OBS-1e silent-override event emission (`evaluate_overrides()` in `greenhouse_logic.h`, `gh_overrides` text sensor, `override_events` DB table)
- Sensor fault resilience, per-probe staleness exclusion (`average_*` lambdas in `sensors.yaml`), OTA auto-rollback via `make firmware-deploy`
- OBS-3 control-state exposure: `ctl_relief_cycle_count`, `ctl_vent_latch_timer_s`
- 8-month historical replay gate (`make test-firmware`)

Sprint counter starts fresh under this agent. See `docs/backlog/firmware.md` for the current queue.


## 2026-06-09 replan — control-optimization + landing rules

**Active control program.** Epic #287 (Greenhouse Control Optimization, req A–E) and its children #289–#300 are now this agent's active control program. It rolls up under #286; deploy enablement is the sibling epic #288 (#301–#307). All tracked on Org Project "Iris / Verdify" #1. Much of req B/C/D (dispatcher/registry/DB-policy) ships WITHOUT OTA — only the firmware halves (button fix #289, dusk-cutoff firmware #292) need the compile→bake→OTA path.

**Recurring fan-button bug (#289).** Operator (Jason) reports the R4 fan button press FAILED 2026-06-08 PM and that this happens REGULARLY — recurring, not a one-off. Primary cause is the min-off dwell (H2): fans are applied with `force_on=false` at `controls.yaml` ~808–809, so a press within ~90s of a fan cycle-off is silently swallowed; secondary is a habitual double-press toggle-cancel (H1). Fix direction: pass `force_on=true` for fans/vent during the manual window, mirroring the fog micropulse. This proceeds WITHOUT waiting on exact-timestamp telemetry — the DB pull is now confirmation, not a blocker.

**PR-only landing rule (load-bearing).** Changes to `firmware/**`, `verdify_schemas/**`, `ingestor/entity_map.py`, and `mcp/server.py` MUST land via a `firmware/*` PR — never direct-to-live. The `firmware-replay-diff` and `no-new-fire-and-forget` CI gates run ONLY on `pull_request`, so a direct push bypasses them entirely. This is how the `e7781a3` fire-and-forget tunables slipped in. Docs-only changes may go direct.

**OTA sealing is backlog, not a blocker (#301).** OTA password sealing is tracked BACKLOG item #301 (under deploy-enablement epic #288), not an active blocker. Turnkey SealedSecret-shape artifacts + runbook are in PR #309 (→ `live/platform-main`). Reframe: #301 is a tracked prerequisite for the firmware-OTA subset only (#289 button fix, #292 dusk-cutoff), not for the dispatcher/registry/DB-policy work.
