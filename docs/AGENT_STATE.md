# Agent State

Last updated: 2026-06-14

## Current Purpose

Verdify is the production control platform for one 367 sq ft greenhouse in
Longmont, CO. Track A is live greenhouse safety and continuity. Track B is
platform/product evolution through the k3s/GitOps service plane.

Future Codex sessions should start with `AGENTS.md`, then read this file,
`README.md`, `AGENT_LANE.md`, `PROJECT_BOARD.md`, `EPICS.md`, and the relevant
runbook or architecture references before editing.

## Architecture Pointers

- `docs/SERVICE_MAP.md` is the current service/API/k8s map for the
  `verdify-platform` lane.
- `docs/runbooks/laptop-operator.md` is the operator path for DB access,
  pipeline triggers, promotion, prod sync, and OTA flow.
- `deploy/k8s/argocd/apps/` defines the dev/prod ArgoCD applications.
- `deploy/k8s/base`, `deploy/k8s/components`, and `deploy/k8s/overlays/{dev,prod}`
  define desired app state. Staging is retired.
- `deploy/k8s/SECRETS.md` documents secret names and keys only; never expose raw
  secret values.
- `docs/site-publishing-pipeline.md` is the current lab.verdify.ai publishing
  path: k3s `verdify-lab-publisher`, S3-compatible object store as durable
  content/public/state, and PVC as live cache only.
- `docs/SYSTEM-ARCHITECTURE.md` and `docs/FOLDER-HIERARCHY.md` remain useful but
  include VM-era details; prefer `AGENTS.md`, this file, and the k3s manifests
  when docs conflict.

## Active Plans

- GitHub issues are the live tracker for `VerdifyConsultancy/verdify-platform`.
- `EPICS.md`, `MILESTONES.md`, `SPRINTS.md`, and `PROJECT_BOARD.md` mirror the
  lane-level planning state.
- Issue #331 is closed as the API/service-map workstream; its durable artifact
  is `docs/SERVICE_MAP.md`.
- Issue #334 is closed as the lane board normalization workstream; its durable
  artifact is `PROJECT_BOARD.md`.
- Issues #286, #111, and #112 are closed as superseded by the canonical
  `verdify-platform` board and current two-environment operating model.
- Issue #332 keeps the Fable workstream in clarification until in-repo code,
  docs, or issue evidence exists.

## Known Risks / Blockers

- Production is live. Do not create a second ESP32/device writer.
- Jason is the human gate for firmware OTA, prod ArgoCD sync that can touch the
  live writer, device VLAN work, destructive prod DB work, credential rotation,
  and public DNS/edge/org changes.
- The exact required `verdify-platform` project board exists as
  <https://github.com/orgs/VerdifyConsultancy/projects/5> with EPIC-level cards.
  Keep issue `## Project Tracking` blocks as the durable fallback.
- Some architecture docs predate the 2026-06-10 branch/deployment simplification
  and the k3s service-plane work.
- 2026-06-14 planner recovery: Hermes storage remount, OpenAI credential repair,
  and MCP `ClientIP` session affinity restored manual planning.
- 2026-06-14 lab publishing cutover: `verdify-lab-publisher` now runs in k3s
  and uses S3-compatible storage as durable source of truth. The
  `verdify-lab-site-cache` PVC is only the build/serve cache; lab publishing no
  longer depends on NAS/NFS content paths.
- The DB NetworkPolicy includes a DB-port-only pod-CIDR fallback for publisher
  pods because the CNI did not honor the narrower namespace/pod selector during
  short-lived CronJob DB checks. Keep the fallback scoped to TCP/5432 and revisit
  if CNI policy behavior is fixed.
- The k3s ingestor still logs VM-era local MCP restart failures and a missing
  `/mnt/agents/iris/skills/greenhouse-planner.md` playbook path. They did not
  block the 2026-06-14 21:00Z trigger ack, but fixing either in prod would
  restart the live ingestor/device writer and requires Jason's gate.

## Last Verified Commands

- 2026-06-13: GitHub Project #5 rechecked with 17 items and 22 fields; no open
  EPIC issue was missing from the canonical board.
- 2026-06-13: `git diff --check` passed for the board/issue audit docs.
- 2026-06-13: `git diff --check` passed for lane board normalization docs.
- 2026-06-13: `git diff --cached --check` passed for lane-tracking and
  service-map docs.
- 2026-06-13: GitHub CI and Container Publish were green on `main` after the
  board-normalization docs push.
- 2026-06-14: Manual planner recovery wrote `plan_journal` row
  `iris-20260614-1438` at `2026-06-14 20:39:48Z`; ack-only smoke row 1366
  reached `acked`.
- 2026-06-14: Live `verdify-mcp` Service patched to `sessionAffinity=ClientIP`
  with 10800s timeout; durable manifest change is in
  `deploy/k8s/base/mcp-deployment.yaml`.
- 2026-06-14: SOPS/age encrypted S3 Secrets were added in
  `/Users/jason/repos/agent-fleet-control` for `verdify-dev` and `verdify-prod`
  as `verdify-lab-publisher-s3`; live Secrets were applied without storing raw
  secret values in this repo.
- 2026-06-14: Scheduled `TRANSITION` trigger at `2026-06-14 21:00:36Z`
  reached `acked` in row 1370 with Hermes run
  `run_20df11bb564c414f99da341f7efcc689`.
- 2026-06-14: `bash -n scripts/lab-publish-k3s.sh
  scripts/lab-publisher-docker-shim.sh scripts/rebuild-site.sh
  scripts/publish-site-content.sh` passed.
- 2026-06-14: `actionlint .github/workflows/container-publish.yml` passed.
- 2026-06-14: `kubectl kustomize deploy/k8s/overlays/{dev,prod}` rendered the
  lab publisher and MCP affinity, with zero `kind: Secret` objects emitted.
- 2026-06-14: `docker build -f scripts/Dockerfile.lab-publisher -t
  verdify-lab-publisher:codex-local .` passed using Colima with a temporary
  Docker config that removed the broken Docker Desktop credential helper.
- 2026-06-14: Local container smoke passed for `verdify-lab-publisher:codex-local`
  (wrapper present, Quartz docs present, generator files present, firmware
  tunable YAML present, Python imports ok, Quartz CLI starts).
- 2026-06-14: `git diff --check` passed for the current doc/config/script diff.
- 2026-06-14: Pushed final amd64 bootstrap publisher image
  `ghcr.io/verdifyconsultancy/verdify-lab-publisher:bootstrap-20260614222642-portable-lockfix`
  at digest `sha256:7dbb28f2aa95e28855e4cb642dc102d396ef5ab387fb5c86f69099e0485300d8`
  and pinned dev/prod/prod-dark overlays plus the live prod CronJob to that
  digest. Repo manifests now supersede it with the CI-built k3s package below.
- 2026-06-14: CI published the repo-linked replacement image package
  `ghcr.io/verdifyconsultancy/verdify-lab-publisher-k3s` at digest
  `sha256:5f734f8dac3bc08665445e8d09f5e9768f3f82d737962a890112628ee34f4a7b`
  after the historical `verdify-lab-publisher` package rejected `GITHUB_TOKEN`
  pushes because it was not linked to this repository.
- 2026-06-14: Verdify S3 target is bucket `verdify-platform` on
  `https://s3-hdd.vallery.net` with signing region `garage`. Seal/apply it as
  `verdify-lab-publisher-s3` via SOPS/age; prod uses prefix `lab`, dev uses
  `lab-dev`.
- 2026-06-14: S3 content was seeded from the local website tree to
  `lab/content` and `lab-dev/content` (372 objects, about 403 MB each).
- 2026-06-14: Final-digest scheduled publisher run succeeded. Live CronJob
  status recorded `lastScheduleTime=2026-06-14T22:40:00Z` and
  `lastSuccessfulTime=2026-06-14T22:42:46Z`: the publisher generated
  `plans/2026-06-14.md`, rebuilt 297 pages, uploaded content/public/state to S3,
  and served `https://lab.verdify.ai/plans/2026-06-14` with HTTP 200. Live
  CronJob is unsuspended and pinned to
  `sha256:7dbb28f2aa95e28855e4cb642dc102d396ef5ab387fb5c86f69099e0485300d8`.
- 2026-06-14: S3 `head-object` check confirmed
  `lab/content/plans/2026-06-14.md` (35182 bytes) and
  `lab/public/plans/2026-06-14.html` (59747 bytes); live lab pod served the HTML
  from `/usr/share/nginx/html/plans/2026-06-14.html`.
- 2026-06-14: GitHub Actions `CI`, `Container Publish`, and `K8s Manifests`
  were green on `main` after the S3-backed publisher merge and format follow-up.
- 2026-06-14: GitHub Actions `CI` and `Container Publish` were green on
  `df0fd1f`; the publish run proved `verdify-lab-publisher-k3s` is writable by
  the repository workflow token and pushed an overlays/dev digest bump.
- 2026-06-14: `18aa498` switched k8s manifests to
  `ghcr.io/verdifyconsultancy/verdify-lab-publisher-k3s@sha256:5f734f8dac3bc08665445e8d09f5e9768f3f82d737962a890112628ee34f4a7b`.
  GitHub Actions were green: CI `27515244921`, Container Publish `27515244935`,
  K8s Manifests `27515244931`. Dev ArgoCD was refreshed and reached
  Synced/Healthy at `18aa498`.
- 2026-06-14: Jason approved prod push for the lab site cache-policy fix. Full
  `verdify-prod-dark` sync was not used because the app diff included unrelated
  device-adjacent resources; instead only `verdify-lab-nginx-config`,
  `Deployment/verdify-lab`, and `CronJob/verdify-lab-publisher` were applied
  from the prod overlay. `kubectl diff -f /tmp/verdify-prod-lab-only.yaml`
  returned clean after apply. Public `https://lab.verdify.ai/` and
  `/static/contentIndex.json` now return
  `Cache-Control: no-cache, no-store, must-revalidate`, `Pragma: no-cache`, and
  `Expires: 0` from origin nginx. The live prod publisher CronJob uses
  `ghcr.io/verdifyconsultancy/verdify-lab-publisher-k3s@sha256:5f734f8dac3bc08665445e8d09f5e9768f3f82d737962a890112628ee34f4a7b`.
  ArgoCD app `verdify-prod-dark` still reports OutOfSync/Progressing due to
  broader unrelated drift; do not treat that as a failed lab cache rollout.

## Next Recommended Codex Prompt

```text
Wake in /Users/jason/repos/verdify-platform. Read AGENTS.md, README.md,
docs/AGENT_STATE.md, AGENT_LANE.md, PROJECT_BOARD.md, EPICS.md, and
docs/SERVICE_MAP.md. Report branch/worktree state, access assumptions, live
greenhouse safety gates, relevant tracker items, and the smallest safe
verification path before proposing edits.
```
