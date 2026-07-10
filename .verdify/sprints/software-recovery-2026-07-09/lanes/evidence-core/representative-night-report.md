# Representative solar-night dry-out report

Generated: 2026-07-10T00:57:18Z
Scope: read-only evidence for issue #410; no production migration or control change

## Disposition

`ineffective`

This is not a completed control fix. A historical opportunity is validly
`blocked`, while the current held-temperature flavor has been physically
admitted during solar day. That measured safety-gate failure makes the current
packet `ineffective`, even if individual night episodes show useful response.
Any firmware change requires a separately accepted firmware-control scope.

## Current firmware and armed-feature evidence

Production diagnostics identify firmware `2026.7.3.1931.ab18fe8`, first seen at
`2026-07-04T01:33:20Z` and still current at the probe. The
`sw_dehum_vent_hold_enabled` readback changed from 0 to 1 at
`2026-07-04T14:49:37Z` after a confirmed operator request.

For the latest completed window, 2026-07-09 02:00-06:00 MDT:

- The action ledger has 1,701 rows and covers 239 of 240 minutes; 02:05 is the
  only action gap.
- It has 1,026 `DEHUM_VENT` / priority-`vpd` rows. All 1,026 carry complete
  temperature/VPD bands, estimator context, vent+fan relay evidence, and plan
  correlation; the estimator reason is `vent_plus_heat_hold`.
- Heat 1 is on in 456 action rows; heat 2 is on in zero.
- Climate plus outdoor evidence is complete for 232 of 240 minutes, and 90
  minutes have attributable admitted dry action.

The hold path can act while VPD is below target but above the served low edge.
At 2026-07-09 04:00 MDT, for example, observed VPD was about 0.666 kPa,
historical served low was 0.250 kPa, and the actual action was `DEHUM_VENT` /
`vent_plus_heat_hold`. Migration 191 therefore defines episode activity as
either a measured below-served-low opportunity or an actual row-level
dry-action admission. It reports `below_served_low_minutes` separately; a
below-low-only predicate would incorrectly erase current armed behavior.

The migration's updated semantics were independently applied to a read-only
copy of current evidence for night date 2026-07-08. It found 11 admitted night
episodes. All fail the safety gate because 13 following-day minutes, 05:59-06:11
MDT on July 9, were solar day (`fn_solar_phase` 0.057-0.091) with actual
`DEHUM_VENT`, vent+fan+heat1, heat2 off, and held-temp attribution. Production
contains normal daytime dehumidification as well; only actual held-temp
admission with vent+fan+heat1 is the solar-night gate violation. Projected
`hold_required` with heat1 off is not labeled realized hold.

The resulting current-version values are
`safety_gate_status=fail`, `evidence_status=gate_failed`, and disposition
`ineffective`. This activates the dry-out decision's firmware-control revisit
condition; it does not authorize this evidence lane to change control behavior.

## Historical blocked opportunity

The refreshed stock replay corpus contains a contiguous low-VPD opportunity for
solar night `2026-06-24`, after midnight on June 25. Diagnostics identify its
firmware as `2026.6.23.0146.995c9b3`, before the current held-temperature
candidate:

| Field | Observed value |
|---|---:|
| Local interval | 2026-06-25 05:18-05:32 MDT |
| UTC interval | 2026-06-25 11:18-11:32 UTC |
| NOAA/device solar phase | 3.959-4.000 (night; then sunrise) |
| Elapsed duration / samples | 14.4 min / 14 samples |
| Historical served VPD low | 0.330-0.340 kPa |
| First-five-minute VPD / samples | 0.325 kPa / 5 |
| 10-20-minute VPD / samples | 0.333 kPa / 10 |
| Observed VPD delta | +0.008 kPa |
| Indoor / outdoor absolute humidity | 14.31 / 13.12 g/m3 |
| Average outdoor drying advantage | 1.19 g/m3 |
| Outdoor evidence / fresh under 600 s | 14 / 14 samples |
| Minimum indoor / served floor | 67.01 / 61.90 F |
| Vent / fan relay-on samples | 0 / 0 |
| Heat 1 / heat 2 relay-on samples | 0 / 0 |
| Action-ledger minute coverage | 15 contiguous minutes |
| Action-ledger decisions | 35 `IDLE`, priority `vpd` |
| Dry-out admissions | 0 |
| Stop reason | sunrise |

The outside air was drier and indoor temperature stayed above the served floor,
but every action remained `IDLE` with the dry relays off. Complete evidence plus
zero admission is `blocked`; the small VPD rise is context, not proof of
controller effectiveness. This episode validates the blocked-opportunity
analytics but cannot by itself characterize current firmware.

## Outcome and safety semantics

- Admission is classified on each action row before minute aggregation, so an
  action from one row cannot be combined with relay truth from another.
- Physical dry admission is independent of heat2 qualification. Forbidden
  heat2 therefore remains visible and fails the safety gate instead of making
  the action disappear.
- Realized hold requires held-temp attribution plus actual `DEHUM_VENT`, vent,
  fan, and heat1 on the same row. General daytime VPD dehumidification is
  surfaced but allowed.
- `effective` requires both a VPD rise of at least 0.05 kPa and measured indoor
  absolute-humidity removal of at least 0.05 g/m3 in the 10-20-minute response
  window. Sensible heating that raises VPD without removing moisture is
  `ineffective`.
- `ineffective`, `blocked`, and `insufficient_evidence` are unresolved; none is
  a completed control fix.

## Read-only production and provenance checks

- `climate_action_log` had more than 161,500 current `vallery` rows from
  2026-05-25 onward. The deployed writer is byte-identical to main, the app role
  has INSERT/SELECT, and current pod logs show no action validation/write errors.
- An earlier zero-row probe used the wrong greenhouse id (`greenhouse1`) and was
  discarded. Production and the evidence contract use `vallery`; no emitter
  source fix belongs in this lane.
- Action logging has unrecoverable gaps because the single insert path has no HA
  backfill. Last-24-hour cadence had p50 10.107 s, p95 30.250 s, 17 gaps over 90
  seconds, and a maximum gap of 866.639 s. Affected episodes must remain
  `insufficient_evidence`.
- Refreshed corpus SHA-256:
  `47ab56eac236c3e7af85d39b45159e3c871aa7eb3674fecc958972c278ca56dc`
  (`296,580` unique timestamp rows; `295,715` source-backed; `199,480`
  source-backed/fresh under 600 seconds).
- Archived prior corpus SHA-256:
  `b31ce9348f9602e6b94935a63c819b7048275fdb410c53632848f3f697bb261e`
  (`193,525` rows; no populated outdoor-age values).
- Historical `temp_low`/`vpd_low` comes from served `cfg_*` readback. Current
  band functions are fallback only, so current anchors cannot reinterpret old
  episodes.
- Migration 191 is intentionally not applied by this worker. Its disposable-DB
  fixture covers effective, heat-only ineffective, blocked, incomplete,
  mixed-row attribution, projected-vs-realized hold, forbidden heat2, daytime
  hold failure, and daytime episode exclusion.

## Required follow-up

Deploy migration 191 in the controller-owned release sequence and restart
`verdify-mcp` and `verdify-ingestor`. The firmware-control owner must then decide
and implement the accepted solar-day behavior. Acceptance for that scope is:

1. preserve legitimate ordinary daytime dehumidification;
2. prevent actual held-temp admission during solar day, or explicitly revise
   the human-approved zero-daytime requirement;
3. preserve temperature-floor, re-entry, wind/dwell, and heat2-off safeguards;
4. prove the change with source-backed replay, a daytime-hold negative fixture,
   current firmware identity, and independent safety review; and
5. recollect realized episodes before claiming dry-out effective.
