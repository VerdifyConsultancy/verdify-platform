# Step-test qualification machinery (audit §8.3)

> **Historical/platform-v2 only — not the current #581/#588 launch path.**
> ADR-0010 replaced this 96-transition generalized-vector gate for the first
> physical study with setter-prefix replay/HIL, supervised baseline↔template
> canaries and a 48-hour A/A rehearsal. `VERDIFY_POLICY_VECTOR_MODE` remains
> `off` for that fast path. Preserve this machinery for #586/#638; do not run it
> or cite it as fast-path readiness.

Historical issues #584/#588 and epic #581. Spec: audit §8.3
(`docs/research/planner-efficacy-current-firmware-2026-08-14.md`) — the
pretrial gate that decides whether a two-hour wall-clock washout (and
therefore daily switching) is admissible for the 30-day randomized phase.

Pieces (this PR):

| Piece | Where |
|---|---|
| Regime classifier + locked 24-cell layout | `verdify_schemas/experiment_regimes.py` |
| Deterministic FIFO scheduler worker | `ingestor/tasks/experiment_qualification.py` |
| Claim/resolve/ledger SQL guards | `db/migrations/212-qualification-scheduler.sql` (on 207/208) |
| Frozen settling analyzer + CLI | `qualification/settling.py`, `python -m qualification` |
| Specification instance template | `qualification/qualification-spec-v1.template.yaml` |

## Flow: spec hash → create → arm → worker → analyzer → binds to A/A

1. **Author the specification instance.** Copy
   `qualification-spec-v1.template.yaml`, resolve every `TO-LOCK` value
   (revisions, template content hashes, study/greenhouse ids, disturbance
   columns, diagnostic sign-off), commit it, and hash its exact bytes:

   ```bash
   cd research/planner-efficacy
   uv run python -m qualification spec-hash qualification/<instance>.yaml
   ```

   §8.3: the spec freezes the 24 edge/regime FIFO cell queues, four ordered
   slots per cell, the deterministic scheduler, all eligibility predicates,
   the 45-local-day window, the response model + diagnostics, and every
   revision — but never invents 96 future UTC ranges.

2. **Create the experiment (kind=qualification).** Load the three templates
   (protocol loader), register the six-edge graph, materialize the 96
   `qualification_transition_slots` rows (24 cells × 4 ordinals, cell layout
   `cell_index = edge_index*4 + regime_code`), and set
   `protocol_sha256 = <spec hash>` plus `permitted_producers` from the spec.
   `fn_experiment_transition(draft→locked)` enforces 3 complete templates,
   6 edges, and exactly 96 slots.

3. **Arm and run.** `locked→armed→running` (frozen revisions required at
   arm). Delivery mode must be `VERDIFY_POLICY_VECTOR_MODE=live` with
   `VERDIFY_ACTIVE_EXPERIMENT_ID` set — the worker is deliberately inert in
   `off`/`shadow` (the protocol needs real device echoes).

4. **The worker drives §8.3.** Every 60 s
   `ingestor/tasks/experiment_qualification.py`:
   - initial **positioning** (fixed 3 h: 2 h settle + 60-min pretrace) onto
     the first needed source vector;
   - at every boundary, **claim** the next FIFO slot when the eligibility
     predicates pass (fresh inputs, no override, gap-free 60-min
     source-content pretrace by content hash, regime match) via
     `fn_claim_qualification_slot` — the immutable analyzed assignment (6 h)
     commits before any outbox row — then proposes the target template
     through the sole arbiter path;
   - otherwise chain 15-min **identity_hold** assignments, or **reposition**
     when the current source has no open work;
   - resolve finished steps via `fn_resolve_qualification_slot`
     (safety/override event, delivery failure, or missing post-step data ⇒
     `failed`, never replaced; terminal states never reopen);
   - ledger every move (`control_transition_ledger`), including `skipped`
     boundary decisions.

5. **Extract + analyze.** After the window (or 96/96 resolution), extract
   per-transition traces (indoor VPD kPa, indoor temp °F, nine-device
   duty device-min per 15-min bin; 24 post-step bins each; identity
   confirmation latency from `policy_exposures`) into the analyzer input
   JSON, then:

   ```bash
   uv run python -m qualification settle \
     --input transitions.json --output qualification-result.json
   ```

   The frozen analyzer fits the disturbance-adjusted first-order model,
   computes each settling time, applies the locked diagnostics, and gates on
   the **maximum** settling time (≤ 2 h) plus identity-within-120 s in every
   transition. Exit code 0 = gate pass.

6. **Bind the result hash.** `result_sha256` from the output is recorded on
   `policy_templates.qualification_result_sha256` and each passing
   `policy_template_edges` row. The `aa` experiment's arm gate
   (`fn_experiment_transition`) requires that bound passing hash — a failed
   or absent qualification blocks A/A and the randomized phase. A failed
   gate is never waived: author a new spec version and rerun (§8.3).

## Tests

- `tests/test_experiment_qualification_regimes.py` — classifier goldens.
- `tests/test_experiment_qualification_scheduler.py` — worker state machine
  vs fake DB, migration-212 guards.
- `tests/test_experiment_qualification_settling.py` — analyzer synthetic
  traces (known settling, >2 h failure, identity-late failure, hash
  determinism).
- `tests/test_experiment_qualification_spec.py` — spec template ↔ code
  constant agreement.
