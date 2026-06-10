# Verdify — Agent Working Guide

This repo is worked by several Claude agents in parallel plus a human coordinator (Jason). Every session that edits code here should read this file first.

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

## Agents

Five persistent agents, each owning one scope. Branches are prefixed by agent name; worktrees live at `/mnt/iris/verdify-worktrees/{agent}/`. Per-agent scope docs live in `docs/agents/`.

| Agent | Owns | Branch prefix | Scope doc |
|---|---|---|---|
| [`firmware`](docs/agents/firmware.md) | ESP32 C++ (`greenhouse_logic.h`), ESPHome YAML, firmware replay, OTA, sensor health | `firmware/*` | `docs/agents/firmware.md` |
| [`ingestor`](docs/agents/ingestor.md) | `ingestor/*.py`, setpoint dispatcher, HA/Shelly/Tempest sync, `alert_monitor`, daily snapshot | `ingestor/*` | `docs/agents/ingestor.md` |
| [`genai`](docs/agents/genai.md) | `iris_planner.py`, `mcp/server.py`, `templates/`, prompts, scorecard/lessons/plan-evaluation | `genai/*` | `docs/agents/genai.md` |
| [`web`](docs/agents/web.md) | `api/main.py`, `scripts/generate-*`, `scripts/vault-*`, Quartz `site/` | `web/*` | `docs/agents/web.md` |
| [`saas`](docs/agents/saas.md) | Cloud Run, Cloud SQL, GCE MQTT, Firebase Auth, future React app | `saas/*` | `docs/agents/saas.md` |
| [`coordinator`](docs/agents/coordinator.md) | Schemas, migrations, CI, infra, cross-cutting refactors, review + merge | `coordinator/*` or direct to main | `docs/agents/coordinator.md` |

**Find your scope doc and read it before touching files.** Scope docs name what's yours, what adjacent agents touch, and what to route through coordinator.

## Shared territory

No agent owns these. Changes here go through coordinator (Jason) — file a focused PR, don't edit autonomously:

- `verdify_schemas/` — cross-layer Pydantic contracts; touched by every agent
- `db/migrations/` — schema migrations; serialized, reviewed holistically
- `docker-compose.yml`, `systemd/`, `traefik/`, `mqtt/`, `.github/workflows/` — infra
- `CLAUDE.md` (this file), `README.md`, `docs/agents/**` — organizational docs
- `pyproject.toml` — tool config

Rule: if the file listed here is in your diff, pause and ask coordinator.

## How agents coordinate

1. **Schema changes land first.** If your work needs a new `verdify_schemas/` model or a field addition, land that in a schema-only PR (coordinator reviews). Next cycle, the consumer PR (yours) lands against the new schema.
2. **Migrations are serialized.** One migration PR at a time across the whole repo. Coordinator approves the sequence.
3. **When you need another agent's territory**, file a focused PR into their scope, don't reach across. Label it `requested-by: {your-agent}` in the PR body. The owning agent reviews on their next cycle.
4. **Drift guards are the wire protocol.** If `verdify_schemas/tests/test_drift_guards.py` passes, two agents can merge independently — the boundary is intact.
5. **Hand off by doc, not by DM.** Anything a future session of any agent needs to know goes into that agent's `docs/agents/{name}.md` or a memory file, not into chat.

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

## Branches & sprints

- Each agent has its own sprint counter. Example: `ingestor/sprint-5-...`, `firmware/sprint-7-...`, `saas/sprint-11-...`.
- The old dual-stream numbering retires. The prior operational sprints (17–23) are documented in each agent's scope doc where they overlap.
- Sprints land as **one commit per sprint** with a detailed multi-section message (see `e96f9ba`, `47f8154` for examples).

## Worktrees & memory

- Worktrees: `/mnt/iris/verdify-worktrees/{firmware,ingestor,genai,web,saas}/`. The `main` worktree at `/mnt/iris/verdify` is coordinator-only.
- Persistent agent memory: `~/.claude-agents/verdify-{agent}/projects/-mnt-iris-verdify-worktrees-{agent}/memory/`.
- User-level and feedback memories (about Jason, how he likes to work) are shared across all agent dirs — duplicate them at the start of each agent's life.

## Backlog

See `docs/BACKLOG.md` for the cycle index. Per-agent backlogs in `docs/backlog/{agent}.md`. Cross-cutting work (schemas, infra, Grafana, deps) in `docs/backlog/cross-cutting.md`.

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

9. **Required PR artifacts** for firmware changes:
   - Replay diff output (`make firmware-replay OLD=<base> NEW=HEAD`)
   - Invariant-suite output (`make firmware-invariants`)
   - Unit-test delta (`make test-firmware`)
   - Coordinator (iris-dev) independent replay reproduction
   - Iris planner concurrence brief for any interface-level change

Coordinator approves merge only when all three reviewers (firmware agent, coordinator, iris) agree. Then 48-hour wait before OTA.

## Testing infrastructure (phase-0 deliverables)

- `make firmware-invariants` — runs the 15 bulletproof invariants (`firmware/test/invariants.h`) against the replay corpus. First breach fails.
- `make firmware-replay OLD=<ref> NEW=<ref>` — dual-worktree diff of firmware mode/relay decisions. Default THRESHOLD_PCT=0.
- `make replay-corpus-refresh` — pulls a fresh CSV from live DB, archives the prior corpus, validates no >5% size regression.
- `scripts/export-replay-overrides.sh` — CSV export includes outdoor sensors, equipment_state, mode_reason (sprint-15.1+).
