# ADR-005: Reconcile observed state, not event labels

- Status: approved
- Date: 2026-07-09
- Owners: device-writer module
- Requirement IDs: `NSR-001`, `NSR-007`

## Context

Generic cfg changes currently set a global force flag named reconnect, all 56 staged anchor IDs miss actual ESPHome wire IDs, and rows/cache are advanced before a paced batch completes. A stable device therefore receives repeated broad pushes and cancelled work can look delivered.

## Decision

Use a monotonic transport generation for true reconnect, canonical generated wire IDs for readback, normalized confirmed-state comparison, one bounded yielding writer queue, and post-success per-command lifecycle accounting. Cfg drift never clears the full cache or creates a reconnect event.

## Alternatives considered

Longer polling, threshold changes, suppressing only constant anchors, and retaining pre-delivery confirmation were rejected because they hide rather than correct state semantics.

## Consequences

Registry/readback changes precede dispatcher work. Runtime acceptance must distinguish deliberate drift and real reconnect. Tests generate the expected wire contract from the same authority as firmware.

## Validation and revisit trigger

Two steady hours with zero broad stable-connection pushes; one deliberate drift writes exactly once; real reconnect restores only required values; no periodic task exceeds twice intended cadence. Revisit only if ESPHome changes its object-ID contract.
