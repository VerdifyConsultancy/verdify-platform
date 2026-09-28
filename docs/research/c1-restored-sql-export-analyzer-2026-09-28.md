# #783 restored SQL export → analyzer contract

The test fixture `tests/fixtures/experiment_v2_restored_255_sql_export.json`
contains exact `export_payload::text` bytes plus a repository text-file newline
(stripped before verification) from a **synthetic four-day study**
run in a rollback transaction on the socket-only, network-denied restore of
the 2026-09-28 backup pair `verdify-20260928T062636Z`. Its dump SHA-256 is
`ecdd6f0f05e9bc6b8c252e6fe3b0bb909ebb55945448669161c68f9739680e9c`;
the clone had 263 ledger rows through migration 255. The SQL freezer's domain
hash of the retained bytes is
`f81d0bacb96cdf11faf4697ce76841530a18199390c24087e23aa638cc15759d`.
The fixture has four assignment, outcome-freeze and day-evidence rows, two
selector choices, a retained null/fallback day and no mapping or secret field.
The same transaction called `fn_experiment_v2_freeze_export_at` twice; its
immutable retry check passed. The transaction rolled back before the clone
stopped. These rows are **not** pilot observations.

`switchback.v2_analysis.analyze_revealed_sql_export` now checks that exact SQL
hash and frozen payload directly, binds the expected experiment and analyzer
environment, verifies every SQL `itt_range` against its assigned Denver local
[06:00,24:00) window across UTC-offset changes, recomputes the ordered
evidence-bundle hash under the freezer's SQL domain, validates each
assigned-day outcome against the source contract,
and computes paired contrasts without producing a second export format. The
restored fixture yields an inconclusive null-endpoint decision while retaining
all four assignments. The standalone Python day-export fixture remains a
separate research contract.

Private render, Job, SQL and log receipts are under
`/Users/jason/Documents/Codex/verdify-783-vertical-20260928/`. Earlier
isolated attempts are retained there: replaying migration 214 over the 255
schema broke the later observation-window function, and the first extracted
fixture lacked the newer separate #642 approval order/audit reference. Four
real socket login denials passed for randomizer, executor, freezer and ordinary
API duties. Source tests cover the generated setter order, command receipts,
restart and rollback behavior, but this restored SQL fixture still seeds
delivery/observation evidence synthetically. It does not prove one integrated
selector → Python setter → raw receipt → freezer path or any physical #641
result. No production experiment, device or firmware state changed.
