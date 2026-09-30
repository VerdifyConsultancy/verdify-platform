# C0 native route freeze and prospective pilot audit — September 30, 2026

Read-only database and public API evidence was captured at approximately
17:14 UTC. This audit changes no controller, experiment, draw or production
resource. Private readbacks are retained in
`/Users/jason/Documents/Codex/verdify-route-pilot-audit-20260930/`.

## Completed September 29 native route observation

The natural `verdify-fixed-panel-native-freezer-29845830` Job succeeded at
06:30:07 UTC. Its immutable receipt 1 covers Denver September 29 `[06:00,24:00)`,
September 29 12:00 through September 30 06:00 UTC. The database receipt froze at
06:30:04.686339 UTC and its stored projection hash independently recomputes to
`2837c7a22a4d23eefca47ff24bf9a276a9fd461401dd61f5919832cb78f75074`.

The bounded source window contains 38,880 callbacks: 6,480 for each of the six
north/east/west temperature/RH routes. It contains one runtime instance and one
transport generation, no connected or gap event, and contiguous source
sequences 24,344–63,223. All 72 bins contain 15 complete source minutes;
47/72 bins meet both declared bands. This is a route observation under target
revision 2, contributor revisions `[5,4,6]` and source binding 1. Its firmware
revision is `2026.7.10.1500.09ee886`, collector revision
`20b5a0b36e8c53e3e88266a2d10c5248e22e405d`, and route SHA-256
`36d3f8ee0be85895683f319aeafad2a4262e1b4cf26741b45cac2d1adbe3729d`.

The bounded ordinary reader and public
`/api/v1/scorecard?date=2026-09-29` agree on the receipt, hash, lineage, callback
count, eligibility and available state. Physical serial verification, Modbus
poll timing, physical-proof eligibility, experiment-endpoint eligibility and
causal-effect flags are all false. The result supplies no randomized outcome
or crop placement proof. The current-day public home card remains correctly
`not_frozen`; that endpoint has no historical-day parameter.

The live session ledger also records a disconnect at September 30
16:14:24.624919 UTC and reconnect at 16:14:27.242088 UTC. Those events are after
both the window cutoff and its immutable freeze, and do not invalidate the
retained September 29 receipt. They are not evidence of uninterrupted current
transport after the window.

Private readback SHA-256s:

- `route-db-readback.txt`: `5c0fc2b4bedbf4c4a236f39424ce503004b949e1ebb35e415eafa6730ced0459`
- `scorecard-20260929.json`: `bc84b1c3dbe76569c262b8116f4b901d49b16a072a9a2738e732b94e71282360`

## Historical replay and warm pilot

[#779](https://github.com/VerdifyConsultancy/verdify-platform/issues/779#issuecomment-5895297871)
has a completed negative historical scope result: 93 weather-matched pairs
reproduce, but authenticated August crop-target/contributor revisions are
unavailable and the stale label is inseparable from delivery failure. Do not
repeat the historical extraction or report a corrected causal crop-band effect.

The current warm calendar preregistration chooses June 1–July 30, 2027: 60
consecutive Denver local days, 30 adjacent pairs, and `[06:00,24:00)` outcomes
of 64,800 seconds each. Its climate analogue has 60/60 eligible days,
30/30 eligible adjacent pairs and 4,309/4,320 eligible bins. This supersedes
the earlier July-start discussion for calendar preparation; the September 27
seasonal note remains the original winter protocol source.

The calendar is selected before the draw, without a design lock. The accepted
0.13776 joint advance power remains provisional model output; source-faithful
joint power, actual selector choices, as-of forecast vintages, future crop
targets/bands, nine-state equipment source receipts, paired three-endpoint
variance/covariance, future daily eligibility, design lock and blinded
schedule are null. Historical bin-level covariance is not paired endpoint
covariance. The remaining scientific work is the exact 18-hour source/selector
replay and prospective source qualification followed by the immutable lock.
No elapsed 60-day randomized result exists at this audit.

Preserve every assigned day in its original arm, including fallback, failure,
rescue and missing endpoints. The selected operating endpoint is heterogeneous
nine-control-state minutes; it supports no energy, water, cost, carbon or
resource-efficiency claim. The all-pairs completeness sensitivities remain
`.99^30 ≈ .740` and `.95^30 ≈ .215`.

## Separate winter feasibility packet

The registered winter packet remains a passive route-only observational study
for November 2–December 31, 2026. Its original target/panel/cfg source bytes and
pinned extractor/protocol are already staged and backed up, with isolated
restore evidence in the existing #782 receipts. No re-registration or duplicate
target declaration is needed.

The natural September 30 `verdify-winter-feasibility-29845830` Job succeeded at
06:30:28 UTC and reported no missing completed day in the registered interval.
That is truthful pre-window collector evidence; it is not a November outcome.
The first eligible collection remains November 3 at 00:30 Denver for the
completed November 2 day. Daily completeness, retained 60-row final manifest,
virtual selector opportunity/fallback and future observed resource scope
remain future deliverables. The winter packet cannot replace the randomized
hot/dry pilot.

## Restored receipt durability witness

`scripts/qualify-restored-native-route.sql` provides a read-only witness for a
retained route receipt after a real paired restore. It refuses a database other
than `verdify_rehearsal`, TCP connectivity or nonempty `listen_addresses`.
The caller supplies the independently retained day, receipt ID and projection
hash. Both the immutable stored projection and the bounded reader must match;
all physical/experiment/causal eligibility flags must remain false.

On a held socket-only paired restore, invoke:

```sh
psql -X -qAt -v ON_ERROR_STOP=1 -d verdify_rehearsal \
  -v route_day=2026-09-29 -v route_receipt_id=1 \
  -v route_projection_sha256=2837c7a22a4d23eefca47ff24bf9a276a9fd461401dd61f5919832cb78f75074 \
  -f scripts/qualify-restored-native-route.sql
```

The restore's independently verified paired backup remains the source of these
rows. This script inserts no synthetic receipt or outcome and cannot establish
a connected randomized executor path or physical device evidence.
