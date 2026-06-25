# Observability Diagnostics

Use this reference when strategy refresh, review-ready status, release health,
platform readiness, or user feedback depends on runtime evidence.

`observability-diagnostics` is a promoted capability contract owned first by
`release-verification`. It remains a mode, not a standalone canonical skill,
until one reliable manual run proves the packet shape and owner.

## Inputs

- Sprint, lane, issue, PR, wave, session, deployment, policy, commit, image,
  trace, environment, namespace, and North Star IDs.
- Wave release plan and review inbox packet when present.
- GitHub workflow runs, check runs, deployment records, environment status, and
  logs.
- Runtime telemetry links: dashboards, metrics, logs, traces, alerts, events,
  probes, endpoint checks, pod status, restarts, and Kubernetes events.
- User feedback, release-health alerts, incident notes, missing instrumentation,
  state-of-union questions, and platform-readiness gaps.

## Procedure

1. Define the diagnostic unit: readiness, review, release, incident, or
   feedback.
2. Record correlation identifiers before interpreting evidence.
3. State one or more hypotheses with confidence and current status.
4. Attach telemetry links and classify each signal as normal, degraded,
   failing, missing, unknown, or not applicable.
5. Record runtime checks and deployment markers separately from CI/check
   evidence.
6. Record findings, missing instrumentation, and any evidence limitations.
7. Write
   `.agent-workflow/sprints/<sprint-id>/release/observability-diagnostic-packet.yaml`
   or an equivalent readiness path, then validate it against
   `../../schemas/observability-diagnostic-packet.schema.yaml`.
8. Route to state-of-union, review inbox, release verification, platform
   readiness, lane delivery, sprint planning, incident handling, or no action.

## Completeness Rules

Mark a packet `ready` only when:

- scope and correlation IDs are explicit;
- at least one diagnostic hypothesis is recorded;
- material metrics, logs, traces, alerts, events, checks, deployments, and
  probes are linked or explicitly marked missing/not applicable;
- runtime checks and deployment markers distinguish runtime state from CI state;
- findings and missing instrumentation are routed with owners or target refs;
- the recommendation is tied to evidence, not a process-health claim alone.

## Stop Conditions

Stop and route to `instrument_first`, `hold`, `state_of_union`,
`platform_readiness`, or `incident` when:

- the running revision cannot be identified;
- required telemetry is missing for a safety-critical review or release gate;
- Kubernetes probes/events/logs contradict CI or deployment claims;
- release-health signals are degraded and rollback readiness is unproven;
- the diagnostic conclusion would require hidden chat context.
