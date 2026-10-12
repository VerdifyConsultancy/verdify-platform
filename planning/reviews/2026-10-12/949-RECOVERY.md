# #949 current ordinary control decision — October12

Recommendation: restore current source-owned ordinary policy through the existing confirmed-halt archive and a fresh bounded run. Keep the12-command ceiling and one ingestor connection. This is ordinary policy recovery, not GateP or a physical experiment. Existing delivery authorization may already cover it; a file called approval is a technical capability bound to one exact snapshot, not a new discretionary human gate. If the current scope is planning-only, surface the choice to resume ordinary control versus intentionally preserve its hold.

## What is actually stopped

Run `76d18133852b490680bce2639e6d8397` started `2026-10-06T23:43:24.293556+00:00` under `81d76a4c3d35a925ebd2438d89099d1f657b3f8f`, pod `verdify-ingestor-6d95d5fc9f-cscxw`, session `46fc0e1997bb4432b84e5d930cdc4048`, generation3. It halted for **unapproved candidate appeared**, expiry `2026-10-07T00:13:24.285569+00:00`. Its12 exact native requests were confirmed by `2026-10-06T23:45:05.15111+00:00`. Pending records are empty. The current-generation readbacks still equal those completed effects under the existing numeric/seconds comparator. Untouched baseline changes are only tiny float representation differences within the source `_equal` tolerance. There are no later non-ESP32 request rows on the12 fields.

Current pod `verdify-ingestor-69754948bf-mzdm5` runs source `f48c6bf0257b25ed2820689154ca3502507129c6`, session `4517326b3ee44dd6ac4f4d5bb0e371a4`, generation1. Preview captured `2026-10-12T17:38:39.386187+00:00` lists17 desired changes. **This audit snapshot must be recaptured before a live approval** (six-minute freshness fence). Current plan expires `2026-10-13T12:30:00+00:00`.

| Desired parameter | Resolved requested value | Current native cfg readback |
|---|---:|---:|
| `vpd_target_south` | 1.15 | 1.0199999809265137 |
| `vpd_target_west` | 1.05 | 0.7799999713897705 |
| `vpd_target_east` | 1.18 | 0.9700000286102295 |
| `vpd_target_center` | 1.09 | 0.949999988079071 |
| `cool_exit_hysteresis_f` | 1.96 | 1.600000023841858 |
| `cool_stage2_over_high_f` | 1.6425 | 1.7725000381469727 |
| `dwell_gate_ms` | 270000 | 210000.0 |
| `min_fog_off_s` | 60 | 54.0 |
| `min_fog_on_s` | 59.25 | 66.0 |
| `mister_all_delay_s` | 90 | 75.0 |
| `mister_all_kpa` | 1.154 | 1.207 |
| `mister_engage_kpa` | 0.954 | 1.04 |
| `mister_pulse_on_s` | 41.25 | 33.0 |
| `mister_vpd_weight` | 1.83333 | 1.7 |
| `temp_hysteresis` | 1.96 | 1.600000023841858 |
| `vpd_hysteresis` | 0.2075 | 0.1875 |
| `vpd_watch_dwell_s` | 67.5 | 52.0 |

## Supported route and boundaries

The v2 `confirmed_halt_forward` mechanism exactly matches a single fully confirmed1–12 field halted stage with empty records and no rollback history. Preserve raw approval/state and original full Pod custody; re-export actual12 rows; capture fresh48/current identity/complete current plan; use `scripts/prepare-writer-stage.py --confirmed-halt-custody ... --native-requests ... --original-writer-custody ...` to create the local archive-only manifest. After exact Pod/file guards and atomic installation, the ingestor persists immutable halt history and leaves the old halt active. Then capture another fresh preview and prepare a **different** ordinary approval. The writer rechecks history and current source/session/generation/lease/plan/readback guards before stages of at most12, with real confirmation between stages.

The original full Pod readback already exists in `docs/handoffs/s1-2026-10-05/evidence/949-source81/final949-terminal-pod.json` with UID `237002d4-5fb9-450f-8fe2-e266c969a1e1`. The authoritative original generation3 preview is preserved inside the current stage approval/state. The published pre-transition preview is generation1; never relabel it as3. The actual raw custody bundle and original-writer receipt still need to be assembled and validated; filtered audit JSON is not a substitute for raw file hashes.

An ordinary<=12 batch cannot bypass this terminal halt. Replacing only the approval is rejected for run-ID mismatch. Automatic new-stage admission cannot clear halted history. Same-stage rollback is bound to the original process/generation and must not be attempted blindly from this replacement pod. Deleting state, replaying old requests, widening the cap, or relabeling historical confirmation is outside the supported path.

`docs/runbooks/writer-staged-reconcile.md` explicitly says there is no facility inspection precondition. The autonomous convergence runbook keeps one connection, the12 cap, exact-source/current48 proof, real plan transition and two steady hours. No OTA, physical GateP execution, diagnostic toggle, or extra ESP32 client is needed or authorized by this packet.

## What Jason actually needs to decide

If not already covered by the active delivery instruction: whether to resume the already-selected source-owned ordinary Iris/crop/lighting/activity/safety policy now, or intentionally leave the controller hold in place. No new crop strategy, experimental arm, physical inspection sign-off, or cap exception is required by the supported recovery. Custody preparation, local validation, obtaining fresh previews, supported archival, confirmation verification and the steady observation are implementation work once ordinary recovery is in scope. Missing/changed evidence is a technical blocker to resolve, not a reason to ask Jason to waive it.
