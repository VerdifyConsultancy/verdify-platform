# ADR-007: Explicit irrigation topology and commissioned wall fertigation

- Status: adopted
- Date: 2026-07-09
- Owners: firmware-control and evidence-contract modules
- Requirement IDs: `NSR-003`, `NSR-004`, `NSR-007`

## Context

Climate wet-assist rotates across center, south, and west. Current wall jobs also queue south/west fertilizer, center is live-enabled despite no connection, a 10:30 schedule is dropped outside the feed window, and a global 90-minute hold blocks unrelated climate mist.

## Decision

One firmware resolver owns wet relays. Climate demand targets center only. South/west clean irrigation requires explicit disabled-by-default intent. Center drip and all non-wall fertilizer remain represented but disabled. Automatic wall feed runs at most weekly on solar cadence only after commissioning, uses calibrated liters for prewet/feed/immediate flush, and records exact-once terminal outcomes. Center climate mist may resume when fertilizer master is confirmed off.

## Alternatives considered

Deleting future infrastructure, moving the existing schedule earlier, rotating climate for fairness, and guessing a shared recipe were rejected.

## Consequences

All firmware greenhouse policy edits are serialized in one module. Physical commissioning remains an operator step; software ships fail closed. Relay attribution and feedback become product evidence.

## Validation and revisit trigger

Firmware behavior tests and live attribution show center-only climate, zero non-wall fertilizer, explicit south/west irrigation only, and correct wall sequence. Revisit zone enablement when a dormant zone is planted and explicitly commissioned.
