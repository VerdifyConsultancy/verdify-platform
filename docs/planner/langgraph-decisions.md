# LangGraph Planner Decisions

**Status:** active decision log, 2026-05-18.

This log records project decisions that are useful while implementing the LangGraph planner. It is
lighter than an ADR; promote a decision to an ADR only when the trade-off is hard to reverse,
surprising, and likely to be re-litigated.

## 2026-05-18: First Public Interface

Decision:

- Implement only the initial internal planner API surface first:
  - `GET /health`
  - `POST /triggers/{trigger_id}/run`
  - `GET /runs/{trigger_id}`

Deferred:

- `POST /runs/{trigger_id}/evaluate`

Rationale:

- The first slice should prove planner run lifecycle, status inspection, and
  offline/dry-run execution before implementing delayed outcome evaluation.
- Evaluation depends on settled downstream outcome data and can be added after the immediate trigger
  graph exists.

## 2026-05-18: Separate Planner FastAPI Service

Decision:

- The planner is a separate private FastAPI service/app.
- It is not mounted into the existing crop/public API app.

Rationale:

- The planner has a different operational role from the crop/public API.
- Planner health includes worker, checkpoint, MCP, OpenAI, and run lifecycle concerns.
- Keeping it separate reduces coupling between public crop endpoints and private planning execution.

Implementation consequence:

- Add `planner_graph/app.py` for the planner API.
- Do not add planner routes to `api/main.py` in the first slice.

## 2026-05-18: Minimal Worker In First Slice

Decision:

- Include a minimal worker module now.
- Keep execution dry-run-only and stubbed at first.

Rationale:

- Request handlers should not own long-running graph execution.
- The worker boundary is central to retry, resume, checkpoint, and idempotency behavior.
- A minimal worker lets tests prove the correct execution ownership before production graph behavior
  exists.

Implementation consequence:

- Add `planner_graph/worker.py` in the first slice.
- The API starts or resumes runs; the worker executes them.
- Early graph nodes may be stubs, but dry-run validation must perform no
  production MCP writes.
