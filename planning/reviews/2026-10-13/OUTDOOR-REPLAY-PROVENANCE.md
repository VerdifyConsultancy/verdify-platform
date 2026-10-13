# Outdoor replay coverage guard (#419)

The offline replay harness now requires at least one observation-backed finite
outdoor age at or above its 600-second staleness limit. Missing age still fails
safe, but cannot satisfy this finite-stale coverage guard. Existing fresh-regime
and moisture-action thresholds remain unchanged. The meaningful rejection test
compiles the actual native gate: an otherwise-complete all-fresh case is rejected
after the fix and incorrectly accepted by the preserved pre-fix source. Synthetic
regression cases do not count as empirical greenhouse observations.

This review is based on main `8ee4775a6ae89b116c9d0a92e7f17720cc934019` and the
guard/test bytes first committed as `467c3733d3b6f291174cd53b772d1cb020fb93e9`.
Source delivery remains pending; no firmware control logic, exporter, corpus,
device entity, schema, service startup, or deployment pin changes are included.
Restart: none.

## Literal original #419 clauses

1. **Source evidence met; ordinary delivery pending.** "Corpus contains measured
   finite outdoor age with provenance and both fresh/stale branches; a
   zero-coverage corpus fails loudly." The unchanged 296698-row corpus has
   199604 finite ages: 199598 fresh and 6 stale at the actual 600-second limit.
   Every finite age has explicit `conservative_change_observation` provenance
   and an observation timestamp. Current compiled replay also exercises
   MX_VENT_DEHUM 29857 times, MX_HEAT_ASSIST 71725, MX_VENT_HUMIDIFY 716, and
   hold-required 356. The guard closes the zero-finite-stale coverage gap;
   missing age and absent provenance cannot earn its stale counter.
2. **Source evidence met in the recorded replay scope.** "No future outdoor
   sample leaks into replay; missing values preserve safe stale behavior."
   All 296698 retained rows were inspected: zero future observations, negative
   ages, finite ages without provenance, or age/elapsed mismatches, allowing
   PostgreSQL integer rounding of 0.5 seconds. The actual embedded export-merge
   tests reject a future callback and preserve missing/malformed/sentinel stale
   behavior. Conservative change observations do not claim raw Tempest packet
   time or original-device age provenance.
3. **Source qualification met; ordinary delivery pending.** "Native/invariant/
   band and targeted outdoor tests pass against the candidate and known-good
   self-diff." The guard/test bytes pass 28 focused tests and 288 native
   greenhouse cases; default invariants pass 296698 rows with zero violations.
   Exact 8ee-to-candidate band-derived replay passes 296698 rows with zero
   decision or diagnostic differences at threshold zero. The accepted ordinary
   self-diff is reused because its entire `firmware/lib`, `replay_emit.cpp`, and
   corpus inputs remain byte-identical. The documentation relocation does not
   alter those tested bytes; ESPHome compilation is not repeated.

## Provenance and evidence limits

The corpus gzip SHA256 is
`9db25b8c9118bd485e95a8ce203229ac318d48eaffc23a2bde438e65d8f2cb2b`;
its decompressed SHA256 is
`ab03e16bd833fa5530832f85fceb67c36a1d3abb624ba5d5a90415d8b7b234ef`.
Its window is 2025-08-05T09:26:00Z through 2026-07-10T02:16:51.308068Z.
There are 97094 unavailable ages, 295833 observation-backed rows, and zero
original-device age rows. The 716 HUMIDIFY rows are historical proxy replay
coverage. Earlier zero-HUMIDIFY or 300-second descriptions do not describe this
source/corpus combination. Original-device branch coverage and physical benefit
remain unproven for broader #390; they are not added gates for original #419.
Default invariants impose 0/48 full policy fields; the retained ROM-policy run
remains 27/48, not full treatment-vector coverage.

Optional stronger device provenance would require a separately named read-only
export with fixed UTC bounds, original event IDs/hashes/runtime/generation,
missing/malformed ages, sentinels, and transport-boundary history. A short export
is an availability measurement, not a complete corpus: truncated-window first
callbacks cannot become fresh subscription evidence. The existing exporter must
retain its complete first-callback exclusion history and climate/setpoint/
equipment as-of inputs. No live export is required or performed by this guard
change; do not overwrite the accepted corpus or manufacture fresh/HUMIDIFY rows.

## Runtime COPY correction and delivery scope

The initial three-path commit also edited
`docs/runbooks/outdoor-replay-provenance.md`. That file is an ingestor image input:
`ingestor/Dockerfile` COPYs `docs/`, and `.dockerignore` explicitly re-includes
`docs/**`. The earlier expectation that the initial patch had no image inputs
was false. A follow-up commit restores that runbook byte-for-byte to both 8ee
and current main `0b03466a8f30a0494482c983853128f98cdc9e0a`, preserving the initial
commit and moving the new guard explanation here under excluded `planning/`.
The Dockerfiles, ignore rules, classifier, firmware logic, exporter and corpus
are unchanged. The final reviewable source delta is the harness, its regression
tests, and this planning review. Authoritative COPY classification of that delta
on current main must select no images before ordinary source delivery.

Local qualification evidence and the optional bounded read-only query are
retained at `/private/tmp/verdify-419-evidence-20261013/`. The authorized writer
coordinates the ordinary source PR after the existing CI capacity lease;
this lane does not push, open a PR, submit CI, promote images, sync Argo,
contact the controller, or perform OTA.
