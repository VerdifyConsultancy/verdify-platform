# Repository map — software recovery 2026-07-09

Baseline: `0a9a19a840be6bae1beba604497d880b3b74b1ef` (`origin/main`). Controller worktree: `/Users/jason/repos/verdify-worktrees/software-recovery-20260709` on `codex/software-recovery-2026-07-09`.

## Authority

- `AGENTS.md`, `README.md`, and `docs/handoff/k3s-agent-handoff.md` define the live-greenhouse operating model, single-prod environment, one writer, protected actions, tests, migrations, and OTA gates.
- GitHub Issues are backlog authority; `main` is accepted source; GHCR digests and ArgoCD identify runtime; device cfg readbacks identify effective firmware state.
- Approved recovery authority lives in `.agent-workflow/northstar`, `.agent-workflow/project`, `.agent-workflow/architecture`, `.agent-workflow/modules`, and `.agent-workflow/strategy`.

## Implementation surfaces

| Surface | Primary paths | Recovery responsibility |
| --- | --- | --- |
| Device writer | `ingestor/ingestor.py`, `shared.py`, `esp32_push.py`, `tasks/{_common,dispatcher,confirmation}.py`, `entity_map.py` | Connection generation, canonical readbacks, fair queue, terminal write truth |
| Shared registry | `verdify_schemas/tunable_registry.py`, drift/fidelity tests | Actual ESPHome wire IDs, normalized bounds/types/readbacks |
| Planner/MCP | `mcp/`, Hermes component manifests, planner trigger/delivery code, `ingestor/tasks/forecast.py`, `scripts/gather-plan-context.sh` | Tool liveness, terminal actions, bounds, lifecycle/expiry, forecast semantics |
| Firmware control | `firmware/greenhouse/*.yaml`, `firmware/lib/*.h`, firmware tests/replay/invariants | Center-only climate, explicit irrigation, wall feed, DLI availability, solar-night signals |
| Evidence/data | serialized `db/migrations/`, `db/schema.sql`, telemetry/MCP schemas, daily/alert/API/site/dashboard consumers | Availability, job ledger, solar parity, dry-out outcomes, historical provenance |
| Delivery | `.github/workflows/`, `deploy/k8s`, Makefile, runbooks | Schema-first promotion, Argo drift reconciliation, stale-plan retirement, one OTA and rollback |

## Discovery commands and gates

- Python: `make lint`, `make test`; the laptop venv is `/Users/jason/repos/verdify-platform/.venv` and must be passed as `VENV=...` from this worktree.
- Migrations: `make migration-rollback-safety` plus targeted rollback proof, serialized one at a time.
- Firmware: `make test-firmware`, `make firmware-invariants`, worktree replay, band replay for curve-sensitive changes, and `make firmware-check`.
- UI/evidence: site lint/render and targeted consumer tests.
- Release: immutable images, digest-only promotion PR, gated Argo sync, critical-alert/weekly/bake/last-good OTA checks, live readbacks.

## Worktree/branch reality

The controller is isolated at current `origin/main`. The laptop root checkout is the now-closed PR #409 branch. Many older lane worktrees and gone-upstream branches remain attributable and are preserved; none has an open PR. Historical S8 workflow state is explicitly cancelled/superseded.

## Security finding

Five tracked renderer/snapshot/writer scripts contained a literal fallback equal to the live prod application DB password. The fallback is removed and guarded by `tests/test_no_committed_db_password.py`; rotation remains protected by `.agent-workflow/hygiene/gates/g-prod-db-credential-rotation.yaml`. Raw values were never emitted.
