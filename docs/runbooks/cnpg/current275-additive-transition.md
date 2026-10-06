# Current275 additive clone qualification

`scripts/cnpg-current275-transition.py` emits a separate closed transition for
`verdify-cnpg-s2`, `verdify-cnpg-s2-pitr-a-frozen` and
`verdify-cnpg-s2-pitr-b-frozen` only. It never admits the
production database or arbitrary targets. The protected274 physical pair, full
parity and old target receipt table remain historical evidence.

The adapter requires exact reviewed source275 SHA256
`f4f8da110236461680f41770bad766a7e28b1a6db1982ecc632e93930a375dc4`
from source commit66e60964. It changes only six computed boundary comparisons to
the qualified native clone OIDs; copied canonical production receipt guards and
updates remain untouched. The production migration file itself is never edited.
It explicitly records the source275 filename/hash as a target-qualified runner
artifact, with measured native duration and a separate qualified payload SHA.

The complete native274 predecessor witness SHA is
`6d896a5d41ac0fb65e3a0115c6b71798a0115c5fd8311a474246f903b49f0d7e`.
Both actual native rollback qualifications produced the complete275 bridge
post-witness SHA
`7089e3bc3df63d583d4e95c6f350b96e6ce889122bafc1865bfc68c4e5d76d8b`.
Those complete profiles are pinned, rather than blessing arbitrary current hashes.

## Migration bridge, separate from admission

`bridge-qualify` always runs inside an owner transaction ending in ROLLBACK.
The emitted native SQL compares the complete bound predecessor before acting,
checks every pg_proc field except the exact appended jit=off configuration,
retains every other full catalog/native field, and checks the original full row
custody of historical ledger and receipts. Its only persistent-shape changes
inside that rolled-back transaction are:

- `fn_lighting_minutes_policy(timestamptz,text)` exact function proconfig addition;
- two canonical ordinary receipt hashes and their native captured_at timestamps;
- one exact275 source filename/hash/runner ledger row.

Actual API native digest129946… becomes872775…; ingestor84d233… becomes3390c…;
MCPfc94… remains unchanged. Canonical copied receipts67bb…/126f… become the
independently qualified production-native509162…/8690…; MCP7083… is unchanged.
Clone digest values are not substituted into those canonical receipt rows.

`bridge-install` requires the complete actual rollback artifact, its SHA,
identical before/post witnesses and exact source/profile/payload lineage. The
same complete native checks run before COMMIT. A new source role/password or
client qualification is not silently inferred from this function-config proof.
Before execution, independently bind context, native Cluster UID/current primary
UID/Ready state/image, DB OID16447, staged SQL SHA, and full before data/sequence
custody. Use the native downward-API UID shell guard and one checksummed target-
local `psql -f` dispatch with stdin closed. Capture all stdout/stderr and failures.
After rollback/install, independently recapture the complete witness and all
sequence values; full data comparisons permit only the explicitly expected two
receipt rows and ledger row on a real install. No physical Backup repetition is
required for this additive function-config change.

For a large native COPY output, use `scripts/cnpg-complete-copy-receipt.py` on a
private mode600 target-local native stdout file, then retrieve the compact
receipt and verify its SHA. Native exit0 alone cannot prove all relations were
transported. The reader requires every ordered relation marker and the terminal
marker, preserves row multiplicity, and refuses any truncated protocol. Row
values stay in private native custody and never enter user-facing receipts.

## Actual readiness uses a distinct275 admission table

After the bridge has passed independently, `admission-qualify` emits a second
separate ROLLBACK qualification. `admission-install` requires its exact native
artifact and reviewed complete post witness. This stage creates only
`public.cnpg_s2_275_runtime_receipts` and replaces the two target attesters with
source-generated bodies bound to the exact S2, frozen-A or frozen-B cluster and new table.
It preserves `public.cnpg_qualified_runtime_receipts` byte-for-byte as274 history.
This deliberate target admission delta is not folded into the narrower migration
bridge allowance.

The consumer readiness calls already exist:

- API `api/main.py:_ORDINARY_RUNTIME_ROLE_ATTESTATION_SQL` calls
  `public.fn_runtime_attest_ordinary_login()`.
- Ingestor `ingestor/ingestor.py` calls the same ordinary function.
- MCP `mcp/server.py:_MCP_RUNTIME_DB_ATTESTATION_SQL` calls
  `public.fn_mcp_runtime_attest_ordinary_login()`.

The installed275 ordinary body reads the new table in place of
`runtime_ordinary_login_attestation_receipts`; MCP reads it in place of the old
singleton receipt. Both enforce the exact native275 digests, three bounded rows,
one qualification SHA, owner/table/constraint/ACL posture and literal cluster.
Missing or changed new-table authority refuses readiness; there is no274 fallback.
Consumer probes must bind actual endpoint/Cluster/Pod/Service identities and the
new profile/body SHA before actual startup/endpoint-flip credit. Nine runtime
credential custody, the independent six-role bootstrap275 profile, collector,
allowed duties and the actual frozen-A→frozen-B→frozen-A endpoint reversal remain
separate ROOT/Iris acceptance.

## Actual installation and recovered-candidate preservation

The earlier [rollback-only receipt](../../handoffs/s2-2026-10-05/evidence/cnpg-current275-rollback-qualified.json)
is immutable historical evidence; its statement that admission was unexecuted
describes that earlier capture. Separate persistent bridge and admission installs
subsequently completed. The original S2 clone later failed complete preservation
when a replacement primary resumed Timescale maintenance and lost9,953 diagnostics
rows. Its [failed transport witness](../../handoffs/s2-2026-10-05/evidence/cnpg-current275-s2-rejected-transport.json)
remains rejected; unchanged admission authority cannot waive missing workload data.

The intact frozen A and B candidates each passed complete current275 preservation
after natural operator primary switches. Their separate transport receipts bind
the new primary UID/IP/timeline to the original installed authority; historical
installation and receipt bytes are unchanged. Full952 rowsets, all77 genuine
sequence values and all42 study tables are retained as checksum-bound public
metadata in the [recovered-candidate receipt](../../handoffs/s2-2026-10-05/evidence/cnpg-current275-recovered-candidates.json).
All six instances have source-declared workers0 and actual restoringoff. This
inspection freeze pauses automatic maintenance and requires a separately qualified
production scheduler policy; it is not permission to resume unknown restored jobs.

The A-only versus A+B-not-C bootstrap markers and target-native admission digests
remain legitimate target differences. Compare each candidate with its own complete
protected baseline, not whole-database A/B equality. Credential custody does not
credit actual owning-client authentication, endpoint reversal or spool/inverse
acceptance. Those and the final independent post-client/inverse reseal remain open
in the receipt until genuine execution results are recorded.

## Emission interface

All output files are exclusive creates. Inputs are hash-bound, complete witnesses;
installation additionally requires `--qualification` and its exact SHA.

```text
python scripts/cnpg-current275-transition.py bridge-qualify \
  --cluster verdify-cnpg-s2 --before <complete274.json> \
  --before-sha256 <literalSHA> --migration <exact-source275.sql> --output <new.sql>

python scripts/cnpg-current275-transition.py bridge-install \
  --cluster verdify-cnpg-s2 --before <complete274.json> --before-sha256 <literalSHA> \
  --migration <exact-source275.sql> --reviewed-post <complete275.json> \
  --reviewed-post-sha256 <literalSHA> --qualification <actual-rollback.json> \
  --qualification-sha256 <literalSHA> --output <new-install.sql>
```

Admission uses the same witness/qualification options and `--copied-rows` containing
all three independently read old274 target receipt rows. Its before witness is the
qualified275 bridge post. Generated SQL is source preparation; actual native
execution, compact row receipts and pool behavior establish acceptance.
