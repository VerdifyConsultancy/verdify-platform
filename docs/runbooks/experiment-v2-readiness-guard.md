# Experiment-v2 Gate R / Gate P readiness guard

`scripts/experiment_v2_readiness_guard.py` is the metadata-only, non-actuating
guard for issue #749. It consumes one exact JSON packet, prints one
machine-readable result, and exits nonzero unless the requested gate/boundary
is safe. The canonical input example is
`tests/fixtures/experiment-v2-readiness/base-proof.json`.

The guard has two intentionally separate modes:

- `recovery / gate-r` can authorize only Gate R. It requires the corrected
  one-off backup, healthy core workloads, one stable current writer/lease,
  current 48/48 component truth, climate quorum, feature-off/empty-ID/vector-off
  and zero exposure. Writer/lease/generation stability must cover at least 30
  minutes. It records Argo, controller-owned backup, #686, provider, #424, and
  Gate P issue state but does not use those proof-only fields to authorize Gate
  R. The deterministic recovery packet overlay is
  `tests/fixtures/experiment-v2-readiness/recovery-gate-r.overlay.json`; it
  materializes against `base-proof.json` and binds the corrected one-off receipt
  plus fresh cycle-aligned HA contributor evidence.
- `proof` can authorize only Gate P. It additionally requires issue #747 full
  acceptance, an exact-pin Synced/Healthy successful Argo state, current
  controller-owned backup, and the complete named #641 Gate P prerequisite set.
  Four distinct packets form the one-use chain `gate-p -> baseline-before ->
  aggressive -> baseline-after`.

## Live input contract

The packet is a projection of existing read-only evidence. It must contain
metadata and hashes only—never bearer values, DSNs, cookies, session IDs,
Secret payloads, response bodies, or device command payloads.

| Packet section | Existing authoritative producer |
| --- | --- |
| `provenance`, `workloads`, `argo` | exact GitOps render, running workload/image inspection, and Argo application status |
| `backup` | corrected one-off backup receipt and controller-owned backup freshness/status |
| `climate.qualification_capture` | source-stamped 30-minute #748 HA qualification receipt or current gate capture |
| `climate.samples` | fresh, cycle-aligned Home Assistant entity events for every zonal and aggregate temperature/RH/VPD value |
| `alerts` | current `sensor-health-sweep.sh` / alert-log metadata projection |
| `evidence.component_grid` | `scripts/component_grid_capture.py` 48/48 result |
| `evidence.served_control_observed_424` | passive #424 served/control/observed capture |
| `evidence.writer_433` | current #433 writer/lease/generation and component-truth receipt |
| `evidence.authentication_686` | current-replica #686 acceptance receipt |
| `evidence.provider_preflight` | non-actuating current provider preflight |
| `dependencies` | source hashes and classified causal inputs for the component proof, selector, executor control contract, and locked outcome calculation |

The corrected one-off is an immutable accepted receipt, not a rolling backup:
its source pin is fixed to the reconciled #752 revision
`6b48dba7217438f5fdd7fb14fc8e067975cf1c35` and its completion must not be in
the future. Gate P separately requires a current controller-owned backup bound
to the requested Git pin and backup-age policy.

Do not substitute an arbitrary same-row `climate` query for the HA event
projection. The #748 audit found cycle-aligned HA events coherent while
asynchronous database rows could mix contributor membership. Every metric cell
therefore carries both `source_event_id` and `source_cycle_id`; all cells in a
sample must bind to the exact sample cycle. Cross-cycle membership fails closed.

Contributor status is computed independently from the four complete zonal
temperature/RH/VPD triplets. A triplet contributes only when every value is
finite, fresh, and cycle-aligned. Partial triplets and disagreement with
`declared_contributors` are ambiguous and fail. The aggregate timestamps must
advance, and values must match the included-zone mean within these source
tolerances:

- temperature: 0.10 °F
- RH: 0.10 percentage points
- VPD: 0.010 kPa

The existing causal source limits are 4 °F for temperature and 0.50 kPa for
VPD. RH has no independent source limit, so the guard does not invent one; RH
still must be finite, cycle aligned, and match its independently recomputed
aggregate mean. These thresholds are guard constants and any change is a source
change.
`active_probe_count` and `probe_health` are diagnostic-only: a `4/4 + OK + null
south` packet truthfully returns `3/4 degraded-pass`, excludes south, and emits
`diagnostic_contradiction:false_green_probe_health`.

South and hydro alerts remain mandatory packet members. They are nonblocking
only with classification `accepted_nonblocking_degradation`, `causal=false`,
and exact links to decision #748 and maintenance #751. Any unclassified alert,
causal alert, hydro reference in a causal source surface, or causal dependency
without a registered classification fails closed.

## Invocation

Use exact expected values from the activation/recovery source, not values copied
back out of the packet:

```sh
python3 scripts/experiment_v2_readiness_guard.py \
  --input /secure-metadata-path/gate-r.json \
  --mode recovery \
  --boundary gate-r \
  --expected-git-pin "$EXPECTED_GIT_PIN" \
  --expected-application-source "$EXPECTED_APPLICATION_SOURCE" \
  --expected-experiment-id "$EXPECTED_EXPERIMENT_ID" \
  --repo-root .
```

Gate P and every physical boundary use a persistent attempt-local state file.
The prior successful result's `receipt_sha256` must be copied into the next
packet's `guard.previous_receipt_sha256`; sequence numbers are 0 through 3.

```sh
python3 scripts/experiment_v2_readiness_guard.py \
  --input /secure-metadata-path/gate-p.json \
  --mode proof --boundary gate-p \
  --expected-git-pin "$EXPECTED_GIT_PIN" \
  --expected-application-source "$EXPECTED_APPLICATION_SOURCE" \
  --expected-experiment-id "$EXPECTED_EXPERIMENT_ID" \
  --repo-root . \
  --state /attempt-local/readiness-chain.json
```

Repeat with a newly captured packet for `baseline-before`, `aggressive`, and
`baseline-after`. A packet is current for at most 120 seconds. A later boundary
without the state file, a skipped/repeated sequence, a predecessor mismatch, or
a reused packet fails. The state file contains only mode, attempt UUID, next
sequence, and receipt hash; it is updated atomically only after a passing guard.

Exit codes are `0` for `pass` or `degraded-pass`, `1` for a well-formed unsafe
packet, and `2` for malformed input/arguments. Results never echo alert text,
external response bodies, or credentials.

The 30-minute qualification has two explicit lifetimes. A
`source_qualification` receipt is reusable for its exact application source,
while every gate still requires fresh cycle-aligned HA samples. A
`current_gate_capture` must itself have ended within five minutes of the packet.
An old current capture, an unstamped source kind, or asynchronous membership is
unsafe.

## Boundary failure semantics

Before actuation, any blocker returns
`block_before_actuation_preserve_attempt`. When the packet reports an open
exposure, any blocker instead returns
`close_exposure_first_revoke_nonbaseline_enter_emergency_hold_preserve_attempt`.
The consuming controller must use the existing atomic emergency-hold transition:
it closes the exposure first in the same transaction, disables component
authority, increments the lease generation, and preserves the failed attempt.
The guard itself never performs that mutation or a device write.

The source implementation intentionally does not include a universal live
collector. Production evidence currently comes from several separately scoped
read-only identities and tools. The caller must assemble their metadata-only
projections immediately before each gate/boundary; missing live input is a
blocker, never inferred from an older packet.

This source change also does not yet wire invocation into the existing
all-in-one direct-proof Job. Until that consumer integration invokes the guard
before Gate P and each baseline/aggressive/baseline transition (and honors its
failure action), issue #749 acceptance is incomplete. The separate recovery
hook likewise remains responsible for invoking `recovery / gate-r`; the guard
does not duplicate that resolver.
