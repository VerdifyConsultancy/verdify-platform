# Release control lane

- Issues: `#377`, `#390`
- Branch: `lane/recovery-release-377-390`
- Worktree: `/Users/jason/repos/verdify-worktrees/software-recovery-release-control`
- Sprint baseline: `0a9a19a840be6bae1beba604497d880b3b74b1ef`

## Outcome

Add a trustworthy topology-aware cycling gate, release the independently accepted recovery in schema-to-services-to-state-to-OTA order, retire stale `band_track_fraction=0.25` intent without repin, deploy one exact reviewed firmware image, and prove immediate plus 48-hour settled production health.

## Readiness and authority

This lane is `NOT_STARTED`. Its tooling/manifest phase becomes dispatchable after all implementation dependencies in [lane.yaml](lane.yaml) are independently accepted and merged; they do not need to be live first because the controller-owned delivery phases produce that evidence. Issue `#438` credential rotation remains a protected production gate: Jason has not yet authorized it. No production mutation may occur until that separate authorization, rotation, caller verification, and redacted old-invalid/new-valid proof exist.

An autonomous worker may implement and validate cycling/release tooling only. Production schema/service delivery, stale-row retirement, Argo sync, and OTA are controller-integrator actions after every gate. No failed alert, weekly, bake, heap, cycling, migration, CI, telemetry, or action-log gate may be overridden.

## Boundaries and sequence

The lane owns release scripts, Makefile wiring, release/evidence packets, and router/strategy reconciliation. It does not own firmware behavior, ingestor/planner/MCP code, or migrations. Prod-promote workflows, overlays, intent rows, and secret authority require explicit coordination.

The order is strict: tooling/manifest preparation first; independently accepted migrations and services become live; writer, DLI, planner, resource, and dry-out dispositions are verified; the planner produces a valid terminal plan and clears its critical alert; stale 0.25 intent is retired atomically; zero repin is proven; then the exact reviewed binary passes preflight and receives the single OTA. Verify running firmware from telemetry, retain the prior rollback artifact, and keep the kube DB backend active throughout post-OTA checks.

## Acceptance

The cycling gate rejects incomparable windows and records immutable source/SHA/firmware/policy metadata. South/west climate cycles must be zero; center is compared with the prior combined climate envelope; other relays use explicit tolerances/canaries. The final packet ties Git, migrations, image digests, Argo, firmware, stale-state cleanup, alerts, writer/planner health, DLI availability, routes, heap/resets, resource scopes, and cycling to immediate and 48-hour evidence.

Independent critics review both the immutable pre-release tooling/manifest and the final runtime packet. Issues `#377` and `#390` close only when production reality, GitHub, durable workflow records, clean Git state, CI, rollback disposition, and settled evidence agree.
