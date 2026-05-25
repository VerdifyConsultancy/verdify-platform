# LangGraph Planner Implementation Approach

**Status:** working approach, 2026-05-18.

This document describes how Verdify will begin implementing the LangGraph planner described in
`docs/langgraph-planner-design.md`.

The goal of the first implementation slice is to create the planner service shape without changing
production greenhouse behavior.

## First Slice

Build a separate private FastAPI service and a minimal worker module.

Initial interface:

- `GET /health`
- `POST /triggers/{trigger_id}/run`
- `GET /runs/{trigger_id}`

Deferred from the first slice:

- `POST /runs/{trigger_id}/evaluate`
- production MCP writes
- production trigger cutover
- delayed outcome scoring

## Service Boundary

The LangGraph planner is a separate FastAPI app, not a route group inside the existing crop/public
API app.

Initial modules:

- `planner_graph/app.py`: private FastAPI app for health, run start/resume, and run status.
- `planner_graph/worker.py`: execution owner for planner runs.
- `planner_graph/graph.py`: graph construction and routing.
- `planner_graph/state.py`: `PlannerState` and strict node boundary models.
- `planner_graph/nodes/`: node implementations.
- `planner_graph/clients/`: DB, MCP, OpenAI, and reporting adapters.

Request handlers must not own long-running graph execution. They should enqueue, start, or resume a
run and return quickly. The worker owns graph execution semantics even while early nodes are stubbed.

## Initial Runtime Behavior

The first runtime behavior is dry-run-only and must not be wired into the live
production trigger path.

In the first slice:

- `POST /triggers/{trigger_id}/run` accepts a trigger ID and creates or resumes one planner run.
- `thread_id` is always equal to `trigger_id`.
- The worker runs a minimal graph path with stubbed deterministic nodes.
- The graph records enough state to support `GET /runs/{trigger_id}`.
- No production MCP writes occur.
- Dry-run write nodes must prove they do not call `set_plan`, `set_tunable`,
  `acknowledge_trigger`, or `plan_evaluate`.

The first slice may use an in-memory or test double execution store only for local tests, but the
module boundaries should match the eventual Postgres checkpointed design.

## Test Approach

Use vertical TDD slices rather than writing all tests up front.

First behavior to prove:

- A caller can request a run through the planner API.
- The API returns accepted status without executing long-running work in the request handler.
- The worker owns execution.
- The status endpoint can report the resulting dry-run execution.
- Dry-run mode performs no production greenhouse writes.

Useful early tests:

- `GET /health` reports service health without requiring production writes.
- `POST /triggers/{trigger_id}/run` starts or resumes a run for that trigger ID.
- `GET /runs/{trigger_id}` returns a bounded planner state summary.
- duplicate run requests for the same `trigger_id` use the same `thread_id`.
- dry-run execution does not call MCP write tools.

## Documentation Flow

Keep the documentation split by purpose:

- `docs/langgraph-planner-design.md`: architecture and final intended shape.
- `docs/planner/langgraph-implementation-approach.md`: current implementation plan.
- `docs/planner/langgraph-decisions.md`: decisions made while refining the design.
- `docs/planner/greenhouse-reference.md`: greenhouse operational facts the planner must respect.
- `docs/planner/greenhouse-playbook.md`: operational playbook and planning heuristics.
