# Firmware AI Tunable Audit - 2026-05-24

This audit traces the AI-first tunable strategy from the last week of work
through the current candidate implementation.

## Candidate refs

- Platform candidate: `codex/firmware-ai-moisture-stress`
- Platform base/live local main: `e349e5e`
- Planner companion branch: `VerdifyConsultancy/verdify-planner`
  `codex/ai-tunable-contract-2026-05-24`

## Last-week delta reviewed

Relevant platform commits since 2026-05-17 cover:

- setpoint dispatch retry/heap recovery and registry-derived route cleanup;
- direct-wet gate and relay inventory hardening;
- OTA freeze/preflight guardrails;
- planner trigger ledger, setpoint lifecycle, and active-plan range guards;
- public lab plan publishing repair for 2026-05-23;
- AI-tunable firmware design docs;
- PR1/PR2 cooling policy tunables and diagnostics;
- PR3 direct-wet/fog stress override tunables.

Unrelated generated vault/site history still contains old `d_cool_stage_2`
plan prose because those pages are historical evidence. Current generated
traceability marks that parameter retired.

## Contract trace

The executable contract is registry-first:

1. `verdify_schemas/tunable_registry.py` defines every canonical parameter,
   owner, default, range, ESPHome object id, cfg readback id, tier, and
   planner writeability.
2. `verdify_schemas/tunables.py` derives legacy Pydantic tunable sets from the
   registry.
3. `mcp/server.py` derives `PLAN_REQUIRED_PARAMS`, `TIER1_TUNABLES`, and the
   planner allowlist from registry views.
4. `ingestor/entity_map.py`, `api/main.py`, and `scripts/setpoint-server.py`
   derive dispatcher/API/fallback setpoint routes from registry views.
5. Firmware exposes matching HA controls in `firmware/greenhouse/tunables.yaml`
   and cfg readbacks in `firmware/greenhouse/sensors.yaml`.
6. `firmware/lib/greenhouse_types.h` validates/clamps values, and
   `firmware/lib/greenhouse_logic.h` consumes the executable subset.
7. Planner/site context is generated from the same registry through
   `scripts/generate-ai-tunables-page.py` and `scripts/gather-plan-context.sh`.

## Findings fixed during audit

- The standalone `verdify-planner` repo still mirrored the old 37-param Tier 1
  contract and required retired rows such as `bias_cool`, `bias_heat`,
  `d_cool_stage_2`, `d_heat_stage_2`, and `sw_fsm_controller_enabled`.
  Fixed on planner branch `codex/ai-tunable-contract-2026-05-24`; it now
  matches the platform 39-param Tier 1 default map exactly.
- `scripts/gather-plan-context.sh` showed the new cooling knobs in its compact
  active-plan table but not the PR3 direct-wet/fog stress switches or latest
  hours. Fixed so Iris sees `dw_stress`, `dw_until`, `fog_stress`, and
  `fog_until` before the full generated traceability brief.
- `docs/firmware-ai-tunable-control-design-2026-05-24.md` implied PR3 enforced
  leaf-wetness gates. Fixed wording: PR3 enforces dew-margin and latest-hour
  gates now; true leaf-wetness lockout waits for live ESP32 instrumentation.
- `scripts/generate-lessons-page.py` did not recognize the PR3 stress knobs as
  tunable tokens. Fixed so future lesson pages can surface them.
- `docs/tunable-cascade.md` switch inventory omitted the new stress switches
  and cooling all-fans switch. Fixed.
- The PR3 deployment order needed one more guard: services can receive/backfill
  the seven new active-plan rows before OTA exposes matching ESPHome entities.
  Fixed by gating dispatcher pushes for the PR3 moisture-stress params until
  either ESPHome keys or cfg readbacks prove firmware support.
- Added `scripts/backfill-ai-moisture-stress-defaults.sh`, a dry-run-first
  routine-plan backfill for the seven PR3 defaults. It excludes one-shot plans
  and should run only after PR3 services are deployed.
- Follow-up cross-repo search caught a hand-copied backfill default mismatch:
  fog stress defaults are `fog_stress_window_latest_hour=19` and
  `fog_stress_min_dew_margin_f=10` in the platform registry and standalone
  planner. Fixed the backfill script to derive values from
  `verdify_schemas.tunable_registry.REGISTRY` and print candidate defaults in
  dry-run output.
- Pre-OTA proof caught six active visual-health observations without
  `position_id`/`zone_id`, created by the Gemini snapshot analyzer. Fixed
  `scripts/analyze-greenhouse-snapshot.py` to carry crop topology IDs into
  observations and to link rows to the `image_observations` id returned by the
  insert instead of `SELECT MAX(id)`. Repaired the six live rows
  (`919`-`924`) from their crop records.

## Current PR3 behavior

- Default-off firmware behavior is replay-preserving.
- Direct-wet stress override only applies to mister zones, not wall drip or
  irrigation/fertigation paths.
- Direct-wet stress override requires valid local time, master direct-wet gate,
  min temperature, VPD above `vpd_high + direct_wet_stress_vpd_margin_kpa`,
  dew margin at or above `direct_wet_stress_min_dew_margin_f`, and local hour
  before `direct_wet_stress_latest_hour`.
- Fog stress extension only extends the time window; RH ceiling, minimum temp,
  VPD-high, dew margin, and latest-hour gates still apply.

## Validation evidence

Platform candidate before this follow-up:

- `make lint` passed.
- `make test` passed: 513 passed, 2 skipped, 1 xfailed.
- `make test-firmware` passed: 162 passed, replay override self-test passed.
- `make firmware-check` passed; ESPHome compiled.
- `make firmware-invariants` passed: 193,525 rows, all 16 invariants.
- `make firmware-replay-worktree OLD=e349e5e` passed: 0 divergent rows over
  193,525 rows.
- `make firmware-audit-traceability-proof` passed after the live observation
  topology repair.
- `scripts/audit-tunable-traceability.py` passed: 152 registry/schema tunables,
  39 Tier 1 required params, 149 setpoint routes, 134 cfg readback routes, and
  12 cfg readback aliases.

Planner companion branch:

- `.venv/bin/python -m pytest -q` passed: 52 passed.
- `git diff --check` passed.
- Cross-repo comparison passed: platform and standalone planner both have
  exactly 39 Tier 1 defaults, no missing or extra keys, and no value mismatch.

Live service/backfill follow-up:

- PR3 platform services were deployed from `/srv/verdify` at candidate commit
  `3f34f07` and `verdify-ingestor`, `verdify-mcp`,
  `verdify-setpoint-server`, and `verdify-api` were restarted.
- `APPLY=1 scripts/backfill-ai-moisture-stress-defaults.sh` inserted the 35
  missing default cells: seven PR3 params across five routine-plan
  transitions.
- `scripts/validate-plan-coverage.sh` now reports plan
  `iris-20260524-1347` with five complete transitions and full 39-param
  tactical Tier 1 coverage.
- A follow-up dry run of `scripts/backfill-ai-moisture-stress-defaults.sh`
  reports zero candidate rows, plans, or transitions.
- `scripts/generate-ai-tunables-page.py --planner-context` now reports
  `future_active_rows=156`, `future_active_params=39`, and
  `reserved_active_rows=0`.

## OTA readiness notes

- Live severe-alert gate is clear: only warning `irrigation_feedback_gap`
  alerts were open during audit.
- Live rollback artifact exists in `/srv/verdify/firmware/artifacts`.
- Forecast max for the next 24h was above 85 F, which is warning context.
- Weekly OTA limit is currently blocking because firmware
  `2026.5.24.1341.e349e5e` first appeared today. OTA requires an explicit
  operator-approved `FIRMWARE_OTA_FREEZE_OVERRIDE_REASON`.
- Active future routine-plan rows now have all 39 Tier 1 params after the PR3
  service deploy and default backfill. The seven PR3 defaults added to each
  transition are:
  `sw_direct_wet_stress_override_enabled`,
  `direct_wet_stress_vpd_margin_kpa`,
  `direct_wet_stress_min_dew_margin_f`,
  `direct_wet_stress_latest_hour`,
  `sw_fog_stress_window_extend_enabled`,
  `fog_stress_window_latest_hour`, and
  `fog_stress_min_dew_margin_f`.

Do not run OTA until the weekly freeze override is supplied by the operator and
the preflight plus firmware validation suite still pass from `/srv/verdify`.
The backfill should remain dry-run clean immediately before OTA:

```bash
scripts/backfill-ai-moisture-stress-defaults.sh
scripts/validate-plan-coverage.sh
```
