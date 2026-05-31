# Planner Graph

`planner_graph` is a standalone planner service for Verdify.

Its job is to:
- accept a trigger-led planning request from Verdify
- run a LangGraph workflow over a bounded planning state
- produce one primary structured proposed action
- return that proposal to Verdify

It is a planner, not an actuator.

It does not:
- push relay state
- write directly to firmware
- bypass Verdify's dispatcher
- become the greenhouse control loop

Verdify remains the system that validates and executes any chosen action locally.

## Architecture

The production direction is:
- Verdify sends a fully pre-shaped action-ready context pack
- `planner_graph` runs asynchronously, keyed by exact `trigger_id`
- `planner_graph` returns one primary structured proposed action
- the returned payload mirrors Verdify's downstream execution contracts as closely as possible
- Verdify validates and executes the proposal locally

The service runs in production planning mode but remains non-actuating: it
returns one bounded payload and Verdify's MCP performs the only greenhouse
write. When `OPENAI_API_KEY` is configured, diagnosis and draft proposal
generation use the OpenAI Responses API with Structured Outputs. When it is not
configured, the service falls back to a deterministic bounded planner path for
local development and rehearsal.

## API

Private endpoints:
- `GET /health`
- `POST /planner-runs`
- `GET /planner-runs/{trigger_id}`

Interaction model:
1. Verdify assembles a planning request.
2. Verdify `POST`s it to `/planner-runs`.
3. The planner returns `202 Accepted`.
4. Verdify polls `GET /planner-runs/{trigger_id}` for the structured proposal.

`thread_id` is always equal to `trigger_id`.

## Request Shape

The request contract has three top-level sections:
- `trigger`
- `planner`
- `context`

Example:

```json
{
  "trigger": {
    "trigger_id": "11111111-1111-1111-1111-111111111111",
    "greenhouse_id": "vallery",
    "event_type": "SUNRISE",
    "event_label": "Sunrise planning cycle",
    "expected_action": "set_plan",
    "triggered_at": "2026-05-19T06:00:00-06:00",
    "planner_instance": "planner_graph",
    "source": "solar"
  },
  "planner": {
    "run_mode": "production",
    "contract_version": "2026-05-24",
    "context_version": "v1",
    "request_id": "req-11111111",
    "trace_id": "trace-11111111"
  },
  "context": {
    "climate_snapshot": {"temp_f": 72.5, "vpd_kpa": 1.1, "rh_pct": 60},
    "scorecard_summary": {"planner_score": 80.0, "compliance_pct": 90.0},
    "forecast_summary": {"headline": "Hot and dry afternoon expected", "max_vpd_kpa": 1.8},
    "active_plan_summary": {
      "cool_stage2_over_high_f": 1.0,
      "cool_exit_hysteresis_f": 1.5,
      "cold_vent_guard_delta_f": 10.0,
      "sw_cool_all_fans_at_high_enabled": 0.0,
      "sw_direct_wet_stress_override_enabled": 0.0,
      "direct_wet_stress_vpd_margin_kpa": 0.05,
      "direct_wet_stress_min_dew_margin_f": 8.0,
      "direct_wet_stress_latest_hour": 22.0,
      "sw_fog_stress_window_extend_enabled": 0.0,
      "fog_stress_window_latest_hour": 19.0,
      "fog_stress_min_dew_margin_f": 10.0,
      "fog_escalation_kpa": 0.4,
      "mister_engage_kpa": 1.6,
      "vpd_hysteresis": 0.3
    },
    "alerts_summary": ["warning: no blocking alerts"],
    "clamp_summary": {"active_clamps_24h": 0},
    "guardrail_audit_summary": {"readback_freshness_seconds": 45},
    "retrieval_refs": [{"id": "lesson-1", "snippet": "Watch afternoon VPD peaks."}],
    "site_refs": [{"id": "playbook-1", "snippet": "Sunrise plans should bias for midday stress."}]
  }
}
```

`active_plan_summary` is abbreviated above. For `set_plan` triggers, the
planner emits bounded `climate_intent` transitions; Verdify MCP validates and
materializes them into the live Tier 1/firmware contract. See
[fixtures/sunrise-production-request.json](fixtures/sunrise-production-request.json)
for a copyable full request.

For the full shared Verdify ↔ planner contract, see [docs/planner-contract.md](docs/planner-contract.md).

## Response Shape

`GET /planner-runs/{trigger_id}` returns run metadata plus the proposal:
- `status`
- `terminal_status`
- `diagnosis`
- `primary_action`
- `validation_summary`
- `guardrail_preview`
- `planner_metadata`

The planner returns exactly one primary proposed action.

Example action payloads:
- `set_plan`
- `set_tunable`
- `acknowledge_trigger`
- `fail`

The planner does not execute the action. Verdify MCP is the only write boundary.

## Code Layout

Core modules:
- [planner_graph/app.py](planner_graph/app.py): app creation and lifespan wiring
- [planner_graph/api.py](planner_graph/api.py): HTTP routes
- [planner_graph/contracts.py](planner_graph/contracts.py): external request and response contracts
- [planner_graph/state.py](planner_graph/state.py): internal planner state
- [planner_graph/graph.py](planner_graph/graph.py): LangGraph construction
- [planner_graph/worker.py](planner_graph/worker.py): background execution
- [planner_graph/store.py](planner_graph/store.py): in-memory and Postgres run stores
- [planner_graph/server.py](planner_graph/server.py): container entrypoint

Graph nodes:
- `trigger_intake`
- `context_pack`
- `context_gate`
- `retrieve_memory`
- `diagnose`
- `draft_proposal`
- `validate_contract_shape`
- `guardrail_preview`
- `revise_proposal`
- `fail_closed`
- `materialize_proposal`
- `execution_verify`
- `report`

For the service and graph description, see [docs/planner-graph.md](docs/planner-graph.md).

## Install

```bash
uv venv
source .venv/bin/activate
uv sync --extra dev
```

## Run Locally

```bash
uv run uvicorn planner_graph.app:create_app --factory --host 127.0.0.1 --port 8000
```

Example calls:

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/planner-runs \
  -H 'content-type: application/json' \
  -d @request.json
curl http://127.0.0.1:8000/planner-runs/11111111-1111-1111-1111-111111111111
```

## Run In Docker

```bash
./scripts/cloud-run-build.sh
docker run --rm -p 8080:8080 planner-graph:local
```

The container reads `PORT` and binds to `0.0.0.0`, which matches Cloud Run's container contract.

## Deploy To Cloud Run

Required environment:

```bash
export SERVICE_NAME=planner
export GOOGLE_CLOUD_PROJECT=your-gcp-project
export GOOGLE_CLOUD_REGION=us-central1
```

Optional environment:

```bash
export SERVICE_ACCOUNT=planner-graph@your-gcp-project.iam.gserviceaccount.com
export VPC_CONNECTOR=projects/your-gcp-project/locations/us-central1/connectors/serverless-connector
export SECRETS_SPEC='PLANNER_DB_DSN=planner-db-dsn:latest,VERDIFY_DB_DSN=verdify-db-dsn:latest'
```

Deploy:

```bash
./scripts/cloud-run-deploy.sh
```

Smoke check:

```bash
export SERVICE_URL="https://planner-xxxxx-uc.a.run.app"
./scripts/cloud-run-smoke.sh
```

Notes:
- the deploy script keeps the service private with `--no-allow-unauthenticated`
- non-secret runtime config can live in [cloudrun/env.example.yaml](cloudrun/env.example.yaml)
- secret DSNs should come from Secret Manager via `SECRETS_SPEC`
- if the planner must reach a private database, set `VPC_CONNECTOR`

## Runtime Configuration

Useful env vars:
- `APP_ENV`
- `PLANNER_INSTANCE`
- `PLANNER_STORE_BACKEND`
- `PLANNER_DB_DSN`
- `VERDIFY_DB_DSN`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`
- `OPENAI_REASONING_EFFORT`
- `OPENAI_TIMEOUT_SECONDS`
- `PLANNER_WORKER_POLL_SECONDS`
- `PLANNER_WORKER_LEASE_SECONDS`
Notes:
- `PLANNER_DB_DSN` is for the planner's own durable run table
- `VERDIFY_DB_DSN` is optional support wiring for Verdify read-only context
- Verdify-pushed context is the primary planning input path
- if both DSNs point at the same database, the planner creates and owns `planner_graph_runs`
- `OPENAI_API_KEY` enables the real structured LLM planner path
- without `OPENAI_API_KEY`, the planner uses a deterministic fallback and `/health` reports `openai: fallback`
- `InMemoryRunStore` is only allowed for `APP_ENV` in `development`, `dev`, `test`, `testing`, or `local`
- non-development startup now fails unless `PLANNER_STORE_BACKEND=postgres` with a valid planner DSN

## Verification

Test:

```bash
.venv/bin/python -m pytest
```

Type check:

```bash
npx pyright planner_graph tests
```

Container build:

```bash
docker build -t planner-graph:test .
```

Replay a saved request fixture:

```bash
.venv/bin/python scripts/replay_request.py fixtures/sunrise-production-request.json
```

Optional:

```bash
.venv/bin/python scripts/replay_request.py \
  fixtures/sunrise-production-request.json \
  --base-url http://127.0.0.1:8000 \
  --output artifacts/sunrise-production-result.json
```

The replay harness:
- submits a saved planner request
- polls `/planner-runs/{trigger_id}` until terminal
- prints the final response
- optionally writes the terminal response to a JSON artifact file

Prompt/eval loop:

```bash
.venv/bin/python scripts/eval_openai_planner.py \
  fixtures/evals/sunrise-set-plan.json \
  fixtures/evals/critical-alert-set-tunable.json
```

Prompt assets live in [planner_graph/prompts.py](planner_graph/prompts.py).
The eval harness:
- loads saved request fixtures plus expected planner actions
- runs diagnosis and draft proposal generation through the current planner client
- reports pass/fail against the saved expectation set
- can write a JSON summary artifact with `--output`

Eval fixtures can now assert more than action choice. Supported expectation fields include:
- `selected_action`
- `min_confidence`
- `rationale_contains`
- `diagnosis_contains`
- `required_payload_fields`
- `forbidden_payload_fields`
- `payload_fields`

## Current Limits

- the live LLM path still needs production prompt iteration and evals against Verdify's existing planner behavior
- the live Cloud Run revision verified on 2026-05-20 still reported
  `checkpoint: in-memory` and `openai: fallback`; configure
  `APP_ENV`, `PLANNER_STORE_BACKEND=postgres`, `PLANNER_DB_DSN`, and
  `OPENAI_API_KEY` before treating the service as durable planner runtime
- the current manual smoke path uses keyless service-account impersonation;
  long-term service-to-service auth should still move to Workload Identity
  Federation or another non-human identity path
