# Sprint S8 — vanda-night-dehum (awaiting approval)

**Goal:** make the overnight center-zone (bare-root Vanda) climate dryable per the
converged #410 analysis and the 2026-07-03 adversarial design review — land the
firmware change flag-OFF, land the telemetry that makes the bake observable, fix
the freeze-rule-adjacent doc drift, and pre-stage the #411 anchor migration — so
one Jason-gated OTA + tunable-push sequence can take 02–06h median VPD from
**0.61 → ≥ 0.78** with `night_min ≥ 64 °F` as the hard rollback canary.

Baseline: `origin/main` @ `0efdb2f` (includes #385). Milestone: Greenhouse
Control Optimization. Board: GitHub Project #5. This is the first structured
sprint transaction under `.agent-workflow/sprints/`; S4–S7 remain tracked in the
root docs.

## Why now (one paragraph of context)

Two independent reviews converged on the wet-night problem (#410); the design
review (comment on #410) endorsed the fix with three required changes (actual-temp
hold floor, hold-to-`temp_target` reheat with dwell bypass, OFF-default flag) and
found two deploy confounds: the next OTA silently resets `band_track_fraction`
0.25 → 0.0 (the #377 float flip rides along), and #410 cannot mechanically engage
until #411 re-raises the night corridor. Separately, the door screen-window has
been open since ~06-19 and **stays open until fall** (#412) — it is the current
passive night dryer and the baseline for every number in this plan.

## Lanes

| Lane | Issue | Branch | Owner → Reviewer | Responsibility |
|---|---|---|---|---|
| `fw-410-vent-reheat-hold` | #410 | `fw-410-vent-reheat-hold` | worker → independent-critic | Estimator ladder + hold gate + flag + cold-night fixture + behavior-coupled docs (fsm-spec, ADR-0003 §6.4, ADR-0004, control contract) + heat1-electric comment fixes |
| `data-327-moisture-telemetry` | #327 | `data-327-moisture-telemetry` | worker → independent-critic | Migration **187** (schema-first) + ingestor + MCP exposure incl. the two new #410 fields; rule-7 restarts documented |
| `docs-413-freeze-drift` | #413 | `docs-413-freeze-drift` | worker → independent-critic | Checklist re-pin/record steps, OTA-reset mechanics in handoff/runbook, dated envelope notes on three review docs |
| `db-411-night-anchors` | #411 | `db-411-night-anchors` | worker → independent-critic | **BLOCKED on gate g-411**: migration **188** (night anchors + vpd_target ~0.83, one migration), prepared not applied |

**Dependency order:** group 1 = the first three lanes in parallel (disjoint owned
paths; one soft coordination: the two new telemetry field names between fw-410 and
data-327). Group 2 = `db-411-night-anchors`, hard-blocked on gate g-411 **and** on
187 merging first (migrations serialized).

## Gates (all owner: jason)

1. **plan-approval** — this document; the plan PR is the approval vehicle.
2. **g-411-night-temp-priority** — deep-DIF vs dry-roots vs seasonal split
   (GitHub #411). Blocks lane 4 and the activation steps. `seasonal-split`
   returns to planning.
3. **g-377-pinch-repin** — post-OTA pinch state: re-pin 0.25 (clean bake) vs
   accept float 0.0 (joint #377 trial). Executed at wave STEP-05, recorded either way.
4. **HR-02 OTA approval** and **HR-03 outcome acceptance** — in the wave release plan.

## Release sequence (details in `release/wave-release-plan.yaml`)

merge lanes → prod-promote + gated sync + **migration 187** + bounce
ingestor/mcp → *(g-411)* **migration 188** → **flag-OFF OTA** (Jason; freeze
rules) → *(g-377)* pinch decision + record envelope/pinch/flag state → 48 h soak →
**flag ON** (tunable push, no OTA) → 48 h canary window → bake report + HR-03.
Every layer reverts independently; `night_min < 64 °F` ⇒ immediate flag-off.

## Deferred (explicitly out)

#383 remainder (post-OTA), #378 corridor widths, #379 MPC, daytime dry-side
issue, #412 execution (fall), anything touching verdify-www/crm.

## Exceptions to record

- **Missing formal prerequisites:** no approved project-definition /
  architecture-contracts / state-of-union artifacts exist in `.agent-workflow/`
  (the repo predates the standard shape). This plan substitutes the repo-native
  contracts (CLAUDE.md ownership + freeze rules, fsm-spec, ADRs, drift guards)
  and the live GitHub board as its inputs. Approving this plan accepts that
  substitution for S8.
- **Module contracts** in lane files reference those repo-native docs, not
  architecture-contracts artifacts.
- `#413` item 2 file placement corrected here: `docs/firmware-fsm-spec.md`
  belongs to the fw lane (single writer), not the docs lane.

## Evidence the sprint must produce

Rule-9 artifact blocks on the firmware PR (replay diff / invariants / unit
delta), flag-ON divergence bucket report, cold-night fixture output, migration
187/188 rollback proofs, the explain-query, doc diffs, and finally
`docs/reviews/s8-vanda-night-dehum-bake-report.md` with the canary numbers and
the recorded envelope + pinch + flag states.
