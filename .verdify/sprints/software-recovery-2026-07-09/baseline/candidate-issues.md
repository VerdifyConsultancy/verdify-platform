# Executable recovery issues

GitHub query: `is:issue is:open label:sprint:software-recovery-2026-07-09`

| Issue | Outcome | Lane | Key dependency |
| --- | --- | --- | --- |
| #438 | Remove credential fallbacks; inventory callers; rotate only on explicit approval | security-hygiene | Protected rotation gate blocks release only |
| #433 | Canonical readbacks, true reconnect, fair/terminally truthful writer | device-writer | Stale row removed later |
| #293 | Apply/prove DB solar parity | evidence-core | Serialized migration 186 proof |
| #424 | Correct served-VPD divergence | evidence-core | Reserve migration 189 |
| #389 | Raw-transition complete-day cycles/runtime | evidence-core | Migration sequence and MCP hand-off |
| #410 | Realized solar-night dry-out episodes | evidence-core | #293 parity and #389 transition truth |
| #419 | Source-backed fresh-outdoor replay coverage | evidence-core | Corpus export/refresh |
| #437 | Canonical equipment plus fresh water and scoped energy evidence | resource-accounting | evidence-core and active-slug migration |
| #435 | Interior DLI unavailable across all consumers | dli-availability | resource migration/schema head |
| #427 | Tool-healthy, bounded, terminally observable active planner | planner-delivery | #433 and #435 |
| #434 | Center-only climate and commissioned wall feed | firmware-control | #433 readback contract; physical feed remains uncommissioned |
| #428 | Heap/WDT floor, diagnostics, and soak | firmware-control | #433 live quieting and exact combined image |
| #299 | Preserve/test live mister re-fire fence | firmware-control | #434 topology and #389/#390 evidence |
| #383 | Preserve solar-night safety; tune only after realized failure | firmware-control | #410 realized episodes |
| #386 | Prove lighting min-on boundary; no speculative tunable | firmware-control | #389/#390 raw circuit counts |
| #390 | Topology-aware pre/post OTA cycling gate | release-control | #389 and exact firmware artifact |
| #377 | Retire stale 0.25 intent and prove no repin | release-control | #433/#427 live and verified |

## Explicitly deferred or parent-only

- #367 broad fan/fog anti-chatter consolidation: deferred until trustworthy raw-edge/response evidence proves a residual defect.
- #371 broad outcome composite/homepage redesign: deferred; required DLI/cycle/night/resource truth lands in the owning issues.
- #430/#350 remain umbrellas; #433/#434 are executable children.
- #365 remains a dependent objective; #435/#427 enforce DLI exclusion in this recovery.
- #436 vision source/image-pull work is real but outside greenhouse control recovery.
- #37/#323/#397 and PR #409 are closed as superseded.

## Evidence-driven scope corrections

- #299 adds no 120-second min-on or max-cycles entity. The live 45-second fence is effective and must survive center-only routing without extending pulses.
- #383 adds no new post-wet hold or closed-heat flag. #410 must first prove a repeatable realized failure.
- #386 adds a boundary regression and raw-count watch only. Recent raw counts do not support the old severe-cycle premise.
- #437 includes the stale water-event materializer, active lighting slug mismatch, and partial-versus-modeled KPI scope defect.
