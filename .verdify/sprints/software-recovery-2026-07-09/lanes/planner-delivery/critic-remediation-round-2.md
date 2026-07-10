# Planner-delivery critic remediation — round 2

Remediation head: `8147aa3df104a6893c380818945f0382484dbb48`

1. The renewal thread now records repository exceptions and lost-lease
   outcomes in worker health immediately, before the graph returns. Successful
   authoritative renewal restores readiness. A blocking-graph fixture proves
   readiness becomes false while execution is still in flight and recovers
   only after a later successful store operation.
2. The MCP availability test now verifies the actual `_custom_route` wrapper,
   its FastMCP `custom_route` lookup, the `/readyz` registration, and the
   Kubernetes readiness path instead of requiring an obsolete decorator shape.
3. The controller recorded narrow coordination grants in `lane.yaml` for the
   two direct developer entrypoints and one PostgreSQL fixture path. These
   grants repair contracted validation only and confer no production routing or
   write authority.

Verification at the remediation head:

- required planner targeted suite: PASS;
- planner_graph app/server/PostgreSQL/worker suite: 35 passed;
- `make lint`: PASS;
- `make migration-rollback-safety`: PASS, migration 196 remains safe to wrap;
- `git diff --check`: PASS.

Exact-head GitHub CI and renewed independent criticism remain required before
merge. Production acceptance remains release-control work.
