# Security-hygiene closeout

State: **READY_FOR_CRITIC**. Source remediation, protected production rotation,
negative-old proof, and consumer acceptance are complete. No credential material
is present in this record.

## Delivered outcome

- The five standalone clients require approved injected authentication and fail
  closed without it; the regression suite and redacted scans passed in PR #439.
- Jason's 2026-07-10 completion objective resolved the protected gate as
  `rotate-now` while preserving every runbook hard stop.
- The production SOPS authority changed only `POSTGRES_PASSWORD` and merged as
  `jvallery/agents#2904` at
  `deae5f50848597fb90057b2914c105113a23e3c0` after green CI and independent
  security criticism.
- The live Secret and the existing `verdify` database role were rotated in
  place. `replacement_uri_safe`, `new_credential_valid`, and
  `old_credential_rejected` are true. Rollback was prepared but not used.
- All long-running consumers and all healthy scheduled consumers accepted the
  new credential. The sole ingestor still holds the writer lease.

## Acceptance

| Criterion | Verdict | Evidence |
|---|---|---|
| LANE-AC-01: no literal/default fallback and fail closed | PASS | SEC-EV-001, SEC-EV-002 |
| LANE-AC-02: regression and redacted scans | PASS | SEC-EV-001 through SEC-EV-004 |
| LANE-AC-03: complete caller/restart/validation/rollback matrix | PASS | SEC-EV-005, SEC-EV-006, SEC-EV-012, SEC-EV-015 |
| LANE-AC-04: authorized rotation with new-valid/old-invalid proof | PASS | SEC-EV-010, SEC-EV-014 through SEC-EV-016 |

## Runtime proof

- Deployments Ready: ingestor `1/1`, API `2/2`, MCP `2/2`, planner `2/2`,
  setpoint-server `1/1`, Grafana `1/1`.
- Authentication errors across those deployments after cutover: `0`.
- Direct read proofs: MCP `SELECT 1`, both planner replicas `SELECT 1`,
  setpoint-server `SELECT 1`, and Grafana TimescaleDB datasource query all pass.
- Public health: `api.verdify.ai`, `lab.verdify.ai`, and
  `graphs.verdify.ai/api/health` return HTTP `200`.
- Controlled scheduled runs: band-curve refresh passed; database backup wrote a
  non-empty `179.1M` dump; HA gap backfill passed; lab publish emitted 322 pages
  and completed object-store delta sync. Those four CronJobs are resumed.
- `verdify-vision` remains suspended because its pre-existing ImagePullBackOff is
  unrelated to the database credential. No failed Vision Job was reintroduced.
- The temporary laptop copy of the SOPS age key was securely removed after the
  rollback window closed.

## Independent follow-ups

- `verdify-setpoint-server` has a pre-existing `/setpoints` diagnostic-path bug:
  it selects the legacy Docker psql backend inside k3s. Its service health, DB
  pool, and direct database read all passed, so this did not block the credential
  cutover; it must be tracked separately.
- The two pre-existing critical planner alerts remain open and continue to block
  firmware OTA until the planner lane produces a valid terminal action.
- Vision image-pull repair remains outside this security lane.

## Immutable references and rollback

- Source-remediation PR: `VerdifyConsultancy/verdify-platform#439`.
- Rotation authority PR: `jvallery/agents#2904`.
- Pre-rotation encrypted authority: `3d342882b794fd7e089b4cdadb85571510e4f4a2`.
- Merged rotated authority: `deae5f50848597fb90057b2914c105113a23e3c0`.
- Live Secret resourceVersion after rotation: `67776214`.
- Controller code/release-plan baseline during the operation:
  `19988c1ad955442a45b70f5957e53a6e5be2c480`.
- Rollback disposition: **not used**. The documented layered rollback remains the
  recovery path; restoring literal fallbacks or retired `.env` behavior is not.

The next state transition is a fresh independent critic verdict tied to the
immutable evidence commit, followed by issue #438 closure and `COMPLETE`.
