# Winter feasibility source candidates

These three canonical JSON files record current source routes and contracts for
the **observational** winter packet. Their `source_file_sha256` entries bind
actual repository bytes. They are preparation artifacts, not a registered
study instance, controller readback, physical panel attestation, or crop-target
qualification.

- `route-only-panel.json`: fixed north/east/west database columns and their
  ESPHome/Modbus routes. No sensor serial, physical identity history, or
  independent callback freshness is established. Route-only observation is
  acceptable for reporting data yield, with this limitation retained.
- `crop-target-reference-only.json`: current band defaults and crop-profile
  code references. It contains no frozen future 15-minute crop target bins,
  assignment history, or on-chip consumed-band evidence. **Do not pass this
  file as `--crop-target-source` to `winter_feasibility.py register`.**
- `cfg-schema-source.json`: the 48 canonical fields and their owning source
  bytes. `setpoint_snapshot` provides shared flush timestamps; it does not
  persist a device callback generation.

Live read-only audit on 2026-09-27 found 288
`crop_target_profile_revisions` (all migration-time baselines), zero
`fixed_panel_target_revisions`, and zero `fixed_panel_contributor_revisions`.
The latest `diagnostics.firmware_version` at 16:41:47Z was
`2026.7.10.1500.09ee886` with `active_probe_count=4`. This is a timestamped
readback, not a claim that the same version will run on November 2.

Before registration, freeze the actual prospective crop/zone target definition
and explicit 15-minute bins from a source revision recorded before the first
window, with a future-effective target revision or equivalent immutable
source-owned artifact. Retain the exact source bytes privately and verify
their lineage. If those inputs are unavailable by November 1, record the
candidate as missed and select a new future interval. The passive collector
can still describe raw data availability; it cannot derive crop compliance
from this reference candidate. No physical walkthrough is a gate for the
route-only observational packet. The separate hot/dry efficacy qualification
still needs its own physical and authority evidence.

The `register` command accepts only canonical JSON with schema
`verdify-winter-frozen-crop-target-v1`: exact study/house/timezone/start,
`recorded_at` before registration and observation, exact first/last window
`effective_from`/`effective_to`, a positive
`fixed_panel_target_revision_id`, concrete `target_version`, sorted positive
`profile_revision_ids`, `source_profile_state_sha256`,
`crop_assignment_revision_sha256`, and exactly 4,320 ordered `target_bins`.
Each bin has an explicit UTC `bucket_start` and finite ordered temperature/VPD
low/high bounds. `target_bins_sha256` is SHA-256 of the extractor's canonical
JSON encoding of that bin list (including its trailing newline). This checks
the frozen input shape and bytes; the source revision and crop assignment
still need independent provenance review. A marker-only JSON file is rejected.
