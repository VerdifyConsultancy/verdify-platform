# Planner Graph

**Status:** active architecture doc, 2026-05-19.

This document describes the standalone `planner_graph` service itself: what it owns, how the graph is structured, what runtime seams exist, and what the current production target is.

## 1. Purpose

`planner_graph` is a standalone planner service. Its job is to:

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

Verdify remains the system that validates and executes any chosen action.

## 2. Runtime Role

The intended runtime shape is:

- isolated deployment, currently targeted at GCP Cloud Run
- private HTTP API
- durable run storage
- LangGraph execution engine
- background execution worker per service instance
- no direct greenhouse writes from the planner

The current implementation supports production planning mode, durable run
storage, and checked-in Cloud Run deployment artifacts. Verdify MCP remains the
only write boundary.

## 3. Service Boundary

`planner_graph` owns:

- the external planner API
- the LangGraph `StateGraph`
- planner run lifecycle and idempotency
- planner-owned durable run storage
- internal planner state
- deterministic validation and guardrail preview logic
- LLM-based diagnosis and draft proposal generation, with deterministic fallback when credentials are absent

`planner_graph` does not own:

- Verdify's dispatcher
- Verdify's MCP server
- ESP32 control logic
- crop-band policy
- greenhouse write execution

## 4. Control Boundary

The control boundary is:

1. Verdify detects or creates a planning trigger.
2. Verdify assembles an action-ready context pack.
3. Verdify sends that payload to `planner_graph`.
4. `planner_graph` produces one primary structured proposed action.
5. Verdify validates and executes the chosen action locally through its existing dispatcher and MCP path.
6. ESP32 remains authoritative for real-time relay behavior and safety.

The planner is therefore a decision engine only.

## 5. Current Code Layout

Core modules:

- `planner_graph/app.py`
  - app creation and lifespan wiring
- `planner_graph/api.py`
  - HTTP routes
- `planner_graph/contracts.py`
  - external API contracts
- `planner_graph/state.py`
  - internal `PlannerState`
- `planner_graph/graph.py`
  - LangGraph construction
- `planner_graph/worker.py`
  - run execution
- `planner_graph/store.py`
  - durable run store seam
- `planner_graph/config.py`
  - runtime configuration
- `planner_graph/server.py`
  - container entrypoint that binds to Cloud Run's `PORT`
- `planner_graph/clients/`
  - adapters for Verdify reads, OpenAI, MCP, Slack
- `planner_graph/nodes/`
  - graph nodes
- `Dockerfile`
  - production container image build
- `scripts/cloud-run-deploy.sh`
  - source-based Cloud Run deploy wrapper

## 6. Graph Shape

The graph currently uses these node boundaries:

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

These boundaries are now closer to the intended final planner loop:

- `context_gate` is the explicit context-health checkpoint
- `draft_proposal` and `validate_contract_shape` separate generation from planner-owned contract preflight checks
- `revise_proposal` allows one guarded revision loop when the initial proposal is likely to be clamped
- `fail_closed` converts invalid planner output into a structured `fail` proposal instead of crashing the run
- `materialize_proposal` makes the Verdify-facing bounded payload
- `execution_verify` is planner-side audit/verification support, not greenhouse actuation

## 7. Planner State

`PlannerState` is internal execution state, not the shared API contract.

It exists to capture:

- trigger identity
- lifecycle progress
- context summaries and digests
- retrieval outputs
- diagnosis and draft action
- validation outputs
- guardrail preview
- proposed action metadata
- reporting and terminal status

State rules:

- `thread_id = trigger_id`
- state must remain bounded
- no secrets, credentials, or large raw prompt blobs
- state is for execution flow, not as a second operational source of truth

## 8. Run Lifecycle

Planner runs are trigger-led and idempotent:

- every run is keyed by `trigger_id`
- repeated submissions for the same `trigger_id` reuse the same logical run/thread
- request handlers should not own graph execution
- durable storage should allow restart and resume

The current service supports:

- in-memory run storage for tests
- Postgres-backed run storage for durable production planning

## 9. Runtime Mode

The only valid runtime mode today is:

- `production`

In production mode:

- the planner produces one bounded action payload
- the planner must not execute greenhouse writes
- Verdify MCP validates and executes locally
- there is no production shadow/proposal controller path

Offline replay may compare alternatives, but it is diagnostic only.

## 10. Production Direction

The target production architecture is:

- Verdify pushes fully pre-shaped action-ready context
- `planner_graph` returns one primary structured proposed action
- the proposal mirrors Verdify's existing downstream action contracts closely
- Verdify executes locally
- Cloud Run hosts the isolated planner API behind authenticated service-to-service access

Recommended auth model:
- private Cloud Run service
- authenticated service-to-service access
- identity and caller provisioning are deployment concerns, not planner repo concerns

This means the long-term value of `planner_graph` is not "remote MCP execution".
It is "isolated planning logic with a stable decision contract".

## 11. Current Gaps

The main work remaining is:

- deploy this branch's stricter `set_plan` contract validation/materialization
- configure live Cloud Run durability; the verified 2026-05-20 revision still
  reported `checkpoint: in-memory`
- configure the live OpenAI secret; the verified 2026-05-20 revision still
  reported `openai: fallback`
- finish service-to-service auth hardening so normal runtime uses a dedicated
  non-human caller identity instead of an operator smoke account
- expand integration tests around durable storage, replay, and contract behavior
- improve prompt/eval parity between the remote planner and Verdify's existing planner behavior

## 12. Non-Negotiable Rules

- LangGraph is the workflow engine, not the actuator.
- Verdify owns local validation and execution.
- Dispatcher and ESP32 remain authoritative for delivery and safety.
- Planner output must stay bounded, structured, and versioned.
- `trigger_id` is the cross-system correlation key.
