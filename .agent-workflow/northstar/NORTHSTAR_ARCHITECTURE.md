# North Star Architecture — verdify-platform

Status: `approved`

Iteration: `1`

Review: `approved` by Jason Vallery on 2026-07-09

Evidence registry: `.agent-workflow/northstar/evidence-registry.yaml`

Product pair: `.agent-workflow/northstar/NORTHSTAR_PRODUCT.md`

## ARCH-001 Architecture Intent

One deterministic ESP32 owns five-second climate actuation; one production ingestor owns remote device writes; bounded AI proposes only registry-valid tactical intent; TimescaleDB and evidence consumers preserve provenance; GitHub/GHCR/ArgoCD deliver one production environment. The target closes false state transitions and makes every missing measurement, write, plan, crop-care job, and release terminally observable.

Non-goals are a second writer/environment, new physical equipment, proxying missing interior DLI, deleting dormant plumbing, fertilizer through non-wall paths, unbounded planner authority, or bypassing CI and firmware safety gates. Product links: `PRQ-001` through `PRQ-008`.

## ARCH-002 Architecture Stories

| Story ID | Actor / system | Story | Acceptance signal | Product links | Evidence |
| --- | --- | --- | --- | --- | --- |
| AST-001 | Sole device writer | Distinguish connection generation from cfg value changes and deliver bounded writes fairly | Stable connection has no broad batches; deliberate drift writes once | PRQ-001, PRQ-002 | Review |
| AST-002 | Planner/MCP | Health includes tool usability and terminal contract action | Pod failure/restart self-heals and plans terminate correctly | PRQ-003 | Review |
| AST-003 | Firmware irrigation resolver | One authority resolves climate mist and intentional irrigation | Center-only climate attribution; no relay ownership race | PRQ-004, PRQ-005 | Brief/review |
| AST-004 | Evidence plane | Invalid interior DLI cannot become product truth | Availability and provenance propagate schema-first | PRQ-006 | Brief/review |
| AST-005 | Dry-out controller/reviewer | Solar phase and guards determine admission; realized response determines effectiveness | Solar-night-only events and bounded outcome view | PRQ-007 | Brief/review |

## ARCH-003 Architecture Requirements

| Requirement ID | Requirement | Quality / domain | Acceptance signal | Product links | Evidence |
| --- | --- | --- | --- | --- | --- |
| ARQ-001 | Use a real connection-generation signal, canonical wire IDs, normalized comparison, post-success confirmation, and a bounded writer queue independent of periodic-task cadence | Reliability/safety | Zero stable full pushes, no false sent state, no starvation | PRQ-001, PRQ-002 | Review |
| ARQ-002 | Make MCP tool liveness a readiness input; recover indefinitely with bounded backoff; persist terminal result action; intersect bounds; enforce one expiring active plan and neutral fallback | Planner reliability | Restart and failure injection pass; active plan is unique and valid | PRQ-003, PRQ-008 | Review/brief |
| ARQ-003 | Route climate wet demand only to center; represent south/west clean irrigation as explicit disabled-by-default jobs; keep fertilizer wall-only; resolve relay ownership once | Control safety | Firmware behavior tests and relay attribution match topology | PRQ-004 | Brief/review |
| ARQ-004 | Model weekly wall feed as commissioning-gated liters for prewet/feed/immediate flush with exact-once solar scheduling and terminal ledger | Crop care/data integrity | Dry-run and commissioned run prove volume, sequence, and failure states | PRQ-005 | Fertigation research |
| ARQ-005 | Propagate interior-DLI availability and provenance schema-first; preserve raw forensic data and DLI-independent lighting control | Evidence integrity | All active product and planner paths return unavailable while invalid | PRQ-006 | Brief/review |
| ARQ-006 | Keep dry-out in firmware night solar phase and materialize realized response with environment, guards, actuator duty, and stop reason | Control evidence | No day admission and ineffective episodes are observable | PRQ-007 | Brief/review |

## ARCH-004 High-Level Design

The ESP32 resolves sensor state, solar phase, validated tunables, ownership, and safety rails into relays. The ingestor maps actual wire IDs, tracks a monotonic connection generation, updates confirmed readback state, and drains one paced device-write queue without blocking other scheduled work. Each write becomes sent, failed, cancelled, confirmed, or superseded only after the corresponding event.

Hermes and MCP expose tool-level health. Terminal polling records run outcome and actual tool action; materialization normalizes through the strict intersection of firmware and planner registry bounds. The database owns one effective expiring plan and retains historical provenance. The stale 0.25 band plan is retired atomically after repaired consumers are live.

Firmware has one irrigation resolver: VPD demand selects center; explicit clean irrigation may target enabled south/west zones; fertilizer may target wall only. A weekly solar scheduler admits a wall job only when commissioning is current, then executes liters-derived prewet, inject, fertilizer-off, immediate flush, and terminal accounting. Center mist may resume once fertilizer master is confirmed off.

DLI is an availability-bearing evidence contract. The invalid raw/proxy value may remain forensic, but product, planner, and scoring surfaces emit unavailable. Night dry-out remains solar phase based; database and diagnostics score realized rather than projected effect.

Failure modes fail neutral: stale or invalid plan expires, MCP degradation opens a visible circuit breaker, incomplete commissioning cannot irrigate fertilizer, invalid DLI cannot score, relay ownership conflict force-offs safely, and delivery rollback uses immutable prior images or retained firmware.

## ARCH-005 Infrastructure And Environments

| Repository / application boundary | Environment / namespace model | Purpose | Owner | Quotas | Secrets model | Deployment path | Observability | Product links |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `verdify-platform` / `verdify-prod-dark` | One prod namespace; no dev/stage | Full greenhouse stack | Project agent; Jason protected gates | Existing k3s resources | Kubernetes/scoped credential references only | Main → GHCR digest → promotion PR → gated Argo sync | Argo, pods, DB, API, Grafana, HA | PRQ-001–PRQ-008 |
| ESP32 firmware | One live device | Deterministic control | Jason OTA gate; agent implementation | Heap/flash and weekly/bake limits | Existing OTA secret reference | Firmware checks → gated single OTA → readback proof | Diagnostics, API logs, relay states | PRQ-001, PRQ-004–PRQ-007 |

## ARCH-006 Interfaces And Integration Contracts

| Interface ID | Provider | Consumer | Contract | Versioning rule | Product links | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| IFACE-001 | ESPHome cfg entities | Ingestor writer | Canonical wire ID, normalized type/value, connection generation, post-write readback | Registry and generated drift test change together | PRQ-001, PRQ-002 | Review |
| IFACE-002 | Planner/MCP/database | Ingestor/firmware | Terminal action, strict bounds intersection, one effective expiring plan, provenance | Schema/migration first, consumers second | PRQ-003, PRQ-008 | Review |
| IFACE-003 | Irrigation policy | Firmware relays/job ledger | Center climate, explicit zone irrigation, wall-only fertilizer, single owner, liters/flow sequence | Tunables require cfg readback and replay evidence | PRQ-004, PRQ-005 | Brief/research |
| IFACE-004 | Sensor validity/evidence | Firmware/DB/API/planner/site | Value nullable plus availability, reason, provenance, valid interval | Schema first; no silent reinterpretation | PRQ-006 | Brief/review |
| IFACE-005 | Solar/dry-out control | Firmware/DB/planner | Shared solar phase semantics, admission/stop reasons, realized response | Migration and fixture parity test | PRQ-007 | Review |

## ARCH-007 Security, RBAC, And Secrets

One service account and one ingestor identity have bounded runtime access; only the sole writer may reach device-write endpoints. Planner tool calls pass MCP validation and cannot bypass firmware rails. Raw secrets stay in existing Kubernetes or local credential stores and never enter reports, logs, commits, prompts, or artifacts. The July 9 approval covers this recovery's protected rollout only, not credential, public-edge, or unrelated product changes. Product links: `PRQ-001`, `PRQ-003`, `PRQ-008`.

## ARCH-008 Observability And Diagnostics

`OBS-001` requires connection generation, batch/write lifecycle, queue delay, task cadence, MCP tool liveness, run terminal state/action, active-plan provenance/expiry, irrigation intent/owner/job/volume, DLI availability/reason, dry-out solar phase/admission/stop/realized effect, exact code/image/firmware revision, and rollback signals. Alerts must distinguish transport loss, drift, invalid commissioning, stale plan, missing action, invalid evidence, and ineffective dry-out. Product links: every `PRQ-*`.

## ARCH-009 Delivery, Release, And Rollback

Migrations are serialized and rollback-classified. Consumers deploy schema-first. Every lane has targeted tests and an independent critic. Integration passes `make lint`, `make test`, migration safety, manifest validation, and all targeted planner/irrigation/DLI checks. Firmware integration additionally passes unit tests, invariants, stock replay, band replay when applicable, compile/check, heap/map review, alert gate, weekly limit, and bake.

Accepted main publishes immutable images; promotion changes only prod digests; Argo sync is followed by live probes. One combined firmware OTA carries the approved firmware surfaces due weekly/bake limits. Rollback returns manifests to prior digests or the device to the retained last-good artifact. There is no preview environment; isolated worktrees, unit/integration tests, replay, rendered manifests, and production-safe dry runs are the review substrate. Product links: `WAVE-001` through `WAVE-003`, `PRQ-007`, `PRQ-008`.

## ARCH-010 ADR And Decision Index

| Decision ID | Decision / topic | Status | ADR path | Product links | Evidence |
| --- | --- | --- | --- | --- | --- |
| ADR-001 | Deterministic firmware and bounded AI | Approved existing | `docs/adr/` and firmware FSM | PRQ-001, PRQ-003 | Project definition |
| ADR-002 | One writer and actual-readback reconcile | Approved for recovery | Module contract to create | PRQ-001, PRQ-002 | Brief/review |
| ADR-003 | Center-only climate; wall-only commissioned fertilizer | Approved for recovery | Module contract to create | PRQ-004, PRQ-005 | Brief/research |
| ADR-004 | Interior DLI unavailable while invalid | Approved for recovery | Schema/evidence contract to create | PRQ-006 | Brief |
| ADR-005 | Solar-night realized dry-out | Approved for recovery | Module contract to create | PRQ-007 | Brief/review |

## ARCH-011 Planning Questions And Research Queue

| Question ID | Question | Owner | Blocking only for final lock? | Proposed resolution or research path | Evidence |
| --- | --- | --- | --- | --- | --- |
| NSQ-001 | Which measured wall recipe, flow, volume, uniformity, and flush endpoint complete commissioning? | Jason + project agent | No; automation fails closed | Measure and record after software surface exists | Fertigation research |

## ARCH-012 Traceability Index

Every `ARQ-*` links to one or more `PRQ-*`. Operator intent supports `ARQ-002` through `ARQ-006`; the adversarial review reveals `ARQ-001`, `ARQ-002`, `ARQ-005`, and `ARQ-006`; primary fertigation research supports `ARQ-004`; the approved project definition constrains every architecture requirement.

## ARCH-013 Alignment And Learning Proposal Contracts

No recurring learning loop is scheduled. Any future proposal uses the typed learning-capture schema, redacts secrets and personal data, remains proposal-first, and cannot alter protected product, architecture, runtime, skill, hook, command, or schedule without the configured approval. Product link: `PRODUCT-012`.
