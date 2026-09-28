# Winter feasibility target-source audit — 2026-09-27

Scope: the separate, passive 2026-11-02–2026-12-31 observational packet in
`research/planner-efficacy/protocols/seasonal-decision-2026-09-27.md`.
This audit is read-only. It neither registers that packet nor qualifies the
randomized hot/dry study.

## Source and live result

At source revision `ed84ea950d9d870b8368ef75e1fe42c4162a946c`,
`winter_feasibility.py` requires one canonical, pre-window target artifact with
exactly 4,320 ordered 15-minute bounds and a positive
`fixed_panel_target_revision_id`. The target declaration table was installed
by migration 252, but its trigger intentionally does not create historical
declarations. It permits only future-effective declarations whose source
profile-state hash equals the live crop-profile snapshot.

During this audit on 2026-09-27 Denver time, the following query ran through the production DB
pod in a `READ ONLY` transaction, with no writes or device connection:

```sql
BEGIN READ ONLY;
SELECT 'target_revisions', count(*) FROM public.fixed_panel_target_revisions
UNION ALL SELECT 'profile_revisions', count(*) FROM public.crop_target_profile_revisions
UNION ALL SELECT 'contributor_revisions', count(*) FROM public.fixed_panel_contributor_revisions;
ROLLBACK;
```

It returned `target_revisions=0`, `profile_revisions=288`, and
`contributor_revisions=0`. A second read-only group count returned
`baseline=288` and no other profile operations. Those records do not contain a
future-effective target interval or assignment revision. There is therefore
no database target revision or frozen 4,320-bin source to export for the
November packet. The current route-only and cfg candidates remain preparation
artifacts, as their README states.

## Why a current resolver read cannot fill the gap

`fn_zone_band(zone, ts)` derives *grading* bounds from the mutable current
`crops.is_active` join and uses `fn_current_season()` rather than deriving the
season from the requested future `ts`. Its result now is not an authenticated
November target schedule. `crop_band_anchors` is the distinct served/on-chip
control-band source; substituting that curve would silently change the target
definition. `crops` has `updated_at`, but there is no complete immutable
assignment revision in the current registration artifact. The current
`crop-target-reference-only.json` explicitly declares that future bins and
assignment lineage are unavailable. An arbitrary positive revision number or
hash would satisfy only a *shape* check, not source provenance.

## Concrete forward path

Before the first 06:00 Denver observation, select and document the actual
fixed-panel crop-target rule and its zone aggregation, then capture the
current crop assignment and exact relevant profile revisions. Declare an
append-only, future-effective target revision under migration 252's guard.
Produce all 4,320 ordered UTC bin starts and temperature/VPD low/high bounds
from that reviewed source rule; retain the assignment source bytes, profile
revision IDs, declaration row, bin bytes and hashes in a private archive.
Independently compare the registration artifact to those source records before
calling `winter_feasibility.py register`. No physical inspection is a gate
for this passive route-only packet; its physical crop-band compliance remains
unavailable. If the inputs are still incomplete before the November start,
record a missed candidate and choose a later prospective interval without
backdating or filling in observed days.
