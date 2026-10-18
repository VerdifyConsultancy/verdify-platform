# Contributor validity and relay dwell — #368 / #367

This source packet reuses the exact archived #368 candidate and the preserved
#367 duration-refresh change. It does not change controller policy, aggregate
membership, tunables, wire identities, thresholds, physical sensor assignment,
relay history or safety overrides. No OTA is included.

The diagnostic count now reports complete finite wall temperature/RH/VPD probes
within the existing300-second window. Health preserves its earlier120-second
warning window and reports fresh nonfinite data as `INVALID`; exactly120 seconds
is `STALE`. The case probe has temperature/RH only. A partial wall probe may still
contribute its valid field to an unchanged individual aggregate; the complete
probe count does not claim those three aggregate cohorts are identical. This is
not jump/flatline detection, a physical quorum change or scientific-panel proof.

The relay wrapper refreshes the eight current snapshot/legacy duration values
on each tick while preserving original transition stamps and runtime. A changed
limit is measured from the original edge, including wraparound; force overrides
remain effective. The84-case actual-YAML test is byte-identical to the retained
bc351370 test. Its historical results are provenance, not new candidate results.

The added70-case diagnostic test compiles the actual YAML count and health
lambdas with only clock/value storage adapters. It covers every wall field with
NaN/+Inf/-Inf, case-probe validity, strict freshness boundaries, missing-timestamp
precedence, complete error reporting, valid steady/zero-VPD inputs, recovery and
uint32 rollover. Three prepared helper cases also enter the existing native
firmware test runner. No new test pipeline is added.

`source-reconciliation.json` binds the source bytes and cheap completed checks.
`qualification-plan.json` defines the existing ROOT-owned native path and outputs.
`causal-regressions.sh` compares the same current tests against the real prior
firmware tree and current source. It must run only in admitted native Linux CI;
all compilation, replay and image work stays off the Mac. Its old-source failure
counts are expected criteria until actual native results are retained.

ROOT owns source composition, shared CI, native admission and eventual release.
Use the existing repo-validate template for strict `make test-firmware
firmware-invariants` and the exact declared secretless-compile check. The weaker
declared firmware-logic exit2 legacy-corpus waiver cannot count as invariant
acceptance. PR validation additionally includes the two focused YAML tests;
existing contracts and zero-threshold committed replay are reused. A worktree
replay run must retain its real chronology and cannot be relabelled precommit.
No band-curve change is present, so no new band-curve qualification is requested.

The corpus replay does not execute these changed diagnostic/relay wrappers; the
actual-source regressions prove the intended delta. Neither expands the previous
27/48 historical treatment coverage or supplies an ORDER_REVISION. Keep #368,
#367 and #390 open for their remaining validity, physical control and recovery
acceptance. Compile-only firmware remains NONDEPLOYABLE and is not last-good.

Restart: none — firmware source and native qualification only; no service or
controller deployment is part of this packet.

ROOT's one-shot read-only production diagnosis at2026-10-18T07:32:45Z
independently reproduced the mismatch: South temperature/RH/VPD were SQL NULL,
North/East/West were finite, while diagnostics at07:32:44Z reported count4 and
healthOK on firmware2026.10.2.0637.620a218a-wifi-bound. Database identity and
restart count were stable. This supports the diagnostic defect; it is neither a
hardware-cause diagnosis nor proof this source candidate is deployed. Private
receipt: `/Users/jason/maintenance/verdify-next-ready-20261018/749-diagnosis-01/receipt.json`.
