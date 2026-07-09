# Question candidates

The human review resolves all product, architecture, priority, planner-authority, and OTA decisions needed for implementation. No interview is required before local work.

## Risk/deployment decision — blocking release, not implementation

1. **Production DB credential rotation.** A tracked fallback matched the live secret. Source is fixed, but rotation is a protected action. Durable gate: `.agent-workflow/hygiene/gates/g-prod-db-credential-rotation.yaml`. Allowed answers: `rotate-now` or `defer-with-release-block`.

## Non-blocking commissioning clarification

2. **Wall feed measurements.** Exact product, source-water chemistry, injector ratio, aggregate flow, distribution uniformity, prewet/feed/flush liters, distal flush endpoint, delivered EC/pH, and seasonal multiplier must be measured. Software remains fail closed, so no answer is required to implement or test it.

## Counts

- Blocking product decisions: 0
- Blocking architecture decisions: 0
- Scope and priority decisions: 0
- Risk/deployment decisions: 1
- Non-blocking clarification: 1
