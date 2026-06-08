# Verdify — Agent Working Guide

This repo is worked by several Claude agents in parallel plus a human coordinator (Jason). Every session that edits code here should read this file first.

## What Verdify is

An AI-driven climate controller for a single 367 sq ft greenhouse in Longmont, CO. **Production** — plants are alive, the ESP32 is in the loop every 5 s, the planner runs on real data. Keeping the greenhouse operational ("Track A") always outranks SaaS/cloud refactor progress ("Track B"). See `README.md` for the architecture one-pager.

## Deployment reality — Verdify runs on k3s now (2026-06-07)

The legacy docker-compose/systemd stack on the **iris VM `.150` is DECOMMISSIONED and POWERED OFF**
(kept as instant rollback; `qm destroy` is a gated later step). **The whole product plane now runs
on the homelab k3s cluster, ns `verdify-prod`,** deployed GitOps via ArgoCD from this repo.

- **Deploy topology.** Kustomize: `deploy/k8s/base` + `deploy/k8s/components` + per-env overlays
  `deploy/k8s/overlays/{prod,prod-dark,staging,dev}`. Live prod = the **`prod` overlay** (ArgoCD app,
  auto-sync). The **`prod-dark` overlay** is a manual-sync dark/canary lane (ArgoCD app
  `verdify-prod-dark`) — build/validate there before touching live prod. `staging`/`dev` overlays
  are non-prod. Images are GHCR digests under `ghcr.io/verdifyconsultancy/verdify-*`; CI builds, the
  overlay pins the digest, ArgoCD reconciles. **git == live** is an enforced invariant on prod.
- **Edge / public surfaces.** `verdify.ai`, `lab.verdify.ai`, `graphs.verdify.ai` serve from k3s
  behind a two-tier Traefik edge: apps-Traefik (`.7.10` VIP, WAN edge) → in-namespace
  `verdify-traefik` → services. Cloudflare tunnel is the primary WAN path; the `.7.10` LAN edge +
  Pi-hole local DNS is the tunnel-down backup. The old `.100` tunnel is killed.

## The device-writer single-writer MASTER GATE (read before any ingestor/device change)

**Invariant: exactly ONE process writes the ESP32** (`192.168.10.111:6053`) at all times — never
two, never a sustained zero-writer gap. In k3s this is the **`verdify-ingestor` Deployment,
`replicas:1` + `strategy:Recreate`** (sole writer; runs on a worker, 0 restarts). **NEVER scale it
>1, never run a second writer (no parallel dev/staging writer against the live device — dev/staging
overlays pin the ingestor `replicas:0`).**

**Critical, non-obvious safety fact:** the firmware (`firmware/greenhouse.yaml`) sets
`api: max_connections: 20` and `reboot_timeout: 0s`. **So the device is NOT a natural fence** — two
ingestor pods CAN both connect and both push setpoints (real split-brain is physically possible),
and a zero-writer gap does NOT self-heal via reboot (the ESP32 holds its last commanded setpoint
until a writer returns — safe, high thermal mass). The exactly-one guarantee is therefore a
**`coordination.k8s.io` Lease (`verdify-ingestor-writer`) with renew-or-die self-fencing** (acquire
before connecting, renew every 10s, gate every push on a fresh lease, disconnect immediately on any
renewal failure incl. API-server partition). It is **built and ARM-READY but NOT yet armed (gated,
dev-first)**. Out-of-band oracle: `verdify-writer-exporter` DaemonSet (ns `observability`) →
`sum(verdify_esp32_writer_estab)` must be `1` (`>=2` = split-brain critical page). The single-writer
gate holds until the Lease is the live mechanism. **The greenhouse device/plant lane is Jason-gated;
the firmware-freeze rules below still apply.**

## Planning delivery path

Plan lifecycle is `gather → HERMES → key-delivery` (the `verdify-hermes-iris` deployment runs the
HERMES agent; planning gateway proven at gw=202). Touching the planner/MCP/HERMES path still routes
through the `genai` scope + firmware-freeze schema-restart rules below.

## HA posture (epic #225 / milestone M7) + firmware-optimization direction

- **HA.** Stateless surfaces (`www/lab/api/mcp/planner`) run **2 replicas, hard hostname
  `topologySpreadConstraints` + PDB `minAvailable:1`** (chaos-proven); resource governance is in
  place (CPU limits, per-ns `LimitRange`/`ResourceQuota`, 4-tier PriorityClasses). The DB is moving
  to **CNPG** (`verdify-db-cnpg`, 1+2 sync + Barman PITR) — **built + healthy but INERT**; live DB
  is still the `verdify-db` StatefulSet (`timescale/timescaledb:2.25.2-pg16`). Full design +
  chaos-test contract: `root/docs/verdify-ha-architecture-2026-06-07.md` (in the agents share).
- **Firmware optimization (SHADOW, epic #249).** Direction: **rip out the orchid time-of-day band
  curve** (#250) and **realign the VPD + temperature bands** (#251) via `crop_target_profiles`;
  shadow shows in-ideal-band 19.8%→58.6%, the band dashboard is live. Apply is gated (band-live-apply
  runbook + the firmware-freeze rules below).
- **Gated runbooks (Jason-gated, do not self-apply):** the writer-fence arm (HA-3.2 #240 / HA-3.1
  #239), the **gated atomic live-DB cutover to CNPG (#245)**, and the firmware band live-apply.
  Unblocked HA backlog is exhausted; what remains is the gated set.

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
- `deploy/k8s/**` (base/components/overlays — the LIVE k3s deploy; prod overlay is git==live),
  `traefik/`, `mqtt/`, `.github/workflows/` — infra. (`docker-compose.yml`/`systemd/` are the
  retired `.150` VM stack, kept only for reference/rollback — not the live runtime.)
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
