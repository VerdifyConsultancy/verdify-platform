# Evidence-core critic and closure record

- Pull request: `#443`
- Initially rejected head: `e559020e88cf7ef98454c5520cb262e6bc552be4`
- Replacement implementation head: `ab56cf4556e262472333b1c813a9d4a4d44eee63`
- Review date: `2026-07-10`
- Final controller integration verdict: **APPROVE_FOR_CI**

Two fresh critics rejected the initial head even though its CI was green. The
rejections were correct: the evidence surfaces could report deploy-eligible or
effective outcomes from incomplete or confounded observations. The replacement
head reproduces and closes every reported false-positive path, plus the
follow-on edge cases found during controller integration review.

## Finding closure

1. **Same-timestamp conflict carry — closed.** Migration 190 now emits a reset
   transition after a conflict and carries `conflicting_carry_state` across day
   boundaries until a later unambiguous observation. A conflicted state cannot
   become a complete, deploy-eligible 1,440-minute day after midnight.
2. **Cross-midnight short-cycle bucketing — closed.** Pulse duration is measured
   to the next global transition while runtime remains split by local day. The
   fixture proves a 23:58-00:10 pulse is one 12-minute cycle, not a clipped
   one-to-five-minute pulse.
3. **Missing relay truth — closed.** All nine required relay keys must be present
   on every contributing action row. `{}` produces zero complete action minutes
   and `insufficient_evidence`, never inferred relay-OFF truth.
4. **Temperature safety — closed.** Episode and 10-20-minute response windows use
   same-row temperature-minus-served-floor margins. An admitted breach is
   `gate_failed|ineffective`; a missing floor is
   `incomplete|insufficient_evidence`. A blocked episode is not blamed for a
   post-window floor condition when no action was admitted.
5. **Elapsed coverage — closed.** Episode coverage uses wall-clock duration and
   a 90-second maximum gap. The anchored response window independently checks
   head, internal, and tail gaps, so eight clustered samples cannot masquerade
   as a complete ten-minute response.
6. **Weather and wetting confounds — closed.** Effectiveness requires the minimum
   indoor-minus-outdoor AH advantage to stay positive. Any outside-wetter
   interval, simultaneous fog/mister relay, or response-window wetting yields
   `confounded|insufficient_evidence`.
7. **Duplicate weather synthesis — closed.** Migration 192 and the exporter pick
   one deterministic whole climate row per greenhouse/timestamp; temperature
   and RH from different duplicates are never `MAX`-combined. The refreshed
   corpus has zero duplicate timestamps.
8. **Provenance terminology — closed.** Historical value-change inference is
   named `conservative_change_observation`, never a Tempest source timestamp.
   `DEC-015` keeps issue `#419` open until firmware emits exact
   `outdoor_data_age_s` in existing telemetry and post-OTA rows prove it.
9. **Daytime contract — closed.** `DEC-014`, the lane contracts, topology, and
   live issue `#410` prohibit only physically realized solar-day held-temperature
   admission. Ordinary daytime VPD dehumidification remains allowed and visible.
10. **Executable contract path — closed.** The migration preflight now names the
    real `186-noaa-solar-phase-parity.sql`; the lane hash and execution runbook
    agree.

## Replacement-head validation

- Migration rollback classification: migrations 186 and 189-192 are all safe
  for the required outer-rollback proof.
- Disposable TimescaleDB: all five SQL fixtures pass, including every new
  adversarial case above.
- Fresh `db/schema.sql`: loads successfully; `pg_get_viewdef`/function hashes for
  migrations 190-192 are unchanged after reapplying those migrations.
- Python/static: Ruff passes; the nine evidence contract tests pass; shell and
  diff checks pass.
- Firmware: 267 native tests pass; all invariants pass over 296,698 rows.
- Stock replay: 295,833 observation-backed rows, 199,598 fresh rows, all required
  outdoor-aware branches covered, zero force-fresh override.
- Replay diff `origin/main..ab56cf4`: 296,698 rows, zero behavior divergence and
  zero diagnostic-only divergence.
- Corpus SHA-256:
  `9db25b8c9118bd485e95a8ce203229ac318d48eaffc23a2bde438e65d8f2cb2b`.

## Limitations and authority

This verdict approves the source replacement for GitHub CI and merge. It does
not claim that migrations are applied, services restarted, the writer repair is
live, the held-temperature firmware fix is shipped, or overnight drying is
effective. Those remain release- and firmware-control acceptance items. The
GitHub author account cannot formally approve its own pull request; this durable
record captures the review basis, and merge still requires a fully green final
PR head.
