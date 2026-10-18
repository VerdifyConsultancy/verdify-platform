# Native evidence for #368 / #367

The exact source `bdef62c1d640636411995c23aedeee85f342303f` passed the
ROOT-admitted native run on 2026-10-18. The workflow finished at 07:43:43 UTC;
its only check Pod exited 0 without restarting. [The structured receipt](native-result.json)
binds the source, base, workflow/Pod UIDs, image, resources, commands and original
private output hashes. PR: [#985](https://github.com/VerdifyConsultancy/verdify-platform/pull/985).

| Actual check | Original source | Candidate source |
| --- | --- | --- |
| Diagnostic YAML, 70 cases | 49 failures, 21 passes | 70 passes |
| Relay dwell YAML, 84 cases | 72 failures, 12 passes | 84 passes |

All four suites had zero errors and skips. The [complete case index](causal-junit-index.json)
is derived from the four original, hash-bound JUnit files.
[Two complete original failure bodies](original-causal-failure-examples.json)
show the former `4 / OK` result for a nonfinite probe and the cached dwell limit
violating the original relay history. Full original failure logs, JUnit and
extracted C++ remain in private custody; their hashes are in the receipt.

Strict `make test-firmware firmware-invariants`, the causal helper and the
zero-threshold worktree replay all exited 0. The native checks passed 273 policy
vector, 251 policy engine, 30 recovery, 14 injection and 291 greenhouse cases.
Invariants found zero violations across 296,698 rows; no exit-2 waiver was used.
The [native coverage record](native-invariant-coverage.json) also states that
zero of 48 policy fields were fully imposed, with a compiled ceiling of 27.
This corpus run does not certify an experimental policy state.

The postcommit worktree replay compared the actual base `2999abe5` with `bdef62c1`
across 296,698 rows: zero decision or diagnostic divergence. Its six missing
setpoint-column/default warnings are retained in the receipt. The replay does
not execute the changed ESPHome diagnostic/relay wrappers; the actual-YAML cases
provide that causal evidence. This run is not relabelled as precommit evidence.

The original `-01` workflow remains an Error: the controller refused its
`Parallelism` override before starting any Pod. The `-02` submission removed
that redundant override and executed once. The first terminal collector also
stopped after counting Retry/TaskGroup nodes as Pod attempts; its corrected
Pod-only selection accepted the same unchanged native results, without a rerun.
Both events remain explicit in the receipt.

At this receipt, the separate secretless ESPHome compiler result is pending.
No OTA, production change, live repaired diagnostics, physical chatter/recovery,
full48 prefix qualification or whole-issue acceptance is claimed. #368 and #367
remain open for their remaining criteria. Compile-only artifacts are never
last-good firmware. Original source-preparation documents remain historical and
are not rewritten as executed receipts.

The later [secretless compiler receipt](secretless-compile.md) now records both
maintained targets compiled at the same exact source, with the expected negative
and complete compiler provenance. The earlier pending-at-receipt field remains
historical; physical and whole-issue acceptance remain open.
