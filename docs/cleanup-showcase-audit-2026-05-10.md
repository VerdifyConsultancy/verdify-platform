# Verdify GA Cleanup / Showcase Audit — 2026-05-10

Verdify is at a clean GA checkpoint after the launch-site work. This audit looked at the main repo, persistent worktrees, archive branches, open PRs, the vault content repo, generated site output, scripts, and obvious dead/abandoned code markers.

## Completed During Audit

- Merged `#55` into `main`: restored the useful planner/dispatcher registry guardrail work from the genai/ingestor archive WIP.
  - Planner prompt ranges now match the executable tunable registry for the incident-prone VPD/mist/timing knobs.
  - Dispatcher now clamps numeric stale active-plan rows to registry min/max or rejects non-numeric/switch/enum violations before `setpoint_changes` inserts or ESP32 direct pushes.
  - Local validation: `make lint`; `make test` (`320 passed, 1 xfailed`).
  - GitHub Actions: green, including firmware replay/invariants and schema drift guards.
  - Production: restarted `verdify-ingestor`; it reconnected to ESP32 and completed post-reconnect setpoint dispatch.
- Merged `#56` into `main`: restored `scripts/generate-baseline-vs-iris-page.py` and wired those pages into `site-doctor`.
  - Regenerated `/data/baseline-vs-iris` and `/evidence/baseline-vs-iris`.
  - Pushed vault commit `0f3fd9e` (`Refresh generated launch evidence pages`).
  - Quartz watcher rebuilt production output; `make site-doctor` reports `0 findings`.
  - Local validation: generator `--stdout`, generator `--check`, `make site-doctor`, `make lint`, `make test`.
  - GitHub Actions: green.
- Closed stale open PRs and deleted their remote head branches:
  - `#6` voice-note Copilot draft: unrelated old branch/base, not Verdify GA.
  - `#17` tunable-cascade docs: superseded by registry-backed docs/process.
  - `#23` firmware architecture proposal: useful history, superseded by current firmware-freeze + registry contract.
  - `#48` GreenByte/GreenLight raw implementation: valuable research direction, but not a GA merge candidate due size, generated outputs, and conflicts.
- Deleted temporary GA archive branches after salvaging useful work:
  - `archive/genai-ga-unmerged-2026-05-10`
  - `archive/ingestor-ga-unmerged-2026-05-10`
  - `archive/web-ga-unmerged-2026-05-10`
- Removed local `.DS_Store` filesystem junk from the repo/vault trees.

## Current Clean State

- Main repo: clean on `main` at `a77ed41`; `origin/main` matches.
- Vault repo: clean on `main` at `0f3fd9e`; pushed to GitHub.
- Runtime generated site repo: clean on `v4`; no pending rebuild.
- Production site: rebuilt and live; `make site-doctor` has `0 errors, 0 warnings`.
- Open PRs: none.
- Latest main GitHub Actions: successful for `#56`, `#55`, and the nav fix `#54`.

## Inventory Of Remaining Loose Ends

### Worktrees

All persistent worktrees are clean. Most are now behind `main` with zero unique commits, which means their work has landed and the worktree branches are stale:

| Worktree | Branch | Unique commits vs main | Recommendation |
|---|---:|---:|---|
| `firmware` | `firmware/post-deploy-observability-vpd` | 0 | Safe to retire/reset when not needed by the persistent agent. |
| `genai` | `genai/sprint-4-planner-knowledge` | 0 | Safe to retire/reset. |
| `ingestor` | `ingestor/sprint-25.1-value-aware-dedupe` | 0 | Safe to retire/reset. |
| `saas` | `saas/sprint-10-rescope-foundation` | 0 | Safe to retire/reset. |
| `slot-1` | `iris-dev/sprint-22-pydantic-rollout` | 0 | Safe to retire/remove; old rollout checkpoint. |
| `slot-2` | `iris-dev/agent-org` | 0 | Safe to retire/remove; agent-org is on main. |
| `web` | `web/sprint-4-iris-instance-panel` | 12 | Do not merge raw. Generator was salvaged; remaining diff is old launch/Grafana/nav work that conflicts with current site direction. |

I did not delete the persistent worktree directories because `AGENTS.md` treats them as part of the operating model. The clean next step is to reset/remove stale worktrees deliberately, not strand future agent sessions.

### Branches

Remote branches still not merged into `main` and not represented by open PRs:

- Old product/backend experiments: `origin/archive/saas-v1`, `origin/backend-dev`, `origin/dev`, `origin/feature/model-architecture-overhaul`.
- Copilot/Obsidian leftovers: `origin/copilot/fix-aa9e66b7-94a9-4c13-824c-fb2737d9b1d3`, `origin/copilot/vscode1755666171709`.
- Old firmware/model tracks: `origin/feature/vpd-primary-state-machine`, `origin/sprint-12/mode-firmware`, `origin/firmware/sprint-1-housekeeping`, `origin/firmware/sprint-15-summer-vent`, `origin/firmware/sprint-15.1`.
- Old genai/web branches with partial unique commits: `origin/genai/sprint-1-doc-truthing`, `origin/web/sprint-1-sprint22-loose-ends`, `origin/web/sprint-4-iris-instance-panel`.

Recommendation: schedule one branch-pruning pass. Keep only `main`, intentionally active agent branches, and one named research archive if GreenByte is being revived. Everything else should be deleted after a final `git diff --name-status main...branch` export is attached to an issue or doc.

### Code And Script Loose Ends

- `scripts/generate-baseline-vs-iris-page.py` is now restored; this was the most concrete broken generated-page contract.
- `scripts/smoke-feedback-loop.py` now uses a purpose-named feedback-loop smoke path instead of the old sprint-specific filename.
- Zero-reference scripts from the repo text scan: `backfill-plan-evaluations.py`, `transcode-launch-video.sh`, `update-evidence-snapshots.py`, `vault-harvest-writer.py`, `vault-treatment-writer.py`, `warm-grafana-render-cache.py`. These may be operational/manual tools; each needs either a README/backlog reference or deletion.
- `docs/tunable-cascade.md` still has a “Remaining tunables — TBD” section. For showcase quality, either complete those rows from the registry or mark the doc historical and point readers to `verdify_schemas/tunable_registry.py`.
- `config/zones.yaml` still says “Species TBD — needs Emily inventory” for one zone note. That is acceptable operationally, but not showcase-polished.
- Analytics is intentionally ambiguous: `docker-compose.yml` provisions Umami/GoAccess routes, but `site/quartz.config.ts` has `analytics: null`. Decide whether public analytics is intentionally disabled or enable/document the chosen provider.
- GreenByte/GreenLight should not return as a giant PR. If revived, it should be a curated package or separate repo with source, small fixtures, tests, and ignored generated outputs.

## Suggested Finish Order

1. Retire/reset stale persistent worktrees whose branches have zero unique commits.
2. Delete or archive-with-explicit-owner the remaining old remote branches.
3. Decide the GreenByte direction: curated research package vs delete.
4. Resolve the small showcase polish items: analytics decision, tunable-cascade TBDs, zone species note.
5. Add a scheduled or documented generated-page check for Baseline vs Iris now that the generator exists.

## GA Definition Going Forward

GA clean means:

- `main`, vault `main`, and production site output are clean and pushed.
- No open PR is older than the current milestone unless explicitly labeled active.
- No remote branch exists without an owner, purpose, and next action.
- Generated pages name scripts that exist and can reproduce them.
- `make lint`, `make test`, `make site-doctor`, and GitHub Actions are green.
- Production runtime services affected by code changes have been restarted or explicitly documented as not requiring restart.
