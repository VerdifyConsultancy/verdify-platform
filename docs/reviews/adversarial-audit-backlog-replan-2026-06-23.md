# Adversarial audit and backlog replan - 2026-06-23

## Scope

This audit reconciles the latest control findings, recommendations, open GitHub
issues, and stale planning docs into one backlog replan. It is intentionally
adversarial: every recommendation is treated as unproven until it agrees with
current source, live issue state, and the June 2026 review evidence.

Inputs inspected:

- `README.md`, `docs/handoff/k3s-agent-handoff.md`, root lane docs, `Makefile`,
  `pyproject.toml`, `.github/workflows/`, `.pre-commit-config.yaml`.
- `docs/adr/0004-floating-corridor-control.md`.
- `docs/reviews/firmware-band-performance-review-2026-06-22.md`.
- `docs/reviews/mechanical-response-matrix-overnight-dehum-2026-06-22.md`.
- `docs/reviews/diurnal-solar-cycle-math-review-2026-06-23.md`.
- Current source in `firmware/`, `db/`, `ingestor/`, `verdify_schemas/`, and
  `tests/`, using `grep` rather than `rg`.
- Open GitHub issues #17, #20, #291, #293, #300, #327, #359, #361, #365-#371,
  plus #377-#379, #381, #382, #383, and open PRs via authenticated `gh`.

No production, device, DB, ArgoCD, or tunable writes were performed. GitHub
issue edits were performed only for tracker cleanup; this file is the durable
planning artifact.

## Access and safety summary

- Filesystem: unrestricted local checkout at `/Users/jason/repos/verdify-platform`.
- Network: available; GitHub read access verified with `gh` as `jvallery`.
- Branch/worktree: `main` tracking `origin/main`; pre-existing untracked review
  docs remain present.
- Secrets: no raw secrets read or printed. `gh auth status` only confirmed
  account and masked token state.
- Risk posture: tracker/docs changes only. Device-affecting tunables, firmware
  OTA, prod ArgoCD sync, destructive DB work, credential rotation, and device
  VLAN actions remain Jason-gated.

## Executive conclusion

The backlog should pivot from target-hugging to corridor floating. ADR-0004 is
the correct north star. The 2026-06-23 tracker cleanup now records the sequence
in the parent and child issues instead of leaving the board pointed at ADR-0003,
target-distance metrics, and migration 147 reward semantics.

The next work should be sequenced as:

1. Fix DB solar phase correctness.
2. Run the `band_track_fraction -> 0` float trial with operator approval.
3. Add outcome/KPI reporting for corridor compliance and actuator cost.
4. Add moisture-estimator and actuator-effectiveness telemetry.
5. Tune VPD/dehum policy from that telemetry, especially overnight low-wet and
   high-light dry-side behavior.
6. Reframe issue bodies and close or downgrade stale issues so the board stops
   pulling toward superseded target-compliance work.

Do not start by retuning anchors, moving the summer curve later, applying
migration 147 as written, or widening every band edge blindly.

## Findings that survived the adversarial check

| Finding | Status | Evidence | Planning effect |
|---|---|---|---|
| ADR-0004 should govern new climate work. | Valid. | ADR-0004 accepted 2026-06-18; live data shows served temp compliance is high while pinched compliance creates apparent misses. | #359, #365, and #371 now frame acceptance around outcomes in the corridor. |
| DB solar math is seasonally wrong. | Valid and high confidence. | `db/schema.sql` `fn_solar_altitude()` uses `hour_angle := RADIANS(15.0 * (local_hour - 13.0))`; the diurnal review shows winter sunrise/noon/sunset are roughly one hour late versus firmware/Python NOAA. | #293 is now the focused P1 issue under L5/L6 and #359. Fix before anchor retunes or seasonal planner claims. |
| Firmware solar math is good enough. | Valid for current planning. | `tests/test_solar_band_anchors.py` covers NOAA-style Longmont solstice values within the +/-5 minute contract. | Do not change firmware solar math first. Mirror it in DB/tests. |
| `band_track_fraction -> 0` is the clean next control experiment. | Valid, but device-affecting. | Device is on `2026.6.17.2042.dcc6078`; issue #377 correction confirms pinch is wired and live at 0.25. Served temp compliance post-sync is 99.0% overall and 97.1% from solar noon to +5h, while pinched temp compliance is materially lower. | #377 remains P0 with explicit Jason/operator approval, observation window, and acceptance metrics. |
| Summer curve should not move later right now. | Valid. | Diurnal review: target peak about solar noon +194.5 min; measured indoor temp/VPD peaks about +112 to +121 min, vent about +180 min, outdoor temp about +255 min. | #361 now says DB solar fix and float trial precede anchor retune; if target tracking is kept, earlier not later is the likely direction. |
| VPD is the real remaining control axis. | Valid. | Firmware performance review: VPD compliance remains weak; mechanical review shows hot/dry misses coupled to venting and wet duty. Diurnal review shows VPD served-band compliance barely improves when pinch is removed. | Split VPD work from temp-anchor work. Prioritize moisture telemetry and policy coverage. |
| Moisture estimator exists but is not observable enough. | Valid. | `firmware/lib/greenhouse_logic.h` has `MoistureExchangeEstimate` fields for action, reason, gains, freshness, and heat co-run; `climate_action_log` schema/model does not persist these fields. | #327 is now the P1 telemetry issue before deeper dehum policy changes. |
| Overnight low-wet in-band rows can idle despite heat-assist being the effective physics. | Valid. | Mechanical review: overnight low-wet no-action rows drift wetter on average; `MX_HEAT_ASSIST` was not a standalone normal action unless temperature or safety also called for heat. | #383 now has a local source-policy implementation for bounded closed-vent heat-dehum; live proof remains gated on OTA/deploy plus outcome KPI review. |
| Fog/dehum ping-pong is real enough to track. | Valid. | Mechanical review sequence around 2026-06-17 22:49-23:17 MDT: fog pushes dry VPD to low-wet, followed by short dehum pulses and fog again. | Add self-induced dehum classification and anti-ping-pong acceptance. |
| #366 is stale as written. | Refuted as current P0. | Current code uses `min_dew_margin_for_wetting()` with `max(day, night)` at night, default night margin 10 F, and BC-7 tests cover stricter night behavior. | #366 was closed as stale/completed unless contrary live evidence appears. |
| Anti-chatter/dead-code cleanup remains valid but lower priority. | Partially valid. | `sw_dwell_gate_enabled`, `bias_heat`, `bias_cool`, fan/stage latches, and cleanup comments still exist. Current risk is cleanup and complexity, not immediate plant safety. | Keep #367/#369/#370 open, but move behind DB solar, float trial, and VPD telemetry/policy. |
| Root lane docs are stale. | Valid. | `PROJECT_BOARD.md`/`EPICS.md` marked L3 climate done using "DB verified live" language that the seasonal DB solar finding contradicted. | Root docs now carry the G2 follow-through overlay so future sessions do not inherit a false "L3 complete" assumption. |
| Stale PRs are planning noise. | Valid. | PRs #309, #272, #271, #203, #125, #101 targeted retired `live/platform-main`; #311 and #208 were old `main` PRs. | Retired-base PRs were closed; #311 was closed as superseded; #208 remains open for separate CODEOWNERS disposition. |

## Findings rejected or constrained

- Do not treat "temperature misses" as proof the curve is wrong. Against the
  served crop corridor, temperature is mostly fine; pinching creates much of the
  apparent miss.
- Do not solve daytime VPD by moving the whole VPD curve later. Measured wetting
  demand and VPD peak around solar noon +1.5h to +2h, not +4h.
- Do not apply migration 147 exactly as #20/#365 currently describe. ADR-0004
  supersedes reward-on-target-compliance. A new outcome objective is needed first.
- Do not tune `wet_taper_before_sunset_min` expecting climate behavior. The
  diurnal review found it is currently a no-op shim in this path.
- Do not demote platform reliability to "later." #382 and #218 remain Track A
  reliability risks, but they are parallel infra work rather than climate-control
  tuning prerequisites.

## Replanned backlog sequence

### P0/P1 immediate control sequence

1. **DB solar phase parity with firmware/Python NOAA**
   - Issue action: update #293 from broad P2 feed-forward to focused P1 solar
     phase correctness, or create a new child under #347/#359 and link #293.
   - Scope: replace DB solar altitude/sunrise/sunset/phase with firmware/Python
     NOAA-equivalent event math, or source DB analysis from device-published
     solar events where appropriate.
   - Acceptance: March equinox, June solstice, September equinox, and December
     solstice agree with `ingestor/solar.py`/firmware contract within +/-5 min.
   - Verification: migration classification, targeted SQL tests, schema dump
     refresh, `make migration-rollback-safety`, and relevant Python tests.

2. **Float trial: `band_track_fraction -> 0`**
   - Issue action: keep #377 P0 but update acceptance with June 22-23 evidence.
   - Gate: Jason/operator approval because this is real device behavior even
     though it is a reversible no-OTA tunable push.
   - Acceptance: device readback is 0; observe at least 48-72h including one clear
     and one cloudy/variable day if weather allows; compare served/pinched
     corridor compliance, VPD high stress, dew margin, vent/fan/fog/mister cycles,
     zone spread, and water runtime.
   - Rollback: restore 0.25 if VPD stress, cycles, dew margin, or temperature
     excursions regress beyond the predeclared guardrail.

3. **Daily corridor and actuator KPI**
   - Issue action: fold into #371 or create L6 child under #348/#359.
   - Scope: daily pinched-vs-served compliance, day/night splits, solar-phase
     buckets, actuator starts/runtime, peak transitions/hr, water runtime, VPD
     high/low stress, dew margin, and zone spread.
   - Why now: this is the scoreboard for #377, #378, and #383 VPD policy work.

4. **Moisture estimator telemetry (#327)**
   - Issue action: done 2026-06-23; #327 is the focused P1 child under L5/L6
     and #359.
   - Scope: persist `mx_action`, `mx_reason`, `vent_vpd_gain_kpa`,
     `heat_vpd_gain_kpa`, `vent_overcools`, `outdoor_fresh`,
     `heat_assist_corun`, and enough estimator context to classify self-induced
     fog-to-dehum.
   - Gate: likely schema + firmware/ingestor work; schema lands first; OTA is
     Jason-gated if new firmware-published fields are required.

5. **Overnight dehum and dry-side VPD policy (#383)**
   - Issue action: done 2026-06-23; #383 is the focused P1 child under #359.
     Local source progress exists after the read-only KPI surface and #327
     telemetry wiring: low-wet night `MX_HEAT_ASSIST` can select bounded
     closed-vent heat1 dehum when temperature headroom is available.
   - Scope: bounded heat-first low-wet action when temp is in band and venting
     overcools or underperforms; fog/dehum anti-ping-pong; review
     `dehum_heat_assist_min_dwell_ms`; tighten high-dry wetting response when
     temp is in band.
   - Verification: replay diff, invariants, unit tests, and before/after daily
     equipment-effectiveness KPIs.

### P1 after immediate evidence

6. **Corridor width and crop tolerance (#378)**
   - Keep P1, but do not blindly widen all temp/VPD edges.
   - Temp corridor may already be wide enough for summer operation.
   - VPD corridor needs a crop-physiology decision: tolerate a higher high-light
     VPD edge, or keep the edge and improve wetting/vent-humidification response.

7. **Anchor optimization and bias cleanup (#361)**
   - Reframe as "after DB solar parity and float trial."
   - Bias cleanup remains valid, especially if tied to #369/#370.
   - Any band-curve change must run `make firmware-replay-band OLD=<base>`.

8. **Sensor input integrity (#368)**
   - Keep P1. Floating makes edge decisions more sensitive to bad sensor input.
   - Add spike/flatline/stuck-in-range tests and fail-safe behavior.

### P2 cleanup and longer-horizon work

9. **Anti-chatter and dead-code cleanup (#367/#369/#370/#300)**
   - Keep open but rank behind solar parity, float trial, and VPD observability.
   - Focus on net-negative complexity only when replay/invariant coverage proves
     behavior is unchanged or intentionally changed.

10. **Actuator-aware model and MPC (#379)**
    - Keep P2. It should consume the daily nature-alignment report and actuator
      KPI history, not precede them.

11. **Stale PR/issue hygiene**
    - Done 2026-06-23: closed `live/platform-main` PRs #309, #272, #271, #203,
      plus #125 and #101 without deleting branches.
    - Done 2026-06-23: closed superseded main docs PR #311 without deleting its
      branch.
    - Draft main PR #208 remains open for separate CODEOWNERS/repo-policy
      disposition.
    - Done 2026-06-23: rewrote and closed #17/#20 as superseded by ADR-0004
      outcome scoring (#365/#371).

## Issue disposition table

| Issue | Current disposition | Required action |
|---|---|---|
| #359 | ADR-0004 epic body rewritten 2026-06-23. | Keep as the canonical floating-corridor tracker; reconcile child issue states as work lands. |
| #377 | Valid P0; body updated 2026-06-23 with source-default progress and live-trial gate. | Run only with Jason/operator approval, observation window, and rollback guardrails. |
| #378 | Body rewritten 2026-06-23. | Decide corridor width after #377/#371 data; split temp corridor from VPD corridor decision. |
| #379 | Body rewritten 2026-06-23. | Keep P2; consume #293/#327/#371/#377 evidence before MPC work. |
| #361 | Body rewritten 2026-06-23. | DB solar parity and float trial precede any anchor retune; keep bias cleanup. |
| #365 | Body rewritten 2026-06-23 around ADR-0004 outcomes; migration 147 marked not-as-is. | Implement the replacement outcome objective. |
| #371 | Body rewritten 2026-06-23 around corridor outcomes and resource KPIs. | Implement daily outcome/KPI surface. |
| #366 | Closed 2026-06-23 as stale/completed with code/test evidence. | None unless contrary live evidence appears. |
| #367 | Valid cleanup. | Re-rank after #377 and KPI evidence unless flapping increases. |
| #368 | Valid P1. | Keep and link to floating edge-sensitivity. |
| #369 | Valid cleanup. | Keep behind functional control/telemetry priorities. |
| #370 | Valid cleanup; current body is not an ADR-0003 retune blocker. | Keep behind functional control/telemetry priorities. |
| #293 | Promoted/rewritten 2026-06-23 as focused P1 DB solar parity task. | Review/merge/apply migration 186, refresh dependent surfaces, then revisit feed-forward. |
| #291 | Mostly superseded by current solar/band work. | Re-evaluate after DB solar parity; close or narrow if no longer actionable. |
| #327 | Body rewritten 2026-06-23 as P1 moisture-estimator telemetry lane. | Implement schema-first estimator telemetry after migration 186 is serialized. |
| #383 | Created 2026-06-23 as focused VPD/dehum policy lane; bounded low-wet night `heat_dehum` source path added locally and offline firmware gates passed. | Live proof only after OTA/deploy approval and outcome KPI review; fog/dehum ping-pong and high-light dry-side VPD remain. |
| #300 | Still valid cleanup. | Keep as registry/doc drift cleanup; do not let it block P0/P1 control sequence. |
| #17/#20 | Rewritten and closed 2026-06-23 as superseded by ADR-0004 outcome scoring. | None; replacement work is #365/#371. |
| #218/#382 | Track A reliability. | Keep parallel P0/P1 infra attention; do not block climate planning, but do not schedule writer restarts lightly while #382 is open. |

## Root-doc status and remaining follow-up

This audit adds an interim overlay to `PROJECT_BOARD.md`, `EPICS.md`,
`MILESTONES.md`, and `HISTORY.md` so future sessions do not inherit the stale
"L3 is simply done" conclusion. The root docs now point at this replan and pull
DB solar parity, #377 float follow-through, outcome KPIs, and moisture-estimator
telemetry into G2/L5/L6 follow-through.

The second root-doc pass on 2026-06-23 mirrored the #327/#361/#378/#379/#383
tracker state and left this audit as the detailed evidence artifact.

## Implementation progress

### 2026-06-23 DB solar parity lane

- Implemented locally in migration `186-noaa-solar-phase-parity.sql`, the schema
  mirror, a Python SQL-contract test, and a rollback-wrapped psql replay fixture.
- Verification passed for targeted solar tests, migration rollback-safety,
  rollback-wrapped prod-DB proof, ruff, and `git diff --check`.
- Full `make test` did not prove repo-wide green in the local Mac checkout
  because baseline tests still assert retired Docker/systemd/laptop services.
  The DB solar lane itself was covered by the targeted tests and SQL proof.
- Remaining before production: review/merge, apply the migration through the
  normal migration job, refresh cached band-curve materialized surfaces that
  depend on `fn_solar_phase()`, then update #293/#359 tracker state.

### 2026-06-23 ADR-0004 source-default lane

- Implemented locally: firmware source defaults now set `band_track_fraction=0`
  in both ESPHome globals and C++ `default_setpoints()`, and the band-derived
  replay path defaults to full float unless explicitly overridden.
- Planner/MCP guidance no longer tells Iris to drive target-reference deviation
  toward zero. The registry keeps `band_track_fraction` planner-writable only for
  the value `0`; firmware still clamps `[0,1]` for operator diagnostics/readback.
- Verification passed: prompt/registry tests, `make test-firmware`,
  `make firmware-invariants`, stock worktree replay diff against `origin/main`
  with 0 divergent rows, band-derived replay report with 48,090 divergent rows
  (24.85%, intentional float-vs-pinch behavioral diff), ruff, ESPHome compile,
  DB solar targeted tests, migration rollback-safety, and `git diff --check`.
- No live tunable push and no firmware OTA were performed. The runtime #377
  float trial remains Jason/operator-gated with a predeclared observation window
  and rollback value.

### 2026-06-23 tracker cleanup

- GitHub issue bodies updated: #359, #365, #371, #293, #377, #327, #361, #378,
  and #379.
- New GitHub issue #383 created for evidence-gated VPD/dehum policy tuning after
  the #293/#327/#371/#377 evidence set.
- GitHub issues #17 and #20 rewritten and closed as superseded by ADR-0004
  outcome scoring; #366 closed as stale/completed with source/test evidence.
- Retired-base PRs #309, #272, #271, #203, #125, and #101 closed without branch
  deletion.
- Superseded main docs PR #311 closed without branch deletion; draft CODEOWNERS
  PR #208 remains open for a separate repo-policy decision.

### 2026-06-23 #383 low-wet heat-dehum source-policy lane

- Implemented locally: when night VPD is below the served corridor and the
  moisture estimator selects `MX_HEAT_ASSIST`, the band-first firmware can run
  heat1-only closed-vent dehumidification while temperature remains inside the
  served band and the 1.5 F heat probe stays below the high edge.
- The effective climate decision labels this as VPD-priority `heat_dehum`, and
  the mirrored firmware twin header is byte-identical to the firmware source.
- Verification passed: `make test-firmware` (257/0), `make firmware-invariants`
  (193,525 rows), `THRESHOLD_PCT=0.1 make firmware-replay-worktree OLD=origin/main`
  (50 intended heat1-only divergent rows, 0.03%), `make firmware-replay-band
  OLD=origin/main` (51,809 intentional ADR-0004/source-policy divergent rows,
  26.77%), `SECRETS_SRC=$HOME/.verdify/esphome-secrets.yaml make firmware-check`,
  and `git diff --check`.
- The default zero-threshold replay intentionally failed before the thresholded
  rerun: the 50 divergent rows all kept mode `IDLE` and turned on only `heat1`.
  This is the #383 behavior change, not a hidden relay/mode churn.
- No OTA, live tunable push, prod sync, service deploy, or live KPI proof was
  performed. The remaining #383 scope is live before/after review plus fog/dehum
  ping-pong and high-light dry-side VPD policy.

### 2026-06-23 #383 VPD policy sequence KPI lane

- Implemented locally without a migration: `outcome_kpi(target_date)` now
  compresses `climate_action_log` rows into action episodes and reports wetting
  episodes, vent-dehum episodes, heat-dehum episodes, and 30-minute wet->dehum /
  dehum->wet transition counters.
- This gives the fog/dehum ping-pong review an explicit daily scorecard field
  instead of relying on manual timeline inspection.
- Read-only prod SQL validation of the new aggregate shape succeeded for
  2026-06-22; observed output was 325 total episodes, 207 wetting episodes,
  4 vent-dehum episodes, 0 heat-dehum episodes, and 0 wet/dehum transitions
  inside the 30-minute ping-pong window for that date.
- GitHub progress comments were added to #371, #383, and #327 so the tracker
  matches this local state without claiming deployment.
- No live service deploy or dashboard rollout was performed; live rows still
  depend on the normal service deploy path, and the richer moisture-estimator
  action/reason buckets still require the gated OTA plus ingestor deployment.

## Proposed next work packages

1. **Docs/tracker reframe**
   - Done 2026-06-23: edited #359/#365/#371/#377/#378/#361/#293/#327/#379.
   - Done 2026-06-23: closed/downgraded stale #17/#20/#366.
   - Done 2026-06-23: created #383 for overnight dehum/dry-side VPD policy.

2. **DB solar parity implementation**
   - New migration for DB solar helpers.
   - SQL/Python tests for equinoxes and solstices.
   - Refresh schema dump and any materialized band curve surfaces.

3. **Float trial preparation**
   - Define guardrails and observation dashboard/query.
   - Get Jason/operator approval.
   - Push tunable only after no critical alerts and current telemetry health are
     checked. This audit does not perform the push.

4. **Outcome/KPI surface**
   - Daily pinched/served corridor and actuator lifecycle metrics.
   - Nature-alignment report: device-vs-DB solar events, measured solar centroid,
     outdoor/indoor/VPD/relay peak offsets, and phase-bucket costs.

5. **VPD observability and policy**
   - Estimator telemetry source wiring is in place.
   - Bounded heat-first overnight dehum source policy is in place.
   - Still needed: OTA/deploy/live KPI proof, fog/dehum anti-ping-pong, and
     high-light dry-side VPD response review.

## Verification requirements by work type

- Docs-only tracker/root-doc work: `git diff --check`.
- DB solar migration: `make migration-rollback-safety`, targeted migration proof,
  targeted SQL/Python solar tests, and schema dump review.
- Firmware telemetry/policy: `make test-firmware`, `make firmware-invariants`,
  `make firmware-replay OLD=<base> NEW=HEAD`, `make firmware-check`; if band curve
  shape changes, also `make firmware-replay-band OLD=<base>`.
- Site/dashboard KPI work: relevant site/Grafana generation plus local render or
  query verification.
- Device-affecting tunable trial: Jason/operator approval, preflight health,
  explicit rollback value, and 48-72h observation artifact.

## Bottom line

The strongest next move is not another broad refactor. It is to fix the solar
source-of-truth bug, stop chasing a target line when the crop corridor is already
met, and make VPD/actuator physics observable enough that the next policy change
can be proved. The issue tracker should be edited so future work cannot
accidentally fall back into ADR-0003 target-hugging.
