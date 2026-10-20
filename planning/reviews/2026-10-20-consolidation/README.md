# Campaign consolidation — October 20, 2026

Jason approved this estate consolidation on 2026-10-20. It removes six roll-up or decision layers that had no work of their own. Each one had at most one open child or a single go/no-go decision. Before any issue closes, every outstanding acceptance clause moves to a canonical open issue, in both `backlog.yaml` and a carry-over comment on that issue. Nothing is marked accepted. No deployment, OTA, device command, draw or experiment launch is involved.

| Closed (not planned) | Canonical | What moved |
|---|---|---|
| #14 twin roll-up | #31, now a direct child of #775 | Three clauses: coverage limitations, build/runtime/output receipts with the consolidated #606 registry obligations, and pilot independence with one selected oracle question |
| #786 comparator decision | #785 | Comparator go/no-go, fixed rule and held-out corpus, no revealed-schedule tuning, separate identity/lock/draw |
| #787 resource-study decision | #785 | Resource/overnight go/no-go, calibrated per-resource coverage, no unearned claims or automatic purchases. #410 and #981 become decision inputs, not blockers |
| #379 grey-box/MPC option | #785 | Held-out gates against simpler baselines, no new arm in the current experiment, separately reviewed controller plan |
| #428 heap/watchdog diagnosis | #390 | Cause or bounded uncertainty, separate reset counts, predeclared worst-load criteria, last-good qualified by actual recovery. S3B P4 evidence stays linked |
| #750 C1 recovery roll-up | #581, and #641 for the safe-state clause | #749 and #641 move under #581. #641 now carries the final safe-state and recovery-receipt clause, and #581 carries the mismatch-receipt clause. #747 stays closed history |

## Graph changes

- **Hierarchy:** #31's parent changes from #14 to #775. #749 and #641 move from #750 to #581.
- **Blocking edges removed:** #786→#379, #785→#786, #785→#787, #410→#787 and #981→#787. Each one now ends at a closed decision record. The relations are kept as decision inputs in #785's acceptance.
- **Counts:** the canonical graph has 94 records and 68 blocking edges, down from 73. Execution-plan coverage drops to 41 open issues.
- **Kept:** closed-to-closed predecessor records, such as #371/#419→#379, #780→#786, #781→#787 and #433→#428, stay as history.

## Body drift

Re-rendering changes the generated bodies of #14, #31, #359, #379, #390, #428, #430, #581, #641, #749, #750, #775, #785, #786 and #787. Publish them with `python -m planning.render --body-dir`, keeping each issue's appended `planning-status` block. Until they are published, the carry-over comments on #31, #390, #581, #641 and #785 are the readable record of the moved clauses.

Validation: `python planning/schema.py planning/backlog.yaml`, `python -m planning.render --check` and `pytest planning/tests/test_backlog.py` all pass.
