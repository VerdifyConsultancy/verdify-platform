# Verdify — Agent Working Guide

This repo is worked by **autonomous agent(s) running in the k3s cluster**
(Codex/Claude with kubectl, the in-cluster DB, and prod secrets) plus Jason as
the human gate for device-affecting and outward-facing actions. **As of
2026-06-18 development moved off Jason's laptop** to k3s-resident agents — **read
[`docs/handoff/k3s-agent-handoff.md`](docs/handoff/k3s-agent-handoff.md) first**
for the operating model, the kubectl-host-portable dev loop (build/test/DB/deploy),
repo-as-source-of-truth, the gated firmware-OTA procedure, and the known autonomy
blockers. Every session that edits code here should read this file (via
`AGENTS.md`), the handoff doc, and `README.md` first. (Retired: the laptop
single-agent model and the earlier five-persistent-agents model.)

## What Verdify is

An AI-driven climate controller for a single 367 sq ft greenhouse in Longmont, CO. **Production** — plants are alive, the ESP32 is in the loop every 5 s, the planner runs on real data. Keeping the greenhouse operational ("Track A") always outranks SaaS/cloud refactor progress ("Track B"). See `README.md` for the architecture one-pager.

## Codex operating protocol

Goal: a future Codex session should be able to wake up from repo files, report
the current operating constraints, and propose a safe plan before editing. Do
not rely on chat history for project state.

First-turn orientation, before editing:

1. Read this file through `AGENTS.md` (symlink to `CLAUDE.md`), then
   `README.md`. For lane, board, ArgoCD, access, or handoff work, also read the
   current root lane docs: `AGENT_LANE.md`, `PROJECT_BOARD.md`, `EPICS.md`,
   `MILESTONES.md`, `SPRINTS.md`, `HISTORY.md`, `ARGOCD.md`,
   `ACCESS_MATRIX.md`, and `COORDINATION_REQUESTS.md`. If local Orbit context is
   available, also read
   `/Users/jason/Orbit/context_dump/verdify-platform/MANIFEST.md` and any moved
   file relevant to the task.
2. Inspect repo state: `git status --short --branch`, `git log --oneline -n 10`,
   and any visible `AGENTS.override.md` or local config such as `.codex/` /
   `.claude/`.
3. Inspect the authoritative discovery surfaces before choosing commands:
   `Makefile` (`make ci` is THE validation gate), `pyproject.toml`,
   `scripts/ci-local.sh`, `.pre-commit-config.yaml`,
   and area manifests such as `site/package.json`, `planner_graph/pyproject.toml`,
   and `*/requirements*.txt`.
4. Read the relevant architecture and operation references for the touched
   area: `docs/SYSTEM-ARCHITECTURE.md`, `docs/FOLDER-HIERARCHY.md`,
   `docs/runbooks/laptop-operator.md`, `docs/RUNBOOK.md`,
   `docs/BCDR-AND-OPERATIONS.md`, `docs/adr/`, and the matching
   `docs/agents/*.md` subsystem note.
5. Report a short access summary before risky work: filesystem scope, network
   status, approval policy, current branch/worktree state, secrets policy, and
   anything unavailable or unverified.
6. State the plan and verification path before edits unless the task is a tiny
   direct change. If production, device, migration, schema, or firmware behavior
   may be affected, call that out explicitly.

Discovery rules:

- Use `grep -rnE` (or Python globs), `make help`, and the CI workflows to
  discover structure, entrypoints, tests, and dependency manifests. **`rg`/
  ripgrep is unreliable in this repo** — it silently returns empty/misses,
  especially in `.sql` files; cross-check any "zero hits" with `grep`.
- Prefer references over duplicated instructions. README is the one-page
  architecture summary; the Makefile defines commands (`make ci` = the gate;
  GitHub Actions was REMOVED 2026-07-11 — all CI is local/in-cluster); runbooks define laptop,
  deploy, DB, and OTA operations.
- Treat GitHub issues on `VerdifyConsultancy/verdify-platform` as the live
  tracker. Historical backlog, handoff, sprint, audit, evidence, and context
  files were moved to `/Users/jason/Orbit/context_dump/verdify-platform/`.
- Treat the root lane docs as current operating indexes for `verdify-platform`;
  they summarize GitHub Project Board fallback tracking, active epics,
  milestones, sprint labels, history, ArgoCD ownership, access boundaries, and
  coordination requests.
- Older docs may predate the 2026-06-10 branch/deployment simplification. When
  docs conflict, prefer this file, `docs/runbooks/laptop-operator.md`, CI, the
  current worktree, and the Orbit context dump only as historical context.

Safety and do-not rules:

- No destructive git commands (`git reset --hard`, `git checkout --`, force
  pushes, history rewrites) unless Jason explicitly asks for that operation.
- No secret exposure: never print, paste, commit, log, or summarize raw tokens,
  passwords, API keys, client secrets, private keys, or decrypted secret files.
  Reference credential locations and auth modes only.
- No broad rewrites or style churn. Keep changes scoped to the requested
  behavior/docs and the touched ownership boundary.
- No production-impacting changes without explicit approval: firmware OTA,
  prod ArgoCD sync, device VLAN actions, destructive prod DB work, credential
  rotation, public DNS/edge/org settings, or anything that can create a second
  live device writer.
- Do not wrap self-committing migrations in an outer transaction. Use the
  migration safety tooling in this file.

Definition of done:

- The requested change is implemented with minimal product-code/config/docs
  surface.
- Relevant repo docs or the Orbit context dump capture any handoff state a
  future session needs.
- The smallest safe verification has been run and the result is recorded. If a
  required command cannot run, state why and what remains unverified.
- `git status --short` is reviewed so unrelated user changes are not hidden.

Verification order:

1. For docs-only changes, run `git diff --check`. There is no repo-level
   markdown lint configured as of 2026-06-12.
2. For Python/runtime changes, run `make lint`, then `make test`.
3. For schema or migration changes, also run `make migration-rollback-safety`
   and the targeted rollback proof described by the migration.
4. For firmware logic or ESPHome changes, run `make test-firmware`,
   `make firmware-invariants`, the required replay diff
   (`make firmware-replay OLD=<base> NEW=HEAD` or worktree variant), and
   `make firmware-check`. For **band-CURVE** changes — `greenhouse_solar.h`
   `band_value_at_phase()`, the anchor resolution, or anything that changes the
   shape of the diurnal band — the stock replay is **corpus-fed and will show 0
   divergence**, so ALSO run `make firmware-replay-band OLD=<base>` (derives
   setpoints from the curve and reports the real behavioral diff). This is the
   gap that let a lumpy/wet-night curve ship blind.
5. For lighting changes, run `make lighting-audit-static` and the live/current
   audit only when the task and access make live checks appropriate.
6. For site/UI changes, run the relevant site command from `Makefile` or
   `site/package.json` and verify render locally.

Handoff protocol:

- For repo-owned durable decisions, update the relevant file under `docs/`.
- For out-of-lane context, historical tracking, handoff, cleanup inventory, or
  one-off evidence artifacts, move/update files under
  `/Users/jason/Orbit/context_dump/verdify-platform/`.
- Put durable decisions, invariants, and runbook changes in `docs/`, not only in
  chat. Keep context dumps concise; link to deeper docs instead of copying them
  into multiple places.

## Branch & deployment model (Jason, 2026-06-10; SINGLE-ENV 2026-06-16)

- **`main` is the single canonical branch.** PRs land on main; all CI
  (build/publish/validate) and every ArgoCD targetRevision point at main. The
  2026-05-31 `live/platform-main` deploy branch is RETIRED.
- **ONE environment — prod only.** The `verdify-dev` proving environment AND
  the staging overlay are **DECOMMISSIONED and DELETED** (2026-06-16: ns / DB /
  PVC / PV+Synology-LUN / ArgoCD app gone; `overlays/{dev,staging}` removed
  here; `applications/local-dev/verdify.yaml` removed from `jvallery/agents`).
  Prod (ns `verdify-prod`, ArgoCD app `verdify-prod-dark` — legacy name) is the
  only env and serves lab/graphs/api.verdify.ai. It is **manual-sync behind the
  device-write gate**. NOTE: `verdify-www` (verdify.ai/www marketing) and
  `verdify-crm` are SEPARATE products in SEPARATE repos — unrelated to the
  greenhouse, do not touch.
- **Pipeline (single-env, ZOT as of 2026-07-11 / ADR-0021):** GitHub Actions
  VALIDATES only — `Container Publish` builds every image with `push: false`
  (a Dockerfile/COPY break still fails the push/PR). PUBLISHING is in-cluster:
  `repo-build` Argo Workflows in ns `agent-fleet-ci` (Kaniko) build the exact
  main revision and push `registry.vallery.net/verdifyconsultancy/<image>@sha256`
  to the zot origin; digests are pinned into `overlays/prod/kustomization.yaml`
  by a digest-only commit (procedure: `docs/runbooks/prod-promotion.md`).
  GHCR is retired for publishing. A human reviews the pin commit and an
  operator runs the gated `argocd app sync verdify-prod-dark`. Workloads pull
  with the `zot-origin-cluster-pull` secret.
- **No dev device / no dev DB.** Firmware is hot-staged direct to prod. There is
  no nightly prod-restore copy anymore (it lived in dev).
- **Operating from the laptop:** see `docs/runbooks/laptop-operator.md` for
  DB access (`scripts/verdify-db.sh prod`), pipeline triggers, promotion, the
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
lands autonomously on `main`, keeping `make ci` green.

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
- `make ci` (scripts/ci-local.sh) runs the classification on every gate run;
  GitHub Actions is removed — the gate is local/in-cluster.

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
Historical `docs/BACKLOG.md` / `docs/backlog/*` files were moved to
`/Users/jason/Orbit/context_dump/verdify-platform/`; don't recreate or extend
them in this repo.

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

8. **Every firmware PR must show a replay-diff.** `make ci` with `CI_BASE_REF=<base>` runs `scripts/firmware-replay-diff.sh` against merge-base. Default `THRESHOLD_PCT=0` means zero mode/relay divergence allowed. Intentional divergence (e.g. Phase 2 dwell-gate rollout) requires coordinator approval + explicit `THRESHOLD_PCT` override in the PR.

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
