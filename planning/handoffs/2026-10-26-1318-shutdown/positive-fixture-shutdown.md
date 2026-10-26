# Positive private-exposure fixture shutdown handoff — 2026-10-26

User requested shutdown preparation. Implementation is stopped at a coherent source checkpoint. No PostgreSQL test, native CI, image build, deployment, cluster job submission/cancellation, production DB operation, device connection, node cordon, credential/grant change or message delivery was performed by this subagent. Leave every existing cluster job running. Root is responsible for GitHub updates and remote preservation; this subagent has not committed or pushed.

## Exact source custody

- Worktree: `/private/tmp/verdify-positive-exposure-20261026`
- Branch: `codex/positive-exposure-fixture-20261026`
- Base HEAD: `ca9120fa308c77b2c71054758d0ca143428bdea6`
- Status:
```
M scripts/qualify-component-containment.py
 M tests/test_component_containment_postgres.py
```
- Uncommitted binary patch: `/private/tmp/verdify-resume-review-20261026/positive-fixture-uncommitted.patch`
- Patch SHA-256: `7603595ee5f37b943ce23731e0ef1966b3e1e406b5174f604ee001801af86d16`
- `tests/test_component_containment_postgres.py` SHA-256: `88285899af597f30bff3a4103f5c78ad80c07668b2dba83f6a46047f82ff1457`
- `scripts/qualify-component-containment.py` SHA-256: `9da18eff3c647e178012a276e557ae9cb705ba01bee31af120a11df47069f811`

Only the two authorized files changed. Parent will preserve the branch remotely; do not mistake this local patch for that durable remote custody.

## Implemented, not dynamically validated

Three parameterized positive cases `test_native_positive_exposure_alert[multiple|expired|stale]` target exact current SQL reasons `multiple_open_exposures`, `open_exposure_work_expired`, and `open_exposure_observation_stale`. Intended manifest is22 mandatory cases,13/16 native SQL reasons, with the existing19 cases retained. This is expected coverage, not a passing receipt.

Positive flow: actual existing lifecycle/executor LOGIN connections over the private Unix socket; native baseline state registration; explicitly unavailable deterministic shadow fixture; native commissioning and baseline-recovery admission; runtime registration; real bounded recovery work/claims; requested/queued/sent component journal; native bundle completion; two complete synthetic epochs separated by31s actual wait; refusal checks for47 fields, one epoch and unconfirmed component; native confirmation/open/retry; native recovered event; exact reason selection; real capture-only producer fire/dedupe; native exposure/admission closure; resolution/final silence.

All positive transitions commit. There is no SET ROLE, direct positive ledger insert, fabricated approval, trigger disable, clock alteration, sequence reset, SQL delete or fake rollback. Shadow *_at helpers remain private-owner deterministic fixture setup and use explicit unavailable/NULL outcomes, not physiological values. The actual-time observation and expiry paths do not use *_at clock overrides.

`committed_containment` wraps the existing `socket_pg`/`isolated_pg` generator only for the new tests. It observes the exact generated private cluster path/start identity and verifies host auth reject and listen_addresses empty. Its finalizer closes the generator, which executes existing pg_ctl fast/wait stop and exact directory removal, then records stop returncode, original-process disappearance, socket absence and directory absence. The receipt is appended with record_property before pytest's teardown report/finalization; installed pytest source confirms JUnit finalizes at teardown. Missing/failed disposal cannot qualify.

The default no-toolchain collection skip is preserved: when isolated_pg skips before creating any cluster, the finalizer records absent disposal but does not turn the skip into a teardown failure. Native qualifier still refuses skipped tests.

The qualifier has a distinct `committed_private_cluster_disposed` path. It requires exact inserted row/sequence deltas, catalog/ACL/role/membership and trigger identity, old-row preservation except declared native control updates, immutable rows preserved across closure, actual LOGIN identity, full receipt bindings/hashes, capture-only alert lifecycle and disposal. It also hashes the existing isolated_pg source as an input. The original nineteen-case qualifier expression is AST-identical to the original.

## Expected source-derived effects

With D devices (D2 multiple, D1 expired/stale), sequence deltas:
- experiment_events_event_id_seq:3+D
- experiment_v2_work_events_work_event_id_seq:1+2D
- experiment_v2_runtime_generations_generation_event_id_seq:D
- experiment_v2_component_outcomes_component_outcome_id_seq:4D
- alert_log_id_seq:1
- all other sequences:0

Expected inserts: one state artifact/shadow cycle/context/choice/NULL preview;1+D work;1+2D work events;D runtime generations/bundles/completions/exposures/closures;4D component outcomes;2D epochs/receipts;3+D experiment events;one alert. Native control phase/capability/lease/updated_at and producer alert resolution are the only accepted row updates. All other rowsets remain exact.

Every accepted epoch is read back for exact48 IDs,178-byte synthetic vector, exact revision/runtime tuple, post-delivery and <=90s insertion freshness, per-wire advancement and>=30s pair separation. Canonical payload SHA and domain-separated receipt SHA are recomputed independently. Exposure binds the exact pair and no assignment. This is synthetic private SQL acceptance only; no authentic grid/source, physical/runtime worker or historical-prefix credit.

## Static checks already passed

- Ruff check on both changed files: passed.
- Ruff format --check on both: passed.
- Python ast.parse on both: passed.
- git diff --check: passed.
- AST comparison: all preexisting test function bodies unchanged.
- AST comparison: original nineteen-case qualifier expression unchanged after extraction into case_qualified.
- Manifest-only evaluation:22 required cases,13 covered reasons,3 unexercised reasons.

No test collection or PostgreSQL/test execution was run on the Mac. Do not claim private22/22 or CI success.

## Review findings and remaining validation

Source review checked current function signatures/return columns/grants, native claim fence, shadow unavailable outcome shape, observation guards, native success retaining an exposure, baseline confirmation and alert precedence. It corrected a possible alert readback race by taking the comparison row after the dedupe refresh, because that refresh may advance observation_age_seconds. It preserved no-toolchain skips as described above.

Remaining: independent review of source/diff; native exact-head full PR CI including this22-case private phase; confirmation that actual row/sequence deltas and own-LOGIN permissions match the source-derived expectations; proof all three teardown properties reach the emitted JUnit/report; full regression results. Native tests have intentionally not run yet, so runtime/SQL failures remain possible. Keep current600s private-phase timeout initially; waits are approximately31s +60s +122s=213s, one private PG server at a time. Do not lengthen or bypass gates preemptively.

The remaining defensive labels stay explicitly unexercised: runtime_generation_missing and open_exposure_observation_missing are protected by existing exact refusal tests; open_exposure_state_mismatch is closed first by the supported recorder. Do not forge inconsistent immutable rows solely to claim16/16 native positives.

## Continuation

1. Resume from the parent-preserved remote branch/commit or exact patch above, verifying hashes before changing it. Check current origin/main and avoid unrelated state.
2. Review these two files. No further implementation or test submission is authorized by the shutdown request; wait for user resume.
3. After resume, submit ordinary existing native PR CI through root's supported path. Retain source-bound report and JUnit/terminal workflow metadata. No ad-hoc local PG, extra cluster credentials, new target or production/device operations.
4. On passing real evidence, update #587/#639 narrowly:22 private cases/13 reasons, with the synthetic and physical limits explicit. Do not close either issue or change writer-hold status solely from this packet.

Earlier design notes: `/private/tmp/verdify-resume-review-20261026/positive-committed-custody-plan.md`. Those describe intent; this handoff and actual diff describe implemented source.
