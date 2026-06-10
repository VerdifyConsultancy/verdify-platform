# Verdify — Agent Working Guide

This repo is worked by **one agent** (Claude, running on Jason's laptop with full
project ownership) plus Jason as the human gate for device-affecting and
outward-facing actions. Every session that edits code here should read this
file first. (The earlier five-persistent-agents model is retired — Jason,
2026-06-10.)

## What Verdify is

An AI-driven climate controller for a single 367 sq ft greenhouse in Longmont, CO. **Production** — plants are alive, the ESP32 is in the loop every 5 s, the planner runs on real data. Keeping the greenhouse operational ("Track A") always outranks SaaS/cloud refactor progress ("Track B"). See `README.md` for the architecture one-pager.

## Branch & deployment model (Jason, 2026-06-10 — supersedes the live/platform-main split)

- **`main` is the single canonical branch.** PRs land on main; all CI
  (build/publish/validate) and every ArgoCD targetRevision point at main. The
  2026-05-31 `live/platform-main` deploy branch is RETIRED (kept aligned as a
  pointer during transition; do not push to it).
- **Two environments, no staging.** `verdify-dev` (ns verdify-dev, auto-sync)
  is the proving environment: every push to main publishes digest-pinned
  images and the `bump-dev-digests` job pins them into `overlays/dev`, which
  dev deploys automatically. Prod (ns verdify-prod, ArgoCD app
  `verdify-prod-dark` — legacy name) is **manual-sync behind the device-write
  gate**: the `prod-promote` workflow opens a dev-equality-guarded PR, a human
  merges, and an operator-initiated sync applies it. The staging overlay in
  this repo is retired dead weight pending removal.
- **Dev is device-dark by construction** (ingestor replicas:0 +
  deny-esp32-egress + VERDIFY_DEVICE_WRITE_ENABLED=0) and its database is a
  **nightly restored copy of prod** (overlays/dev/db-restore-from-prod.yaml;
  dev-written plans are wiped nightly and never replicate to prod). Firmware
  is hot-staged direct to prod — there is no dev device.
- **Operating from the laptop:** see `docs/runbooks/laptop-operator.md` for
  DB access (`scripts/verdify-db.sh`), pipeline triggers, promotion, the
  gated prod sync, and the firmware OTA procedure (all runnable from any
  kubectl host).

## Ownership

**One agent owns the whole project** — firmware (ESP32 C++ / ESPHome / OTA),
ingestor, planner/genai (iris_planner, mcp, prompts), web (api, site
generators, Quartz lab site), deploy/k8s + CI, schemas, and migrations. The
per-subsystem docs under `docs/agents/` survive as **subsystem reference
docs** (what each layer does, its invariants, its gotchas) — read the
relevant one before deep work in that layer; ignore their multi-agent
routing/handoff language.

**Jason is the human gate** for: device-affecting actions (firmware OTA, the
prod ArgoCD sync that touches the live writer, anything on the device VLAN),
DB-destructive operations on prod, credential rotation, and outward-facing
infra (public DNS/edge, org settings). Everything else — code, schemas,
migrations (with the safety rules below), CI, k8s manifests, docs — the agent
lands autonomously on `main`, keeping CI green.

Discipline that stays (it was never about the org chart):

1. **Schema changes land first**, consumers next — keeps drift guards meaningful.
2. **Migrations are serialized** — one migration change at a time, classified
   by the rollback-safety tooling below.
3. **Drift guards are the wire protocol** — `verdify_schemas/tests/test_drift_guards.py`
   green means layer boundaries are intact.
4. **Hand off by doc** — anything a future session needs goes into docs/
   (or project memory), not chat history.

## Migration safety: never wrap a self-committing migration

**Lesson from the 2026-05-30 live-commit incident (#23).** A migration that owns
its own top-level `COMMIT;` — or contains a *commit-forcing* statement that
cannot run inside a transaction block (`CREATE INDEX CONCURRENTLY`, `DROP INDEX
CONCURRENTLY`, `REINDEX ... CONCURRENTLY`, `VACUUM`, `CREATE/DROP DATABASE`,
`CREATE/DROP TABLESPACE`, `ALTER SYSTEM`) — must **never** be replayed under an
outer `BEGIN; … ROLLBACK;` dry-run. The inner `COMMIT` (or commit-forcing
statement) commits to the **live** database the instant psql reaches it,
silently defeating the rollback. On 2026-05-30 exactly this happened.

The two shapes (per `docs/runbooks/backlog-closeout-deploy-2026-05-30.md`):

- **Self-transactional** (e.g. 149, 150): own top-level `BEGIN;`/`COMMIT;`.
  Apply as-is; rollback-validate by swapping the trailing `COMMIT;` for
  `ROLLBACK;`. **Do NOT wrap in an outer `BEGIN..ROLLBACK`.**
- **Non-self-transactional** (e.g. 134, 146, 147, 151–155): no top-level
  `COMMIT`; only DO-block `BEGIN`s. Safe to rollback-validate by wrapping in an
  outer `BEGIN; … ROLLBACK;`.

The guard is codified, not prose-only:

- `scripts/check_migration_rollback_safety.py` classifies each migration
  (stripping comments, string literals, and dollar-quoted bodies, so a
  `CONCURRENTLY` mentioned only in a `--` comment never trips it).
  `--rollback-wrap FILE` is the preflight `make irrigation-migration-check` /
  `irrigation-migration-proof` run before piping into psql; it refuses to wrap a
  self-committing migration. `make migration-rollback-safety` lists the full
  classification.
- CI job `migration-rollback-safety` (`.github/workflows/ci.yml`) flags any
  self-committing migration touched in a PR.

## Branches, working copy & memory

- Work lands on `main` (direct commits for routine work, PRs when review is
  useful or a workflow generates them, e.g. prod-promote). Topic branches are
  free-form; no sprint counters (retired with the multi-agent model — the
  iris-VM worktrees at `/mnt/iris/...` are gone with the .150 VM).
- The working copy is `~/repos/verdify-platform` on Jason's laptop; persistent
  agent memory lives in the laptop project-memory directory and survives
  sessions.

## Backlog

GitHub issues on `VerdifyConsultancy/verdify-platform` are THE tracker.
`docs/BACKLOG.md` / `docs/backlog/*` are historical; don't extend them.

## Checks before commit

- `make lint` (ruff) — required, no exceptions.
- `make test` — required; 1 pre-existing flaky timeout (`test_dew_point_risk_computes`) is tolerated, everything else must pass.
- `make firmware-check` — required for `firmware` agent only.
- For UI/site changes, verify render locally; type-checks and tests don't catch visual regressions.

## Firmware freeze rules (Phase 0 stabilization)

Post-2026-04-21 incident (sprint-15/15.1 fix-it-forward spiral producing repeated regressions). Background + full plan at `.claude-agents/iris-dev/plans/yo-iris-dev-you-help-humming-stonebraker.md`. These rules apply to every agent and every change to `firmware/lib/**`, `firmware/greenhouse/**`, `verdify_schemas/**`, `ingestor/entity_map.py`, or `mcp/server.py`.

1. **No firmware OTA deploy while any `severity='critical'` alert is open; legacy `high` rows are still treated as deploy blockers if present.** `make firmware-deploy` preflight queries the alerts table and aborts. Override requires explicit operator sign-off and a documented reason in the PR body.

2. **≤1 firmware OTA per calendar week** during rewrite phases (Phase 2-3 of the plan). Tunable pushes via `set_tunable` / `set_plan` are exempt but logged. Counter resets Monday 00:00 MDT.

3. **48-hour bake minimum** between firmware OTA deploys. `make firmware-deploy` preflight checks `firmware/artifacts/last-good.ota.bin` mtime. "Bake" = the new binary runs 48 hours without the sensor-health sweep flagging critical alerts.

4. **No sprint numbers.** Every change is PR-scoped and must carry replay-diff output + invariant-suite result + unit-test delta as artifacts in the PR description. Don't create `sprint-N.M` docs.

5. **Stress-window warning.** If outdoor_temp > 85°F forecast for the next 24 hours, `make firmware-deploy` reports it as operator context but does not block. Severe alerts, 48-hour bake, and weekly OTA limits remain hard gates.

6. **Every new tunable needs a `cfg_*` readback.** CI job `no-new-fire-and-forget` enforces this on PRs touching `firmware/greenhouse/tunables.yaml`. Fire-and-forget tunables are silent-push-corruption risks.

7. **Schema changes require explicit restart documentation.** If a PR touches `verdify_schemas/**`, `ingestor/entity_map.py`, or `mcp/server.py`, the PR body must mention which services need to bounce post-merge (`verdify-mcp`, `verdify-ingestor`). CI job `service-restart-drift-guard` enforces this. Observed need from the 2026-04-21 MCP staleness incident.

8. **Every firmware PR must show a replay-diff.** CI job `firmware-replay-diff` runs `scripts/firmware-replay-diff.sh` against merge-base. Default `THRESHOLD_PCT=0` means zero mode/relay divergence allowed. Intentional divergence (e.g. Phase 2 dwell-gate rollout) requires coordinator approval + explicit `THRESHOLD_PCT` override in the PR.

9. **Required artifacts** for firmware changes (PR body or commit message):
   - Replay diff output (`make firmware-replay OLD=<base> NEW=HEAD`)
   - Invariant-suite output (`make firmware-invariants`)
   - Unit-test delta (`make test-firmware`)

Single-agent model: the CI gates (replay-diff, invariants, unit tests) are
the reviewers; Jason is the human gate for the OTA itself. The 48-hour bake
between OTAs stays.

## Testing infrastructure (phase-0 deliverables)

- `make firmware-invariants` — runs the 15 bulletproof invariants (`firmware/test/invariants.h`) against the replay corpus. First breach fails.
- `make firmware-replay OLD=<ref> NEW=<ref>` — dual-worktree diff of firmware mode/relay decisions. Default THRESHOLD_PCT=0.
- `make replay-corpus-refresh` — pulls a fresh CSV from live DB, archives the prior corpus, validates no >5% size regression.
- `scripts/export-replay-overrides.sh` — CSV export includes outdoor sensors, equipment_state, mode_reason (sprint-15.1+).
