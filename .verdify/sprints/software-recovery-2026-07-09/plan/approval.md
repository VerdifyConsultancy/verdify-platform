# Sprint and lane approval

- Status: **APPROVED FOR IMPLEMENTATION AND REVIEW**
- Human approver: Jason Vallery
- Approval source: the active objective to “take this feedback, and deliver all of your proposed software fixes,” with explicit permission to perform the required OTA without another OTA check, followed by repeated `Continue` instructions.
- Recorded by: recovery controller
- Recorded at: 2026-07-09T21:56:01Z
- Approved sprint: `software-recovery-2026-07-09`
- Approved issue set: #293, #299, #377, #383, #386, #389, #390, #410, #419, #424, #427, #428, #433, #434, #435, #437, #438
- Approved topology: eight logical lanes in `.verdify/sprints/software-recovery-2026-07-09/plan/lane-topology.yaml`, with at most three worker lanes active while the controller occupies the fourth slot.

## Recorded evidence-driven revisions

The approval is interpreted against the verified July 9 evidence, not stale issue prose:

- #299 preserves/tests the already-effective 45-second mister re-fire fence; it does not add a 120-second min-on or new max-cycles tunable.
- #383 preserves solar-night safety and uses realized episodes to decide future tuning; it does not add a new post-wet hold or closed-heat flag in this recovery.
- #386 adds the missing lighting boundary regression and runtime watch; it does not add a new shoulder/freshness tunable without a reproduced raw-edge failure.
- Broad #367 anti-chatter refactoring and #371 outcome-UI redesign are deferred; their trustworthy evidence prerequisites still land through the included issues.

These revisions reduce unproven device behavior while still delivering the approved software outcomes.

### Post-dispatch coverage amendment

A fresh read-only coverage audit on 2026-07-09 found that four approved outcomes were weaker in lane acceptance than in Jason's directive and the architecture decisions. The controller therefore tightened the existing eight-lane/17-issue plan without adding authority or changing issue ownership:

- #427 narrowly owns truthful health/recovery or explicit desired-state removal for the already-deployed, zero-run, non-authoritative planner_graph workload; Hermes/MCP remains the only active planner acceptance path and planner_graph cannot become a writer.
- #434 firmware acceptance now proves center-mist reverse exclusivity, removal/ineligibility of both legacy 10:30 jobs, restart-safe weekly solar eligibility, and calibrated liters-to-duration fail-closed behavior.
- #410 evidence must end in `effective`, `ineffective`, `blocked`, or `insufficient_evidence`; an ineffective or insufficient result cannot be called a completed dry-out fix or silently authorize firmware behavior.
- #377/#390 release tooling can be prepared after reviewed implementation merges, while credential closure and live acceptance remain mandatory gates before their owning production phases.

These are evidence-backed acceptance clarifications within Jason's already approved “deliver all proposed software fixes” outcome. They do not authorize physical feed, credential rotation, production mutation by workers, a second writer, or a new control policy.

## Authority boundary

Approved:

- repository implementation, issue/PR updates, tests, independent criticism, integration, normal CI/CD and GitOps release work;
- serialized production schema/service delivery;
- retirement of the stale June 0.25 intent after repaired consumers are live;
- one exact reviewed firmware OTA and required runtime/soak verification.

Not approved by the cited directive:

- rotation of the exposed production application DB credential;
- raw secret disclosure, destructive DB work, unrelated DNS/edge/org changes, or new physical equipment/sensors.

Issue #438 and `g-prod-db-credential-rotation-20260709` remain an explicit release-only human gate. Lane implementation, tests, review, and merging may proceed; production release must stop until Jason separately authorizes the scoped rotation.
