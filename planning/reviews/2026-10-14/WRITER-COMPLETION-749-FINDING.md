# #749: finite run-completion settlement evidence assessment

**Existing durable run proof is sufficient for a precise owner review after an actual completed fresh run. Existing log-only collector inputs are insufficient for an automatic settlement predicate.** The producer already persists a genuinely completed run, but does not emit a run-bound terminal-completion observation that the collector can consume. Neither a generic zero-command line nor unchanged source/socket/generation can substitute for it.

Source only: collector `737d562fc35eea1498d0b7c48649c8cb0bf57d89`; unchanged producer/image source `5e992d1747eb77d6f596d7e880db1f30862dae06`. No source edits, tests, product imports or consumer execution, provider/cluster/SQL/HA/Secret/device actions or child occurred. Root/Retry own current source/CI/main and the private fresh16-field proposal. This assessment neither authorizes that send nor claims it would qualify #749.

Root reports the existing confirmed_halt_forward v2 archive consumed at22:45:42UTC, archive hash prefix4439c4bd, old stopped state/approval unchanged, current Pod/source/session/generation bound, then `confirmed halt archived; fresh approval required`. There were no new policy or rollback candidate commands. That is **archive-only HOLD**, not a completed distinct fresh run. The separate pre-existing occupancy sync is not relabeled as completion of this proposal.

## Exact existing run-bound state proof

| Producer source | Existing evidence and limit |
| --- | --- |
| `bounded_reconcile.py:205–235` | Preview fingerprint binds source, writer session, Pod, generation, exact candidate changes, immutable plan rows and readbacks. The preview has captured time/plan expiry. Its existence is current intent, not a confirmation. |
| `bounded_reconcile.py:143–168` | Preview readbacks require all48 canonical fields in a fresh single atomic snapshot and equality with this exact connection's cfg observations. This is entity/readback truth, not27/48 native policy injection qualification. |
| `bounded_reconcile.py:981–1007` | Confirmed-halt archive retains the original stopped state/approval, forward authority and native requests, and binds the current identity/readbacks. Its return value requires a different approval run ID. Archive creation alone keeps the old state and returns false while the old approval remains. |
| `bounded_reconcile.py:1073–1084` | A false archive return produces `Decision('hold', reason='confirmed halt archived; fresh approval required', run_id=old_run)`; only genuinely distinct fresh admission moves to a new state. |
| `bounded_reconcile.py:1123–1159` | Fresh approval/preview fingerprint, session/generation, freshness and expiry are validated. The new durable state has its own run ID and approved preview, completed list/values and status. Existing history is not silently reinterpreted. |
| `bounded_reconcile.py:392–429` | Active run validation binds source/session/Pod/generation, plan identity, expiry and exact expected baseline/completed readbacks. |
| `bounded_reconcile.py:432–451` | Each actual requested lifecycle key must exist as `delivery_status=confirmed` with `confirmed_at`; current cfg must match. Failed/missing/expired/superseded/cancelled rows fail. Pending confirmation is not completion. |
| `bounded_reconcile.py:1193–1216` | After confirmation, completed parameters/values are retained, pending records cleared and residual validated. Only no residual persists `status=complete` and returns `Decision('complete', run_id=...)`. A≤limit ordinary crop-VPD handoff can still remain in `Decision.changes`. |
| `bounded_reconcile.py:1172–1192` | A later pass can carry the same completed run ID while comparing remaining current desired/readback differences. This makes a run-bound observation possible; a free-standing equality result remains insufficient. |
| `bounded_reconcile.py:104–121` | On a later broad candidate, the completed archive retains exact approval+state, with run-ID/fingerprint checks, before the active names are removed. It is completion custody, not a clock or physical proof receipt. |
| `bounded_reconcile.py:1247–1317` | A successful physical dispatch initially yields awaiting_confirmation. Partial failure, unknown outcome, deferred-for-heap/no-attempt and rollback states retain their distinct status; none may be relabeled as complete. |

The owner can later read/custody the actual new approval/state/completed archive, original confirmed-halt archive, native confirmation keys/readbacks and fresh preview. A valid review must bind the distinct run, actual admitted/requested effects and current writer/plan/source identity. It cannot rely on assumed16/16 counts or erase the old approval/archive. No such fresh-run completion exists in this assessment.

## Why a collector-only log inference is not supported

- `Decision` carries `run_id` at bounded_reconcile58–63, but dispatcher1285–1297 omits it from the blocked-stage log. The collector cannot identify which historical run is held from that line alone.
- Dispatcher1309–1310 consumes `Decision('complete')` only by replacing `changes`; it emits no completion event or fingerprint. The generic zero-command log1368–1374 carries reason/generation/count but no run ID or admitted-preview binding. The success branch1826–1827 marks transport reconciled without a completion log.
- Dispatcher1811–1817 emits `stage_awaiting_confirmation run_id=... failure_count=...`; this proves dispatch-awaiting-confirmation, not the successful return of `_check_records` or full-run completion.
- `writer-policy-hold.json`, produced by bounded_reconcile238–272, has capture/source/session/generation/reason/fields and `bounded_hold_no_dispatch`. It has no run ID/approval fingerprint/terminal-settlement link. It is written on HOLD; this path supplies no later clearing event. Its old existence or a newer equal preview cannot prove recovery.
- The collector737d `writer_stability(logs, started_at, connection_generation, observed_at)` at186–281 receives no current writer-stage file or native-confirmation input. It explicitly keeps terminal policy holds through replay, grid reseal and aging, and denies at277–278. Its normal-pending/30-minute logic is a different condition.

The existing readiness Job has API/log read access and backup/tmp mounts, not an authenticated current-stage file input. A locally edited template, manual true flag, restarting the writer to drop history, generation-only check or unbound zero/equality trace would introduce evidence rather than observe it. Do not add a collector-only clearing branch on that basis. The existing durable files are useful owner evidence, but currently lack an independently collected channel into this log-only predicate.

## Smallest source/interface remedy to propose

Do **not** change production just to clear a latch. After root has an actual complete fresh-run receipt and wants automatic collector settlement, the smallest honest interface is a metadata-only, run-bound producer completion observation, then narrow collector parsing. Reuse existing state and confirmations; add no new writer, endpoint, permission, authority, service or broad gate.

The observation must be emitted only after genuine `Decision('complete')` for the current exact run and the corresponding ordinary handoff is settled. `status=complete` alone can still have nonempty ordinary handoff changes, so emit at the existing successful/no-outstanding-work boundary, not merely on entering the complete branch or after sending a stage. A later run-bound no-residual observation is legitimate; an unrelated zero command is not.

Bind the run ID and approved-preview fingerprint, current source/Pod/generation and writer-process identity (a hash is sufficient; emit no credentials or opaque session value), the completion observation time, state/effect receipt hash and actual terminal/pending counts. Where settlement supersedes a historical halt, retain an explicit link to the immutable old halt archive/run. Persist that link when existing confirmed-halt admission validates and creates the genuinely distinct new state; do not infer it later from generation or chronology alone. Hold observations should retain their bound run/receipt identity too. A missing legacy linkage remains unresolved until authoritative owner custody supplies it, rather than globally clearing every older error.

The collector may settle **only that bound historical hold**, on a current same-writer/source receipt from a distinct genuinely completed admitted run. Preserve raw logs/history; any newer hold/error relatches. Genuine transport/lease/new-attestation resets and the existing quiet30-minute requirement remain. Source/clock/identity mismatches, missing/truncated completion evidence or unproved handoff remain fail-closed. This proposal is an interface/observability delta, not a request to weaken the guard or bypass the fresh16-field decision.

A collector-only alternative would require a genuinely authenticated, source-bound read channel to the existing durable stage/approval/confirmation state. Such a channel is not available in the current interface. Creating an arbitrary trusted receipt input or new Pod-exec/PVC access is broader than the narrow producer observation and is not proposed here.

## Finite future regression proposal — not implemented or run

Extend the existing `tests/test_bounded_reconcile.py` completion/archive cases and `tests/test_experiment_v2_proof_packet.py` stability cases:

1. Archive-only old approval, pending confirmation, heap-deferred/no-attempt, partial dispatch, rollback and unknown outcome emit no settlement and remain blocked.
2. A distinct properly admitted run, exact native confirmation/readback proof and settled ordinary handoff emits one idempotent run-bound completion; old immutable archive/approval remain unchanged.
3. A nonempty/unconfirmed ordinary handoff does not emit settlement just because bounded state is complete.
4. Wrong/unlinked run/archive fingerprint, old approval reuse, different source/Pod/session/generation, stale/truncated/replayed event and generic zero/equality cannot settle the hold.
5. Exact current completion settles only its linked historical hold; a later different hold or genuine transport/lease failure relatches and retains the original30-minute/fail-closed semantics.

These are source-contract regressions, not physical acceptance or a reason to replay the old actuation. No tests or fixtures were added in this assessment.

## #749 closure limit

Even an actually completed distinct ordinary16-field run would supply only its bounded policy reconciliation/confirmation evidence. It does not itself prove current three-probe freshness/means/spreads/semantic consistency, hydro exclusion in the exact delivered causal sources, current backup/auth/source/writer boundary or physical exposure-loss ordering. Full #749 closure still needs its original current-boundary and close-exposure-before-recovery evidence in the applicable authoritative qualification scope. Reuse valid existing native/physical evidence if it meets those clauses; this assessment does not invent a mandatory physical fault procedure, authorize one, or substitute static source order for an unproved physical claim.

Archive-only HOLD, completed fresh ordinary run and later physical/scientific readiness remain distinct. Native treatment27/48 remains UNPROVEN. Stop this finite source assessment here.
