# Vanda Band-Compliance OTA — staged artifact + evidence

Branch: `firmware/vanda-band-compliance-rearch`
Built: 2026-05-29 22:22 MDT (America/Denver)
Source SHA: `fb17f43` (working tree dirty — branch edits uncommitted, per orchestrator convention)

**STAGED, NOT DEPLOYED.** `last-good` rollback target was NOT promoted (still `2026.5.17.1849.9353df5`).

## Staged OTA artifact

- FW version (archive label): `2026.5.29.2232.fb17f43-vanda-band`
- OTA binary: `/mnt/iris/verdify-worktrees/firmware/firmware/artifacts/2026.5.29.2232.fb17f43-vanda-band/firmware.ota.bin`
- sha256(firmware.ota.bin) = `9ae13495f0d4de2e16427565c217c42bf6fd402b7c695e9f8622c7e4239e6cbc`
- Size: 1,061,616 bytes. Flash 57.8% (1,061,219 / 1,835,008), RAM 14.4%.
- Internal `fw_version` substitution = YAML default `2026.4.15.1` (firmware-check does not pass `-s fw_version`; the deploy step stamps the real version at OTA time).

## What this firmware changes (in scope)

Two firmware behaviors + one feed-hold scaffold, all DB-curve companions:
- **C1 — night econ-heat suppression (ENV-2):** the IDLE econ VPD-rescue heat path (`greenhouse_logic.h` ~1850) is suppressed during the night window `[night_start_hour=20, night_end_hour=6)`. Stops heat-to-chase-humidity once the DB curve raises the night VPD floor.
- **C2 — authoritative dusk cutoff (CYC-1/SAF-3):** `past_dusk_cutoff()` is a VPD-independent rail. All fog + climate mister/drip wetting ceases at/after `dusk_cutoff_hour=18` (sunset−2h, dispatcher-pushed), evaluated before any stress-extension. Both stress latest-hours capped at the cutoff. Survival cooling (SAFETY_COOL fog, `temp >= safety_max`) is explicitly exempt.
- **FRT-6/FRT-7 — post-feed absorption hold:** `feed_hold_active` blocks all clean wetting while `now < feed_hold_until_ms`; the post-fert clean flush is relocated to fire after the hold (irrig state 11). SAF-4 daily-volume hard ceiling + non-bypassable center duty cap + midnight runtime-counter reset also wired.
- New tunables all carry `cfg_*`/`num_*`/`sw_*` readbacks (CLAUDE.md rule 6).

IRR-3/IRR-4 (dawn rehydrate / midday drench) are explicitly DEFERRED as a marked stub — not shippable-validatable in the C++ replay harness.

## Validations (all GREEN)

| Check | Result | File |
|---|---|---|
| `make firmware-check` (esphome compile) | SUCCESS | (build log) |
| `make test-firmware` (unit + replay overrides) | 190 passed / 0 failed; replay self-test all ✓ | `test-firmware.txt` |
| Unit-test delta vs main | 178 → 190 (+12, 0 fail) | — |
| `make firmware-invariants` | 16/16 over 193,525 rows | `firmware-invariants.txt` |
| `make firmware-replay-worktree OLD=main` | 2.54% divergence (intended) | `replay-diff-worktree-vs-main.txt` |
| migration 145+146+147 BEGIN..ROLLBACK replay | clean apply, no ERROR, nothing committed | `migration-rollback-replay.txt` |
| `make lint` (ruff) | all checks passed | — |

## Replay-diff characterization (THRESHOLD_PCT sign-off basis)

`make firmware-replay-worktree OLD=main` → **4,919 / 193,525 divergent rows = 2.54%**.
THRESHOLD_PCT=0 default → harness exits non-zero (expected: this is an intentional-divergence
firmware change requiring operator THRESHOLD_PCT sign-off, CLAUDE.md rule 8).

NOTE on replay-harness time: `replay_emit` reads `local_hour` from the UTC hour of the
`ts` column (no tz conversion). The diff is a consistent OLD-vs-NEW comparison so the
characterization holds; the "hours" below are replay-hours, and the new defaults
(dusk_cutoff_hour=18, night window 20→6) are active on the NEW side via `default_setpoints()`.

Divergence is **entirely confined to the econ-heat-night + dusk-cutoff changes** plus their
second-order FSM-state carryover at the window boundaries:

- **Hour distribution:** all divergence in the dusk window (hours 18-21: 2,859 rows) and the
  night window (hours 00-06: 1,827 rows), with a stage-carryover tail (hours 07-12: 233 rows).
- **Net actuator effect:**
  - fog: **2,355 turned OFF**, 7 turned ON. The 7 ON = 6 SAFETY_COOL survival-cooling rows
    (2026-03-25 19:0x, dusk-exempt by design) + 1 FSM mist re-engage at hour 06 (window end).
  - heat1: **1 turned ON**, 0 OFF. The single ON (2026-04-22 04:34) is legitimate low-temp
    BAND heating (temp 64.1F, band 62.4-65.6) exposed when a futile night VENTILATE dry-stress
    vent correctly drops to IDLE. It is NOT econ-rescue heat (vpd 0.836 ≥ vpd_low 0.3, and the
    econ path is suppressed at night anyway).
  - heat2: 0 changes. No cooling/vent/fan logic changed except where dusk/night gates apply.
- **Mode transitions:** SEALED_MIST→IDLE 2,857 (wetting suppressed past dusk/at night);
  VENTILATE→VENTILATE 1,896 (fog dropped within an otherwise-unchanged vent decision);
  IDLE→SEALED_MIST 80 + SEALED_MIST→SEALED_MIST 79 (mist-stage machine resync after a
  diverged night); SAFETY_COOL→SAFETY_COOL 6 (survival fog now firing).
- **Hours 07-12: zero core-relay diffs** — 100% mode/mist-stage carryover.

Conclusion: the divergence is the intended dry-down behavior — ~2,355 fewer fog-minutes
overnight/at-dusk, no overnight heat-to-chase-humidity, no daytime control change. This is
the % an operator signs off as the THRESHOLD_PCT (recommend THRESHOLD_PCT=3 to cover 2.54%).

## Migration replay note (coordinator)

147's ladder ordinal-stability self-check reports 51.7% (75/145) `derivation=binary_fallback`
in the single rolled-back tx — expected, because the re-anchor needs 146's dual-written graded
history (the Phase-1 backfill) populated first. This is the Phase-2 gate, a coordinator/migration-
sequencing concern, not a firmware-build blocker. No ERROR; rollback confirmed nothing committed.
