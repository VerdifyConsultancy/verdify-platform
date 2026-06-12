# Verdify Agent State

Last updated: 2026-06-12

This is the concise handoff for Codex sessions. Keep it factual, link to deeper
docs, and update it when a session leaves state a future agent needs.

## Current repo purpose

Verdify is the production control stack for a single 367 sq ft greenhouse in
Longmont, CO. The ESP32 owns real-time relay control, the ingestor captures
telemetry and dispatches setpoints, TimescaleDB stores telemetry/views/plans,
and Iris plans through MCP/Hermes. Track A is keeping the greenhouse healthy;
Track B is platform/SaaS/deploy evolution.

## Architecture pointers

- One-page architecture and component map: `README.md`.
- Full system shape: `docs/SYSTEM-ARCHITECTURE.md`.
- Repository and host path map: `docs/FOLDER-HIERARCHY.md`.
- Laptop operator flow for DB, CI/CD, prod promotion, and OTA:
  `docs/runbooks/laptop-operator.md`.
- Operations and failure modes: `docs/RUNBOOK.md` and
  `docs/BCDR-AND-OPERATIONS.md`.
- Subsystem invariants: `docs/agents/firmware.md`,
  `docs/agents/ingestor.md`, `docs/agents/genai.md`,
  `docs/agents/web.md`, and `docs/agents/saas.md`.
- Future device-write design: `docs/adr/0001-device-write-api-and-single-writer-at-scale.md`.

## Active plans and milestones

- Current task brief lives in `GOAL.md`: refactor greenhouse lighting so
  occupancy is lux-gated task-light demand rather than unconditional lights-on,
  with firmware, traceability, dashboard/status, and validation changes.
- GitHub issues on `VerdifyConsultancy/verdify-platform` are the authoritative
  tracker.
- Current board/milestone map: `docs/verdify-backlog-replan-2026-06-09.md`.
- Branch/deploy model was simplified on 2026-06-10: `main` is canonical,
  `verdify-dev` auto-syncs, prod is manual-sync behind the device-write gate.
  Use `AGENTS.md` and `docs/runbooks/laptop-operator.md` over older branch
  descriptions when they conflict.
- Least-privilege application lane docs now live at the repo root:
  `AGENT_LANE.md`, `APP_INVENTORY.md`, `ACCESS_MATRIX.md`,
  `SECRETS_AUDIT.md`, `COORDINATION_REQUESTS.md`, and `FINAL_REPORT.md`.
  They are provisional because the lane prompt did not provide a concrete
  namespace/environment.
- Branch/worktree cleanup inventory:
  `docs/git-cleanup-inventory-2026-06-12.md`. Follow-up tracker:
  GitHub issue #330 for archived-branch review.

## Known risks and blockers

- This is a live greenhouse. Firmware OTA, prod ArgoCD sync, device VLAN work,
  destructive prod DB work, credential rotation, and public edge/org changes are
  Jason-gated.
- Firmware and schema-adjacent work must follow the freeze rules in `AGENTS.md`,
  including replay diff, invariants, unit-test artifacts, and restart
  documentation where applicable.
- Migrations must be serialized and classified with
  `scripts/check_migration_rollback_safety.py`; never wrap a self-committing
  migration in an outer `BEGIN`/`ROLLBACK`.
- Some older docs are historical. Prefer `AGENTS.md`, this file, current CI,
  `Makefile`, `docs/runbooks/laptop-operator.md`, and live GitHub issues.

## Last verified commands

- 2026-06-12 orientation pass:
  - `git status --short --branch` -> `## main...origin/main` before docs edits.
  - `git log --oneline -n 12` inspected recent history.
  - `Makefile`, `pyproject.toml`, `.github/workflows/ci.yml`,
    `.pre-commit-config.yaml`, `site/package.json`, and
    `planner_graph/pyproject.toml` inspected for verification sources.
- 2026-06-12 docs-only Codex protocol update:
  - `git diff --check` -> passed for tracked Markdown changes.
  - `if rg -n "[[:blank:]]$" docs/AGENT_STATE.md docs/CODEX_WORKFLOW.md; then exit 1; fi`
    -> passed for new Markdown files.
- 2026-06-12 least-privilege lane codification:
  - Read-only repo discovery only; no live Kubernetes namespace was queried
    because the assigned namespace remained a placeholder.
  - `kustomize build deploy/k8s/overlays/{dev,prod,prod-dark,staging}` rendered
    locally for inventory extraction.
  - The same four overlay renders emitted `0` `kind: Secret` objects, matching
    `deploy/k8s/SECRETS.md` placeholder-exclusion contract.
  - `git diff --check` -> passed.
  - Secret scan over the six new root lane docs found no raw secret-looking
    values.
- 2026-06-12 branch/worktree cleanup and Codex protocol commit prep:
  - `git diff --check` -> passed.
  - Secret-pattern scan over changed docs -> no matches.
  - `kustomize build deploy/k8s/overlays/{dev,prod,prod-dark,staging}` ->
    `0` rendered `kind: Secret` objects for each overlay.
  - `make lint` -> passed.
  - `.venv/bin/ruff format --check ingestor/ api/ mcp/ scripts/*.py tests/ verdify_schemas/`
    -> passed after formatting `scripts/reconcile-derived-history.py`.
  - `.venv/bin/python -m pytest tests/test_migration_rollback_safety.py -q`
    -> 24 passed.
  - `.venv/bin/python -m pytest tests/test_device_write_gate.py -v`
    -> 8 passed.
  - `make test` was attempted and failed because this Mac is not running the
    live-stack smoke-test prerequisites (`verdify-timescaledb` Docker
    container, `systemctl`, and `/srv/greenhouse/.venv`).
  - Local schema/drift suites that query Docker Postgres were also blocked by
    the missing `verdify-timescaledb` container.

## Next recommended Codex prompt

Wake up in `/Users/jason/repos/verdify-platform`. Read `AGENTS.md`,
`docs/AGENT_STATE.md`, `AGENT_LANE.md`, `APP_INVENTORY.md`,
`ACCESS_MATRIX.md`, `SECRETS_AUDIT.md`, `COORDINATION_REQUESTS.md`,
`FINAL_REPORT.md`, `docs/git-cleanup-inventory-2026-06-12.md`, `README.md`,
`GOAL.md`, `docs/runbooks/laptop-operator.md`, `Makefile`, `pyproject.toml`,
and `.github/workflows/ci.yml`. Report branch/worktree state, assigned
namespace/environment status, access assumptions, safety gates, and the
verification plan before editing.
