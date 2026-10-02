# Facility-safe experiment closure status

Migration270 corrects a status projection, not controller state or experiment
recovery. A lawful immutable facility-owned closure can finish the authority
incident without asserting a confirmed baseline observation.

## Exact qualification

The projection reports `facility_safe_closed` only with an immutable closure of
kind `facility_owned_safe_state`, matching current lease, not in the future;
shadow phase, closed admission, capability off; no selected or nonterminal work,
no open exposure, no work created after closure, and no fault at or after closure.
A missing or invalidated closure retains the existing failure/recovery status.
Critical integrity checks remain active. Baseline readiness and `rollback_ready`
retain their existing predicates; facility closure supplies neither.

The existing alert monitor resolves its owned integrity alert only from the
successful projection, using the explicit resolution:
`facility-safe authority closure; historical runtime faults retained; no confirmed baseline recovery claimed`.
It retains historical alert details, faults, work events and the closure artifact.
Unavailable status preserves the prior alert, as before. No failed work is replayed.

## Evidence and delivery

Read-only production snapshot October2 2026 01:12:46Z: experiment
`45039c86-c1d9-52f6-a0a9-d94a17bc4b14` draft/shadow/closed/off, lease19;
111 terminal work rows,94 historical faults,zero open exposures. Closure Sep30
17:50:12Z has current lease19 and artifact
`933cecec55c04398f724f22a03582c66d378dd40a94bf6cd2c6347c90af305c9`.
Alert10453 remained acknowledged warning because the old projection compared
August30's fault only with August29's actual baseline recovery.

Read-only catalog projections verify the exact installed ordinary217 digest
source and MCP263 source. API and ingestor each change only the catalog entry for
`public.fn_experiment_v2_ops_status()`; MCP has no changed entry. These are
prospective digest projections, not an applied migration or physical proof.
Full pre/post entry hashes and original raw definitions are in the private packet
`verdify-stale-authority-audit-20261002` under Jason's Documents/Codex.

Migration270 requires exact applied269 and all current predecessor receipts;
postflight requires exact projected successors before replacing the two receipts.
It changes no grants, roles, study authority, device state or prior migration.
The atomic C0 runner admits only its exact file hash and ordered successor;
the initial-only six-duty bootstrap checks the270 ledger and new seals.

Deliver migrate/API/MCP/ingestor/orchestrator together through existing source
CI, immutable origin qualification, release pins and full Argo sync with hooks.
After adoption, verify270's ledger/hash, unchanged authority/closure/work/fault
counts, `facility_safe_closed`, honest rollback readiness, and alert10453's
explicit canonical resolution on the normal monitor tick. Do not manually clear
an alert, insert a recovered event, enable an experiment or request device replay.
Rollback is a forward migration restoring the prior projection and exact reviewed
boundary seals; retain the closure and all history. Never edit applied270 bytes.
