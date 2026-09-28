# #783 restored interface qualification: bounded device-denied witness

The source-bound `--v2-interface-audit` option extends the existing scheduled
backup-pair restore **after** its blocking Timescale owner, C0 seal, role and
materialized-view checks. It starts a PostgreSQL 16/TimescaleDB 2.25.2 server
on a pod-local Unix socket, mounts the dump PVC read-only, applies a deny-all
NetworkPolicy and mounts no ServiceAccount token. The option adds two identical
read-only passes of `scripts/qualify-v2-restored-interface.sql`; a missing
function, misplaced grant, mapping column or differing result exits nonzero.
The default restore path is unchanged.

The SQL witnesses five exact installed interfaces: selector cycle and choice,
randomized resolver, component outcome receipt, and outcome freezer. It checks
the randomizer, executor, freezer and blinded analyst allow/deny matrix, the
blinded assigned-day view shape, and a canonical hash of function signatures,
owners and view column names. It does not call a selector, setter or freezer and
does not create an assignment. It emits no production row, credential or
randomization material.

The audited Job was rendered with:

```sh
python3 scripts/render-backup-pair-restore-job.py \
  --backup-stem verdify-20260927T081703Z \
  --run-id v2int0928 --v2-interface-audit
```

The Job `verdify-prod/verdify-backup-pair-restore-v2int0928` (UID
`41d8ef2e-9823-49d5-8134-5cb35ba4dc32`) completed at
2026-09-28T02:44:34Z. It restored the scheduled
`verdify-20260927T081703Z` dump, SHA-256
`1b6fa25a68d399f5a677e0ff7946d1a205741027c8f6cd25f0c25bd05ec66e3a`.
The existing blocking owner/ACL/C0 checks, exact role parity and materialized
view audit passed. The optional interface check passed twice with the same
signature/owner/column SHA-256
`f16c092e40246c6e80a75c9fd44440b69f378a56cc3d7aa51b96c1f11b6ee55c`:
five functions, 29 blinded-view columns, no assignment or setter invocation.
The restore read 257 ledger rows through migration 249, 22 hypertables and
422,188 climate rows. Private rendered manifest SHA-256
`52fe4a25fc4be1217be30e66edccce48731297efa5a94001f847166d20caa02e`;
private complete Job log SHA-256
`836e24906f6e0b5dd16b2ef90bba56c4723df9d532de430aca2e8ec582309cf5`.
Both are under
`/Users/jason/Documents/Codex/verdify-783-restored-interface-20260928/`.

The separate frozen-export fixtures cover a complete first-day row, a fallback
with zero exposure, and a rescue with a null outcome. Replaying each canonical
byte stream yields the same domain-bound SHA-256 and retains each assignment;
the analyzer tests preserve locked-pair missingness instead of replacing pairs.
These tests use synthetic fixtures. They are not outcomes from this restored DB.

An additive paired-export schema now binds the source assignment's `pair_index`
to each blinded day row and hashes the complete canonical bytes under a new
domain. The original v1 day-export bytes and fixtures remain unchanged. Its
strict replay preserves first-day, fallback/zero-exposure and null/rescue rows,
rejects duplicate identities or mapping fields, and reproduces identical
bytes/hash. The post-reveal analyzer reads those exact frozen bytes; it requires
the separately authorized X/Y-to-AI mapping, returns inconclusive on missing
locked pairs or null endpoints, and never filters on exposure. A synthetic
four-day/two-pair fixture has exact paired-export SHA-256
`f513de4b786724d751398c37655287cfa80c2b05b434b07a3694d956bdc2e8e1`.

## Remaining vertical contract

The restored database has no authorized randomized assignment for this study.
#782 has not frozen a source-owned target/contributor design, and #639/#587
runtime/kill-switch qualifications remain open. The existing database view
already exposes `pair_index`, and the new paired export requires that source
index as an explicit input; it never infers a pair from dates or assignment IDs.
The actual freezer-to-export binding must still be exercised with locked
assignments and restored production data. The synthetic paired bytes establish
a compatible schema and analyzer path, not the issue's complete
selector/receipt/outcome vertical or a physical pilot.
No synthetic case or live draft/shadow row is counted as a pilot assignment.
