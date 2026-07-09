## Problem

Required planner misses are not intermittent LLM hangs. Fresh production audit shows a deterministic delivery-system failure class:

- Hermes loses Verdify MCP tool connectivity after several hours, exhausts five retries, and remains tool-dead while Kubernetes/TCP probes stay green.
- 142 of 144 mapped timeout rows contain completed model sessions; the tool action could not reach Verdify.
- The delivery ledger does not persist Hermes terminal status or actual result action. A terminal run can age into `timed_out`, and `set_tunable` can satisfy a trigger that required `set_plan`.
- Full-plan materialization applies broader firmware bounds before stricter planner bounds.
- Plan rows lack durable validity/expiry semantics; thousands of stale active rows exist and the stale operator `band_track_fraction=0.25` still reaches `v_active_plan` despite approved/device zero.
- Forecast accuracy compares outdoor forecast VPD with observed indoor VPD.
- Tactical deviation volume and context are excessive, while `planner_graph` is not an operational writer path.

Open critical alert 7676 is the current production symptom and blocks normal OTA until a valid required plan resolves it.

## Desired outcome

The current Hermes/MCP planner is tool-healthy, self-recovering, terminally observable, strictly bounded, and produces one effective expiring full plan or explicit neutral fallback. It becomes active immediately after acceptance checks, per Jason's approval.

## Acceptance intent

- [ ] Readiness fails when required Verdify MCP tools are unusable; MCP pod deletion/rolling restart self-heals without manual intervention.
- [ ] Retry is indefinite with bounded backoff and resets after stable operation; keepalive RPCs cannot race.
- [ ] Every run persists terminal status/time, actual result action, correlation, retries, and classified failure before SLA evaluation.
- [ ] A trigger requiring `set_plan` is satisfied only by an actual valid full-plan action; one-shot actions are classified separately.
- [ ] Materialization uses the strict intersection of all applicable bounds and normalizes once before persistence/delivery.
- [ ] Exactly one plan is effective at an instant; all plans have validity and expiry; stale/one-shot rows cannot remain effective indefinitely.
- [ ] Repaired consumers are deployed before the stale 0.25 row is atomically retired; zero-repin is observed.
- [ ] Forecast fixtures compare outdoor forecast with observed outdoor temperature/RH-derived VPD and prove indoor/outdoor divergence.
- [ ] Required SUNRISE/SUNSET cycles terminate in a bounded plan or explicit neutral fallback and alert 7676 resolves from a valid action.
- [ ] Planner is active after these checks; no proposal-only soak is required.

## Non-goals

- Moving deterministic five-second control or safety rails into AI.
- Treating obsolete `planner_graph` as production-ready without a separate contract.
- Forcing every tactical event into a full plan when its contract calls for acknowledgement or a one-shot bounded action.
- Bypassing firmware bounds or the single writer.

## Dependencies and related issues

- Umbrella dynamic planning: #214
- Legacy in-pod self-heal/log spam: #210 remains separate
- Forecast observability: #348
- Stale float intent: #377
- Device writer/task starvation: residual child of #430
- Current live blocker: alerts 7676/7677

## Initial risk

Critical for recovery delivery: planner failure leaves required cycles unresolved and blocks the approved OTA. Deterministic firmware safety remains intact.

## Affected surfaces

Hermes/MCP runtime and probes, planner trigger/delivery ledger, materializer and registry validation, plan lifecycle migrations/views, forecast evaluation, context generation, alerts, deployment manifests, and targeted tests.

### Triage investigation

- Existing issue search: this issue is the correct concrete planner failure record; #214 remains an umbrella and #210 is a different legacy probe.
- Evidence inspected: Hermes logs/runs, planner trigger and delivery tables, MCP pod lifecycle/probes, current code/manifests, active plan view, registry bounds, forecast SQL.
- Reproduction: read-only mapping of run IDs and timeouts plus live MCP failure history.
- Likely cause: stateful tool connection with finite retry and TCP-only health, incomplete terminal schema, inconsistent bounds/lifecycle, and invalid forecast comparator.
- Potential fix options: tool-level readiness/self-heal, terminal polling/schema, atomic expiring plan, strict normalized bounds, forecast fixtures, compact tactical context.
- Adversarial audit: actual action must satisfy the trigger contract; no shadow delay is imposed because the user approved immediate bounded activation after acceptance.
- Confidence: high.
- Remaining unknowns: exact backoff constants and plan TTLs can be chosen within the contract and tested.
