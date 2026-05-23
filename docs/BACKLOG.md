# Backlog — Cycle Index

Per-agent backlogs live in `docs/backlog/{agent}.md`. This file is the index + "who's on what this cycle."

## Verdify refocus command (2026-05-15)

Jason is refocusing Verdify from a greenhouse-first public launch / SaaS trajectory toward **Verdify as a consulting brand with the Longmont greenhouse as the public lab proof asset**.

Canonical refocus backlog: [`docs/backlog/refocus.md`](backlog/refocus.md).

Canonical lab-site refactor and data-integrity backlog from the 2026-05-20
live-site feedback: [`docs/backlog/lab-site-refactor-2026-05-20.md`](backlog/lab-site-refactor-2026-05-20.md).
The same file also tracks the later 2026-05-20 live visual/data follow-up
items: Grafana panel chrome/colors/bands/relay states, lighting/lux review,
Resource Use monthly cost and solar-aligned panels, GPU power evidence,
Architecture cleanup, and electric-cost math.

Canonical 2026-05-22 repo recovery backlog:
[`docs/backlog/project-recovery-2026-05-22.md`](backlog/project-recovery-2026-05-22.md).
This plan was created from `PROJECT_STATE.md` and is the active cleanup train
for PR #80, the production-linked dirty root worktree, stale worktrees, temp
worktrees, stashes, partially deployed irrigation/planner/site work, and final
health gates.

Current posture:

- Track A greenhouse operations still outrank all business/site/repo work.
- `lab.verdify.ai` is the greenhouse lab + evidence site; `verdify.ai` / `www.verdify.ai` redirect there until the separate consulting CMS is ready.
- Broad SaaS/multi-tenant work moves behind consulting validation unless needed for Track A or public-site reliability.
- Repo, GitHub, project-board, and agent-scope splits are coordinator-owned until boundaries are decided.
- PR #80 stays intact as the first recovery baseline; unrelated dirty work must
  not be mixed into it.

## Active recovery cycle (2026-05-22)

This section supersedes the older 2026-04-20 "current cycle" status below for
day-to-day coordination. The older section remains as historical context.

| Order | Owner | Recovery focus | Gate |
|---|---|---|---|
| RM-0 | `coordinator` | Keep PR #80 intact as the lighting occupancy baseline | **Merge/CI complete:** PR #80 merged as-is to `main` at `66f0836`; firmware bake remains tracked in `F-RM0` |
| RM-1 | `coordinator` | Quarantine dirty state and split independent PR branches/worktrees | **Complete:** dirty themes split into reviewed PRs or archived RM7 patches; temp worktrees/stashes disposed |
| RM-2 | `coordinator` + `ingestor` + `web` | Integrate irrigation/fertigation canonicalization software without running the finalizer | **Merge/deploy complete:** PR #83 merged at `a8f8ffa`; main CI run `26314849773` passed; migration 134, ingestor/MCP restart, Grafana restart, and software-only live validation completed |
| RM-3 | `coordinator` + operator | Repair physical irrigation feedback and only then run the finalizer | Four feedback rows `ok`; no critical/high alerts |
| RM-4 | `genai` + `coordinator` | Reconcile planner-graph shadow and memory-backfill work, including stale `genai` worktree files | **Merge/deploy complete:** PR #84 merged at `3a2eb87`; main CI run `26315767545` passed; ingestor-only restart completed; live shadow smoke wrote accepted non-authoritative row `4`; deployed backfill dry-runs passed |
| RM-5 | `web` + `coordinator` | Integrate public lab/Grafana refinements and cache-warm fix | **Complete:** PR #81 source/deploy/cache-warmer done; `lab.verdify.ai` and `labs.verdify.ai` both resolve to gateway and serve HTTP 200; issue #82 closed |
| RM-6 | `ingestor` + `coordinator` | Integrate climate overlay semantics and loose guard changes | **Merge/deploy complete:** PR #85 merged at `45758b6`; main CI run `26316119564` passed; ingestor restart/live-tail clean; deployed Tempest script wrote `weather_station` without orphan `climate` rows |
| RM-7 | `coordinator` | Clean temp worktrees, stashes, and generated-state risks | **Complete:** temp/recovery worktrees removed, stale genai worktree reset, stashes archived+dropped, `.git/.DS_Store` removed, persistent agent worktrees fast-forwarded to `45758b6` |
| RM-8 | `coordinator` | Final repository health closure | **Closure branch:** local gates green, runtime/site/Grafana healthy, issues #18/#19/#82 closed; final main CI pending this docs PR |

## Launch command (2026-05-02)

Broad launch is now a cross-agent release train. Canonical launch gates live in [`docs/launch/README.md`](launch/README.md); task tracking lives in [`docs/backlog/launch.md`](backlog/launch.md).

Current launch posture: **engineering/content gates are complete; broad launch now depends on Jason-owned copy, video, identity, and calendar decisions.**

2026-05-03 update: Jason's prior-art review is tracked in [`docs/launch/prior-art-rollup-2026-05-03.md`](launch/prior-art-rollup-2026-05-03.md). It adds post-P0 launch assets around Related Work, Safety Architecture, Baseline vs Iris, builder packaging, counterfactual replay, and progressive autonomy. These are P1/P2 credibility assets, not Track A firmware blockers.

Current launch sprint: [`Launch Sprint: L1 Credibility Package`](launch/sprint-2026-05-03-l1-credibility.md). Engineering/content assets are complete: related work, safety architecture, baseline evidence, FAQ/response pack, builder path, and object-model diagram. The 2026-05-03 OpenClaw/local-Gemma narrative is now historical; production planning has routed through Hermes/GPT-5.5 since 2026-05-11, with greenhouse memory, static site context, forecast/context retrieval, MCP-only tool access, and the ESP32 safety-critical controller unchanged. Remaining broad-launch blockers are Jason-owned: operations clip, final HN copy, identity posture, launch calendar, and comment coverage.

2026-05-03 content-QA update: launch pages and internal launch notes now use the same public story: Iris writes bounded tactical plans at solar milestones/event triggers; the ESP32 owns 8-state deterministic relay control every 5 seconds; telemetry, scorecards, costs, failures, and lessons make claims auditable.

2026-05-16 planner-loop trace update: the remaining backlog target is one
auditable Hermes/GPT-5.5 planner path, complete trigger coverage for forecast
deviations, solar transitions, and fixed local-time boundaries, plus strict
trigger-to-plan correlation and registry-range validation before a plan can
become active. Detailed work is split across `docs/backlog/genai.md` (`G-P*`),
`docs/backlog/ingestor.md` (`I-P*`), and `docs/backlog/cross-cutting.md`
(`C-P*`). Older local-first/OpenClaw rows below are retained as historical
shipping context.

| Owner | Launch focus | Backlog |
|---|---|---|
| `coordinator` / Jason | Privacy/security scrub, launch sequencing, identity/code-transparency decisions, final copy approval | `docs/backlog/launch.md` |
| `web` | Launch page/homepage proof path, lessons curation, daily-plan readability, OG card, Grafana public/fallback QA, metrics consumption, architecture/BOM/cost/outage pages | `docs/backlog/web.md` |
| `genai` | Lesson canonicalization semantics, planner narrative, HN response pack, weekly summary inputs | `docs/backlog/genai.md` |
| `ingestor` | Public metrics/data-health contract, plan delta support, sample export, freshness gates | `docs/backlog/ingestor.md` |
| `firmware` | Safety/control-split facts, BOM/control claims, no launch-blocking OTA unless Track A safety requires it | `docs/backlog/firmware.md` |
| `saas` | Public Grafana/access checks, waitlist/capture option, Cloudflare/Traefik/public API implications | `docs/backlog/saas.md` |

## Current cycle (as of 2026-04-20)

| Agent | Sprint | Status | Detail |
|---|---|---|---|
| `coordinator` | v1.5 landed | **Cross-cutting landed this cycle**: local-first planner contract v1.5, strict audited `set_plan`/`set_tunable` trigger correlation, manual `plan_run` ledger parity, and schema response fields for planner audit metadata | `docs/backlog/cross-cutting.md` |
| `ingestor` | planner hardening in progress | Hermes delivery is active, trigger ledger rows carry Hermes run IDs, fixed 00:00/06:00/12:00/16:00/20:00 boundary triggers supplement sunrise/sunset, forecast, deviation, and solar-derived transition triggers, per-trigger SLA timeouts alert through `planner_trigger_sla_timeout`, and active/future plan range drift is guarded through `planner_tunable_range_drift`. Remaining: canonical trigger matrix, no-missed-sunrise ledger, deviation trigger completeness, and planner health status surface | `docs/backlog/ingestor.md` |
| `genai` | planner hardening in progress | Hermes/GPT-5.5 prompt path is live with strict MCP tool allowlist, validation-mode handling, full context guidance, and MANUAL `plan_run` parity. Remaining: distilled site/lesson digest and context-pack versioning | `docs/backlog/genai.md` |
| `web` | — | Sprint-4 Grafana panels shipped via PR #16 (`9a9a05e`) this morning. No active sprint | `docs/backlog/web.md` |
| `firmware` | phase-1+ | Phase-0 shipped as sprint-10 (`8d2656d`) + sprints 7-9 overnight (`8c64030`, `dda9057`, and the 212b1c5 fog-window fix). No active sprint queued | `docs/backlog/firmware.md` |
| `saas` | `sprint-10` shipped | Rescope landed. Open task: apply migration 090 to prod DB; unblocked (coordinator has `docker exec psql` access) but awaits operator authorization | `docs/backlog/saas.md` |
| `iris-dev` | Hermes planner live | Hermes `hermes-iris` drives GPT-5.5 high-reasoning with no OpenClaw fallback. Active validation correlates `plan_delivery_log.hermes_run_id` to trigger rows and MCP writes after tightening audit enforcement | — |

## Recent ships (2026-04-19 → 2026-04-20)

In chronological order:

- `4cc5df5` ingestor: 6 cfg_* readback sensors routed for firmware sprint-3
- `c085d82` firmware/sprint-3: per-zone VPD target readback sensors
- `3b1d93a` ingestor/sprint-24-alignment: firmware sprint-2 fairness wire-up + mister_state routing + drift allowlist (+merge `b25c09e`)
- `e1b11b0` firmware/sprint-4: leak_detected debounce against pipe bleed-down
- `9e3bca3` ingestor/sprint-24.6: planner observability F14 `plan_delivery_log` + escalation (+merge `2985e78`)
- `1594cb9` ingestor sprint-25 schema PR spec (+merge `95b730d`)
- `c2bb9ba` firmware/sprint-5: replay-corpus auto-refresh + fw_version bump per deploy
- `97c4ea1` firmware/sprint-6: midnight-transition investigation (no firmware issue)
- `212b1c5` firmware/sprint-8: fix R2-3 self-mutation + midnight-wrap fog window + SAFETY_HEAT symmetry
- `8c64030` firmware/sprint-7: per-zone cycle counters for misters and drips
- `0d7445b` genai/sprint-1 squash-merged via PR #14 (9-item sprint)
- **2026-04-19 PM onwards (dual-Iris rollout)**:
- `00231cf` docs: `iris-planner-contract.md` v1.3
- `c88490a` docs: `iris-planner-contract.md` v1.4 (reconcile with `plan_delivery_log`)
- `b624f5c` coordinator: migration 093 + ai.yaml `planner_routing` / `planner_sla`
- `dda9057` firmware/sprint-9: heat2 latch + validate_setpoints asserts + R2-3 comment + SAFETY_HEAT fan
- `51c4781` ingestor/sprint-24.7: alert-hardening — OBS-3 + flap fix + MIDNIGHT watcher + sprint-25 prep (+merge `91cc335`)
- **2026-04-20 overnight**:
- `98ff9a1` ingestor/sprint-24.8: midnight_posture milestone perpetually 24h in the future (+merge `5c95ad4`)
- `8d2656d` firmware/sprint-10 phase-0: both-fan relief + dt_ms clamp + tunable margins + sealed dehum override + day/night setpoints
- **2026-04-20 morning**:
- `d822485` coordinator: `verdify_schemas/` v1.4 audit fields + migration 093 applied to prod TimescaleDB
- `6c2f5b3` genai/sprint-2 (PR #15): planner_stale threshold 8h→14h + cadence note (+merge `9a27fe7`)
- `ec9e1df` coordinator: unbreak schema CI — asyncpg importorskip on physics-invariants test
- `9a9a05e` web/sprint-4 (PR #16): Grafana panels for planner instance metrics

## How to use this

- **Agents:** start of a session, read your own `docs/backlog/{agent}.md`. Pick the highest-priority item not blocked by a handshake.
- **Coordinator:** this file gets updated at sprint kickoff + sprint end. Treat it as the shipping status board. iris-dev refreshes during /loop idle cycles.
- **Cross-cutting work** (schemas, migrations, infra, deps) lives in `docs/backlog/cross-cutting.md` and is scheduled by coordinator.

## Current findings to schedule

- **Planner:** Hermes hardening is now the top planning backlog. Genai owns the GPT-5.5 context pack, tuning rubric, site/lesson digest, MCP `plan_run` audit parity, and registry validation at the planner boundary. Ingestor owns the remaining trigger matrix, no-missed-sunrise behavior, deviation coverage, and planner health status surface; per-trigger SLA lifecycle, exact trigger correlation, fixed-boundary triggers, and active/future plan range guard are deployed. Coordinator owns shared contracts, trigger-ledger schema, and model observability.
- **Web:** Site simplification pass reduced the public entry path and fixed corrupted text. Remaining editorial cleanup: fold detailed Climate subpages into `/climate`, finish hiding or archiving redundant reference routes, and remove drafting scaffolds from hidden reference pages.
- **Web/Data:** The 2026-05-20 lab-site feedback is tracked in `docs/backlog/lab-site-refactor-2026-05-20.md`: initial site/content refactor tasks (`CODEX-001` through `CODEX-012`), later visual/resource/architecture follow-ups (`CODEX-013` through `CODEX-018`), and Grafana/time-series research tasks (`RP-001` through `RP-007`).
- **Web:** Image cleanup removed broken/public backup assets and documented current photo fit. The manual image catalog now has a machine-readable manifest checked by `site-doctor`; crop-specific photos for basil/cucumbers/tomatoes remain a content acquisition issue, not a rendering blocker.
- **Web:** Raw ASCII/Mermaid diagrams were removed from hand-authored public pages. Forecast, daily-plan, plans-index, crop, and zone generated outputs now use web components instead of generated Markdown tables.

## Sprint numbering

Per-agent counters. Past global sprints (17–22) map into individual agents' histories; see each agent's scope doc for the relevant prior work.

## Known open PRs

Current recovery source of truth is
[`docs/backlog/project-recovery-2026-05-22.md`](backlog/project-recovery-2026-05-22.md).
As of 2026-05-22, PR #80
`[codex] Refactor lighting occupancy task demand` was merged as-is at
`66f0836`; the post-merge main CI run passed. PR #81
`[codex] Integrate lab site and Grafana recovery` was merged at `f08e094` and
post-merge main CI run `26313275460` passed. Firmware bake/last-good promotion
remains tracked by the firmware backlog; canonical `lab.verdify.ai` DNS now
resolves to the gateway and GitHub issue #82 is closed.

Historical snapshot from 2026-04-20:

- **#6** DRAFT — `copilot/fix-8a7fddcf-*` (voice-note ingestion; dormant Aug 2025, no recent activity)
- All contract-v1.4-era PRs (#15, #16) merged this morning. No agent PRs outstanding.

## Contract v1.5 rollout — current state

**Phase 1 (contract + schema) ✅ complete** (iris-dev + coordinator):
- Contract `docs/iris-planner-contract.md` v1.5 landed
- Pydantic planner response audit fields in `verdify_schemas/`
- Routing + SLA config in `config/ai.yaml` now local-first
- Hermes config: `hermes-iris` targets OpenAI GPT-5.5 high-reasoning through the MCP-only tool allowlist

**Phase 2 (MCP + dispatch) ✅ complete for first hardening slice** (genai/coordinator):
- `plan_run` creates MANUAL trigger ledger rows and sends through local `send_to_iris`
- `set_plan` / `set_tunable` reject planner-owned writes without exact `trigger_id`
- Successful audited `set_plan` / `set_tunable` calls mark the matching `plan_delivery_log` row `plan_written` immediately

**Phase 3 (ingestor consumption) 🟡 in progress** (ingestor):
- Local-first routing and trigger-scoped sessions live
- Fixed-boundary triggers added
- Per-trigger timeout lifecycle alerts through `planner_trigger_sla_timeout`
- Active/future plan range drift alerts through `planner_tunable_range_drift`
- Remaining: expected-trigger ledger for missed startup windows, canonical trigger matrix, deviation trigger completeness, and planner health status surface

**Phase 4 (session boot + smoke test) ✅ complete** (iris-dev/coordinator):
- Local smoke covered MANUAL, FORECAST, DEVIATION, TRANSITION, SUNRISE, and SUNSET validation rows
- Local full-plan catch-up wrote `iris-20260504-2108`
- Active/future registry audit found zero violations

**Phase 5 (cutover) 🟡 in progress** (ingestor):
- Routine SUNRISE/SUNSET/MIDNIGHT/TRANSITION/FORECAST/DEVIATION/HEARTBEAT/MANUAL now route local by default
- Cloud/opus is explicit escalation only

Remaining high-leverage work: expected-trigger ledger, canonical trigger matrix, deviation trigger completeness, model/status surface, and distilled site/lesson context digest.

---

## Firmware stabilization + Phase 3 architecture (2026-04-21 → 2026-04-23)

### Context

The "fix-it-forward" spiral (sprint-15 regressions → 86°F whipsaw incident on 2026-04-21 12:40 MDT, then a 97°F / 5.03 kPa peak at 15:30 MDT with firmware held in `relief_cycle_breaker`) triggered a 4-phase stabilization plan at `.claude-agents/iris-dev/plans/yo-iris-dev-you-help-humming-stonebraker.md`. Ten coordinator PRs shipped against that plan over 72 hours.

### Ships (2026-04-21 → 2026-04-23)

- `1623e9c` **Phase 0** (#27) — replay-diff harness + 15 bulletproof invariants + CI gates (`firmware-replay-diff`, `firmware-invariants`, `no-new-fire-and-forget`, `service-restart-drift-guard`) + freeze rules (#1–#9) added to `CLAUDE.md`. Baseline green on 30-day corpus.
- `a8b96cc` **Phase 1a** (#28) — `verdify_schemas/tunable_registry.py` with `TunableDef` model + 13-entry seed + `test_registry_clamps_match_controls_yaml` drift guard. Replaces 4-place-drift tunable sync with single source of truth.
- `ba9482f` **Phase 1b** (#29) — `set_tunable` writes to `setpoint_plan` (not `setpoint_changes`), closing the 5-min overwrite gap that caused the initial sprint-15 regression. `set_plan` tightened to preserve `iris-oneshot-*` entries.
- `a140721` **Phase 2 firmware** (#30) — mode-dwell gate + symmetric cooling hysteresis behind `sw_dwell_gate_enabled=false` flag. Replay projects ≥70% transition reduction on stress windows. Gate flipped live 2026-04-21 19:14 MDT, reverted 19:50 after detecting design flaw (gate held THERMAL_RELIEF — a 90s-by-design mode — for 5 min, accelerating the breaker it was supposed to prevent).
- `24b9a34` **Phase 1c** (#31) — 10 new `cfg_*` readback sensors (mister pulse, vpd watch, mist vent coordination, fog escalation). Closes silent-push-corruption risk; `alert_monitor` now verifies landings for all Tier-1 tunables.
- `3209439` **Phase 1b.2** (#32) — registry expanded to 80 entries (every live `SETPOINT_MAP` entry minus 14 slated for deletion). 30 tier-1 planner knobs / 50 tier-2 escape hatch. Clamps validated against `controls.yaml` by drift guard; no mismatches.
- `4a8560e` **Phase 1d** (#33) — slim `_PLANNER_CORE` prompt from 86-tunable dictionary to Tier 1 only (~30 knobs). Reverses sprint-4's "planner must know every param" policy. `test_core_contains_tier1_tunable_dictionary` drift guard scoped to Tier 1.
- `b89ab21` **Phase 2 replay preview tool** (#34) — `make firmware-dwell-preview` runs replay with flag on vs off; quantifies worst-hour whipsaw reduction. First run: 47% worst-hour reduction with post-fix code.
- `a97236e` **Phase 2 firmware fix** (#35) — dwell gate exempts `THERMAL_RELIEF` from dwell via `transient_relief` preempt. Bench tests: 108→109. Not yet deployed — awaiting operator freeze-rule override + 48h bake window.
- `150c133` **PR-A** (#36) — lower `VENTILATE` mode's FW-9b fog trigger from `vpd_max_safe` (3.0 kPa) to `vpd_high_eff + fog_escalation_kpa` (~1.45 kPa production band). Closes measured 653-min/7d concurrent vent+fog gap. Intentional 10.41% replay divergence authorized via new `REPLAY_DIFF_THRESHOLD_PCT:` PR-body override mechanism added in the same PR (CI workflow change).

### Current state (2026-04-23 13:50 MDT)

- PR-A (#36) + Phase 2 fix (#35) merged to main, **undeployed**. Awaiting:
  - Operator freeze-rule override authorization per CLAUDE.md rule 2 (1 OTA/week) + rule 3 (48h bake; last OTA 2026-04-21 16:31, window opens 2026-04-23 ~16:31 MDT).
- PR-B was scoped to add breaker-VPD-emergency-override; **dropped** after verifying yesterday's breaker-latch window (17:53–18:22 MDT, VPD climbed 1.88→2.44 kPa with fog OFF under old code) would have had fog firing continuously under PR-A's lowered threshold.

### 24-hour operational assessment (2026-04-22 → 2026-04-23)

- **Band compliance 45.9%** over last 24h (1,435 samples). Temp-high 14.5%, VPD-high 19.8%.
- **Peaks:** 96.4°F / 5.09 kPa VPD at 15:30 MDT on 4/22 (vs 97°F / 5.03 on 4/21 — same pattern).
- **15 `firmware_relief_ceiling` + 15 `firmware_vent_latched` critical events** (relief cycle breaker class; Phase 2 dwell gate + PR-A target this).
- **Equipment utilization during 14:00-16:00 peak:** fans 67%, fog 20%, misters ~15% combined. User-identified gap — both cooling and humidification axes underutilized exactly when stress is highest.

---

## Phase 3 architecture decision (2026-04-23)

**Supersedes** the original plan.md Phase 3 ("9-mode → 6-mode consolidation"). User proposed **per-zone voting machines + central coordinator**. Five research agents dispatched to validate; all returned. Architecture validated, plan below.

### The problem (data-backed)

Current firmware is **mode-monolithic**: `determine_mode()` picks one of 8 modes; `resolve_equipment()` maps mode → fixed relay prescription. Modes are mutually exclusive. This forces the firmware to bifurcate on stress days:

- Temp axis wants cooling → `VENTILATE` (fans+vent, fog/mister OFF)
- VPD axis wants humidification → `SEALED_MIST` (vent closed, fog/mister ON, fans often off during mist pulses)

The AI planner has **thermostat-knob authority** only — pushes 25 distinct tunables/week (3,004 pushes), but every one is a threshold/dwell/bias. Zero actuator-level authority. Equipment selection is 100% firmware-mode-determined.

### Five research agents — findings summary

| Agent | Key finding |
|---|---|
| Zone heterogeneity | 80% of hours have temp spread >2°F across 4 zones; 51% have VPD spread >0.3 kPa. Consistent directional ranking: south hottest/wettest, north coldest/driest. Opposing-vote rate 1.1% temp / 1.8% VPD — sum-of-votes works, explicit tie-break still needed. Divergence *not* amplified by stress (active fans homogenize air). |
| Stress taxonomy | 43 stress events in 30d. **51% (22/43) are FIRMWARE_WHIPSAW** — mode churn >20/hr even at moderate thermals. **HOT_ONLY does not exist** (0/43) — every hot event is also dry. HOT_DRY events median 3.4h, all 4 recent relief_ceiling-triggering events are HOT_DRY. |
| Probe reliability | South probe: 14.35% NULL (~4-day outage dominates). North/east/west: ≤0.6%. Drift-before-death pattern: south reports lies before going dead — confidence model needs outlier-veto, not just age decay. Recommended: `confidence=100` if age ≤90s, linear decay to 0 over 90-300s, abstain after 300s. Plus ×0.5 weight when probe deviates >5°F from peer median. |
| Threshold calibration | From 7-day histogram of simulated vote aggregates (`temp_sum`, `vpd_sum` ∈ [-12, +12]): `fan1≥1` fires top 8%, `fan2≥5` top 5%, `fog≥7` (on `vpd_sum`) top 7%, `vent≥2 AND outdoor<indoor-3`, `heat1≤-2` bottom 21%, `heat2≤-6` bottom 0.7%. Replayed against yesterday's 15:30 peak — proposed thresholds would have fired fog (vpd_sum=+12 > 7) while actual firmware had fog OFF. |
| Failure modes | 12 failure modes enumerated. **8 require coordinator-level guardrails** (quorum, CRITICAL veto, per-actuator dwell, fog/vent interlock, occupancy veto, warmup window, tunable TTL, WDT-bounded tick). Reboot rate ~800/day in April makes warmup non-optional. 839 `setpoint_unconfirmed` events in 180d makes tunable TTL the highest-leverage new guardrail. |

### Validated architecture — three layers

**Layer 1 — Zone FSMs (×4, one per zone)**

Each zone emits a ballot per tick:
```cpp
struct ZoneVote {
    int8_t   temp_want;     // -3..+3 (negative = want warm, positive = want cool)
    int8_t   vpd_want;      // -3..+3 (negative = want dehum, positive = humidify)
    uint8_t  severity;      // 0=LOW, 1=NORMAL, 2=HIGH, 3=CRITICAL
    uint8_t  confidence;    // 0-100
};
```

Vote derived from that zone's temp/VPD vs that zone's band. Severity escalates with duration-out-of-band. Confidence drops with probe staleness. Outlier self-veto if deviation >5°F from peer median.

**Layer 2 — Coordinator (six-step loop each tick)**

1. **COLLECT** 4 ballots + outdoor sensors + water budget + occupancy
2. **QUORUM** check — <2 live zones → safe-shape all-off
3. **VETO** — any `CRITICAL` severity → safety-shape (SAFETY_COOL/HEAT pattern)
4. **AGGREGATE** `temp_sum = Σ sev_weight × conf × temp_want`, `vpd_sum` similarly
5. **DECIDE** each relay from rules over aggregates + per-zone outputs for misters
6. **GUARDRAILS** — per-actuator dwell, interlocks, warmup, tunable TTL, WDT

Relay rules become one-liners:
```
r.fan1 = temp_sum >= 1 && dwell_elapsed(fan1);
r.fog  = vpd_sum  >= 7 && fog_permitted && (vent_closed || vpd > vpd_critical);
r.mister_south = votes[S].vpd_want >= 1 && water_budget_ok && !occupancy_inhibit;
...
```

Each threshold is a tunable the AI can push.

**Layer 3 — Mode (derived)**

The existing 8-mode enum stays for Grafana/alerts/replay telemetry. But `derive_mode(relay_outputs, votes, state)` runs *after* relay decisions, not before. Mode is a label, not a driver.

### Coordinator guardrails (mandatory implementation constraints)

From failure-mode analysis — cannot be deferred:

1. **Quorum rule** (≥2 live zones or abstain all-off)
2. **CRITICAL veto path** (bypasses aggregation)
3. **Per-actuator dwell** (separate from per-zone hysteresis — replaces Phase 2's mode-level dwell)
4. **Fog/vent interlock** enforced post-aggregation (sprint-15.1 learning)
5. **Occupancy hard-veto** (after aggregation, no zone override)
6. **Warmup window** (≥10 ticks before any actuator ON post-reboot) — reboot rate ~800/day makes this non-optional
7. **Tunable TTL** — stale setpoints snap to defaults, emit `planner_stale` alert (highest-leverage new guardrail per 839 `setpoint_unconfirmed` events in 180d)
8. **WDT-bounded tick** (<100ms) + fail-closed all-off on exception

### Migration plan — 6 PRs

**Short-term (this week, tactical relief):**

- [x] **PR-A** (#36 merged `150c133`) — VENTILATE fog trigger at band+escalation, not safety-threshold. Closes ~38% of concurrent-gap measured in corpus.
- [x] **PR-B** dropped — PR-A handles the breaker-latch high-VPD window. Verified against 2026-04-22 17:53 event.

**Strategic (weeks 2-16, Phase 3 proper):**

- [ ] **PR-1 — Zone FSM + vote struct (pure refactor, zero behavior change).** Define `ZoneVote`, `ZoneBand`, `compute_*_want()`, `probe_confidence()`. Refactor existing per-zone logic (mist_pulse_controller) into explicit `compute_vpd_vote(zone)`. Emit votes to diagnostic sensor for shadow observation. Replay-diff zero. Tunables: 32 zone-band thresholds added (8 per zone × 4 zones) + probe confidence timings.

- [ ] **PR-2 — Coordinator skeleton + fog/mister under coordinator.** New `coordinator()` function with quorum/veto/aggregate/guardrails structure. Feature flag `use_coordinator` (default off). **Partial rollout:** fog + misters only move under coordinator control; fans/vent/heat stay mode-driven. Shadow mode 14 days. Why partial: fog/mister is where the user-visible duty-cycle gap lives and smallest blast radius.

- [ ] **PR-3 — Fan + vent + heat under coordinator + all 8 guardrails.** Full relay resolution moves to coordinator. Per-actuator dwell, quorum, warmup, occupancy, budget, interlocks, tunable TTL. Shadow mode **21 days** (longer than PR-2 because broader surface). Invariant suite expands to 30 (original 15 + 15 coordinator-specific).

- [ ] **PR-4 — `determine_mode()` retired.** Mode becomes derived label via `derive_mode(relay_outputs, state)`. Feature flag flipped `use_coordinator=true`. Delete `determine_mode()` 2 weeks after flip.

### New invariants required (added to `firmware/test/invariants.h`)

15 coordinator-specific invariants on top of the existing 15:

1. `confidence=0` → vote excluded from aggregation
2. Quorum < 2 → all moisture+heat off
3. Any CRITICAL → safety-shape fires
4. `temp_sum` arithmetic matches weighted formula
5. Fog ON ⟹ (vent closed OR vpd > vpd_critical)
6. Mister ON ⟹ water_budget_ok AND !occupancy_inhibit
7. Occupancy inhibit ⟹ 0 moisture relays
8. Heat ON ⟹ temp_sum ≤ threshold for ≥ dwell_min_ms continuously
9. Post-reboot: no actuator ON first 10 ticks
10. Stale tunable (age > TTL) → snaps to default + alert emitted
11. Coordinator tick wall-time < 100ms
12. Exception in coordinator → all-off (not last-latched)
13. Mister minutes ≥ sum of per-zone vpd_want≥1 minutes × confidence_factor
14. No actuator transitions faster than dwell_min_ms
15. Conflict resolution logged with `tiebreak_reason`

### Open decisions

- [ ] **Replace plan.md Phase 3 section.** Current doc has "6-mode consolidation" narrative; research says voting-coordinator is correct.
- [ ] **Zone bands — per-zone vs global + offset.** Per-zone = 32 new tunables; global + offset = 4 new + ~8 offsets. Research leans per-zone; cost is Iris prompt surface.
- [ ] **South probe hardware mitigation.** 14.35% NULL is independent of Phase 3 — needs a new probe / Modbus audit. Separate track alongside firmware work.
- [ ] **Iris tunable tiering.** Phase 3 adds ~32 tunables. Need to decide which are Tier 1 (daily tuning) vs Tier 2 (escape-hatch) so the prompt we just slimmed in Phase 1d doesn't rebloat.
- [ ] **Shadow-mode length.** PR-3 nominally 21 days; can we start shadow telemetry earlier (PR-1) to accumulate observation hours before PR-2 ships?

### Risks

- Coordinator surface is larger than mode-based — 25-30 new tunables, 15 new invariants, 21-day shadow bake. More room for silent-push corruption (mitigated by guardrail #7 tunable TTL).
- Per-zone bands require per-zone probe reliability; south probe is unreliable. Either fix the probe or weight south's votes lower by default until fixed.
- Mode-derived telemetry may break downstream consumers (alert_monitor rules, scorecard, Iris's mental model). Needs audit during PR-4.
- Reboot rate is ~800/day — warmup guardrail MUST be robust, otherwise every reboot creates a 10-tick all-off window that could briefly lose a stress event. Might need to persist `last_healthy_vote` across boot.

### Timeline (calendar weeks)

```
W1 (now):   PR-A shipped ✅ | PR #35 + PR-A deploy when bake opens
W2-3:       PR-1 refactor + shadow vote telemetry
W4:         PR-1 bake
W5-6:       PR-2 coordinator skeleton (fog+misters)
W7-8:       PR-2 14-day shadow
W9-10:      PR-3 full relay coordinator
W11-13:     PR-3 21-day shadow
W14:        Flip use_coordinator=true
W16:        PR-4 delete determine_mode
```

Total: ~16 weeks (vs. original plan's 8 weeks for 6-mode consolidation). Longer because architecture is more ambitious; validates more thoroughly.

### Known open firmware PRs (as of 2026-04-23 13:50 MDT)

- PR #35 (Phase 2 fix) + PR #36 (PR-A) both merged but awaiting combined OTA deploy.
- No outstanding firmware PRs.
