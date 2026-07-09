# Recovery question pack

The product and architecture questions are resolved. One protected release decision remains unanswered; one commissioning question is intentionally deferred.

## Q-001 — Rotate exposed production DB credential

Question: Authorize the agent to rotate the production Verdify application database credential now, update the existing Kubernetes/DB consumers through the approved secret path, restart affected workloads, prove the old value fails and the new value works, and verify backups/API/MCP/planner/ingestor/Grafana with rollback evidence?

Why this matters: A tracked fallback matched the still-live prod credential. Source is repaired, but Git history retains the value, so production release cannot be hygienically accepted until it is invalidated.

Evidence/context: Redacted gitleaks/source scan, boolean-only live comparison, five-source remediation, and regression tests. No value was displayed.

Options:

1. `rotate-now` — recommended; authorize scoped rotation and verification.
2. `defer-with-release-block` — continue local implementation but do not deploy the recovery until Jason rotates or authorizes it.

Recommended default: Continue local work and block production release; never assume credential-rotation permission.

If unanswered: Local implementation/review continues. Deployment, Argo sync, DB state mutation, and OTA remain blocked by this gate.

Affects: repo hygiene, deployment sequence, all DB consumers, release verification.

## Q-002 — Record wall commissioning measurements

Question: When the software surface is ready, what measured product, water chemistry, injector ratio, aggregate flow, distribution uniformity, prewet/feed/flush liters, distal flush endpoint, delivered EC/pH, and seasonal multiplier should be recorded?

Why this matters: Those values determine safe physical actuation for a shared lime/cannabis wall line.

Recommended default: Keep automatic fertilizer actuation disabled. Ship and test the complete fail-closed scheduler/state machine first.

If unanswered: No production fertilizer is delivered; software implementation is not blocked.

Affects: issue #434 commissioning record and later operator activation.
