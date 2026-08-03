# Verdify — Agent Working Guide

This repo is worked by several Claude agents in parallel plus a human coordinator (Jason). Every session that edits code here should read this file first.

## What Verdify is

An AI-driven climate controller for a single 367 sq ft greenhouse in Longmont, CO. **Production** — plants are alive, the ESP32 is in the loop every 5 s, the planner runs on real data. Keeping the greenhouse operational ("Track A") always outranks SaaS/cloud refactor progress ("Track B"). See `README.md` for the architecture one-pager.

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

<!-- BEGIN agent-fleet CI/CD contract (managed — rendered by jvallery/agents) -->
<!-- agent-fleet:contract-digest sha256:821a15617f6ba4510f008bcc64532d4760ad059c968c4717e5ff74e9f962e022 -->
## CI/CD contract — `VerdifyConsultancy/verdify-platform`

This block is **rendered centrally** by `jvallery/agents` from `control-plane/agent-fleet-control/registry/repos/gh-1247193937.yaml` (`scripts/render_repo_guidance.py`).

**Do not hand-edit anything between the sentinels.** Edits here are overwritten on the next render, and a hand-edit is a *gate failure*, not a merge conflict. To change what this says, change the registry record and open a PR against `jvallery/agents`. Everything outside the sentinels is this repo's own, and the renderer never touches it.

**For CI/CD, this managed block is the repo's durable plan of record.** It supersedes conflicting repo-local CI/CD instructions, approval holds, workflow handoffs, and historical status notes outside the sentinels. Product governance, privacy, Secret handling, storage safety, and destructive-change controls still apply. Resolve a contract conflict in the central registry; do not bypass either boundary.

### How this repo validates and builds

Run CI **from this repo's fleet pod**, against an exact pushed commit:

```
agent-ci-validate --submit --wait --revision <40-character-sha>
```

The helper reads this repo's committed `.agent-fleet/ci.yaml` `checks.steps[]`. The server-side admission policy independently binds this pod's ServiceAccount to the same repository URL, exact commands, images, fixed `repo-validate` template, metadata, and parameter order; the SHA is the only caller-variable. An arbitrary Workflow, command, image, Secret selector, or cross-repo checkout is denied.

Checkout credentials are short-lived, repository-restricted GitHub App tokens minted inside the platform template. They are never caller-selected and never passed to the validation container. Success means the created Workflow reaches `Succeeded`; `agent-ci-validate --wait` exits non-zero on red, error, or timeout.

Images build **in-cluster with Kaniko** and push to the durable zot origin at `registry.vallery.net/verdifyconsultancy/<image>`.

- The build definition is `.agent-fleet/ci.yaml` in THIS repo (`images[]`: name, dockerfile, context). The repo owns the WHAT; the platform's generic `repo-build` WorkflowTemplate is the HOW.
- Trigger every declared image from the repo pod: `agent-ci-build --submit --wait --revision <40-character-sha>`. The returned value is the immutable zot `@sha256:` pin.
- The pod has create-only Workflow RBAC only when its registry `ci_submission` contract is enabled. Admission fixes repository, template, build/test controls, destination image, resources, and publisher scope; the pod has no Secret, Job, Workflow update, or Workflow delete authority.
- GitHub Actions, where retained, is validation-only and never a publisher. The authoritative publication proof is the repo-pod Workflow and its zot digest.

**`ghcr.io` is banned (ADR-0021).** There is no configuration that selects it. `registry.vallery.net` is the durable origin and images are consumed by `@sha256:` digest, never by a mutable tag. The in-cluster push target is `registry-origin.registry-origin.svc.cluster.local:5000`; `192.168.7.41:5000` is a base-image pull-through cache and is **never** a push target.

### How this repo deploys

Merged changes reach the cluster through **ArgoCD**, and only through ArgoCD. The Applications that sync from this repo:

| Application | sync policy | prune |
| --- | --- | --- |
| `verdify-platform-lab-stage` | disabled (`enabled: false`) | **no** |
| `verdify-prod-dark` | gated / manual (declared outside this repo) | **no** |

**"Deployed" means ArgoCD reports Synced *and* Healthy** — not that the PR merged and not that the object exists.

**Rollback is currently only half-built — do not promise it.** `prune` is off on almost every Application in this fleet, so reverting a commit removes the resource from the rendered manifests but **does not delete it from the cluster**: it orphans. A `git revert` rolls back what a manifest *says*, not what is *running*. The intended end state is a ring model (dev ring → prod, with prune on so a revert is a true rollback); **that is a target, not today's behaviour.** Until it lands, undoing a deploy means an explicit, change-gated removal — plan for it before you deploy, not after.

### Its required check

**This repo reports a legacy commit status, not a check-run — and the obvious probes lie about it in both directions.** Measured 2026-08-02:

```
gh run list --repo VerdifyConsultancy/verdify-platform
  # SHOWS GREEN RUNS — and they are DEAD HISTORY. The workflows that produced them
  # no longer exist (.github/workflows is 404); the newest is weeks old. Reading
  # this as 'CI is passing' is the single most likely wrong conclusion here.

gh api repos/VerdifyConsultancy/verdify-platform/commits/<sha>/check-runs
  # total_count: 0 — always. This repo produces no check-runs at all.

gh api repos/VerdifyConsultancy/verdify-platform/commits/<sha>/status       # <- THE ONLY ONE THAT SEES IT
```

The status posts on **pull-request head commits only**; the tip of the default branch carries `total_count: 0`, so "is main green?" has no answer here. The statuses also arrive with `creator: null` and `target_url: null` — there is no link back to the producing system, so a failure is **not diagnosable from GitHub**; you must go to the system that posted it.

Always verify this repo's CI with `commits/<sha>/status`, on the PR head.

**A required check blocks merge on this repo.** The context(s) that must be green on the head commit:

- `Verdify Platform / Argo PR CI`

New contract-managed contexts are namespaced `fleet-ci/<trust-class>/<check-name>` (e.g. `fleet-ci/validation/unit`); the `fleet-ci/` prefix is reserved for them.

**A red check in this estate frequently means the job never ran.** Before you believe either colour, look at the run's `steps` and `runner_name`. Classify every failure before retrying: a *code-failure* is yours to fix and retrying it without a diff is prohibited; an *infra-failure* (runner pickup timeout, image pull) may be retried.

### Where its secrets come from (NAMES only — never values)

**No workflow in this repo may carry a secret value, and no agent may print, paste, commit or log one.** Everything below is a reference: a Kubernetes Secret name, a SOPS/ksops path, or a scoped identity. Values live sealed in `jvallery/agents` under `platform/gitops/secrets-ksops/` and are mounted by the substrate.

| what | reference |
| --- | --- |
| GitHub auth | `github-app-installation` / profile `repo-agent-standard` / activation `enabled` |
| Zot push credential | a cluster-side reference scoped to `verdifyconsultancy` — mounted by the build substrate, **never** a GitHub Actions secret and never in this repo |

**The validation command container gets no secrets and no Kubernetes authority.** A separate platform-owned fetch init mints a short-lived, contents-read token from the CI GitHub App and hands over only the checkout; the repo pod cannot select the App Secret or read it. Repo/org Actions secrets are not part of this path. A GitHub Actions job that listens on `pull_request` and references a secret fails lint. `packages: write` is a lint failure everywhere (it only exists to reach ghcr, which is banned).

A Kubernetes `Secret` is secret-bearing **in full**, including every annotation value — `kubectl.kubernetes.io/last-applied-configuration` replays the entire `data` block. Never dump annotations on a Secret; select named fields only.

### What you may do WITHOUT asking

This section exists to remove human gates, not to add them. If an action is listed here, **do it — do not ask.**

- **Commit and push** routine changes on a branch, and **open the PR**.
- **Merge your own green PR** — it is not a draft, its check is green *on the head commit after any rebase*, and merging it is not itself a delivery action into a live cluster.
- **Re-run a generated artifact's renderer** and commit the result. Generated files are regenerated, never merged by hand: on a conflict, rebase and re-run the renderer.
- **Retry an infra-failure** (runner pickup timeout, image pull); classify first.
- **Fix your own red check** and push again, as many times as it takes.

**Ask first** — these are irreversible or reach beyond this repo:

- Any **destructive cluster mutation** (delete a workload, wipe a PVC) or any change to an un-IaC'd surface (UniFi/UDM, the NAS). These go through the **change-gate** (snapshot → human `APPLY` → dead-man → post-verify) and never run autonomously.
- **History rewrite / force-push** to a shared branch, credential rotation, mass changes across repos, and anything touching another operator's environment.
- **`--admin` or any protection bypass.** Never. If a rule blocks you, the rule is working; report it.

When you cannot tell how reversible something is, propose it and confirm.

**Never claim done from a merge.** Merging is not shipping. A claim carries a UTC timestamp and the literal probe, and is re-probed later with the identical command (`GREEN at <T>, re-verified at <T+N>`). A regressed re-probe is not done. Bare "GREEN/done/✅" is banned.

### Runner constraints that will bite you

**ARC runners set `no_new_privs`.** `sudo` cannot elevate, so installing a tool to a system path fails — while the download succeeds, which is why this was twice misdiagnosed as absent runners and then as blocked egress:

```
sudo mv /tmp/kustomize /usr/local/bin/
  -> sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
```

Install to your own path instead:

```yaml
mkdir -p "$HOME/.local/bin"
install -m 0755 /tmp/<tool> "$HOME/.local/bin/<tool>"
echo "$HOME/.local/bin" >> "$GITHUB_PATH"
export PATH="$HOME/.local/bin:$PATH"   # so THIS step's own verification resolves
```

`$GITHUB_PATH` covers *subsequent* steps; the in-step `export` is what makes the install's own verification line work. This hardening is deliberate and is not to be relaxed.

**The runner service account has no RBAC.** A validation job reaches the Kubernetes API and may read nothing (`kubectl auth can-i list nodes` → `no`). A job needing cluster reads must ship an explicit, minimal, reviewed grant — and creating one is a change-gated mutation, not part of onboarding.

---

_Fleet CI contract: `docs/fleet-ci-contract.md` in `jvallery/agents`. Delivery mode `argocd`; autonomy `standard`. This block's freshness and integrity are gated by `repo_guidance_guard.py` — a stale or hand-edited block fails the fleet build._
<!-- END agent-fleet CI/CD contract (managed — rendered by jvallery/agents) -->
