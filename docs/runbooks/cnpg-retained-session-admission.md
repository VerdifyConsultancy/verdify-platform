# Retained-session logical target admission

`scripts/cnpg-retained-session-admission.py` is an explicit, target-only operator
for `verdify-db-rehearsal/verdify-cnpg-rehearsal`, database `verdify_rehearsal`,
PostgreSQL16.13. It does not change ordinary/physical transition emitters,
production endpoints, credentials, or device authority. Its native record version
is `cnpg-native-retained-session-transition-v1`; qualification mode is
`savepoint-rollback-qualification`. Existing full-transaction qualification
records remain unchanged and serve only as reviewed body/boundary references.

One UID/image-bound peer `postgres` connection uses the existing privileged
bootstrap guard and **SET SESSION AUTHORIZATION verdify**. This is owner DDL
posture, with zero ordinary password-authentication credit. The outer transaction
holds the original ledger/seal SHARE locks and AccessShareLock on exactly
`public.v_relay_stuck` and `public.v_climate_merged` through their source-owned
zero-row reads. Native nonconcurrent refresh jobs wait normally. A separate
lock keeper is insufficient: a queued AccessExclusiveLock blocks later independent
AccessShareLock requests. The same backend reuses its already granted locks.

The operator captures the complete source-checked target witness, executes the
complete guarded DDL inside a savepoint, emits the genuine full native before/post
record, and rolls back to that savepoint. A second complete witness proves every
original raw fact restored and the trial receipt table absent; PID/backend start,
postmaster start and both granted locks must still match. Independent full record
checks require the previously reviewed DDL, native/semantic/body/shape fields and
three literal boundaries unchanged. The actual install uses those exact freshly
qualified inputs in the same outer transaction; all native before-COMMIT guards
remain mandatory. Original seals and ledger are never resealed or edited.

Creation under a savepoint uses a child XID. Before the exact first CREATE the
source captures its own granted ExclusiveLock transaction-ID set and snapshot
xmin. Afterwards exactly one newly owned child ID must exist; the created
pg_class tuple xmin equals that precise ID, and relfrozenxid equals the captured
snapshot xmin. Missing/extra IDs, changed cutoff and all existing owner/type/toast/
index/constraint/raw-fact deviations fail closed. There is no range or exemption.

Each statement is bounded at180s, phase reading at240s, native process lifetime
at900s, idle-in-transaction at60s and lock acquisition at2s. psql closes a separate
gzip output pipe after each phase while retaining its database connection;
complete gzip integrity and full JSON are checked. Raw streams, SQL, full records,
errors, code hashes, native bindings and password-free role exports are retained.
After actual COMMIT the same backend RESETs session authorization and verifies
its original bootstrap identity. Any failure closes/rolls back the session;
loss after sending COMMIT remains `unknown-commit` until separately inspected.
No repeated attempts or normalization are implicit.

```sh
python scripts/cnpg-retained-session-admission.py \
  --operator-source "$REVIEWED_MERGED_SOURCE" \
  --source "$IMMUTABLE_SOURCE_WITNESS" --source-sha256 "$SOURCE_SHA256" \
  --binding "$EXACT_CURRENT_TARGET_BINDING" --binding-sha256 "$BINDING_SHA256" \
  --source-roles "$PASSWORD_FREE_ORIGINAL_ROLES" --source-roles-sha256 "$ROLES_SHA256" \
  --reviewed-reference "$ORIGINAL_REVIEWED_NATIVE_RECORD" \
  --reviewed-reference-sha256 "$REVIEWED_RECORD_SHA256" \
  --execute --receipt-dir "$NEW_EXCLUSIVE_PRIVATE_RECEIPT_DIR"
```

The reviewed source pins all operator dependencies to committed bytes. The
binding uses the existing transition's exact four fields: cluster_uid, pod,
pod_uid and operand_digest. No Secret values are consumed by this operator.
Successful admission remains distinct from later real password-authenticated
API/ingestor/MCP pools, source hot queries, fault recovery and production cutover.
Local native regression fixtures use PostgreSQL16.15 and synthetic catalog/digest
stand-ins; they do not claim target16.13 execution or imported-data proof.
