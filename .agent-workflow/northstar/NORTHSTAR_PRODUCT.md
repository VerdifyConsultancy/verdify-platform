# North Star — verdify-platform (Core Verdify Product)

## Purpose

`VerdifyConsultancy/verdify-platform` is the **core Verdify product**: the
AI-driven climate-control platform that keeps a live 367 sq ft greenhouse in
Longmont, CO operational and that carries the SaaS/cloud surfaces built around
it. It is the end-to-end stack — firmware in the device loop, ingestion of live
telemetry, bounded AI planning, an API and MCP control surface, and the
public-facing lab/graphs/api sites — unified behind a single owning agent.

Production is real and device-affecting: plants are alive, the ESP32 runs the
control loop every ~5 s, and the planner reasons over live data. The product's
prime directive is **Track A over Track B** — keeping the greenhouse safe and
operational always outranks platform/product evolution when the two conflict.

## What it owns

- **Firmware & OTA:** the ESP32 / ESPHome firmware (`firmware/`), its diurnal
  band/curve logic and 15 bulletproof invariants, the replay-diff corpus, and
  the gated OTA deploy path (48 h bake, ≤1 OTA/week, severity-gated).
- **Ingestion:** the `ingestor` (entity map, MQTT path) that lands device and
  outdoor telemetry into the TimescaleDB store.
- **Planning / genai:** the `planner_graph` / `iris_planner` LangGraph planner,
  the `mcp` server (the only validated path that executes greenhouse writes),
  and the prompt/tunable surfaces. AI is bounded to tactical tunables; firmware
  stays deterministic and authoritative.
- **API & web:** the `api`, the Quartz `lab`/`graphs` site generators, and the
  `lab.verdify.ai` / `graphs.verdify.ai` / `api.verdify.ai` surfaces.
- **Data contracts:** `verdify_schemas` (the wire protocol between layers, with
  drift guards) and the serialized, rollback-classified migrations under `db/`.
- **Deploy/CI:** the `deploy/k8s` manifests, overlays, ArgoCD ownership of
  `verdify-prod-dark`, and the `.github/workflows/` pipeline (image
  build/publish to GHCR, `prod-promote`, promote-diff-guard, manifest checks).

## Success criteria

- The greenhouse stays safe and operational: no firmware OTA ships while a
  `critical` alert is open, every OTA respects the 48 h bake and weekly limit,
  and firmware changes carry replay-diff + invariant-suite + unit-test evidence.
- There is exactly one live device writer; no change can create a second.
- Layer boundaries stay intact: schema changes land first, migrations are
  serialized and rollback-classified, and `test_drift_guards.py` stays green.
- `main` is the single canonical branch; prod (`verdify-prod`, app
  `verdify-prod-dark`) is advanced only through the gated `prod-promote` →
  digests-only PR → human merge → operator `argocd app sync` path.
- `make lint` and `make test` are green on every landed change (the one known
  flaky `test_dew_point_risk_computes` timeout excepted).
- Durable decisions, invariants, and runbook changes live in `docs/` (or the
  Orbit context dump), not only in chat.

## Scope

- Firmware logic, ingestion, the bounded planner/MCP, the API, and the
  lab/graphs site generators.
- Schemas, drift guards, and serialized migrations against the prod DB.
- The single-env prod deployment surface (`deploy/k8s/overlays/prod`,
  `verdify-prod` namespace) and its CI/CD pipeline.
- Operator runbooks for the kubectl-host dev loop, DB access, promotion, the
  gated prod sync, and the firmware OTA procedure.

## Non-goals

- **Autonomous device-affecting actions.** Firmware OTA, the prod ArgoCD sync
  that touches the live writer, device-VLAN actions, destructive prod DB work,
  credential rotation, and public DNS/edge/org changes remain behind Jason's
  human gate.
- **Multi-environment.** `verdify-dev` and the staging overlay are
  decommissioned; there is one environment (prod) and no dev device or dev DB.
- **The marketing site and CRM.** `verdify-www` (`verdify.ai`/`www`) and
  `verdify-crm` are SEPARATE products in SEPARATE repos and are out of scope.
- **Unbounded AI control.** The planner proposes bounded tunables; it never
  becomes the deterministic control loop or bypasses the firmware/MCP guards.
- **Reviving the retired multi-agent / multi-worktree / sprint-counter model.**
