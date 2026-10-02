# Isolated logical target successor 270

`scripts/cnpg-target270-successor.py` owns only the already admitted
`verdify-db-rehearsal/verdify-cnpg-rehearsal`, database `verdify_rehearsal`,
PostgreSQL 16.13. It preserves the original 277 ledger rows, all three historical
seals, roles and prior target receipt custody. It does not provide production
cutover, physical-target, password-authentication or full product-service proof.

The applied migration SHA is
`672d1afa92e37f243d5893fbd57cd2b84e86ea19c1c79d3975c04dfd39663532`.
The helper selects its exact ops function DDL and uses a distinct target-native
pre/postflight. It does **not** execute the complete production migration: its
production digest literals and historical receipt updates are not appropriate
for the admitted logical target. Production/default/physical paths are unchanged.

## Rollback qualification

Review the complete source/delta and current target binding before execution.
Use the existing immutable original source and actual post269 install record,
password-free role export, exact two-line native original-row/target-receipt export,
and fresh Cluster/primary Pod UID/image binding.
The helper verifies all input hashes and committed operator bytes.

```
python scripts/cnpg-target270-successor.py --execute-qualification \
  --original-source ORIGINAL_SOURCE --original-source-sha256 ORIGINAL_SOURCE_SHA \
  --prior-install ACTUAL_PRIOR_INSTALL --prior-install-sha256 PRIOR_INSTALL_SHA \
  --prior-row-custody ORIGINAL_NATIVE_ROWS --prior-row-custody-sha256 ROWS_SHA \
  --binding FRESH_BINDING --binding-sha256 BINDING_SHA \
  --source-roles PASSWORD_FREE_ROLES --source-roles-sha256 ROLES_SHA \
  --operator-source REVIEWED_MERGED_SOURCE --receipt-dir NEW_EXCLUSIVE_DIRECTORY
```

This action uses one local peer-postgres backend, `SET SESSION AUTHORIZATION
verdify`, the existing retained-session transport and exact two matview locks.
It captures the complete predecessor, performs genuine DDL/ledger stamping within
a SAVEPOINT, retains the complete seven-field native record, rolls back, verifies
all prior rows and full predecessor, then rolls back the outer transaction and
verifies privileged session return. Statement/phase/total budgets remain
180/240/900 seconds. There is no execution retry or installation branch.

The original source/admission lineage must retain exact full catalog, semantic,
body, ledger and seal fields. Only `public.v_climate_merged` may change its native
`relfilenode` and `relfrozenxid`; only `public.v_relay_stuck` may additionally
change `reltoastrelid`. Both must remain the same materialized-view OID, owner,
definition and complete native field types, with every other fact exact. The
observed five-field delta is retained in lineage output. This closed rule follows
the actual completed source refresh jobs and PostgreSQL 16.13
[`refresh_by_heap_swap`](https://github.com/postgres/postgres/blob/REL_16_13/src/backend/commands/matview.c)
and [`finish_heap_swap`](https://github.com/postgres/postgres/blob/REL_16_13/src/backend/commands/cluster.c).
Earlier full raw facts remain in their original record; current full raw facts
remain in the complete fresh predecessor and are required byte-identical
before/after this successor. No other raw drift is permitted or normalized.

## Reviewed installation SQL

After independent full-record/source-delta and native successor review, the same
helper can emit a separate installation transaction using `--before`,
`--before-sha256`, `--reviewed`, `--reviewed-sha256` and `--output`, plus the original
source/prior-install arguments above. It never executes that installation.
The exact full predecessor is checked inside the native transaction before DDL;
any intervening drift refuses the transaction. Only the source ops definition,
one exact 270 ledger row and the three qualified-target receipt successor rows
may change. MCP's boundary remains unchanged. API/ingestor literals come from the
genuine reviewed rollback record, never from an arbitrary current catalog.
All three target rows share that actual record hash. Original seals are untouched.

Retained-session emission helpers also support keeping the original two AS locks
across qualification and subsequent reviewed installation. Both complete native
witnesses, exact pg_proc metadata/deparsed definition/ACL payload, SQL, source
hashes, prior receipt rows and unknown outcomes must be retained. A failed or
unknown operation is not proof of rollback, installation or authentication.
