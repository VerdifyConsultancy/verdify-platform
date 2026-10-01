# Original dump snapshot and later native chunk

This is source-only qualification for the original `verdify-20261001T120237Z`
logical dump. It installs no SQL, changes no original seals or ledger, and grants
no ordinary authentication or recovery credit.

The immutable v2 source witness remains `ec9b3d5a…`, with 44,161 raw entries and
canonical catalog SHA `f79f3c2d…`. The complete actual v3 source witness is
35,152,779 bytes, SHA `f2deda6b…`; every original raw entry remains equal, while
12 additional entries belong to one native managed chunk. The collector retains
all fresh raw, semantic and native facts. The reader has a finite 64 MiB bound
and refuses oversized or nonregular input.

## Native evidence

The exact original source Pod UID is
`b1fa2c0e-a995-481f-a29f-4cfaad4e697b`, database `verdify`, PG160011,
Timescale2.25.2. A repeatable-read READ ONLY transaction captured:

- Chunk1069, `_timescaledb_internal._hyper_19_1069_chunk`, created at
  `2026-10-01T17:32:16.798656Z`, after actual backup completion
  `2026-10-01T12:03:51Z`.
- Native parent19/public.override_events, pg_inherits OIDs1581647→19713,
  dimension15/ts, slice886, validated check constraint886 and range
  `[2026-10-01T00:00Z,2026-10-08T00:00Z)`.
- Uncompressed, nondropped, non-OSM status; native classes, columns, indexes,
  constraints, dependencies, owner and effective ACL.
- Server TimeZoneUTC and native clock within recorded request bounds; unchanged
  source Pod before/after. Archive TOC excludes chunk1069. Archive header wall
  clock is not interpreted as UTC.

All original parent indexes are retained. Two parent indexes have the same native
ts-DESC signature. The new chunk has two signature classes corresponding to the
three retained parent index names. Exact [Timescale2.25.2 index implementation](https://raw.githubusercontent.com/timescale/timescaledb/2.25.2/src/chunk_index.c)
reuses an already matching native index. There is no chunk_index catalog in this
version. [Native tables](https://raw.githubusercontent.com/timescale/timescaledb/2.25.2/sql/pre_install/tables.sql)
and the [chunks view](https://raw.githubusercontent.com/timescale/timescaledb/2.25.2/sql/views.sql)
provide the creation/parent/dimension/constraint metadata.

## Finite qualification

`cnpg-c0-restore-qualification.py --qualify-historical-source` requires:

```
--source <complete-original-native-v3-source.json>
--frozen-source <immutable-ec9-v2-source.json>
--chunk-metadata <native-capture.stdout>
--chunk-capture <capture-receipt.json>
--backup-custody <original-observer-combined-qualification.json>
--dump-toc <original-native-toc.txt>
--historical-dump <verdify-20261001T120237Z.dump>
--output <new-qualified-source.json>
```

The script reads files only. All genuine source/metadata/capture/backup/TOC/dump
hashes are fixed in this closed profile. The new source artifact keeps the complete
fresh witness fields plus explicit historical evidence containing the immutable
v2 bytes. Full fresh content is independently SHA-bound. It is not a relabeled
frozen witness.

`checked(source)` validates all full fresh native facts and the original raw
boundary/native digest implementations, seals and ledger. The explicit historical
catalog view excludes only the 12 independently proved postdump entries and must
reproduce the exact immutable f79f catalog hash. Comparison uses that view against
the full restored target. Reports retain complete fresh raw catalog differences,
profile name and separate full/historical hashes. Default source without this
profile remains strict, and targets refuse any historical source profile.

Historical changes/removals, any further addition, forged metadata or provenance,
wrong parent/range/creation/owner/ACL/column/index/constraint, full fresh native
fact drift, and original seals/ledger/boundary changes remain refused. A future
chunk or different native source capture requires independently reviewed source
qualification; this profile admits neither generically.

## Actual acceptance

The local genuine witness qualification and private PostgreSQL regression tests
prove collector/projection behavior. They do not install target admission. ROOT
must review the source diff, use merged source, bind the complete qualified source
artifact SHA, and then perform the native logical before/rollback/literal
transition and actual clients. Physical A/B recovery remains separately qualified.
No current production dataset or cutover parity is inferred from this historical
schema projection.
