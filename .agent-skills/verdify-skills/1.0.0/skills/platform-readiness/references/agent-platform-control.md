# Agent Platform Control Request

Use this reference when a planner, controller, OpenClaw/Hermes workflow, or
operator proposes an Agent Platform API/MCP operation.

`agent-platform-control` is a promoted capability contract owned first by
`platform-readiness`. It records request, authorization, policy, target,
expected effects, result, review, and handoff. It does not execute the
operation by itself.

## Inputs

- Route decision, sprint/wave/lane context, requested operation, API or MCP tool
  reference, target repository/environment/namespace/branch, and intended state
  change.
- Authorization context: subject, auth mode, scopes, service account,
  SubjectAccessReview requirement, and approved human/operator when required.
- Policy context: policy decision ID, rules, constraints, mutation level,
  reason, rollback/recovery path, and required evidence refs.
- Artifact refs such as platform-readiness, wave release plan, review packet,
  session ledger, diagnostic packet, and human gate.

## Procedure

1. Write `.agent-workflow/platform/agent-platform-control-request.yaml` or a
   more specific request path and validate it against
   `../../schemas/agent-platform-control-request.schema.yaml`.
2. Classify the mutation level: read-only, dev write, protected write, or
   production write.
3. Identify the exact MCP tool or API operation. Record operation ID, method,
   tool name, API reference, and idempotency key when available.
4. Record target identity before execution: repository, environment, namespace,
   branch, issues, lanes, PRs, and deployments.
5. Record authorization and policy verdict. Do not copy raw tokens, keys, or
   private payloads into the request.
6. Require human review for protected or production writes, broad RBAC, browser
   terminal access, secret-sensitive actions, or any policy `deny`/`blocked`
   result.
7. Record result refs after execution only if execution was separately
   authorized.

## Completeness Rules

The request is not executable unless:

- operation identity and target identity are explicit;
- authorization subject, scopes, and service account are recorded or explicitly
  not applicable;
- policy decision is `allow` or an authorized human review has approved the
  exception;
- rollback or recovery path is explicit;
- expected state changes and evidence refs are named;
- session-ledger coverage is planned for the action and result.

## Stop Conditions

Stop and route to `platform-readiness`, `controller-loop`, `human-review`, or
`architecture-contracts` when:

- the requested operation has no concrete MCP/API contract;
- the mutation level is protected or production write without approval;
- authorization or policy verdict is missing, stale, denied, or ambiguous;
- target identity is ambiguous;
- the action would expose secrets, broaden RBAC, or mutate production directly
  from a worker lane.
