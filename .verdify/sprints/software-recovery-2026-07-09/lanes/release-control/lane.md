# Release control lane

- Issues: `#377`, `#390`
- Branch: `lane/recovery-release-377-390`
- Worktree: `/Users/jason/repos/verdify-worktrees/software-recovery-release-control`
- Sprint baseline: `0a9a19a840be6bae1beba604497d880b3b74b1ef`

## Outcome

Add a trustworthy topology-aware cycling gate, release the independently accepted recovery in schema-to-services-to-state-to-OTA order, retire stale `band_track_fraction=0.25` intent without repin, deploy one exact reviewed firmware image, and prove immediate plus 48-hour settled production health.

## Readiness and authority

This lane is `NOT_STARTED`. Phase A tooling becomes dispatchable after all product implementation dependencies are independently accepted and merged and the independently approved, merged security checkpoint PR `#439` supplies the caller matrix/runbook. Security-hygiene remains `BLOCKED`; live deployment and credential rotation are not Phase-A prerequisites. Issue `#438` remains a protected Phase-B production gate: no production mutation may occur until separate authorization, rotation, caller verification, and redacted old-invalid/new-valid proof exist.

An autonomous worker may implement and validate cycling/release tooling only. It can reach `READY_FOR_CRITIC` and merge on the named source/fixture subset of AC01/02 while every live clause and AC03-05 remain pending; that checkpoint never completes the lane. After merge the controller keeps the lane `IMPLEMENTING`. Production schema/service delivery, stale-row retirement, Argo sync, and OTA are controller-integrator Phase-B actions after every gate. No failed alert, weekly, bake, heap, cycling, migration, CI, telemetry, or action-log gate may be overridden.

## Boundaries and sequence

The lane owns release scripts, Makefile wiring, release/evidence packets, and router/strategy reconciliation. It does not own firmware behavior, ingestor/planner/MCP code, or migrations. Prod-promote workflows, overlays, intent rows, and secret authority require explicit coordination.

The order is strict: tooling/manifest preparation first; independently accepted migrations and services become live; writer, DLI, planner, resource, and dry-out dispositions are verified; the planner produces a valid terminal plan and clears its critical alert; stale 0.25 intent is retired atomically; zero repin is proven; then the exact reviewed binary passes preflight and receives the single OTA. Verify running firmware from telemetry, retain the prior rollback artifact, and keep the kube DB backend active throughout post-OTA checks.

## Acceptance

The cycling gate rejects incomparable windows and records immutable source/SHA/firmware/policy metadata. South/west climate cycles must be zero; center is compared with the prior combined climate envelope; other relays use explicit tolerances/canaries. The final packet ties Git, migrations, image digests, Argo, firmware, stale-state cleanup, alerts, writer/planner health, DLI availability, routes, heap/resets, resource scopes, and cycling to immediate and 48-hour evidence.

The Phase-A critic may approve only cycling fixtures, the pre-release baseline/policy and manifest dry run, CI, and the immutable tooling head while explicitly leaving production pending. A separate final critic reviews the Phase-B runtime packet. Issues `#377` and `#390` close only when production reality, GitHub, durable workflow records, clean Git state, CI, rollback disposition, and settled evidence agree.
