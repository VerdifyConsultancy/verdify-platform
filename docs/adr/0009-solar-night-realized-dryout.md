# ADR-009: Solar-night dry-out is judged by realized response

- Status: adopted
- Date: 2026-07-09
- Owners: firmware-control and evidence-contract modules
- Requirement IDs: `NSR-006`, `NSR-007`

## Context

Firmware already uses solar phase for night behavior and has predictive guards, but live database solar semantics lag source and no durable realized-response surface proves whether vent/reheat episodes dry the house.

## Decision

Preserve the existing solar-night controller and safety guards. Apply and prove database solar parity, materialize sunset-to-sunrise realized effectiveness with outdoor absolute-humidity advantage, temperature floor, actuator duty, and stop reason, and let the bounded planner tune only from valid evidence. Add firmware response logic only if realized data proves a device-side failure.

## Alternatives considered

A fixed clock window and another immediate physics rewrite were rejected because they are seasonally brittle and not evidence-backed.

## Consequences

The first implementation is migration, diagnostics, outcome scoring, and alerting; firmware changes remain limited to missing contract signals or verified safety gaps.

## Validation and revisit trigger

Database and firmware solar fixtures agree; no dry-out is admitted by day; each night episode has a realized terminal assessment. Revisit controller physics after a representative evidence window.
