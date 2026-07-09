# ADR-006: Tool-healthy bounded active planner

- Status: approved
- Date: 2026-07-09
- Owners: planner-delivery module
- Requirement IDs: `NSR-002`, `NSR-007`

## Context

Hermes can remain TCP-green after Verdify MCP dies, completed sessions time out without terminal action accounting, broader firmware bounds are applied before stricter planner bounds, plan eligibility has no durable expiry, and a stale 0.25 row overrides the approved/device zero.

## Decision

Health includes usable Verdify MCP tools and self-recovery with bounded backoff. Poll terminal runs, persist actual action, distinguish full plan from one-shot fallback, apply strict bound intersections, own one effective expiring plan, and fail visibly neutral. Retire the stale 0.25 plan atomically. After acceptance checks, the bounded planner is active immediately.

## Alternatives considered

Proposal-only soak was rejected because Jason explicitly approved immediate activation. TCP-only probes, finite retries, and indefinite active rows were rejected as known failure modes.

## Consequences

Schema changes land before consumers; forecast and result semantics gain fixtures. Planner failure remains observable but cannot bypass firmware safety.

## Validation and revisit trigger

MCP pod deletion/rolling restart self-heals; terminal action and fallback classifications are correct; one future valid plan is effective; zero registry violations and no stale 0.25 remain. Revisit authority only if planner scope expands beyond bounded tactical values.
