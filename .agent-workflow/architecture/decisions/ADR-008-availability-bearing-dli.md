# ADR-008: Interior DLI is availability-bearing evidence

- Status: approved
- Date: 2026-07-09
- Owners: evidence-contract and firmware-control modules
- Requirement IDs: `NSR-005`, `NSR-007`

## Context

The interior sensor is broken and reads zero, yet firmware builds a proxy from exterior light with a cadence error and downstream consumers multiply and add components again. The result is presented as crop DLI.

## Decision

Value, availability, reason, provenance, and valid interval form the DLI contract. Until an operator-validity switch is enabled after sensor replacement/calibration, product and planner consumers emit unavailable. Raw invalid history may remain forensic. DLI-independent photoperiod and qualified-minute lighting controls continue.

## Alternatives considered

Repairing only arithmetic, using outdoor irradiance as measured interior DLI, or suppressing all lighting behavior were rejected.

## Consequences

Schema and consumer guards land before firmware publication changes. Historical scores identify invalid provenance rather than silently recalculate truth.

## Validation and revisit trigger

Every active consumer returns unavailable while validity is false and lighting actuation tests remain green. Revisit after replacement sensor calibration defines a new validity contract.
