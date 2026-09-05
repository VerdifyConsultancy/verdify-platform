# Band Traceability Contract

Status: September 5 source correction for #424. The original May 15 contract is
retained in Git history and applied migration 119; its descriptions of desired
rows and snapshots as device proof are superseded here. Source changes are not
a claim of deployment or a resolved physical discrepancy.

## Distinct evidence layers

| Layer | Source | What it can establish |
| --- | --- | --- |
| Agronomic crop targets | Versioned crop/solar definition with historical identity | Requires an explicit frozen definition; not established by current resolver replay |
| Current house-anchor reconstruction | `fn_band_setpoints(ts)`, migration 171 | Current house anchors evaluated at the supplied timestamp; not immutable crop history or observed consumption |
| Desired history | `setpoint_changes` | Recorded desired values and expiry; not accepted, sent, or consumed state |
| Database cfg snapshot | Four scalar parameters in `setpoint_snapshot` | Captured legacy cfg values; capture time is not raw observation time or runtime/connection binding |
| Computed target snapshot | `climate.house_temp_target_f` / `house_vpd_target` | Captured target publishes; original raw slugs differ from DB aliases |
| Consumed band | Source-authenticated raw observation plus branch, unit, grid, timestamp and generation | Requires the separate six-series qualification; cannot be inferred from matching cached numbers |

Firmware selects on-chip curves or the legacy scalar branch. The four scalar
cfg values are not the consumed edges in `onchip_curve`. The two target
publishes alone do not complete six-series proof. Never switch branches, widen
bands, relax resource caps or invoke OTA to manufacture qualification.

The exact six-series mapping, capture-v2 format, freshness/generation checks and
Gate P hold are documented in
[band-lineage qualification](runbooks/band-lineage-qualification.md). The offline
capture tool validates an artifact; its supplying collector must authenticate
the underlying raw observations. It does not authenticate transport itself.

## Public reader and deprecated SQL

Forward migration 243 adds `fn_public_band_trace_v2` and the public API v2
contract. It exposes reconstructed/desired comparisons and timestamped
snapshots separately. Missing samples/axes/resolver output remain missing
comparisons with explicit eligible counts. A known failing axis plus an unknown
second axis is not a measured joint failure. Fractions are sample-weighted
house-average diagnostics, not duration-weighted fixed-panel crop endpoints.

Public `crop_*`, `fw_*`, bare `rb_*`, readback-match and ok-trace aliases are
deprecated and null. A 900-second database capture lookback does not verify raw
freshness. Public disposition remains unobservable and physical-proof
eligibility false. An absent migration yields 503, never legacy fallback.

Legacy `fn_band_trace`, `fn_band_setpoint_provenance`,
`fn_band_timeline`, and their views remain for compatibility. Their
crop/actual/firmware names are not a semantic guarantee. `fn_setpoint_at`
likewise does not confirm delivery; timeline helpers can use planned/default
fallbacks. Do not use those labels for crop-outcome or firmware-consumption
claims. Grafana timeline fills still need explicit source labeling/deprecation;
they must not be treated as physical compliance while that work remains.

## Planner and proof collector

The mounted planner context relabels legacy provenance values as reconstructed
dispatcher, desired history and cfg database capture, preserving timestamps.
It withholds stale cfg values and warns that neither equality nor recapture
establishes acceptance, raw freshness or consumption.

The DB-only proof collector emits passive-424 receipt v2 with all six series
unobservable, never resolved from cache equality. It retains diagnostic values,
duplicate rows and missing targets without calling them control/observed
agreement. Migration 181's target columns are retained as explicitly reconstructed
values, distinct from target snapshots. The collector no longer searches backward
for an older complete target row or drops that row if the resolver is empty.
Climate/diagnostic queries are greenhouse-bound and bound
diagnostic capture age without promoting it to raw-event freshness.

Gate P cannot use this DB-only collector as a successful six-series source.
Integrating authenticated raw capture results remains required; no manual
agreement switch or arbitrary receipt injection is provided. Version-1 caches
cannot be relabeled as current proof. Version-2 passive cache content is checked
against its receipt hash, and a forged pass is rejected. Recovery preflight
metadata stays explicitly unobservable without widening or blocking the
separately bounded Gate R contract.

## Delivery and rollback

Commit source plus verbatim planner ConfigMap and owning config-revision
annotations. The mounted subPath script needs the declarative rollout trigger
to reload. Build affected registered runtime inputs and deliver digests through
the owning GitOps path. Install additive SQL before the v2 API image. Preserve
prior pins, migration ledger/definition/owner/ACLs and failed evidence; require
exact running source, Argo Synced + Healthy, live public/planner adoption and
authenticated passive qualification before issue closure.

No manual pod bounce, unmanaged SQL, physical actuation or experimental launch
is authorized by this document. Do not roll back to false proof: retain the
hold/endpoint unavailability when the truthful contract cannot be served.
Applied migrations stay immutable; removal or correction is ledgered forward.
