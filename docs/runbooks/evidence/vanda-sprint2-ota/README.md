# Vanda sprint-2 OTA — staged artifact + evidence (center-zone moisture-guardrail)

Branch: `firmware/vanda-band-compliance-rearch`
Built: 2026-05-30 ~10:12 MDT (America/Denver)
HEAD: `e7781a3` (committed last-night bundle) + UNCOMMITTED worktree edits (the center-zone moisture-guardrail). `source_dirty=1`.

**STAGED, NOT DEPLOYED.** `make firmware-deploy` / OTA / promote were NOT run. `last-good` rollback target is UNCHANGED (`2026.5.17.1849.9353df5`).

## Staged OTA artifact

- FW version (archive label): `2026.5.30.1012.e7781a3-vanda-center-guardrail`
- OTA binary: `/mnt/iris/verdify-worktrees/firmware/firmware/artifacts/2026.5.30.1012.e7781a3-vanda-center-guardrail/firmware.ota.bin`
- sha256(firmware.ota.bin) = `7838568db1293e209f134428a58c7fb17fab122e6748f4bd609a615ba218ebf2`
- Size: 1,063,008 bytes. Flash 57.9% (1,062,599 / 1,835,008), RAM 14.4%. config_hash=0xbed099a9.
- Internal `fw_version` substitution = YAML default `2026.4.15.1` (firmware-check does not pass `-s fw_version`; deploy stamps the real version at OTA time).
- `firmware-archive-artifacts` run WITHOUT `PROMOTE_LAST_GOOD` → rollback target untouched.

## What this firmware changes (sprint-2, in scope)

ONE firmware behavior: the **Vanda center-zone moisture-guardrail exemption** (zone 3 only).
- `center_engage_threshold_kpa()` in `firmware/lib/greenhouse_logic.h` (canonical formula);
  mirrored inline in `controls.yaml` as `center_engage_kpa`.
- The house moisture guardrail (dispatcher-side) clamps `mister_engage_kpa` UP toward
  `vpd_high` during humid vent recovery for rot protection — correct for potted west/south,
  wrong for the bare-root center Vanda (humidity == velamen root wetting). The exemption
  `center_moisture_relax_kpa` LOWERS the effective engage threshold for the CENTER zone only,
  floored at `vpd_target_center + center_moisture_min_excess_kpa` (operator safety floor).
  West/south keep the unmodified house threshold (rot protection preserved).
- Ships at the no-op default `relax=0.0` ⇒ `center_engage_kpa == mister_engage_kpa` exactly
  ⇒ replay-diff vs the committed bundle = 0 rows.
- Two new planner/operator tunables with `cfg_*` readbacks (CLAUDE.md rule 6):
  `num_center_moisture_relax_kpa` / `cfg_center_moisture_relax_kpa`,
  `num_center_moisture_min_excess_kpa` / `cfg_center_moisture_min_excess_kpa`.
- New invariant **#21 (`check_21_center_relax_bounded`)**: the center exemption may only
  LOWER the center threshold (never raise above house, never drop below the operator floor,
  always strictly positive).

(The committed `e7781a3` bundle — dusk cutoff, econ-heat night suppression, absorption hold,
duty cap, hysteresis, fog/fert invariant, feed-hold — is documented separately in
`../vanda-band-compliance-ota/`.)

## Validations (all GREEN)

| Check | Result | File |
|---|---|---|
| `make firmware-check` (esphome compile) | SUCCESS, Flash 57.9%, RAM 14.4% | `firmware-check-build.log` |
| `make test-firmware` (unit + replay overrides) | 192 passed / 0 failed; replay self-test all ✓ | `test-firmware.txt` |
| Unit-test delta vs main (fb17f43) | 178 → 192 (+14, 0 fail) | `test-firmware-delta.txt` |
| `make firmware-invariants` | 0 violations over 193,525 rows, all 21 invariants | `firmware-invariants.txt` |
| `make firmware-replay-worktree OLD=main` | 2.54% divergent (intended; exit 2 expected) | `replay-diff-worktree-vs-main.txt` |
| `make firmware-replay-worktree OLD=e7781a3` | **0.00% divergent** (isolates the center change as a no-op) | `replay-diff-worktree-vs-e7781a3.txt` |
| migration 146+147+148 BEGIN..ROLLBACK replay | clean apply, no ERROR, nothing committed | `migration-rollback-replay.txt` |

## Replay-diff characterization (THRESHOLD_PCT sign-off basis)

Full detail in `replay-diff-characterization.txt`. Summary:

- **worktree vs main:** 4,919 / 193,525 = **2.54%** divergent. Exit 2 is EXPECTED under
  default THRESHOLD_PCT=0 (intentional-divergence change, CLAUDE.md rule 8) — not a failure.
- **worktree vs e7781a3:** **0 divergent rows.** Proves the center-zone moisture-guardrail
  change contributes EXACTLY ZERO mode/relay divergence (no-op at the shipped relax=0 default).
- Therefore 100% of the 2.54% is the already-reviewed last-night bundle (`e7781a3`); 0% is
  this sprint. Divergence confined to the dusk window (h18-21: 2,859) + night window
  (h00-06: 1,869) + carryover (h07-12: 191); net effect 2,355 fewer fog-minutes overnight/at
  dusk, no overnight chase-humidity heat, no daytime control change. Matches the e7781a3
  bundle characterization row-for-row.
- **Recommended operator sign-off: `THRESHOLD_PCT=3`.**

NOTE (cosmetic, not a gate defect): `firmware-invariants` prints "All 16 invariants passed"
while actually executing and gating 21 (#17-#21 added by the bundle + this sprint's #21).
The literal at `firmware/test/replay_invariants.cpp:326` should be updated to 21 in a follow-up.

## Migration replay note + IMPORTANT operator warning

`migration-rollback-replay.txt` replays 146+147+148 against the LIVE DB (which has 145
applied) inside one `BEGIN`..`ROLLBACK`. 147's ladder ordinal-stability self-check reports
binary_fallback (expected — needs 146's dual-written graded history first; the Phase-2 gate,
a coordinator/sequencing concern). The accuracy family repoints to 231 rows; zone weights
seed 5.

**⚠ MIGRATION 148 CONTAINS ITS OWN `BEGIN;`/`COMMIT;` (lines 55/173).** When 146+147+148 are
fed to a single `psql` session inside an outer `BEGIN`, migration 148's inner `COMMIT`
PREMATURELY COMMITS the whole 146+147+148 chain — the outer `ROLLBACK` then no-ops. This was
hit during evidence-gathering and accidentally committed all three to production; the live DB
was fully restored to its pre-replay state (verified: 0 residual 146/147/148 objects, plan_journal
anchors back to 106, control-path `fn_band_setpoints` healthy, achievable_envelope untouched).
The evidence transcript here uses a 148 copy with its inner `BEGIN`/`COMMIT` neutralized so the
rolled-back replay is faithful and leak-free.

Operator action item for the real apply: either (a) strip 148's inner `BEGIN`/`COMMIT` so the
migration runner owns the transaction boundary, or (b) apply 148 in its own separate `psql`
invocation — never chain it after 146/147 in one outer transaction. Routes through coordinator
(shared territory: `db/migrations/**`).

## Routing (CLAUDE.md shared territory)

- `verdify_schemas/**`, `db/migrations/**`, `grafana/**`, `.github/**`, `docs/backlog/**`,
  `ingestor/entity_map.py` / `mcp/server.py` edits in this branch route through coordinator.
- Schema/`mcp/server.py` touches → PR body must list service restarts (`verdify-mcp`,
  `verdify-ingestor`) per rule 7.
- New tunables carry `cfg_*` readbacks per rule 6 (done).
