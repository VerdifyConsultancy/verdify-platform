# North Star Product — verdify-platform

Status: `approved`

Iteration: `1`

Review: `approved` by Jason Vallery on 2026-07-09

Evidence registry: `.agent-workflow/northstar/evidence-registry.yaml`

Architecture pair: `.agent-workflow/northstar/NORTHSTAR_ARCHITECTURE.md`

## PRODUCT-001 Purpose And Outcome

Verdify keeps one live 367 sq ft Longmont greenhouse and its crops safe while making device control, telemetry, bounded AI, crop-care actions, releases, and outcomes auditable. Track A plant safety always outranks platform evolution.

The current target is a trustworthy software loop: one non-starving device writer; a healthy bounded planner; center-only climate mist; explicit intentional south/west irrigation; commissioned weekly wall-only fertigation; honest unavailable interior DLI; solar-night dry-out evidence; and exact release-to-runtime traceability. The product does not add hardware, infer missing crop measurements, create a second writer/environment, or move deterministic safety into AI.

Evidence: `northstar://evidence/NSE-20260709-operator-delivery-brief`, `northstar://evidence/NSE-20260709-project-definition`.

## PRODUCT-002 Personas And Human Roles

| ID | Persona / role | Need | Review or approval responsibility | Evidence |
| --- | --- | --- | --- | --- |
| USR-001 | Jason, greenhouse owner | Healthy plants, understandable risks, correct automation, trustworthy outcomes | Protected device/production actions and crop intent | Operator brief |
| USR-002 | Authorized project agent | Durable authority, bounded autonomy, reproducible access and tests | Implement, review, deliver, verify, reconcile | Project definition |
| USR-003 | Crop/controls reviewer | Crop-zone provenance, intervention history, calibrated assumptions | Review agronomic and controls evidence | Review and research |
| USR-004 | Technical evidence consumer | Honest API/lab/graph state without control access | Read-only use | Project definition |

## PRODUCT-003 PRD Summary

Current software tells several false stories: ordinary cfg drift looks like reconnect, planner pods can be healthy without MCP tools, climate mist waters unplanted zones, a dead schedule appears to be fertigation, and a broken light sensor still yields crop DLI. The minimum useful recovery fixes those contracts end to end, preserves deterministic firmware safety, records terminal outcomes, and delivers the repaired planner immediately after acceptance.

Exact fertilizer chemistry and volume are not guessed. Software ships a fail-closed commissioning surface; automatic actuation waits for measured water, product, injector, flow, distribution, and flush data.

## PRODUCT-004 User Stories

| Story ID | Actor | Story | Acceptance signal | Priority | Evidence | Architecture links |
| --- | --- | --- | --- | --- | --- | --- |
| PST-001 | Operator | I can trust a stable device connection not to trigger repeated full writes | Two steady hours with zero broad stable-connection pushes | Must | Review | ARQ-001 |
| PST-002 | Operator | I receive a real bounded plan or visible neutral fallback | MCP liveness and terminal action are persisted; one active expiring plan | Must | Review, brief | ARQ-002 |
| PST-003 | Crop owner | VPD climate mist uses center only and never fertilizer | Zero south/west climate cycles and zero non-wall fertilizer | Must | Brief | ARQ-003 |
| PST-004 | Crop owner | Weekly wall feed is automatic only after safe commissioning | Missing commissioning cannot actuate; commissioned run proves liters and immediate flush | Must | Research, brief | ARQ-004 |
| PST-005 | Evidence consumer | Broken interior light sensing is shown as unavailable | No active consumer publishes crop DLI while invalid | Must | Brief, review | ARQ-005 |
| PST-006 | Operator | Night dry-out follows the greenhouse diurnal cycle and reports effectiveness | Actuation only in night solar phase with realized-response evidence | Should | Brief, review | ARQ-006 |
| PST-007 | Release operator | One approved recovery can be safely delivered and OTA'd | All deterministic gates and runtime checks bind exact revisions | Must | Brief, project definition | REL-001 |

## PRODUCT-005 Product Requirements

| Requirement ID | Requirement | Kind | Acceptance signal | Priority | Evidence | Downstream refs |
| --- | --- | --- | --- | --- | --- | --- |
| PRQ-001 | Preserve deterministic local safety and exactly one authorized writer | Safety | Faults never create a second writer or unsafe relay state | Must | Project definition | ARQ-001, SEC-001 |
| PRQ-002 | Reconcile only true differences and confirm only successful writes | Reliability | No stable bulk storm; deliberate drift writes once; real reconnect is bounded | Must | Review | ARQ-001, OBS-001 |
| PRQ-003 | Restore observable bounded planner delivery and activate it after acceptance | Functional | Healthy MCP tool, terminal action, intersected bounds, expiry, neutral fallback | Must | Brief, review | ARQ-002 |
| PRQ-004 | Enforce center-only climate mist and intentional-only south/west irrigation | Functional | Relay attribution matches policy | Must | Brief | ARQ-003 |
| PRQ-005 | Automate commissioned weekly wall-only feed using calibrated liters and immediate flush | Functional/safety | Exact-once ledger and commissioning fail-closed proof | Must | Research, brief | ARQ-004 |
| PRQ-006 | Make interior DLI unavailable until sensor validity is restored | Data integrity | NULL/unavailable with provenance across all consumers | Must | Brief, review | ARQ-005 |
| PRQ-007 | Gate dry-out by solar night and publish realized effectiveness | Functional/evidence | No day actuation; bounded outcome KPI and stop reason | Should | Brief, review | ARQ-006 |
| PRQ-008 | Retire stale `band_track_fraction=0.25` and prevent repinning | Configuration | Active effective value is zero with no repeated write | Must | Brief, review | IFACE-002 |

## PRODUCT-006 Milestones

| Milestone ID | Outcome | Entry criteria | Exit criteria | Required signoff | Evidence |
| --- | --- | --- | --- | --- | --- |
| MS-001 | Contracts and backlog reconciled | Approved North Star/project definition | Module contracts and bounded GitHub lanes | Recorded Jason approval | This artifact |
| MS-002 | Software recovery integrated | Lane tests and independent criticism complete | Full integration gates green | CI and critic evidence | Sprint artifacts |
| MS-003 | Production and device verified | Images promoted; alert and OTA gates pass | Services live, stale plan retired, one OTA verified, acceptance probes pass | Existing July 9 approval | Release verification |

## PRODUCT-007 Waves

| Wave ID | Goal | Scope | Non-goals | CI/CD deployment path | Human review | Exit evidence | Architecture links |
| --- | --- | --- | --- | --- | --- | --- | --- |
| WAVE-001 | Stop false loops | Registry/readback, writer scheduling/accounting, MCP liveness | Crop tuning | PR to main, image publish/promote/sync | Already approved; critics required | Zero churn, recovered MCP | ARQ-001, ARQ-002 |
| WAVE-002 | Restore truth and topology | Planner lifecycle, stale band, forecast semantics, irrigation, DLI, solar dry-out evidence | New hardware or recipe guessing | Schema-first serialized migration, services, then one gated OTA | Already approved; critics required | Runtime/product evidence | ARQ-002–ARQ-006 |
| WAVE-003 | Close and observe | Runtime acceptance, outcome ledger, docs/issues | Unbounded tuning | Release verification and rollback watch | No additional approval unless scope changes | Reconciled GitHub and soak evidence | OBS-001, REL-001 |

## PRODUCT-008 Surfaces And Shapes

| Surface ID | Surface / shape | Primary actor | Inputs | Outputs | States | Errors | Evidence | Architecture links |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SURF-001 | Device writer queue/readback contract | Ingestor | Desired values, actual cfg readbacks, connection generation | Per-write outcomes and confirmed cache | connected, reconnecting, drifted | cancelled, failed, unconfirmed | Review | IFACE-001 |
| SURF-002 | Planner/MCP delivery | Planner/operator | Trigger, context, registry | Full plan or neutral fallback | pending, terminal, active, expired | tool-dead, invalid, timeout | Review | IFACE-002 |
| SURF-003 | Irrigation/fertigation controls | Firmware/operator | VPD demand, explicit zone intent, solar time, commissioning | Relay sequence and job ledger | disabled, eligible, running, flushing, terminal | unsafe, incomplete, dropped | Brief/research | IFACE-003 |
| SURF-004 | DLI evidence | API/planner/reviewer | Sensor validity and raw forensic values | availability-bearing metric | unavailable, valid | stale, invalid provenance | Brief | IFACE-004 |
| SURF-005 | Dry-out outcome | Firmware/planner/operator | Solar phase and climate guards | Admission, stop, realized response | blocked, active, effective, ineffective | guard trip, missing evidence | Review | IFACE-005 |

## PRODUCT-009 Review Script

Review the corrected intent in the operator brief, then verify every acceptance signal in `PRQ-001` through `PRQ-008` against CI, immutable artifact, runtime, database, Home Assistant readback, and relay attribution. The final lock rule is Jason's explicit July 9 authorization for this bounded recovery; any expansion into new hardware, non-wall fertilizer, unbounded AI, or outward-facing infrastructure requires a new decision.

## PRODUCT-010 Planning Questions And Research Queue

| Question ID | Question | Owner | Blocking only for final lock? | Proposed resolution or research path | Evidence |
| --- | --- | --- | --- | --- | --- |
| NSQ-001 | What measured recipe, volume, flow, distribution, and flush endpoint should commission wall feed? | Jason + project agent | No; actuation fails closed | Record required commissioning fields after water/product/emitter tests | Fertigation research |

## PRODUCT-011 Traceability Index

`NSE-20260709-operator-delivery-brief` approves `PRQ-003` through `PRQ-008`; `NSE-20260709-greenhouse-review` reveals `PRQ-002`, `PRQ-003`, `PRQ-006`, and `PRQ-007`; `NSE-20260709-project-definition` constrains every requirement; `NSE-20260709-wall-fertigation` supports `PRQ-005` and `NSQ-001`.

## PRODUCT-012 Alignment And Learning Proposal Shape

No recurring or skill mutation is proposed by this recovery. Reusable lessons remain proposal-first and must cite evidence, verification, destination, risk, approval boundary, verifier, durable state, stop condition, budget, permissions, and a successful manual run before scheduling.
