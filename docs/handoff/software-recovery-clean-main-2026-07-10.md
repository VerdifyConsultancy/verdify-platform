# Software recovery clean-main handoff — 2026-07-10

## Repository outcome

Jason directed the repository cleanup, requested all in-flight work be pushed
to `main`, and required the working context to be persisted in git. The source
checkpoint is complete:

- `#442` merged the truthful/non-starving device-writer repair at
  `f15bf3ac35742278af429d5c3599639f56513f86`.
- `#444` merged `DEC-014` and `DEC-015` contract corrections at
  `d27978a41873fc55a0b0f2abdfa2ec8e4254013f`.
- `#443` merged the hardened evidence foundation at
  `cfc58539c94416b7e8f5275fee73c795f6d8caf1`.
- Controller reconciliation landed directly on `main` at
  `034708e2fbeb3fea11d267da00480d4283bd64d2`.
- GitHub has zero open pull requests for this repository.

Immutable heads, validator findings, closure evidence, and applicable checks
were recorded before merging. No failed or pending applicable check was
overridden.

## Evidence checkpoint

The initially green evidence head `e559020` was not merged after two validators
reproduced false-positive paths. Replacement head `ab56cf4` closes conflict
carry, cross-midnight duration, missing relay truth, temperature-floor, elapsed
coverage, outside-wetter, wet-relay, and duplicate-provenance defects.

Final proof includes:

- five disposable TimescaleDB SQL fixtures;
- fresh-schema load and exact migration-definition parity;
- Ruff, shell, YAML, contract-hash, and focused source checks;
- 267 native firmware tests;
- all invariants over 296,698 live-derived rows;
- 295,833 observation-backed and 199,598 conservatively fresh replay rows;
- zero duplicate replay timestamps;
- zero firmware behavior or diagnostic divergence against main;
- corpus SHA-256
  `9db25b8c9118bd485e95a8ce203229ac318d48eaffc23a2bde438e65d8f2cb2b`.

## Durable issue and decision records

Issues `#293`, `#389`, `#410`, `#419`, and `#424` carry exact replacement-head
evidence and remaining release limitations. `#410` now prohibits only
physically realized solar-day held-temperature admission while preserving
ordinary daytime VPD dehumidification. `#419` remains open until the device
emits exact `outdoor_data_age_s` in existing telemetry and post-OTA replay proves
it; old rows remain honestly labeled `conservative_change_observation`.

The canonical record set is the durable ADRs under `docs/adr/`, credential
records under `docs/security/`, operational procedures under `docs/runbooks/`,
the release handoffs in this directory, and
`docs/reviews/greenhouse-performance-and-project-review-2026-07-09.md`.

An older local greenhouse-review worktree contained an identical copy of the
review document and earlier draft workflow artifacts. The review document was
byte-identical to main; the drafts were superseded by the canonical versions
already on main. Those redundant untracked copies
were removed, and the worktree was fast-forwarded to the canonical revision.
Historical clean topic branches were preserved but not replayed onto main:
several contain retired dev/staging or superseded firmware proposals and are not
uncommitted recovery work.

## What this does not claim

This clean source checkpoint is not a production rollout. The running ingestor
still predates the writer repair; migrations 186 and 189-192 are not yet applied;
MCP/ingestor restart proof, firmware changes, OTA, and settled runtime evidence
remain release work. The production database credential rotation remains a
technical prerequisite for production mutation.

## Next dependency-ready work

1. Implement resource-accounting from merged transition truth.
2. Implement DLI unavailable/provenance semantics.
3. Repair planner delivery and materialization.
4. Build the combined firmware response, including DEC-014 solar-night hold,
   DEC-015 exact-age telemetry, irrigation topology, and heap reliability.
5. Execute the controlled production release, OTA, and runtime validation after
   the credential-rotation prerequisite passes.
