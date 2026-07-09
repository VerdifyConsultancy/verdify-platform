# Lane topology

## Verdict

`SAFE_TO_APPROVE`

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

Owns one ordered evidence foundation: DB solar parity, migration 189 VPD correction, raw-transition cycle/runtime truth, realized solar-night episodes, and replay freshness. These issues intentionally share one branch because they collide in `db/schema.sql`, MCP outcome queries, migration numbering, and replay fixtures. It must not change firmware behavior.

### `resource-accounting` — #437

Starts after evidence-core. It reconciles active equipment slugs, creates provenance-bearing coefficients, restores the stale water-event materializer, separates command from delivered water, and stops partial Shelly/model energy from collapsing into one scalar. It is standalone because the water/energy migration and product surface are large enough for one PR and collide with DLI paths.

### `dli-availability` — #435

A cross-stack single-issue lane. It owns the availability/provenance migration, invalid history, firmware unavailable signal, and every consumer guard. It is serialized after resource accounting and before planner/firmware so no issue is split across agents and no concurrent edit hits the shared schema, MCP, API, Grafana, or firmware files.

### `planner-delivery` — #427

Owns Hermes/MCP liveness, terminal result action, strict bound intersection, plan singularity/expiry, correct forecast comparison, and required-cycle acceptance. `planner_graph` is prohibited. It waits for the writer and DLI lanes, then transfers the finalized registry head to firmware.

### `firmware-control` — #299, #383, #386, #428, #434

One exclusive firmware branch and one OTA artifact. It implements the approved topology and measured heap protection while preserving—not redesigning—the already-effective mister re-fire fence, lighting min-on boundary, and solar-night safety. #299/#383/#386 are regression/evidence slices; no new anti-chatter tunable or control delta is permitted without a new evidence-backed decision.

### `release-control` — #377, #390

Controller-owned. It builds the topology-aware cycling gate, integrates reviewed heads, executes promotion/schema/services/stale-plan cleanup/one OTA, and owns immediate plus settled runtime proof. No worker may mutate production. Credential rotation remains its protected first gate.

## Why this is safer than plausible alternatives

- A directory-per-lane split would put `db/schema.sql`, `mcp/server.py`, `verdify_schemas/tunable_registry.py`, and firmware YAML under multiple simultaneous owners.
- A single mega-lane would be hard to review and could not use the safe Wave-0 parallelism between writer, evidence, and security.
- Splitting #435 between data and firmware agents would violate issue ownership and make “DLI unavailable everywhere” impossible to accept atomically.
- Splitting the firmware issues would create overlapping changes to the same one-second control lambda, globals, sensors, types, tests, replay, and one physical OTA.
- Running resource/DLI/planner in parallel would trade a few hours for repeated migration renumbering and schema/MCP merge conflicts.

## Approval boundary

The topology itself introduces no new product decision. It implements the already accepted software outcome and narrows speculative cycle changes based on fresh evidence. The production credential rotation remains independently unapproved; that blocks Wave 5, not lane implementation/review.
