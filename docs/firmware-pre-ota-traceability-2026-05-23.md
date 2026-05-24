# Firmware Pre-OTA Traceability Evidence - 2026-05-23

Prepared from the firmware worktree on 2026-05-23 after the live planner delivery-log repair, end-to-end traceability sweep, and operator-approved OTA.

## Live State

- Live plan page `https://lab.verdify.ai/plans/2026-05-23` shows planner delivery rows for `iris-20260523-0027`, `iris-20260523-0539`, and `iris-oneshot-20260523-1625`; the stale "No planner delivery-log rows" state is cleared.
- Active plan rows are now planner-pushable: `active_nonpushable=0`, `active_rows=33`.
- Current live firmware version is `2026.5.23.1711.63c59c4.dirty`, first seen `2026-05-23 23:13:20.169178+00`.
- Critical/high alert gate is clear. Current live alert summary has no `critical` or legacy `high` rows, with warnings still present.
- Forecast context for the next 24 hours includes a stress-window warning: forecast max `93.8F`.
- Rollback artifact is present at `firmware/artifacts/last-good.ota.bin`, version `2026.5.17.1849.9353df5`, with more than 48 hours of bake time.

## Corrections Made

- Daily plan publishing now records and renders delivery-log rows on the public site.
- Stale non-pushable operator irrigation rows were retired from `v_active_plan`.
- Public site generated outputs were refreshed, including plan pages, AI tunables, static planner context, and the new 30-day hourly performance dataset.
- Lesson, playbook, and site-doc embeddings were refreshed; active lesson and generated site coverage now has zero missing embeddings.
- Planner prompts and playbook guidance no longer direct the model toward retired crop-band or bias knobs.
- Replay/invariant harnesses now include the current `summer_vent` and `vent_mist_assist` paths and force FSM replay coverage by default.
- Harvest and treatment paths now carry crop, position, greenhouse, follow-up, and outcome linkage through schema and MCP list/record calls.
- Observation and crop-event MCP paths now tenant-filter list results, write `greenhouse_id`/`position_id`, and expose linkage fields in list responses.
- The MCP `climate()` response now exposes both `solar_w` and `solar_w_m2` so the SOLAR_MAX prompt and tool output agree.
- Live crop-work lineage was backfilled where deterministic: 898 observation rows and 7 crop-event rows inherited crop lineage, plus inactive test crop `HYDRO-01` was mapped to `EAST-HYDRO-1`.
- Traceability proof now fails on live source drift, active non-pushable plan rows, and missing active embeddings.
- Traceability proof now also checks active crop-work rows for missing positions and greenhouse tenant mismatches.

## OTA Result

- Operator authorization: Jason approved OTA on 2026-05-23.
- Override reason used for the weekly OTA freeze: `Operator Jason approved OTA on 2026-05-23 after pre-work, replay, invariant, compile, and live no-critical-alert validation`.
- Deployed version: `2026.5.23.1711.63c59c4.dirty`.
- OTA upload succeeded and diagnostics reported the new version at `2026-05-23 23:13:20.169178+00`.
- Post-deploy sensor-health accepted the deploy: 27 pass, 0 fail, 0 warn.
- Build artifacts were archived under `firmware/artifacts/2026.5.23.1711.63c59c4.dirty`.
- `/srv/verdify/state/expected-firmware-version` was updated to `2026.5.23.1711.63c59c4.dirty`.
- Rollback target remains the prior last-good artifact until this build completes its bake.

## Source And Runtime Parity

- Synced the audited worktree source to `/srv/verdify` on 2026-05-23.
- Restarted `verdify-mcp.service` and `verdify-ingestor.service`; both came back active.
- Ingestor reconnected to the ESP32, reconciled cfg readbacks, and resumed direct setpoint pushes.
- Republished generated site content with `scripts/publish-site-content.sh --date 2026-05-23 --reason source-parity-traceability`.
- Rebuilt the Quartz public site; public files advanced to the 17:23 MDT build.
- Refreshed `site_content` / `playbook_content` rows and embedded 49 changed rows.
- Follow-up embedding dry-run queued 0 rows.
- Runtime MCP smoke from `/srv/verdify/mcp/server.py::climate()` returned fresh data with both `solar_w` and `solar_w_m2`.

## Validation Results

All commands below passed in `/mnt/iris/verdify-worktrees/firmware` unless otherwise noted.

- `make lint`
- `make site-doctor`
  - 106 pages checked.
  - 82 generated or partially generated pages.
  - 133 Grafana iframes.
  - 0 findings.
- `make firmware-audit-worktree-proof`
  - `active_crop_work_missing_position=0`
  - `active_lessons_without_embedding=0`
  - `active_plan_out_of_bounds_rows=0`
  - `crop_work_tenant_mismatch=0`
  - `future_nonpushable_rows=0`
  - `playbook_chunks_without_embedding=0`
  - `site_docs_without_embedding=0`
  - `tactical_view_retired_rows=0`
  - routine plan coverage complete for `iris-20260523-0539`.
  - live source parity drift is allowed in this worktree target until these changes are deployed to `/srv/verdify` and relevant services are restarted.
- `make firmware-audit-traceability-proof`
  - DB traceability, active plan coverage, generated AI tunables, active lessons, static documentation, prompts, and live source parity passed.
- `pytest verdify_schemas/tests/test_sprint22_schemas.py verdify_schemas/tests/test_drift_guards.py -q`
- Rollback-only MCP smoke for `observations(record_harvest)` and `observations(record_treatment)`
  - confirmed `greenhouse_id`, `position_id`, `followup_due_at`, and `outcome` are returned.
  - rollback marker cleanup query returned 0 persisted smoke rows.
- `make test`
  - `510 passed, 2 skipped, 1 xfailed`.
- `make test-firmware`
  - 157 native C++ tests passed.
  - replay override self-test passed all flags, including `summer_vent`, `vent_mist_assist`, and `fog_heat_assist`.
- `make firmware-invariants`
  - 193,525 replay rows.
  - all 16 invariants passed.
- `make firmware-replay-worktree OLD=origin/main`
  - 193,525 rows emitted for each side.
  - 0 divergent rows.
  - 0 diagnostic-only rows.
- `make firmware-check`
  - ESPHome compile passed.
  - RAM 13.8%, flash 56.4%.
  - only warning observed was the known GPIO15 strapping-pin warning.
- `make firmware-replay-worktree OLD=origin/main`
  - 193,525 rows.
  - 0 divergent rows.
  - 0 diagnostic-only rows.
- `EXPECTED_FW_VERSION='2026.5.23.1711.63c59c4.dirty' make sensor-health SINCE='10 minutes'`
  - 27 pass, 0 fail, 0 warn.
- `EXPECTED_FW_VERSION='2026.5.23.1711.63c59c4.dirty' make sensor-health SINCE='15 minutes'`
  - 27 pass, 0 fail, 0 warn.
- Post-OTA DB validation
  - latest diagnostics row after service restart: `2026.5.23.1711.63c59c4.dirty`, uptime `722.3590087890625`, RSSI `-58`, reset reason `Software reset`.
  - no critical/high alerts opened in the 20-minute post-OTA window.
  - only recent alert was an existing `vpd_stress` warning.
- `make site-doctor`
  - 106 pages, 82 generated or partially generated pages, 133 Grafana iframes, 0 findings.
- `make lint`
  - Ruff passed.
- `scripts/embed-corpora.py --source site_doc --source playbook --source lesson --dry-run`
  - queued 0 rows.
- Live page smoke
  - `https://lab.verdify.ai/plans/2026-05-23` returned HTTP 200 and contains delivery rows including `iris-20260523-0539`.
  - The stale "No planner delivery-log rows" text is absent.
  - Plan pages are clean of internal planner labels (`Hermes`, `OpenClaw`, `local Gemma`).
  - `https://lab.verdify.ai/static/data/hourly-performance/greenhouse-performance-hourly-30d-latest.csv` returned HTTP 200 with 721 lines.
- `git diff --check`
- `python -m py_compile` for changed Python entrypoints.
- `bash -n` for changed shell scripts.

## OTA Gates Remaining

OTA has been performed and accepted. Do not promote this build to `last-good` until the bake requirement is met.

## Operator Context

Firmware OTA validation, source/runtime parity, generated site content, embeddings, and strict traceability proof are green. The rollback target remains the prior last-good artifact while `2026.5.23.1711.63c59c4.dirty` bakes.
