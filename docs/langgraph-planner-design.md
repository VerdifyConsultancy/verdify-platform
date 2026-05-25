# LangGraph Planner Architecture Design

**Status:** design proposal, 2026-05-18.

## Summary

Verdify will replace the current Hermes prompt-orchestration layer with a deterministic LangGraph planner service while preserving the existing greenhouse control contract.

Implementation companion docs:

- `docs/planner/langgraph-implementation-approach.md`
- `docs/planner/langgraph-decisions.md`

Key decisions:

- LangGraph is the planner workflow engine, not the actuator.
- MCP remains the only write path for `set_plan`, `set_tunable`, `acknowledge_trigger`, and `plan_evaluate`.
- Dispatcher and ESP32 firmware remain authoritative for validation, relay execution, and safety behavior.
- V1 uses Direct OpenAI for structured LLM calls.
- LangGraph state is persisted in Postgres through a LangGraph checkpointer.
- Verdify operational truth remains in existing tables such as `plan_delivery_log`, plan journals, readbacks, scorecards, and guardrail audit views.
- Rollout is a direct single-path replacement only after offline replay,
  deterministic validation, and operator approval; no alternate production
  controller is allowed.

## 1. Architecture Overview

The new planner is a private Dockerized FastAPI service with a background worker.

Core components:

- `planner-graph-api`: internal FastAPI app for run control, health, and status inspection.
- `planner-graph-worker`: claims eligible planning triggers and runs LangGraph workflows.
- Shared Python package: graph definition, state schema, node implementations, DB/MCP/OpenAI clients.
- Postgres: operational data plus LangGraph checkpoint persistence.
- OpenAI: structured diagnosis and plan drafting.
- MCP: the only greenhouse write interface.

Control boundary:

- LangGraph decides and validates proposed planning actions.
- MCP performs bounded writes.
- Dispatcher validates and pushes.
- ESP32 performs relay decisions every 5 seconds.
- LangGraph never writes directly to relay, setpoint, firmware, or dispatcher-owned tables.

## 2. Module Layout

`planner_graph/app.py`

- FastAPI app.
- Exposes health, run trigger, and run status endpoints.
- Does not perform long-running graph execution inside request handlers.

`planner_graph/worker.py`

- Background worker.
- Claims eligible trigger rows or receives queued trigger IDs.
- Starts graph runs with `thread_id = trigger_id`.
- Ensures idempotency around retries and restarts.

`planner_graph/graph.py`

- Builds the LangGraph `StateGraph`.
- Registers nodes, edges, conditional routing, retry limits, and terminal states.
- Wires Postgres checkpointing.

`planner_graph/state.py`

- Defines `PlannerState`.
- Uses a LangGraph-friendly `TypedDict` state shape.
- Uses Pydantic models for strict node input/output boundaries.

`planner_graph/nodes/`

- One module per major planner node.
- Deterministic nodes stay pure where possible.
- Side-effecting nodes call MCP only and are idempotent by `trigger_id`.

`planner_graph/clients/`

- `db.py`: read-only operational queries plus checkpoint support.
- `mcp.py`: typed MCP client for planner write tools.
- `openai.py`: Direct OpenAI structured-output client.
- `slack.py`: reporting client.

`planner_graph/checkpoint.py`

- Configures LangGraph Postgres checkpointing.
- Separates graph execution persistence from Verdify business tables.

## 3. PlannerState Schema

LangGraph state is execution state, not Verdify's source of truth.

`PlannerState` contains identity and audit fields:

- `trigger_id`
- `greenhouse_id`
- `event_type`
- `event_label`
- `thread_id`
- `graph_version`
- `execution_mode`: `offline_replay`, `dry_run`, or `production`

Lifecycle fields:

- `status`
- `current_step`
- `started_at`
- `updated_at`
- `errors`
- `warnings`
- `revision_count`

Context fields:

- `context_digest`
- `context_sections`
- `context_completeness`
- `climate_snapshot`
- `scorecard_summary`
- `forecast_summary`
- `active_plan_summary`
- `alerts_summary`
- `clamp_summary`
- `guardrail_audit_summary`

Retrieval fields:

- `retrieval_queries`
- `retrieved_lessons`
- `retrieved_docs`
- `retrieved_plan_refs`

LLM product fields:

- `diagnosis`
- `draft_plan`
- `draft_action`
- `draft_rationale`

Validation fields:

- `validation_status`
- `validation_errors`
- `registry_violations`
- `band_ownership_violations`
- `tier1_coverage_status`

Guardrail preview fields:

- `guardrail_preview`
- `expected_clamps`
- `hold_risk`
- `transition_audit_refs`

Action/write fields:

- `selected_action`: `set_plan`, `set_tunable`, `acknowledge_trigger`, or `fail`
- `mcp_request`
- `mcp_result`
- `plan_id`
- `tunable_changes`

Verification and reporting fields:

- `delivery_status`
- `readback_status`
- `slack_report`
- `terminal_status`

`PlannerState` must not store secrets, API keys, large raw prompts, credentials, or unbounded context blobs. Large context should be represented by digests, summaries, and stable references.

## 4. LangGraph Statefulness

### LangGraph State

LangGraph state is the durable working record for one planner run. It is the object each node receives, updates, and returns as the graph moves from trigger intake through context gathering, LLM drafting, deterministic validation, MCP write/ack, verification, and reporting.

In this system, LangGraph state:

- Is per-run execution state, not a global planner memory.
- Is passed from node to node as the graph's working context.
- Is typed by `PlannerState`, a LangGraph-friendly `TypedDict` with Pydantic models at node input/output boundaries.
- Is checkpointed after graph steps through the Postgres LangGraph checkpointer.
- Allows failed, restarted, or interrupted runs to resume from persisted state instead of starting over blindly.
- Uses `thread_id = trigger_id` so every trigger has one durable graph execution thread.

The `thread_id = trigger_id` rule is important because it gives the planner a stable idempotency key across retries, process restarts, manual re-runs, and status inspection. A repeated request for the same trigger should resume or inspect the same execution thread, not create a competing planner run. If a trigger has already reached a terminal state, the worker must treat the checkpoint and Verdify operational records as authoritative and avoid duplicate MCP writes.

`PlannerState` stores execution facts such as:

- Execution progress: current node, lifecycle status, timestamps, warnings, errors, and revision count.
- Node outputs: context summaries, retrieved references, diagnosis, draft action, guardrail preview, MCP result, verification status, and Slack report metadata.
- Routing decisions: normal, degraded, acknowledge-only, revision, failure, write, verify, or report paths.
- Validation results: schema errors, registry violations, band ownership violations, Tier 1 coverage status, and guardrail preview findings.
- Final action metadata: selected action, correlated `trigger_id`, resulting `plan_id` when present, tunable changes, delivery/readback status, and terminal status.

State is not the same as Verdify's operational source of truth. A checkpoint can say what the graph attempted and where it left off, but greenhouse truth remains in operational tables such as `plan_delivery_log`, plan journals, readbacks, scorecards, and guardrail audit views. The worker should read those operational records during `verify` and before retrying side effects.

State also must stay bounded. It should store digests, summaries, stable row IDs, document IDs, and short retrieval snippets rather than raw prompts, full context packs, credentials, API keys, large telemetry blobs, or unbounded LLM transcripts. This keeps checkpoints resumable and inspectable without turning them into a second data warehouse or a secret store.

### Checkpoint Behavior

The graph checkpointer persists state at graph boundaries so the worker can resume after common failures:

- If the worker crashes after `context_pack`, the next run can reuse the saved context summary and continue with `data_health_gate` or later routing.
- If the process exits after `draft_plan` but before `deterministic_validate`, the draft remains available for validation after restart.
- If the worker crashes after an MCP call, resume logic must inspect both checkpoint state and Verdify operational records before attempting any side effect again.
- If a run is manually inspected through `GET /runs/{trigger_id}`, the API reads the latest checkpoint summary plus operational status instead of reconstructing the entire run from logs.

Side-effecting nodes must be idempotent by `trigger_id` and selected action. The checkpoint helps the worker know what it intended to do, but it is not enough by itself to prove whether a greenhouse write happened. Before retrying `set_plan`, `set_tunable`, `acknowledge_trigger`, or `plan_evaluate`, the worker must check the MCP result and the relevant Verdify ledger/readback records.

LangChain memory in this system:

- Would be treated as conversational or long-term recall.
- Is not the workflow state machine.
- Is not used to decide which node runs next.
- Is not trusted as the source of operational truth.
- Is replaced here by explicit retrieval from Verdify lessons, docs, prior plans, and embeddings.

Key distinction:

- LangGraph state answers: "Where is this planner run, what has it produced, and what should happen next?"
- Long-term memory/retrieval answers: "What past lessons or documents should inform this run?"

## 5. Node Details

### `trigger_intake`

- Reads the trigger by `trigger_id`.
- Validates event type, expected action, SLA, lifecycle status, and greenhouse identity.
- Initializes `PlannerState`.
- Sets `thread_id = trigger_id`.
- Rejects missing, already-terminal, or unsupported triggers.
- Must be idempotent.

### `context_pack`

- Builds the planning context.
- V1 uses the existing Verdify context-gathering path for compatibility and supplements it with typed structured summaries.
- Gathers climate, scorecard, forecast, active plan, alerts, clamps, guardrail audit, and recent delivery state.
- Writes summaries and stable references into `PlannerState`.
- Does not make planning decisions.

### `data_health_gate`

- Deterministic gate.
- Checks stale telemetry, stale readbacks, stale forecast data, missing scorecard data, and context-gather failures.
- Routes to normal planning, degraded planning, acknowledge-only, or terminal failure.
- Prevents the LLM from planning from bad inputs without an explicit degraded-state marker.

### `retrieve_memory`

- Queries existing Verdify embeddings, planner lessons, relevant site docs, and previous plans.
- Uses the event type, forecast headline, current stress, and recent failure modes to form retrieval queries.
- Stores short snippets and source references in state.
- Does not perform writes.

### `diagnose`

- First LLM node.
- Uses Direct OpenAI structured output.
- Produces diagnosis only: current situation, likely cause, risks, and planning intent.
- Cannot propose final tunables or call tools.
- Output is validated with a Pydantic schema.

### `draft_plan`

- Second LLM node.
- Produces strict JSON for the proposed action.
- Can propose `set_plan`, `set_tunable`, or `acknowledge_trigger`.
- Includes trigger correlation, proposed values, rationale, expected effect, and confidence.
- Output is schema-validated before any deterministic validation runs.

### `deterministic_validate`

- Pure validation node.
- Checks tunable registry bounds, Tier 1 required coverage, trigger correlation, action legality, band ownership, and schema conformance.
- Rejects planner attempts to modify firmware-owned bands or collapse crop bands, enforced bands, and readback bands.
- Routes invalid drafts to one bounded revision attempt or terminal failure.
- No greenhouse writes occur here.

### `guardrail_preview`

- Deterministic preview node.
- Uses current guardrail logic, transition audit views, clamp history, and active holds.
- Estimates whether the proposed plan is likely to be clamped, held, or neutralized.
- May route back to `draft_plan` for one revision if the proposal is likely to be ineffective.
- Does not weaken or bypass guardrails.

### `write_or_ack`

- Side-effecting node.
- Calls MCP only.
- Uses `set_plan`, `set_tunable`, or `acknowledge_trigger`.
- Ensures idempotency by `trigger_id` and selected action.
- In offline/dry-run validation, does not perform production writes.
- Records MCP result in `PlannerState`.

### `verify`

- Confirms the requested action reached the expected downstream state.
- Reads delivery status, setpoint changes, plan journal entries, and readbacks where available.
- Distinguishes accepted writes from fully observed physical outcome.
- Updates terminal status or schedules later evaluation.

### `report`

- Emits Slack summary and durable audit metadata.
- Includes trigger, action, validation result, guardrail preview, MCP result, and verification status.
- Emits exactly one terminal report per trigger run.

### `evaluate_later`

- Implemented as a separate delayed graph/job, not part of the immediate trigger graph.
- Runs after enough outcome data exists.
- Scores the result using existing scorecard/anchor logic.
- Calls existing evaluation paths such as `plan_evaluate`.
- Produces lessons for future retrieval.

## 6. Public Interfaces

The internal FastAPI surface:

- `GET /health`: returns service, DB, MCP, OpenAI config, and checkpoint health.
- `POST /triggers/{trigger_id}/run`: starts or resumes a graph run for a trigger, returns `202 Accepted`, and does not block on full graph completion.
- `GET /runs/{trigger_id}`: returns summarized `PlannerState`, current node, terminal status, and last error.
- `POST /runs/{trigger_id}/evaluate`: starts the delayed evaluation graph when outcome data is available.

Public greenhouse writes remain MCP tools, not FastAPI endpoints.

## 7. Persistence Model

LangGraph checkpoint tables:

- Store resumable execution state.
- Keyed by `thread_id = trigger_id`.
- Owned by the planner graph runtime.

Verdify operational tables:

- `plan_delivery_log`, journals, readbacks, scorecards, guardrail audit views.
- Remain the source of truth for greenhouse planning and delivery.

Long-term retrieval memory:

- Existing embeddings, lessons, previous plans, and docs.
- Queried by `retrieve_memory`.
- Not treated as control-plane state.

## 8. Rollout Plan

1. Design-only phase:
   - Add the architecture document.
   - No runtime behavior changes.
2. Offline replay/dry-run phase:
   - Run LangGraph against captured triggers and fixture context outside the live
     production trigger path.
   - No production greenhouse writes.
   - Compare decisions to historical outcomes and the ClimateIntent contract.
3. Production replacement phase:
   - Move required planner triggers to LangGraph as the one planner path.
   - Keep exact `trigger_id` correlation and delivery ledger semantics.
   - Do not keep a second live planner/controller path.
4. Cleanup phase:
   - Retire the replaced Hermes planner path in the same change window or before
     production traffic is handed over.

## 9. Testing Plan

Required test scenarios:

- `trigger_intake` rejects missing, terminal, or unsupported triggers.
- `context_pack` handles successful context, partial context, and gather failure.
- `data_health_gate` routes stale telemetry/readbacks to degraded or fail-safe paths.
- `retrieve_memory` returns bounded snippets with source references.
- `diagnose` accepts only valid structured output.
- `draft_plan` rejects malformed JSON and unsupported actions.
- `deterministic_validate` rejects out-of-bounds tunables.
- `deterministic_validate` rejects firmware-owned band violations.
- `guardrail_preview` identifies likely clamp/hold outcomes.
- `write_or_ack` is idempotent for repeated `trigger_id` runs.
- Offline/dry-run validation performs no production MCP writes.
- `verify` distinguishes MCP accepted, dispatcher delivered, and readback observed.
- `evaluate_later` runs separately from the immediate graph.
- Full graph can resume from a Postgres checkpoint after interruption.

## 10. Assumptions And Defaults

- The design doc is added as `docs/langgraph-planner-design.md`.
- V1 model gateway is Direct OpenAI.
- V1 service is private/internal only, with no public Traefik route.
- V1 uses Postgres for LangGraph checkpointing.
- V1 uses `trigger_id` as the LangGraph `thread_id`.
- V1 does not run alongside Hermes as a second live planner. It replaces the
  active planner path only after offline evidence and operator approval.
- V1 does not modify firmware, dispatcher ownership, or MCP write contracts.
- V1 does not introduce a new long-term memory system; it reuses existing Verdify retrieval sources.
- Any production cutover is a later implementation step, not part of this design-doc-only change.
