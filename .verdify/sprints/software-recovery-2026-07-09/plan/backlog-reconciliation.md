# Backlog reconciliation

GitHub is authoritative. The query `is:issue is:open label:sprint:software-recovery-2026-07-09` returns the 17 executable items below.

| Issue | Disposition | Recovery outcome |
| --- | --- | --- |
| #293 | Include | Apply and prove DB solar parity. |
| #299 | Include preservation slice; raised P2→P1 | Prove the effective 45-second re-fire fence survives center-only routing; no new governor. |
| #377 | Include as ordered release step | Retire stale 0.25 intent only after consumers are fixed. |
| #383 | Include evidence/safety slice; obsolete separate-OTA sequencing superseded | Preserve solar-night safety and defer tuning until realized episodes prove a repeatable failure. |
| #386 | Include validation slice; raised P2→P1; stale Jason gate removed | Prove existing boundary min-on behavior and observe raw starts; add no new tunable. |
| #389 | Include | Transition-derived completed-day cycle/runtime truth. |
| #390 | Include | Issue-specific pre/post OTA cycling gate and last-good block. |
| #410 | Include | Solar-night realized dry-out evidence and only proven control adjustments. |
| #419 | Include; labels/milestone repaired | Real outdoor freshness in stock replay plus coverage invariant. |
| #424 | Include | Reserved migration 189 fixes served VPD divergence. |
| #427 | Include; body rewritten | Tool-level planner health, terminal actions, strict bounds, TTL, forecast semantics. |
| #428 | Include | Heap diet, actionable WDT evidence, conservative floor, and soak. |
| #433 | Include; created | Truthful, fair, non-starving device writer. |
| #434 | Include; created | Center-only climate and commissioned wall-only fertigation. |
| #435 | Include; created | Interior crop DLI unavailable across every consumer. |
| #437 | Include; created | Canonical equipment and provenance-bearing resource attribution. |
| #438 | Include; created; protected gate | Remove credential fallbacks and rotate/verify the exposed production credential. |

## Superseded or parent-only records

- #37, #323, and #397 are closed because their old schedule/fairness premises conflict with the approved topology; useful fail-closed details moved to #434.
- PR #409 is closed as mixed/superseded. Its vision work is preserved under deferred issue #436.
- #430 and #350 remain umbrellas; #433 and #434 are their executable recovery children.
- #365 remains dependent on #435 and is not separately dispatched; its DLI objective is handled through #427/#435/#371.
- #367 and #371 remain follow-up records. Trustworthy counts/evidence land now, but broad anti-chatter or outcome-UI behavior is not justified or required for the recovery.
- Historical sprint `s8-vanda-night-dehum` is cancelled as a delivery authority. Its delivered source/evidence remains history.

## Deferred

Physical sensor replacement, fertigation commissioning measurements, new hardware/meters, vision issue #436, broad controller deletion/mode collapse, and planner_graph productization are not part of this software recovery.

## GitHub mutations recorded

- Created issues #433–#438 (with #436 explicitly deferred).
- Rewrote #427 and added corrective comments to all relevant parent/dependency issues.
- Created and applied label `sprint:software-recovery-2026-07-09` to the 17 included issues; removed it from #367/#371 after the raw-edge scope audit.
- Removed stale `gate:jason` from #386 because OTA approval is already explicit; kept `gate:jason` on #438 because credential rotation remains separately protected.
- Raised #299/#386 and repaired #367/#419 priority metadata to match the approved recovery risk.
