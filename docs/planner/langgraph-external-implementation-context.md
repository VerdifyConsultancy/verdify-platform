# LangGraph Planner External Implementation Context

**Status:** implementation handoff, 2026-05-18.

This document is for an implementation agent that has no prior Verdify context and will build the LangGraph planner as a standalone feature outside the Verdify codebase.

Use this together with:

- `docs/langgraph-planner-design.md`
- `docs/planner/langgraph-implementation-approach.md`
- `docs/planner/langgraph-decisions.md`
- `docs/planner/greenhouse-reference.md`
- `docs/planner/greenhouse-playbook.md`
- `docs/iris-planner-contract.md`
- `docs/firmware-control-contract.md`

## 1. What Verdify Is

Verdify is an AI-assisted greenhouse control system for a 367 sq ft greenhouse in Longmont, Colorado at 5,090 ft elevation. The greenhouse has fans, heaters, misters, fog, grow lights, weather data, crop profiles, telemetry, and a deterministic ESP32 firmware controller.

The control stack has three separate responsibilities:

- **Crop/band policy:** database functions compute target temp/VPD bands from crop profiles.
- **Planner:** Iris currently uses Hermes to choose tactical setpoints and plans through MCP tools. Repo source selects Cortex's OpenAI-compatible `llm.primary.longctx.mm` route with explicit tool-use enforcement; activation requires the documented replay and deterministic checks. Running Hermes revision `404640a` does not forward the retained xhigh config value through its custom-provider transport.
- **Controller:** ESP32 firmware evaluates real-time climate every 5 seconds and decides relay behavior.

The new LangGraph planner replaces the current Hermes prompt-orchestration path, but it must not replace the control loop. It plans, validates, writes through MCP, and verifies. It does not directly control relays.

## 2. Standalone Project Boundary

The planner should be implemented as a standalone service outside the Verdify repo. Treat Verdify as an external system with stable integration surfaces.

The standalone planner owns:

- LangGraph graph definition, state schema, nodes, routing, checkpoint setup, and worker process.
- Private FastAPI service for health, run control, and run status.
- Postgres checkpointer tables for LangGraph execution state.
- Verdify DB read client for operational context and verification.
- MCP client wrapper for allowed greenhouse writes.
- Direct OpenAI structured-output client.
- Slack/reporting client, if enabled.
- Tests for the standalone planner service.

The standalone planner does not own:

- ESP32 firmware.
- Dispatcher/setpoint push logic.
- Verdify MCP server implementation.
- Verdify database schema migrations, except optional new planner-owned checkpoint tables in its own schema/database.
- Crop profile logic.
- Existing Verdify API routes.
- Hermes retirement/cutover.

The service may live in a separate repository, but it must be deployable next to Verdify on the same private network so it can reach Postgres and MCP.

## 3. Current Verdify Runtime Shape

Current production shape:

- Host: `vm-docker-iris` / Verdify VM.
- Main repo path in production: `/srv/verdify` with `/home/james/verdify` as a symlink.
- Database: TimescaleDB/PostgreSQL 16, container `verdify-timescaledb`, internal port 5432.
- Public API: FastAPI crop/status service, container `verdify-api`, port 8080.
- MCP: FastMCP server in `mcp/server.py`; this is the planner's only greenhouse write interface.
- Ingestor: Python async service that captures ESP32 data, runs periodic tasks, forecast/deviation checks, and setpoint dispatcher.
- Dispatcher cadence: every 300 seconds.
- ESP32 control loop: every 5 seconds.
- Current planner gateway: Hermes `hermes-iris`; any new planner must replace
  that path directly after offline replay and deterministic validation pass.
  Verdify no longer allows an alternate production planner
  path.

Key repo references:

- `README.md`: high-level project overview.
- `docs/SYSTEM-ARCHITECTURE.md`: runtime and data path.
- `mcp/server.py`: authoritative MCP tool behavior.
- `verdify_schemas/plan.py`: set_plan/plan_evaluate Pydantic models.
- `verdify_schemas/tunable_registry.py`: tunable registry and planner-pushable allowlist.
- `db/migrations/092-plan-delivery-log.sql`: initial delivery log.
- `db/migrations/093-planner-instance-audit.sql`: trigger/status audit columns.
- `db/migrations/109-planner-trigger-ledger.sql`: expected trigger ledger.
- `docs/tunable-cascade.md`: generated/curated tunable traceability.

## 4. Greenhouse Control Contract

The most important rule: **LangGraph is the planner workflow engine, not the actuator.**

The control boundary is:

1. LangGraph decides and validates proposed planning actions.
2. MCP performs bounded writes.
3. Verdify database records plan/tunable intent.
4. Dispatcher validates, clamps, overlays policy, and pushes ESPHome number/switch values.
5. ESP32 firmware decides relays every 5 seconds and enforces safety behavior.

The standalone planner must never:

- Write directly to relay tables.
- Write directly to firmware or ESPHome.
- Write directly to dispatcher-owned tables as a shortcut.
- Bypass MCP validation.
- Weaken firmware safety rails or guardrails.
- Treat LLM memory as operational truth.

Allowed production write tools are only:

- `set_plan`
- `set_tunable`
- `acknowledge_trigger`
- `plan_evaluate`

During offline/dry-run validation, the planner must call none of those
production write tools.

## 5. Trigger And Audit Model

Verdify planning is trigger-led. Every planner run must correlate to a trigger UUID.

Core trigger/event types:

- `SUNRISE`
- `SUNSET`
- `SOLAR_MAX`
- `MIDNIGHT`
- `TRANSITION`
- `FORECAST`
- `DEVIATION`
- `FORECAST_DEVIATION`
- `HEARTBEAT`
- `MANUAL`

Important tables:

- `planner_trigger_ledger`: expected trigger schedule and lifecycle.
- `plan_delivery_log`: delivered planner work and MCP outcome correlation.
- `plan_journal`: persisted plan metadata from `set_plan`.
- `setpoint_plan`: future and immediate setpoint waypoints.
- `setpoint_changes`: actual pushed setpoint changes/readable audit surface.
- `setpoint_snapshot`: cfg readbacks from ESP32.

`planner_trigger_ledger` statuses:

- `expected`
- `delivered`
- `acked`
- `plan_written`
- `delivery_failed`
- `timed_out`
- `missed`

`plan_delivery_log` statuses:

- `pending`
- `acked`
- `plan_written`
- `timed_out`
- `delivery_failed`

Trigger invariants:

- Use exact `trigger_id` correlation only.
- Use `thread_id = trigger_id` for LangGraph checkpointing.
- Repeated requests for the same trigger must resume or inspect the same run, not create a competing run.
- Production side effects must be idempotent by `trigger_id` plus selected action.
- Before retrying an MCP write after a crash, inspect both LangGraph checkpoint state and Verdify operational records.
- `SUNRISE` and `SUNSET` normally require `set_plan`; `acknowledge_trigger` is not a normal replacement for those full-plan cycles.

The active current production gateway also stores `plan_delivery_log.hermes_run_id`. The standalone planner may add its own correlation field only if Verdify schema owners approve it. For initial standalone implementation, keep planner-owned run metadata in the planner's own checkpoint/run tables and use Verdify `trigger_id` for cross-system correlation.

## 6. MCP Tool Contracts

The standalone planner should use MCP as an external API. Do not copy MCP write logic into the planner.

### `set_tunable`

Purpose: immediate single-parameter tactical adjustment. Dispatcher applies it within about 5 minutes.

Required arguments:

- `parameter: str`
- `value: float`
- `reason: str`
- `trigger_id: str`
- `planner_instance: str | None`

Important behavior:

- `trigger_id` is required and must be a valid UUID.
- `parameter` must be in Verdify's `ALL_TUNABLES`.
- `parameter` must be in `PLANNER_PUSHABLE_REG`.
- Value must pass `registry_value_error(parameter, value)`.
- Trigger must exist in `plan_delivery_log`.
- Trigger status must be writable.
- `planner_instance` must match the delivery row if the row has an instance.
- Writes a one-shot waypoint to `setpoint_plan` with a plan ID like `iris-oneshot-YYYYMMDD-HHMM`.
- Updates `plan_delivery_log` to `plan_written`.

### `set_plan`

Purpose: write a multi-waypoint plan, normally for SUNRISE/SUNSET 72-hour planning.

Required arguments:

- `plan_id: str`
- `hypothesis: str`
- `transitions: str` containing a JSON array
- `experiment: str`
- `expected_outcome: str`
- `trigger_id: str`
- `planner_instance: str | None`

Plan ID format:

- Current schema expects `iris-YYYYMMDD-HHMM`.
- If the standalone planner needs a new prefix, coordinate a schema change first. Until then, use the accepted format.

Transition shape:

```json
[
  {
    "ts": "2026-05-18T10:00:00-06:00",
    "params": {
      "mister_engage_kpa": 1.25,
      "mister_all_kpa": 1.55
    },
    "reason": "Forecast dry peak stress window"
  }
]
```

Important behavior:

- `trigger_id` is required and must be a valid UUID.
- `transitions` must be valid JSON.
- Transition timestamps must be timezone-aware and strictly ascending.
- Unknown tunables are rejected.
- Registry bound violations are rejected.
- Switch tunables must be `0.0` or `1.0`.
- Plan must include all tactical Tier 1 params for each transition.
- Non-policy tunables are rejected.
- Band-owned params are read-only context; do not rely on them as planner writes.
- Duplicate `plan_id` is rejected.
- SUNRISE/SUNSET plans require a valid structured hypothesis block.
- Successful writes update `plan_journal`, `setpoint_plan`, and `plan_delivery_log.status = 'plan_written'`.

Structured hypothesis expected by MCP for SUNRISE/SUNSET:

```json
{
  "conditions": {
    "outdoor_temp_peak_f": 75.0,
    "outdoor_rh_min_pct": 25.0,
    "solar_peak_w_m2": 900.0,
    "cloud_cover_avg_pct": 30.0,
    "notes": "dominant weather drivers"
  },
  "stress_windows": [
    {
      "kind": "vpd_high",
      "start": "2026-05-18T11:00:00-06:00",
      "end": "2026-05-18T17:00:00-06:00",
      "severity": "medium",
      "mitigation": "lower moisture thresholds during dry peak"
    }
  ],
  "rationale": [
    {
      "parameter": "mister_engage_kpa",
      "old_value": 1.6,
      "new_value": 1.3,
      "forecast_anchor": "RH below 15% from 11:00-17:00",
      "expected_effect": "reduce VPD-high stress hours"
    }
  ]
}
```

### `acknowledge_trigger`

Purpose: record that the planner intentionally wrote no plan/tunable for a trigger.

Required arguments:

- `trigger_id: str`
- `reason: str`
- `planner_instance: str | None`

Important behavior:

- Trigger must exist in `plan_delivery_log`.
- Trigger must be `pending`.
- Reason is required and must be <= 1000 characters.
- Normal SUNRISE/SUNSET triggers require `set_plan`; ack is only allowed for validation ack-only rows.
- Successful ack updates `plan_delivery_log.status = 'acked'`.

### `plan_evaluate`

Purpose: evaluate a settled prior plan and close the learning loop.

Required arguments:

- `plan_id: str`
- `outcome_score: int` from 1 to 10
- `actual_outcome: str`
- `lesson_extracted: str`

Important behavior:

- Plan must exist in `plan_journal`.
- Writes evaluation fields and may create/update planner lesson data.
- This should be run later, after enough outcome telemetry exists. It is not part of the immediate trigger graph.

## 7. Tunable Registry And Ownership

The planner must respect Verdify's tunable registry.

Authoritative source in Verdify:

- `verdify_schemas/tunable_registry.py`

Important exported sets/functions:

- `ALL_TUNABLES_REG`
- `TIER1_REG`
- `PLANNER_PUSHABLE_REG`
- `BAND_OWNED_REG`
- `CROP_BAND_REG`
- `SETPOINT_MAP_REG`
- `CFG_READBACK_MAP_REG`
- `registry_value_error(name, value)`

Ownership classes:

- Crop band params such as `temp_low`, `temp_high`, `vpd_low`, `vpd_high`: read-only in routine plans.
- Planner policy params: mist/fog timing, vent dwell, heat hysteresis, relief/latch knobs, grow-light thresholds; these are the normal planner write surface.
- Operator/site constants: not planner-pushable unless registry says so.
- Firmware fallback rails: firmware-owned.
- Physics/calibration constants: not planner policy.

The standalone planner has two acceptable validation strategies:

1. Query/import a Verdify-provided registry artifact generated from `tunable_registry.py`.
2. Call MCP in dry-run validation mode if Verdify exposes one.

Do not maintain a hand-copied tunable list as the long-term source of truth. If an early prototype needs a static fixture, make it explicitly test-only and fail closed when unknown params appear.

## 8. Planning Cadence And Expected Actions

Normal cadence:

- Full 72-hour plans are emitted at SUNRISE and SUNSET.
- TRANSITION, FORECAST, DEVIATION, FORECAST_DEVIATION, HEARTBEAT, and MANUAL events usually adjust individual tunables or acknowledge no action.
- A 9-hour gap between full plans can be normal.

Useful action defaults:

- `SUNRISE`: `set_plan`
- `SUNSET`: `set_plan`
- `TRANSITION`: `set_tunable`, `set_plan`, or `acknowledge_trigger` depending on severity.
- `FORECAST` / `FORECAST_DEVIATION`: usually `set_tunable` or `acknowledge_trigger`.
- `DEVIATION`: `set_tunable` or replan if material.
- `HEARTBEAT`: normally observe/ack only unless explicitly enabled.
- `MANUAL`: depends on operator request.

The planner should read `expected_action` from `planner_trigger_ledger` when available rather than hard-coding all behavior.

## 9. Context The Planner Needs

The current Iris planner reads this conceptual context before acting:

- Scorecard: compliance, stress hours, cost, utility.
- Climate: current temp/RH/VPD by zone, outdoor conditions, mode.
- Equipment state: active fans, heaters, misters, fog, vent, lights.
- Forecast: next 18-72 hours.
- Current setpoints and active plan status.
- Alerts and unresolved operational risks.
- Recent plan delivery status and readbacks.
- Guardrail/clamp/hold history.
- Lessons and prior plan outcomes.
- Static greenhouse reference and operational playbook.

For V1, the standalone planner can read context directly from Verdify DB views/tables and/or MCP read tools. It should store only summaries, stable IDs, and short snippets in LangGraph state.

Data health gates should check at least:

- Climate freshness.
- Forecast freshness.
- Required sensor coverage.
- Recent readback freshness.
- Context gather failure.
- Existing unresolved critical alerts.

Migration 109 defines `v_data_trust_ledger`; prefer that or successor views for data-quality checks when available.

## 10. Physical Greenhouse Facts That Matter

Important physical context:

- Greenhouse: 367 sq ft, 3,614 cu ft, opal polycarbonate.
- Location: Longmont, Colorado, 5,090 ft elevation.
- Altitude reduces fan cooling effectiveness; nameplate fan cooling is overstated.
- Intake vent is undersized for fan capacity.
- Peak solar load can exceed cooling capacity on hot days.
- Above roughly 85°F outdoor, heat stress can be engineering-limited.
- Dry outdoor air can make VPD-high stress unavoidable; planner should minimize, not promise elimination.
- Sealed misting raises humidity but traps heat.
- Ventilation cools but often dries the greenhouse.
- Fog plus fans can provide strong evaporative cooling.

Zones:

- South: hottest, strongest misting response, canna lilies.
- West: afternoon sun, moderate misting response, shelves/herbs/starts.
- East: coolest, no mister, hydro NFT crops.
- Center: fog machine/Vanda orchids, weaker mister response.
- North: equipment buffer.

Controller modes, priority ordered:

- `SENSOR_FAULT`
- `SAFETY_COOL`
- `SAFETY_HEAT`
- `SEALED_MIST`
- `THERMAL_RELIEF`
- `VENTILATE`
- `DEHUM_VENT`
- `IDLE`

Planner implication: tune tactical intensity and thresholds, but do not try to override safety/controller mode priorities.

## 11. LangGraph Service Shape

The standalone planner should implement two cooperating processes:

- `planner-graph-api`: private FastAPI app.
- `planner-graph-worker`: long-running execution process that claims work and invokes the compiled LangGraph graph.

Initial internal API:

- `GET /health`
- `POST /triggers/{trigger_id}/run`
- `GET /runs/{trigger_id}`

Deferred API:

- `POST /runs/{trigger_id}/evaluate`

The API must not perform long-running graph execution inside request handlers. It should start/resume/enqueue a run and return quickly, typically `202 Accepted`.

The worker is a process, not a LangGraph object. It owns the loop that claims trigger IDs and invokes the compiled graph:

```python
graph = build_graph(checkpointer=postgres_checkpointer)

while True:
    trigger = claim_next_trigger()
    if trigger is None:
        sleep()
        continue

    graph.invoke(
        {"trigger_id": trigger.id},
        config={"configurable": {"thread_id": trigger.id}},
    )
```

## 12. LangGraph State Requirements

Use `thread_id = trigger_id`.

`PlannerState` should include:

- Identity/audit: trigger ID, greenhouse ID, event type/label, graph version, run mode.
- Lifecycle: status, current step, timestamps, warnings, errors, revision count.
- Context summaries: digest, completeness, climate, scorecard, forecast, active plan, alerts, clamps, guardrails.
- Retrieval results: lessons, docs, previous plans, source references.
- LLM outputs: diagnosis, draft action, rationale.
- Validation outputs: schema errors, registry violations, band ownership violations, Tier 1 coverage status.
- Guardrail preview: likely clamps, holds, transition audit refs.
- Action/write metadata: selected action, MCP request/result, resulting plan ID, tunable changes.
- Verification/reporting: delivery status, readback status, Slack report, terminal status.

Do not store:

- Secrets.
- API keys.
- Credentials.
- Large raw prompts.
- Full unbounded context packs.
- Large telemetry blobs.
- Full LLM transcripts unless explicitly bounded and validated.

State is not Verdify truth. Operational truth remains in Verdify tables and views. Checkpoint state records graph execution and resume position.

## 13. Required Graph Nodes

Implement these nodes in the standalone planner:

- `trigger_intake`: load trigger, validate event type/action/SLA/lifecycle, initialize state, reject unsupported or terminal triggers.
- `context_pack`: build bounded structured planning context.
- `data_health_gate`: deterministic freshness/completeness gate.
- `retrieve_memory`: retrieve lessons/docs/prior plans with bounded snippets.
- `diagnose`: Direct OpenAI structured-output diagnosis only.
- `draft_plan`: Direct OpenAI structured-output draft action.
- `deterministic_validate`: schema, registry, Tier 1, action legality, band ownership, trigger correlation.
- `guardrail_preview`: estimate clamps/holds/ineffective plans without bypassing guardrails.
- `write_or_ack`: MCP-only side-effect node; disabled for production writes in
  dry-run validation.
- `verify`: read Verdify operational records to confirm accepted write vs delivered/readback outcome.
- `report`: emit one terminal summary/report per trigger.
- `evaluate_later`: separate delayed graph/job, not part of immediate trigger graph.

LLM nodes must produce structured output validated by Pydantic before deterministic validation and before any write node.

## 14. Execution Modes

Support these modes:

- `offline_replay`: no production MCP writes. Run graph against captured
  triggers/context and compare to historical outcomes.
- `dry_run`: no production MCP writes. Validate request shape and reporting
  around a live trigger without entering the live trigger path.
- `production`: handle required planner triggers as the one planner path after
  reliability is proven.

Default to `dry_run`.

Offline/dry-run validation must be test-proven not to call:

- `set_plan`
- `set_tunable`
- `acknowledge_trigger`
- `plan_evaluate`

The planner can still call read-only MCP tools or DB reads in dry-run mode.

## 15. Verification Semantics

The planner must distinguish:

- MCP accepted the request.
- Verdify DB recorded the plan/tunable/ack.
- Dispatcher delivered setpoint changes.
- ESP32 cfg readbacks observed expected values.
- Physical climate outcome improved later.

Do not report "success" as physical success immediately after MCP returns. Immediate terminal status should say whether the planner action was accepted and whether downstream delivery/readback is observed or pending.

Suggested verification reads:

- `plan_delivery_log` by `trigger_id`.
- `plan_journal` by `trigger_id` or `plan_id`.
- `setpoint_plan` by `trigger_id`/`plan_id`.
- `setpoint_changes` by `trigger_id`/source/parameter.
- `setpoint_snapshot` cfg readbacks.
- Guardrail audit/clamp views if available.

## 16. First Implementation Slice

Build the smallest standalone planner that proves the boundary:

1. FastAPI app with `GET /health`, `POST /triggers/{trigger_id}/run`, and `GET /runs/{trigger_id}`.
2. Worker process with a minimal claim/execute loop.
3. LangGraph graph with stub or deterministic nodes for intake, context, validation, dry-run write, and report.
4. `thread_id = trigger_id` checkpoint behavior.
5. Dry-run-only execution mode.
6. Bounded run status summary.
7. Tests proving duplicate run requests use the same execution thread.
8. Tests proving dry-run mode performs no production MCP writes.

Do not implement production cutover in the first slice.

## 17. Testing Requirements

Minimum tests:

- `trigger_intake` rejects missing, terminal, or unsupported triggers.
- `context_pack` handles successful, partial, and failed context.
- `data_health_gate` routes stale telemetry/readbacks/forecast to degraded or fail-safe paths.
- `retrieve_memory` returns bounded snippets and source references.
- `diagnose` rejects invalid structured output.
- `draft_plan` rejects malformed JSON and unsupported actions.
- `deterministic_validate` rejects out-of-bounds tunables.
- `deterministic_validate` rejects firmware-owned/band-owned writes.
- `deterministic_validate` rejects missing Tier 1 coverage for full plans.
- `guardrail_preview` identifies likely clamp/hold outcomes.
- `write_or_ack` is idempotent for repeated `trigger_id`.
- Dry-run mode calls no production MCP write tools.
- `verify` distinguishes MCP accepted, dispatcher delivered, readback observed, and physical outcome pending.
- Full graph can resume from a Postgres checkpoint after interruption.
- API request handlers return quickly and do not own long-running graph execution.

Use test doubles for Verdify DB, MCP, OpenAI, and Slack in unit tests. Integration tests can run against a fixture Postgres with representative table shapes.

## 18. Operational Defaults

Use these defaults unless Verdify operators override them:

- Service is private/internal only.
- No public Traefik route in V1.
- Direct OpenAI for structured LLM calls.
- Postgres checkpointer for LangGraph state.
- `thread_id = trigger_id`.
- Default greenhouse ID is `vallery`.
- Default execution mode is `dry_run`.
- Do not run beside Hermes as a second live planner. Production traffic moves
  only when LangGraph replaces the active planner path.
- Do not add a new long-term memory system in V1; reuse Verdify lessons/docs/prior plans/embeddings.
- Do not modify firmware, dispatcher ownership, or MCP write contracts.

## 19. Known Compatibility Notes

- Some older docs mention OpenClaw/local Gemma. Current production planning uses Hermes `hermes-iris`; repo source selects Cortex's OpenAI-compatible `llm.primary.longctx.mm` route with explicit tool-use enforcement, while live activation remains separate. Effective upstream reasoning is route-owned, not proven by the retained xhigh config value.
- Some schema literals still list planner instances as `opus`, `local`, or `iris-planner`. Current operational docs also mention `hermes-iris`. The standalone planner should make `planner_instance` configurable and compare exactly to Verdify delivery rows when writing through MCP.
- `/setpoints` compatibility endpoints still exist, but production firmware uses direct ESPHome API pushes/readbacks. Do not build the planner around HTTP setpoint polling.
- The repo has permission-restricted directories such as `analytics/` and `hermes/iris/`; do not assume full tree access is available to every agent.

## 20. Success Criteria

The standalone implementation is successful when:

- It can run a dry-run LangGraph execution for a real Verdify trigger ID.
- It persists and resumes state with `thread_id = trigger_id`.
- It exposes internal health/run/status endpoints.
- It reads enough Verdify context to draft a bounded action.
- It validates against registry/band/Tier 1/action rules before any write path.
- It produces a clear terminal report.
- It performs no production writes in dry-run mode.
- It can be promoted to production only as the single active planner path,
  without changing the Verdify firmware, dispatcher, or MCP tool contracts.
