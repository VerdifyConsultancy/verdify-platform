# Adversarial audit — software recovery 2026-07-09

## Verdict

Source and public availability are green; control delivery and evidence truth are not. The recovery is implementable, but sequencing errors could make it materially worse.

## Critical findings

1. **Writer storm:** ordinary cfg drift uses the reconnect force event; all 56 anchor wire IDs mismatch actual ESPHome IDs; dispatcher advances state before physical completion; a paced batch monopolizes the task loop.
2. **Planner failure:** MCP tool connection dies while TCP/pods stay green; terminal action is not persisted; full plans use inconsistent bounds/lifecycle; forecast VPD compares outdoor forecast with indoor observation.
3. **Stale active intent:** approved/device zero is overridden in `v_active_plan` by old 0.25 intent. Removing the row before repaired consumers are live risks repinning or misclassification.
4. **Irrigation hazard:** moving today's wall schedule into the valid feed window before routing repair would fertilize south and west misters.
5. **Relay ownership:** climate and irrigation paths can write the same mister relays; intentional irrigation cannot be added safely without one resolver.
6. **Fertigation uncertainty:** no defensible shared full-strength lime/cannabis recipe exists from current evidence. Guessed minutes, fixed chemistry, or the 90-minute hold are unsafe; commissioning must fail closed.
7. **DLI fabrication:** a broken interior sensor, incorrect elapsed time, exterior proxy, and downstream re-multiplication create a confident but invalid crop metric.
8. **Dry-out evidence gap:** firmware is already diurnal/solar, so a fixed-clock rewrite would regress design. The real gap is live DB solar parity and realized episode scoring.
9. **Heap/device risk:** the current binary has historical minimum heap of only a few KB and a Task WDT; one combined OTA must prove map/heap and retain last-good rollback.
10. **Credential exposure:** the live prod DB password existed in tracked source/history. Source is remediated; rotation remains protected and blocks release.

## Counterevidence and rejected shortcuts

- PRs #431/#432 and green CI do not prove the writer is fixed; live logs contradict acceptance.
- Center drip inactivity is expected and must not be “fixed” into a watering program.
- The early two-night dry-out PASS is not sustained by the expanded five-night review.
- `planner_graph` is not the operational planner and cannot replace Hermes/MCP without separate contract/evidence.
- Deleting dormant plumbing would contradict future use; keep it represented but disabled.
- A proposal-only planner soak contradicts Jason's explicit immediate-activation decision.

## Safe order

Canonical readback/writer semantics and planner schema/tool health first; serialized DB contracts next; one firmware-control package next; schema/services deploy and stale-plan retirement next; one OTA only after critical alert clearance and all specialized gates.
