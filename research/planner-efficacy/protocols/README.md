# Switchback protocol artifacts

ADR-0010 changes the first physical experiment from the unfinished generalized
policy-vector path to the deployed confirmed-component fast path.

## Current target: version 2

`planner-switchback-v2.template.yaml` is the machine-readable execution target.
It is a template, not a locked protocol and not actuation authority. Resolve
every `TO-LOCK` value and commit an immutable
`planner-switchback-v2.yaml` before randomized day 1.

Version 2 pins these architectural decisions:

- deployed firmware and deterministic safety logic remain unchanged;
- generalized `VERDIFY_POLICY_VECTOR_MODE` stays `off`;
- a sole host executor uses the existing 11 setter/readback routes;
- all 48 raw cfg values use the domain + schema + full manifest + existing
  canonical 178-byte codec with cross-language goldens for a stable state hash,
  plus a separately canonicalized receipt with an exact JSON Schema and golden;
- cfg ingestion—not the executor—owns immutable source epochs, and all 48
  per-wire timestamps must advance before a second confirmation can pass;
- mixed sequential prefixes never count as exposure;
- AI selects `baseline|moderate|aggressive` once per local day;
- baseline is interposed at every boundary and after ambiguity;
- six elapsed hours are excluded by default, pending a frozen joint-power/
  completeness/carryover rerun; v2 forbids DST-offset crossing;
- one accepted 256-bit CSPRNG secret, domain-separated schedule/mapping
  derivation and full-entropy commitment replace the public beacon ceremony;
- operations are safety-visible while comparative analysis remains X/Y-blinded;
- >=12 h shadow spanning a complete scheduled boundary (target 24 h), two
  transport/safety canaries and 48 h A/A are evidence gates.

The additive executable source contracts are in `switchback/v2_selector.py`,
`v2_randomization.py`, `v2_profiles.py`, `v2_power.py`, `v2_outcomes.py`, and
`v2_analysis.py`. The schedule/design/selector JSON Schemas and canonical
schedule/analyzer goldens in this directory are integration inputs for the
runtime/data lanes; they do not themselves provide persistence, provider
access, database role isolation, or actuation authority.

`planner-switchback-v2-power.json` is intentionally **not** a design lock. It
demonstrates the fixed-m/joint-power machinery and selects 150 pairs under its
explicit provisional assumptions, while recording why Git lacks the raw
06:00–24:00 inputs and frozen provider replay needed for the real pre-draw
lock. It reads no randomized/live efficacy data. Fifteen pairs fail that
scenario decisively; the final one-time fixed m must be regenerated from the
missing pretrial inputs before any randomization finalization.

The authoritative reasoning and execution model are:

- `docs/adr/0010-confirmed-component-experiment-fast-path.md`;
- `docs/plans/planner-experiment-fast-path-2026-08-23.md`;
- GitHub epic #581 and launch issue #642.

## Version-2 lock order

1. **Physical and route truth.** Obtain #641's scoped probe approval before the
   first experiment-owned write; ledger #424/#433 diagnostics as immutable
   `commissioning_probe` readiness work. Regenerate baseline, moderate and
   aggressive artifacts on the actual deployed ESPHome entity grid, then
   obtain #641's combined multidisciplinary physical signoff before canaries.
2. **Software evidence.** Pass the recent-Postgres assignment → selector →
   exclusive component calls → two distinct post-delivery observation epochs →
   exposure → outcome/analyzer vertical test and its injected-failure matrix,
   including cached-observation relabel rejection, full-48 reboot recovery and
   phase contamination.
3. **Power and outcome lock.** Recompute historical power/completeness for the
   06:00–24:00 window, selector dilution and cross-endpoint correlation. Freeze
   a fixed pair count with >=80% joint three-condition advance power. Freeze one
   benefit endpoint; if uncommissioned, call the exact nine-stream fallback
   heterogeneous active/open-state burden, not efficiency. Freeze endpoint,
   input, missingness and analyzer code/environment. Primary ITT emits one
   fixed-window row per assigned day, including fallback/rescue/failed delivery;
   exposure coverage and 61,560/64,800 seconds are per-protocol sensitivity
   only, never primary filters.
4. **Runtime rehearsal.** Deploy the initial integrated capability, prove >=12 h zero-write shadow
   across at least one complete scheduled boundary (target 24 h), run both
   supervised template canaries with facility-aware recovery, and pass the
   48-hour A/A pair. Canaries do not establish carryover.
5. **Pre-draw design lock.** Resolve every non-random `TO-LOCK` value and
   freeze an immutable design artifact with exact source, deployed, sensor,
   facility, profile, endpoint, power/sample-size, role and analysis revisions.
   It contains no schedule-dependent value.
6. **Single randomization finalization.** The restricted assignment service
   internally generates one 256-bit OS-CSPRNG secret for the study ID; callers
   cannot supply or replace it. Domain-separated
   HMAC/KDF derives pair order and X/Y mapping. The same transaction records the
   no-redraw receipt and publishes only the blinded schedule/hash and a
   commitment binding study ID, schedule hash and the full secret. A contract
   test permits the finalized `planner-switchback-v2.yaml` to differ from the
   design lock only in receipt-derived fields. The exact start date was already
   frozen. Commit the final instance before day 1; no secret is committed or
   logged. If the start is missed, abort that study ID/draw and preregister a
   new one; never shift the drawn schedule.
7. **Start approval.** Verify no comparative efficacy has been inspected and
   obtain #642's separate randomized-day-1 go/no-go after both #641 approvals,
   then confirm day-1 readbacks and exposure.

After day 1, assignments may not be redrawn, reordered, shifted, replaced or
deleted. Facility rescue remains unconditional and becomes an immutable
deviation/ITT event.

The stable study row is `kind=randomized`, `protocol_version=2`. Its lifecycle
status, execution phase (`shadow|commissioning|aa_rehearsal|randomized`) and
admission state are orthogonal. The additive v2 state machine binds phase onto
every artifact and supersedes the old separate qualification/A/A result gates
only for v2; historical v1 rows keep migration-213 semantics. Five minimum P0
roles separate randomization custody, lifecycle mutation, execution, outcome
freezing and read-only blinded analysis; full platform role hardening remains
#643.

## Historical version 1

`planner-switchback-v1.template.yaml` and the current v1 randomization/analyzer
code preserve the original generalized-vector design. Version 1 was never
locked or run. It requires the public beacon, secret mapping, device-side
manifest/vector identity, 96-transition qualification and seven-day A/A.

Do not delete those artifacts: they remain useful for the deferred platform-v2
work in #586/#638. Do not use them as the current experiment runbook or claim
that their passing unit tests make the fast path executable.
