# Audit mitigation delivery — 2026-07-11

Full remediation of the 2026-07-11 adversarial platform audit (firmware /
planner / ingestor / live-data), authorized and directed by Jason the same
day. This is the review record: what shipped, what was validated live, and
what remains open with owners.

Operating notes from the directive: the 7-hour DB outage RCA is CLOSED
(planned Longhorn maintenance, fixed); firmware deploys during active dev are
acceptable attended; the codified freeze gates are considered overly
restrictive for the current phase (they were made HONEST here — no more
vacuous passes — not stricter).

## Shipped (main, 2026-07-11)

| Commit | What |
|---|---|
| d9ca0f4 | schema: producer payload shapes + `alert_validation_failed` fallback |
| 8a07764 | ingestor: dispatcher crash-loop fix, fail-loud alerts, south soil pager revived |
| 98d98ca | db/197 + sweep: band-source-aware divergence; sweep sees acknowledged band alerts |
| c50c12b | planner: required-trigger retry cap + cross-midnight carry-over |
| 7e0d8c0 | VM-residue: iris_planner alert SQL backend, setpoint-server /setpoints (#447), MCP watchdog |
| 90d6ac8 | out-of-band writer-absent + telemetry-stall watchdog CronJob (P0) |
| 395add7 | CI gates: replay path filter, sw_* fire-and-forget, durable preflight override audit, twin compiles |
| 1be300c | live-truth irrigation fence invariants in the alert monitor |
| 213b5a0 | test: repo-relative API paths (VM residue) |
| 2c3c868 | db/198 dynamic sensor staleness (zombie class killed); planner_graph replicas 1 |
| b81e86d | db/197 column-order fix (applied to prod) |

## Validated live before/at delivery

- Migration 197 applied: `v_band_device_divergence` reads 0.01 °F / 0.000 kPa
  in `onchip_curve` mode (was a false 31 °F). Alert 7803 auto-resolves.
- Migration 198 applied: the 16 phantom-stale sensors read fresh; only the two
  genuinely stale feeds remain (`equipment.sntp_status`, `state.lead_fan` —
  real signal, left open deliberately).
- Both migration fixtures ran green on a scratch database before prod apply.
- 3 obsolete `irrigation_feedback_gap` alerts closed with an audit note
  (center irrigation retired by #450).
- Watchdog busybox pitfalls (date `T`-parse, wget flags) were discovered by
  running the exact image in-cluster before shipping.
- Fence-invariant SQL run against live `equipment_state`: no false positives.

## Post-deploy validation checklist (this session, after image promotion)

- [ ] dispatcher: no `Task setpoint_dispatch failed` in logs; cadence back to
      ~300 s; `setpoint_changes` volume drops from ~18k/day.
- [ ] no new `failed/unroutable` rows for `irrig_*_days_mask`.
- [ ] alert 7803 resolved; 16 zombie `sensor_offline` resolved.
- [ ] `verdify-writer-watchdog` CronJob runs green every 2 min.
- [ ] setpoint-server `/setpoints` returns 200 in-pod (#447 closed).
- [ ] MCP watchdog noise gone from ingestor logs.
- [ ] tonight's SUNSET (ledger 344942, due 2026-07-12 02:30–03:00 UTC)
      delivers; if it misses, `planner_required_plan_missed` now actually
      lands (fail-loud path) — check `alert_log` tomorrow morning.

## 48-hour bake follow-through (due after 2026-07-12 15:03 MDT / 21:03 UTC)

The rollback floor (`firmware/artifacts/last-good.ota.bin`, sha256
`08121f97…`) still points at the pre-recovery binary. After the gate passes
with a clean sweep, promote the candidate:

```bash
export VERDIFY_DB_BACKEND=kube
export EXPECTED_FW_VERSION=2026.7.10.1500.09ee886
make sensor-health SINCE='48 hours'   # 27 pass / 0 fail expected
bash scripts/archive-firmware-artifacts.sh 2026.7.10.1500.09ee886 --promote-last-good
```

Second-night dehum review (07-11→07-12 night) should be recorded alongside
(first night PASSED: RH 72→58 %, dew margin doubled, zero condensation-risk
hours). Then run the deferred rollback-floor refresh (#256) when the ESPHome
toolchain re-home lands.

## Remaining open, with owners

- **South beds physical check** — south_2 at wilt (20 %) with wall drip off
  since 05-29; the pager is now fixed but the WATERING DECISION is Jason's
  (device-affecting). The probes also stepped on 07-05 (heap-incident day) —
  verify placement/calibration while there.
- **PrometheusRule (out-of-cluster backstop)** — still owed by
  monitoring-stack (`observability` ns is outside this repo's RBAC); the
  in-lane watchdog covers the gap meanwhile.
- **Slack paging** — blocked on token-secret sealing (handoff §5.2); the
  watchdog and monitor page via alert_log/k8s Events until then.
- **API `/setpoints` latency** — >8 s from its own pod (pre-existing; the
  public evidence endpoint showed the same class at 13.3 s). Not addressed
  here; candidate for the query-bounds treatment the planner got.
- **Freeze-rule policy** — per operator direction the gates are treated as
  attended-mode guidance during active dev; consider codifying a
  `FIRMWARE_DEV_MODE` that relaxes cadence/bake gates while keeping the
  critical-alert and replay-evidence gates hard.
