# Evidence core lane

## Outcome

Create the serialized evidence foundation for device-parity solar/VPD semantics, transition-derived cycling, realized solar-night dry-out episodes, and provenance-bearing outdoor freshness in stock replay.

## Scope and boundaries

- GitHub issues: `#293`, `#389`, `#410`, `#419`, `#424`
- Baseline: `0a9a19a840be6bae1beba604497d880b3b74b1ef`
- Branch/worktree: `lane/recovery-evidence-293-389-410-419-424` at `/Users/jason/repos/verdify-worktrees/software-recovery-evidence-core`
- Owned: migrations 186, 189, 190-192, matching SQL tests, schema/MCP evidence surface, replay exporter/corpus/harness, and named tests.
- Forbidden: writer/dispatcher, firmware control logic, deploy manifests, Grafana, history rewriting, forced freshness, and autonomous production application.
- Coordinate before changing telemetry schema, `Makefile`, or planner-context gathering.

## Dependencies

There is no hard dependency. Resource accounting consumes transition truth; firmware control consumes replay and night evidence; release control consumes the cycle view. This lane merges first and freezes the migration sequence for downstream work.

## Acceptance

1. Migrations 186 and reserved 189 have rollback proof and solar/VPD fixtures.
2. Cycle evidence handles duplicates, midnight carry-over, open pulses, individual light circuits, partial days, and short-cycle buckets.
3. Solar-night episodes expose realized AH/temperature/duty/stop/effectiveness evidence and end in an explicit `effective`, `ineffective`, `blocked`, or `insufficient_evidence` disposition. Physically realized held-temperature admission is forbidden during solar day; ordinary daytime VPD dehumidification remains visible and allowed under DEC-014. Ineffective or insufficient instrumentation is not a completed control fix; any firmware delta requires a scope-change handoff.
4. Stock replay contains at least 1,000 provenance-bearing conservatively fresh rows and reaches outdoor-aware branches without a force-fresh override. Historical value-change inference is labeled `conservative_change_observation`, never a raw source timestamp; exact device age is preferred when available under DEC-015.

The authoritative validation, evidence, record updates, critic requirements, escalation conditions, and exact definition of done are in `lane.yaml`.
