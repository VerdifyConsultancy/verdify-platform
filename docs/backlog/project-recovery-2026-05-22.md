# Backlog: project recovery train - 2026-05-22

This backlog is the coordinator-owned plan for recovering from the state
captured in `PROJECT_STATE.md` on 2026-05-22. It supersedes ad hoc handling of
the dirty root checkout, stale worktrees, temp worktrees, stashes, and partially
deployed local work. PR #80 stays intact and is the first integration baseline.

## Source state

- Audit artifact: `PROJECT_STATE.md` in the repo root, copied to
  `/mnt/verdify/docs/PROJECT_STATE.md`.
- Current root branch at audit time:
  `coordinator/lighting-occupancy-task-demand` at `19886ea`.
- Open GitHub PR: #80, `[codex] Refactor lighting occupancy task demand`.
- PR #80 status at audit time: open, mergeable, CI green at `19886ea`,
  already deployed to firmware/DB/site, not yet merged to `main`.
- PR #80 status after RM-0 on 2026-05-22: merged as-is to `main` with merge
  commit `66f0836b9cb2bec133f90586380d196cccee5d78`; main CI run
  `26311770932` passed.
- Production caveat: `/srv/verdify` points at `/mnt/iris/verdify`, so dirty
  files in the root checkout can also be production-mounted state.

## Non-negotiables

- Track A greenhouse operation outranks repository cleanup.
- Do not amend, rebase, discard, or mix unrelated dirty work into PR #80. Its
  only acceptable dispositions are merge as-is after final checks or retain open
  with a dated coordinator reason.
- Do not run irrigation finalizers until all feedback rows are `ok`.
- Do not perform a firmware OTA outside the freeze rules. PR #80 firmware is
  already deployed; promotion to `last-good` is a bake decision, not a new OTA.
- Do not run broad `git clean -fdX`. Ignored state includes secrets, ACME data,
  firmware rollback artifacts, and production site output.
- Every milestone closes with tests, deploy/reload steps when applicable, and
  live validation before the next milestone starts.
- Every change that touches `verdify_schemas/**`, `ingestor/entity_map.py`, or
  `mcp/server.py` must document required service restarts in its PR body.

## Definition of done

The recovery train is complete when all of these are true:

- PR #80 is either merged to `main` exactly as the preserved baseline or
  explicitly retained open with a documented reason from Jason.
- `main` has green CI after PR #80 or an equivalent route-guard fix lands.
- The root checkout has no uncommitted work except intentionally ignored
  runtime/build outputs.
- Every dirty root-worktree theme is either integrated through reviewed PRs,
  discarded with a diff note, or moved to a named future backlog item.
- The stale `genai` worktree has no unsalvaged unique planner-graph work.
- Temp lighting worktrees are removed or archived after proving they contain no
  unique work beyond PR #80.
- Stashes are reviewed and either applied through a new PR, archived to a patch
  file, or dropped with explicit rationale.
- Open critical/high alerts remain zero; warning deltas introduced by each
  milestone are understood and accepted.
- The final validation gate passes: `make lint`, `make test`, firmware gates
  for firmware changes, site/Grafana checks for site changes, service restart
  checks, and live validation of planner/ingestor/Grafana/site health.

## Milestone order

| Milestone | Owner | Objective | Exit gate |
|---|---|---|---|
| RM-0 | coordinator | Preserve PR #80 baseline | PR #80 merged as-is or explicitly retained; `main` CI green or equivalent route-guard fix tracked; PR #80 firmware bake tracked |
| RM-1 | coordinator | Quarantine dirty state and split integration branches | **Complete:** dirty themes split into reviewed PRs or archived RM7 patches |
| RM-2 | coordinator + ingestor + web | Integrate irrigation/fertigation canonicalization software | **Complete:** PR #83 merged/deployed; migration/schema/ingestor/site pieces validated without finalizer |
| RM-3 | coordinator + operator | Repair physical irrigation feedback and run finalizer | Four feedback rows `ok`; finalizer accepted; warnings resolved or documented |
| RM-4 | genai + coordinator | Reconcile planner-graph shadow and memory backfill work | **Complete:** PR #84 merged/deployed; root implementation chosen; stale work dropped; shadow validation healthy and non-authoritative |
| RM-5 | web + coordinator | Integrate public lab/Grafana refinements | **Complete:** Site/Grafana changes rebuilt, rendered, reloaded, live-checked; DNS issue #82 closed |
| RM-6 | ingestor + coordinator | Integrate climate overlay semantics and guard changes | **Complete:** PR #85 merged/deployed; ingestor restart/live tail clean; tests cover overlay behavior |
| RM-7 | coordinator | Clean stale worktrees, stashes, and generated-state risks | **Complete:** temp/recovery worktrees removed, unique leftovers archived, persistent worktrees current |
| RM-8 | coordinator | Final repository health closure | **Closure branch:** local gates and runtime checks green; final main CI pending docs PR |

## RM-0 - PR #80 baseline

Goal: keep PR #80 as the first recovery baseline. It already contains the
lighting occupancy task-demand refactor, migration 135, route-guard fix, green
CI, and deployed firmware evidence. Nothing from the dirty root checkout should
be mixed into it.

Status: complete for merge and main-CI validation. Firmware bake/last-good
promotion remains tracked by `F-RM0` in `docs/backlog/firmware.md`.

Tasks:

- [x] Verify PR #80 is still mergeable and all checks at `19886ea` remain green.
- [x] Confirm no critical/high alerts are open before any firmware promotion action.
- [x] Merge PR #80 to `main` or explicitly defer merge with a dated coordinator
  note.
- [x] After merge, update local refs and record the new `main` SHA in this backlog
  or `PROJECT_STATE.md`.
- [x] Keep `/tmp/verdify-lighting-index` untouched until RM-7 proves it contains no
  unique work beyond PR #80.
- [ ] Track 48-hour bake and promotion of
  `firmware/artifacts/2026.5.22.1331.19886ea/` to `last-good.ota.bin` only
  after the firmware freeze gates say it is safe.

Tests and validation:

- [x] Required before merge: GitHub CI all green for PR #80, especially route
  guards, schema/drift guards, firmware compile, firmware replay diff, and
  service restart hygiene.
- [x] Required after merge: latest `main` CI green; live firmware still reports
  `2026.5.22.1331.19886ea`; no new critical/high alerts; lighting dashboard and
  route guard behavior still match the PR body.

Stop conditions:

- Any new critical/high alert.
- PR #80 check regression.
- Evidence that root dirty files have already changed PR #80 runtime behavior
  in a way that must be separated before merge.

## RM-1 - Dirty-state quarantine

Goal: freeze the production-linked root worktree as an inventory source, then
split work into branches/worktrees that can be reviewed independently.

Status: in progress. Patch bundles, a path-disposition manifest, and a
pre-restart runtime snapshot were written outside the repo at
`/mnt/verdify/docs/recovery-2026-05-22/rm1-patch-bundles/`.
Clean integration worktrees now exist for RM-2
(`/mnt/iris/verdify-worktrees/coordinator-irrigation-fertigation`) and RM-5
(`/mnt/iris/verdify-worktrees/web-lab-grafana-recovery`). RM-4 and RM-6 are
merged and deployed through PR #84 and PR #85. Temp worktree cleanup and stash
disposition remain open.

Tasks:

- [x] Create patch bundles or branch diffs for each dirty theme before editing it:
  irrigation/fertigation, planner graph, public lab/Grafana, climate overlay,
  and test/guard adjustments.
- [x] Map every modified/untracked path from `PROJECT_STATE.md` to one milestone.
- [x] Record live process mtimes/start times before restarting anything.
- [ ] Open clean integration worktrees from updated `main` for each reviewed PR
  slice. Do not continue broad edits directly in the production-linked root.
  RM-2, RM-4, and RM-5 were integrated from clean review branches/worktrees.
- [ ] Keep `PROJECT_STATE.md` as an audit artifact until the recovery train closes;
  then decide whether to commit it, archive it under `/mnt/verdify/docs`, or
  remove the repo-root copy.

Tests and validation:

- [x] `git status --short --branch` in every worktree.
- [x] `git diff --name-status` and `git ls-files --others --exclude-standard`
  captured for the root before each split.
- [x] No service restart or deploy in this milestone except emergency Track A work.

Exit gate:

- Every dirty path and stash has exactly one owner, milestone, and disposition.

## RM-2 - Irrigation/fertigation software integration

Goal: integrate the canonical irrigation/fertigation software stack without
pretending the physical feedback blockers are solved.

Candidate branch:

- `coordinator/irrigation-fertigation-canonical`

Status: complete for software integration and deploy. PR #83
(`coordinator/irrigation-fertigation-canonical`) merged to `main` on
2026-05-22 at `a8f8ffaf7aa7834e393746e8d6b37d1549aecede`; main CI run
`26314849773` passed. Deployment applied migration 134 transactionally, synced
the tracked RM-2 files to `/srv/verdify`, restarted `verdify-ingestor` and
`verdify-mcp`, restarted Grafana, and verified the deployed stack with
`make irrigation-stack-software-check`. The finalizer was not run; physical
feedback repair remains RM-3.

Scope:

- Migration 134 canonical views and daily summary columns.
- `db/schema.sql` alignment.
- `verdify_schemas` alert/operations updates for `irrigation_feedback_gap`.
- `ingestor/entity_map.py`, `ingestor/ingestor.py`, and `ingestor/tasks.py`
  changes that feed feedback status and alerts.
- Validation/finalizer/audit scripts and Make targets.
- Tests that prove the schema, views, alert envelope, validators, and
  software-only audit behavior.
- Site/Grafana irrigation pages can either stay in this PR if tightly coupled
  to the canonical views or move to RM-5 if they are presentational.

Explicit non-scope:

- Running the finalizer.
- Marking missing/stuck physical feedback as healthy.
- Cleaning unrelated planner graph or lab-site visual changes.

Tests:

- [x] `make lint`
- [x] `pytest verdify_schemas/tests/ -v --ignore=verdify_schemas/tests/test_drift_guards.py --ignore=verdify_schemas/tests/test_vault_writers.py --ignore=verdify_schemas/tests/test_views.py`
- [x] `pytest verdify_schemas/tests/test_drift_guards.py -v`
- [x] Targeted DB/view tests for migration 134.
- [x] `scripts/validate-irrigation-stack.py` in dry-run/software-only mode.
- [x] `scripts/validate-irrigation-feedback.py` reports the known feedback gaps
  before physical repair and must not report false `ok`.
- [x] Full `make test` before merge.

Deploy:

- [x] Apply migration 134 only after confirming whether the live DB already has the
  objects and how idempotency is handled.
- [x] Restart `verdify-ingestor`; include `verdify-mcp` if schema import consumers
  require it.
- [x] Reload/restart Grafana only if this milestone includes provisioning changes.

Validation:

- [x] `v_irrigation_schedule_current`, `v_irrigation_fertigation_runs`,
  `v_irrigation_program_daily`, and
  `v_irrigation_sensor_feedback_status` exist and return expected rows.
- [x] Four known warnings remain warnings until hardware is fixed:
  `south_soil_probe_1`, `center_root_zone_moisture`, `center_runoff_ec`,
  `center_runoff_ph`.
- [x] No critical/high alerts.
- [x] Five-minute `verdify-ingestor` journal tail has no validation errors.

## RM-3 - Physical irrigation feedback and finalizer

Goal: close the physical feedback loop, then run the finalizer only after the
software stack and sensors prove ready.

Tasks:

- Repair or replace the south soil probe reporting `stuck_zero`.
- Install/map center root-zone moisture, runoff EC, and runoff pH sensors, or
  explicitly change the acceptance contract if those sensors are intentionally
  unavailable.
- Clear stale retained MQTT/HA states only through the dedicated cleanup script
  after confirming each physical sensor path.
- Run finalizer dry-run and acceptance audit.
- Run finalizer only when all four feedback rows are `ok`.

Tests:

- `scripts/validate-irrigation-feedback.py`
- `scripts/irrigation-completion-audit.py`
- `scripts/finalize-irrigation-feedback.py --dry-run`
- Targeted DB checks for feedback status and alert lifecycle.

Deploy/validation:

- Restart or reload only the services touched by sensor mapping changes.
- Verify alerts resolve or transition to documented non-blocking warnings.
- Verify Grafana/site irrigation panels show current feedback state.

Stop condition:

- Any feedback row remains `missing` or `stuck_zero`.

## RM-4 - Planner graph shadow and memory backfill

Goal: choose one planner-graph shadow implementation, reconcile stale `genai`
worktree changes, and make memory backfill executable or remove the promise.

Status: complete. PR #84, `[codex] Reconcile planner graph shadow recovery`,
merged to `main` at `3a2eb87426a355a5e71c036bc3460686b68b5b56`; post-merge
main CI run `26315767545` passed. Deployment synced the merged files to
`/srv/verdify`, restarted only `verdify-ingestor`, and kept planner graph
shadow disabled by default (`PLANNER_GRAPH_SHADOW_ENABLED` unset).

Candidate branch:

- `genai/planner-graph-shadow-reconcile` or
  `coordinator/planner-graph-shadow-reconcile` if shared runtime files dominate.

Tasks:

- Diff root dirty planner-graph files against
  `/mnt/iris/verdify-worktrees/genai`.
- Choose the root implementation unless the stale worktree proves unique
  behavior worth porting.
- Commit or drop:
  `ingestor/iris_planner.py` shadow hook,
  `scripts/planner-graph-shadow-smoke.py`,
  `scripts/planner-graph-shadow-report.py`,
  `tests/test_planner_graph_shadow.py`, and planner docs.
- Either implement `scripts/backfill-planner-memory.py` or edit
  `docs/planner/planner-memory-backfill.md` so it only documents current
  capability.
- Keep the shadow path non-authoritative until acceptance metrics justify a
  separate rollout decision.

Tests:

- [x] `make lint`
- [x] `pytest tests/test_planner_graph_shadow.py`
- [x] `pytest tests/test_04_planner.py`
- [x] `pytest tests/test_11_planner_milestones.py`
- [x] Full `make test` before merge (`492 passed, 2 skipped, 1 xfailed`)

Deploy:

- [x] Restart `verdify-ingestor` only after merge if the runtime hook lands.
- [x] Do not change planner authority or default production routing in this
  milestone.

Validation:

- [x] New `plan_delivery_log_shadow` rows are written on expected triggers.
- [x] Latest smoke completes with `gateway_status=200` and accepted validation, or
  failure is explicit and non-authoritative.
- [x] No duplicate shadow launches per trigger.
- [x] No production `set_plan`/`set_tunable` behavior changes outside the shadow
  table/logs.

Validation evidence: live smoke against planner Cloud Run wrote
`plan_delivery_log_shadow.id=4` with `gateway_status=200`, remote/local
`set_plan`, and `would_accept_remote=true`; the smoke trigger has exactly one
shadow row. The deployed report showed `4` completed rows and `0` failed/timed
out in the last seven days. No shadow rows were written after the ingestor
restart because the shadow env remains unset. Deployed `outcomes` and
`support-docs` backfill dry-runs both produced stable source IDs.

## RM-5 - Public lab site and Grafana integration

Goal: make the already-deployed public lab/Grafana refinements reproducible from
tracked source, then prove the live site and dashboards reflect that source.

Candidate branch:

- `web/lab-grafana-recovery`

Status: merged and partially deployed. PR #81,
`[codex] Integrate lab site and Grafana recovery`, merged to `main` at
`f08e09490c1d1075b705eebd66b0f06a81812f43`; post-merge main CI run
`26313275460` passed. Deployment from the merged source rebuilt the lab site
(`277` pages emitted), restarted Grafana, installed/enabled the tracked
Grafana render-cache warmer timer, and completed one cache-warmer run
successfully (`133/133` render URLs HTTP 200; failures `0`). `make site-doctor`
reported zero findings and source/live Grafana brand checks passed.

DNS caveat resolved during RM-8 validation: public `lab.verdify.ai` and
`labs.verdify.ai` both resolve to `gateway.verdify.ai` / `8.44.158.103` and
serve the lab site with HTTP 200. GitHub issue #82 is closed.

Scope:

- `docker-compose.yml` labs-domain routing, if still intended.
- Grafana dashboard/provisioning changes and provider interval decision.
- Public site navigation, styles, irrigation nav, homepage Grafana wrapper, and
  generator script updates.
- Lab-site refactor items from
  `docs/backlog/lab-site-refactor-2026-05-20.md` that are currently dirty but
  not merged.
- `verdify-grafana-render-cache-warm.service` failure triage.

Tests:

- `make lint`
- `make site-doctor`
- Public-site lint/checks from `scripts/lint_public_site.py`
- Targeted tests in `tests/test_06_website.py` and `tests/test_12_fidelity.py`
- Representative desktop/mobile render checks for changed pages and Grafana
  embeds.
- Full `make test` before merge if generator or API-facing assumptions change.

Deploy:

- Rebuild site from source, not by editing generated `public` output.
- Reload/restart Grafana after provisioning/dashboard mtime changes, then prove
  the loaded dashboard JSON matches tracked source.
- Restart compose services only for routing/label changes.

Validation:

- `lab.verdify.ai` and `labs.verdify.ai` route according to the intended rule.
- `verdify.ai` and `www.verdify.ai` redirect according to the intended rule.
- `make site-doctor` reports zero findings.
- Key Grafana panels render without blank/no-data regressions.
- Cache warm service is healthy or has a documented follow-up with logs.

## RM-6 - Climate overlay semantics and loose guard changes

Goal: isolate the climate overlay and test-guard changes from the larger
irrigation/site branches so they can be reviewed on their own merits.

Status: complete. PR #85, `[codex] Guard Tempest standalone climate overlays`,
merged to `main` at `45758b66c08e8c40ec1eb48cd735db48b8ced0f5`; post-merge
main CI run `26316119564` passed. Deployment synced the merged files to
`/srv/verdify`, restarted only `verdify-ingestor`, and validated the deployed
standalone Tempest script.

Candidate branch:

- `ingestor/climate-overlay-semantics`

Scope:

- `ingestor/ingestor.py`, `scripts/ha-sensor-sync.py`, and
  `scripts/tempest-sync.py` changes that skip standalone climate inserts when
  no recent ESP32 indoor row exists.
- Range rejection/normalization for irrigation feedback sources if not already
  merged in RM-2.
- Planner milestone/test timeout changes only if still justified by current
  runtime behavior.

Tests:

- [x] `make lint`
- [x] Targeted ingestor/sync tests for no-recent-indoor-row behavior.
- [x] Targeted planner milestone tests if those changes remain.
- [x] Full `make test` before merge (`494 passed, 2 skipped, 1 xfailed`)

Deploy:

- [x] Restart `verdify-ingestor` after merge.
- [x] Restart affected sync timers/services only if they are separate from ingestor.

Validation:

- [x] Journal shows warnings, not crashes, when overlay inserts are skipped.
- [x] Climate rows still arrive when ESP32 indoor baseline is fresh.
- [x] No unexpected gaps in public climate pages or Grafana panels.

Validation evidence: deployed `tests/test_tempest_sync.py` passed; deployed
`scripts/tempest-sync.py` wrote current `weather_station` history and merged
outdoor columns into the latest ESP32 climate row; live DB checks showed current
`climate` rows, current `weather_station` rows, zero orphan outdoor-only
`climate` rows in the last hour, and no open critical/high alerts. The root
planner milestone timeout and optional-`MIDNIGHT` test loosenings were dropped
because current `main` passed targeted planner tests and full `make test`
without them.

## RM-7 - Worktree, stash, and generated-state cleanup

Goal: remove or archive all local state that can confuse future agents.

Status: complete. Unique leftovers were archived under
`/mnt/verdify/docs/recovery-2026-05-22/rm7-archives/`; temp/recovery
worktrees were removed; stale `genai` planner files were cleared by resetting
that worktree to `origin/main`; both stashes were dropped after archive; the
path-specific `.git/.DS_Store` was removed; persistent agent worktrees were
fast-forwarded to `45758b66c08e8c40ec1eb48cd735db48b8ced0f5`.

Tasks:

- [x] Delete `/tmp/verdify-lighting-deploy.AtDl8C` after PR #80 merge validation.
- [x] Diff `/tmp/verdify-lighting-index` against PR #80; delete if it has no unique
  content, otherwise archive the unique diff to a named patch and schedule it.
- [x] Recreate or clean `/mnt/iris/verdify-worktrees/genai` after RM-4; no stale
  dirty planner-graph files should remain there.
- [x] Review the May 10 firmware/ingestor stash in a clean temporary worktree.
  Apply through a PR only if still relevant; otherwise archive or drop it.
- [x] Drop the Apr 27 one-line `AGENTS.md` stash if no coordinator need remains.
- [x] Remove `.git/.DS_Store` only if done with a path-specific, non-destructive
  command.
- [x] Keep ignored operational state; clean only allowlisted caches if needed.

Validation:

- [x] `git worktree list --porcelain` shows only intended persistent worktrees.
- [x] `git stash list --date=iso` contains only intentionally retained stashes.
- [x] `git status --ignored=matching --short` contains no surprising new ignored
  operational paths.

Archive evidence: `/tmp/verdify-lighting-index` unique tracked diff plus
`GOAL.md` and migration 135 were archived; stale genai planner work was archived
before reset; May 10 firmware/ingestor and Apr 27 `AGENTS.md` stashes were
archived under `rm7-archives/stashes/`. No archived RM-7 patch was applied
directly to the production-linked root.

## RM-8 - Final health closure

Goal: prove the repo, runtime, and backlog agree.

Status: closure branch complete. Local gates passed from
`coordinator/recovery-status-closure`; post-merge main CI remains the final
GitHub-side confirmation for the docs-only closure PR.

Tests:

- [x] `make lint`
- [x] `make test` (`494 passed, 2 skipped, 1 xfailed`)
- Firmware gates for any firmware-touching changes:
  `make test-firmware`, `make firmware-invariants`, `make firmware-check`, and
  replay diff artifacts as required by freeze rules.
- [x] `make site-doctor`
- [x] Grafana render sample or full render depending on RM-5 blast radius.
- [x] Drift guards, including schema restart hygiene and tunable readback guards.

Deployment validation:

- [x] `verdify-ingestor`, `verdify-mcp`, setpoint server, Grafana, API, and site
  process start times are after their deployed code mtimes when code changed.
- [x] No critical/high alerts.
- [x] Warning count and categories are documented.
- [x] Planner trigger flow and shadow flow are healthy.
- [x] Public site, Grafana, and DB views serve the tracked source state.

Backlog validation:

- [x] `docs/BACKLOG.md` and per-agent backlog files reflect the final disposition
  of every recovery item.
- [x] Open GitHub issues #18 and #19 are closed only if exact readback parity is
  proven; otherwise they stay linked to a concrete future task.
- [x] Any deferred work has an owner, branch prefix, gate, and acceptance criterion.

Validation evidence: `make lint`, `make test`, `make site-doctor`,
`pytest verdify_schemas/tests/test_drift_guards.py -q`, and
`pytest verdify_schemas/tests/test_firmware_drift.py verdify_schemas/tests/test_tunable_registry.py -q`
all passed. Live DB checks showed zero open critical/high alerts, warning-only
open alerts (`irrigation_feedback_gap=4`, `sensor_offline=61`), six planner
deliveries in the last 24 hours, four planner-graph shadow rows in the last
seven days, all nine issue #18 cfg readbacks present in the last ten minutes,
and current `climate` / `weather_station` rows. Public `lab.verdify.ai` and
`labs.verdify.ai` returned HTTP 200; a Grafana render sample returned HTTP 200
`image/png`. GitHub issues #18, #19, and #82 are closed with verification
comments.

## Item disposition ledger

| Item | Source | Disposition | Milestone |
|---|---|---|---|
| PR #80 lighting occupancy task demand | GitHub PR | Keep intact; merge as-is or retain; bake/promote later | RM-0 |
| Latest main red route guard | GitHub Actions | Fix through PR #80 or equivalent; verify route guard green | RM-0 |
| Root dirty irrigation canonicalization | Root worktree | Integrated through PR #83 and deployed; finalizer still withheld until physical feedback is healthy | RM-2 |
| Physical irrigation feedback warnings | Live alerts/DB | Operator/hardware repair before finalizer | RM-3 |
| Root dirty planner graph hook/docs/tests | Root worktree | Integrated through PR #84 and deployed; root implementation chosen as canonical | RM-4 |
| Stale genai worktree planner files | `verdify-worktrees/genai` | Archived under `rm7-archives/genai-stale-planner-graph/`, then reset to current `origin/main` | RM-7 |
| Public lab/Grafana dirty refinements | Root worktree | Integrated through PR #81 and deployed from tracked source; public `lab.verdify.ai` DNS resolved and issue #82 closed | RM-5 |
| Grafana cache warm failed unit | Systemd | Fixed by PR #81; tracked unit installed/enabled and first run completed with 0 failures | RM-5 |
| Climate overlay semantics | Root worktree | Integrated through PR #85 and deployed | RM-6 |
| Loose test/guard adjustments | Root worktree | Dropped during RM-6; current `main` did not need planner timeout/MIDNIGHT loosening | RM-6 |
| `/tmp/verdify-lighting-deploy.AtDl8C` | Temp worktree | Removed after PR #80 merge validation | RM-7 |
| `/tmp/verdify-lighting-index` | Temp worktree | Unique tracked diff and untracked files archived under `rm7-archives/verdify-lighting-index/`, then worktree removed | RM-7 |
| May 10 firmware/ingestor stash | Git stash | Archived under `rm7-archives/stashes/2026-05-10-firmware-agent-changes.patch`, then dropped | RM-7 |
| Apr 27 one-line AGENTS stash | Git stash | Archived under `rm7-archives/stashes/2026-04-27-agents-untracked.patch`, then dropped | RM-7 |
| Ignored runtime/build state | Filesystem | Preserve operational assets; clean only allowlisted caches | RM-7 |
| GitHub issues #18/#19/#82 | GitHub issues | Exact readback parity and public lab DNS verified; issues closed with evidence comments | RM-8 |
