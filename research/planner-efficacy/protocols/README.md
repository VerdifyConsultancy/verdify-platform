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
- the historical staged-v2 template retains >=12 h shadow, two canaries and
  48 h A/A; the one-study accepted direct launch explicitly waives these
  durations in migration 220 and instead requires a sealed #641 physical proof.

The additive executable source contracts are in `switchback/v2_selector.py`,
`v2_randomization.py`, `v2_profiles.py`, `v2_power.py`, `v2_outcomes.py`, and
`v2_analysis.py`. The schedule/design/selector JSON Schemas and canonical
schedule/analyzer goldens in this directory are integration inputs for the
runtime/data lanes; they do not themselves provide persistence, provider
access, database role isolation, or actuation authority.

`planner-switchback-v2-power.json` is intentionally **not** a design lock. It
demonstrates the fixed-m/joint-power machinery and selects 150 pairs under its
historical 0.80 planning target. The accepted exploratory direct-launch design
instead fixes 30 pairs over 60 local days, with provisional modeled joint
advance power 0.13776 and no confirmatory efficacy promise. The pre-draw lock
still needs source-bound 06:00–24:00 outcome scales, completeness and frozen
provider replay; the historical artifact supplies none of those. No internal
sample-size adaptation or date shift follows the draw.

The authoritative reasoning and execution model are:

- `docs/adr/0010-confirmed-component-experiment-fast-path.md`;
- `docs/plans/planner-experiment-fast-path-2026-08-23.md`;
- GitHub epic #581 and launch issue #642.

## Version-2 lock order

1. **Physical and route truth.** Obtain #641's scoped probe approval before the
   first experiment-owned write; ledger #424/#433 diagnostics as immutable
   `commissioning_probe` readiness work. Regenerate baseline, moderate and
   aggressive artifacts on the actual deployed ESPHome entity grid, then
   record #641's combined remote-evidence decision before canaries. An on-site
   crop inspection or handheld spot-check is not a gate; current telemetry,
   readbacks, calibration-age and safety limits still must pass.
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

## Blinded daily assignment reconciliation (#784)

`python scripts/experiment-v2-reconcile.py --experiment-id UUID --output /private/receipt.json`
uses the existing `experiment-aa-gates.py` read-only operator connection contract
(`VERDIFY_DB_BACKEND=kube` on the laptop, or `dsn` with existing process-environment
credentials). It runs one bounded SELECT with `default_transaction_read_only=on`.
It does not call lifecycle, selector, randomization, reveal, setter or device APIs.
The operator needs SELECT on the assignment/outcome/evidence relations; the
current analyst-only view inner-joins freezes and cannot supply the full missing-day
denominator. This command adds no grants and makes no analyst-role qualification claim.

The source is every immutable assignment, with LEFT JOINs for absent outcomes,
freezes and evidence. It checks the locked adjacent-day/pair calendar and Denver
`[06:00,24:00)` 64,800-second window; exposure, failure, fallback, rescue, zero and
null flags never remove a row. A draft has no invented assignments or denominator.
Missing calendar assignments and completed days without freeze/evidence remain
explicit. No efficacy, forecast, resource saving, carryover or power is estimated.

Exact PostgreSQL JSONB text preimages are independently hashed with the existing
migration-214 domains and UUID bytes. The final export must match its exact byte
hash, every assignment and frozen row, ordered evidence bundle and locked analyzer
identity. `status=reconciled` means the snapshot accounts for the locked assignment
calendar and has no missing evidence for already completed days. Future rows
remain `scheduled`; `export_verified=false` can coexist with a reconciled
snapshot. Neither status nor exit 0 means a completed pilot, accepted launch,
physical qualification or a revealed analysis. Reset/source details remain in their hashed evidence; this receipt does not
invent a reset classification from exposure or null outcomes.

Output is exclusive mode-0600, fsynced together with its directory, and conforms
to `daily-reconciliation-v1.schema.json`. Its hash is SHA256 of
`verdify-experiment-v2-daily-reconciliation-v1` + NUL + the sorted-key compact JSON
receipt bytes. Exit 0 means a structurally reconciled snapshot or explicitly
unlocked draft; 1 means missing assignment/day evidence; 2 means refusal. Preserve
all receipts and prior failures. Publication cadence, archive/backup custody and
observer owner must be bound in the prospective design; this source command does
not activate a scheduler, publish a notification or replace actual assigned days.
