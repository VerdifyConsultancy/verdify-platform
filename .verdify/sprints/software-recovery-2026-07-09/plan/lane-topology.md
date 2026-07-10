# Lane topology

## Verdict

`APPROVED_WITH_2026-07-09_COVERAGE_AMENDMENTS`

The 17 issues resolve into eight logical lanes. Only three worker lanes may run concurrently because the controller occupies the fourth available agent slot. Most later work is deliberately serialized: the repository has one schema dump, one migration sequence, one MCP server, one tunable registry, and one firmware control surface. Parallelizing those shared contracts would create more review and integration risk than speed.

## Topology

```text
security-hygiene ----------------------------------------------┐
                                                               │
evidence-core -> resource-accounting -> dli-availability --┐   │
                                                           ├-> planner-delivery -> firmware-control --┐
device-writer ---------------------------------------------┘                                        ├-> release-control
evidence/resource/writer/DLI/planner ---------------------------------------------------------------┘
```

Wave 0 is the only broad parallel wave: security hygiene (#438), writer truth (#433), and core evidence (#293/#389/#410/#419/#424) have disjoint source ownership. Resource accounting then consumes the cycle/migration head. DLI follows because it touches the same schema/MCP/API/Grafana surfaces. Planner follows the quiet writer and DLI contract. Firmware is cut last from the current merged head because it shares registry and firmware interfaces with those lanes. Release is controller-only after independent acceptance.

## Lanes

### `security-hygiene` — #438

Owns the five standalone database clients, the AST/source regression, caller inventory, and redacted rotation evidence. It cannot edit product behavior. The existing controller worktree is used because cleanup was discovered during planning; the source fix may merge before rotation, but the issue and production release remain open until explicit authorization and consumer verification.

### `device-writer` — #433

Owns connection generations, real ESPHome cfg wire identities, normalized desired/observed comparison, fair delivery, and requested→confirmed lifecycle truth. It may not change firmware or planner semantics. Its live two-hour quiet-writer proof is a prerequisite for planner and OTA work.

### `evidence-core` — #293, #389, #410, #419, #424

Owns one ordered evidence foundation: DB solar parity, migration 189 VPD correction, raw-transition cycle/runtime truth, realized solar-night episodes, and honestly provenance-labeled replay freshness. These issues intentionally share one branch because they collide in `db/schema.sql`, MCP outcome queries, migration numbering, and replay fixtures. Historical value-change freshness is an explicitly conservative sufficient proxy under DEC-015, never a claimed raw Tempest timestamp. This lane must not change firmware behavior.

### `resource-accounting` — #437

Starts after evidence-core. It reconciles active equipment slugs, creates provenance-bearing coefficients, restores the stale water-event materializer, separates command from delivered water, and stops partial Shelly/model energy from collapsing into one scalar. It is standalone because the water/energy migration and product surface are large enough for one PR and collide with DLI paths.

### `dli-availability` — #435

A cross-stack single-issue lane. It owns the availability/provenance migration, invalid history, firmware unavailable signal, and every consumer guard. It is serialized after resource accounting and before planner/firmware so no issue is split across agents and no concurrent edit hits the shared schema, MCP, API, Grafana, or firmware files.

### `planner-delivery` — #427

Owns Hermes/MCP liveness, terminal result action, strict bound intersection, plan singularity/expiry, correct forecast comparison, and required-cycle acceptance. A post-dispatch live audit found that the already-deployed, non-authoritative `planner_graph` worker can die behind a green `/health` surface. The lane now also owns the narrow worker-health/probe surface needed to make that workload truthful or explicitly decommission it. `planner_graph_runs=0` confirms this is not the active Hermes/MCP failure, and the contract still forbids production trigger routing, plan authority, or device writes through `planner_graph`. It waits for the writer and DLI lanes, then transfers the finalized registry head to firmware.

### `firmware-control` — #299, #383, #386, #428, #434

One exclusive firmware branch and one OTA artifact. It implements the approved topology and measured heap protection while preserving the already-effective mister re-fire fence and lighting min-on boundary. DEC-014 narrowly makes the existing held-temperature response solar-night-only while preserving ordinary daytime dehumidification; DEC-015 adds exact device outdoor age only to existing moisture-exchange telemetry. Acceptance also proves center mist has climate-only origins, both legacy 10:30 jobs are ineligible, weekly solar eligibility survives restart/missed windows exactly once, and calibrated liters convert to bounded wall durations or fail closed. #299/#383/#386 are regression/evidence slices; no broader anti-chatter tunable or control delta is permitted. The combined image cannot freeze until evidence-core records an explicit dry-out disposition.

### `release-control` — #377, #390

Controller-owned overall, with one bounded autonomous Phase-A tooling checkpoint. After product implementation heads and independently approved merged security checkpoint PR #439 are available, the worker proves cycling fixtures and a manifest dry run, reaches `READY_FOR_CRITIC`, and may merge without claiming any live criterion; the controller returns the lane to `IMPLEMENTING`. Phase B then executes schema/services, live acceptance, stale-plan cleanup, one OTA, and settled proof in order. No worker may mutate production. Credential rotation remains the protected gate before Phase B.

## July 9 coverage amendments

The controller ran a fresh read-only audit against Jason's exact feedback after Wave 0 dispatch. It found four ways the original contracts could have passed without delivering the requested outcome: unowned false-green planner runtime health, missing reverse-exclusivity/10:30/cadence/liters irrigation tests, no dry-out effectiveness disposition, and release live evidence listed as its own prerequisite. The amended contracts close those gaps without adding an issue, changing deterministic control authority, enabling uncommissioned fertilizer, or weakening any production gate.

## Why this is safer than plausible alternatives

- A directory-per-lane split would put `db/schema.sql`, `mcp/server.py`, `verdify_schemas/tunable_registry.py`, and firmware YAML under multiple simultaneous owners.
- A single mega-lane would be hard to review and could not use the safe Wave-0 parallelism between writer, evidence, and security.
- Splitting #435 between data and firmware agents would violate issue ownership and make “DLI unavailable everywhere” impossible to accept atomically.
- Splitting the firmware issues would create overlapping changes to the same one-second control lambda, globals, sensors, types, tests, replay, and one physical OTA.
- Running resource/DLI/planner in parallel would trade a few hours for repeated migration renumbering and schema/MCP merge conflicts.

## Approval boundary

The topology itself introduces no new product decision. It implements the already accepted software outcome and narrows speculative cycle changes based on fresh evidence. The production credential rotation remains independently unapproved; that blocks Wave 5, not lane implementation/review.
