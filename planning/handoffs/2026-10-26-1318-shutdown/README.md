# Verdify shutdown handoff — 2026-10-26

Execution is paused at Jason's request. Leave existing cluster jobs, collectors,
scheduled workloads and production services active. No follow-on build, compiler,
rollout, OTA, device probe or cordon should be started until Jason resumes.

This handoff supersedes the [October 25 operational checkpoint](https://github.com/VerdifyConsultancy/verdify-platform/issues/775#issuecomment-6400478943).
It preserves the [full 41-issue review](../../reviews/2026-10-26-resume/README.md)
and later shutdown observations separately. Earlier status notes inside this
bundle retain their original timestamps; the sections below state the latest
verified outcome. Unmerged branch source is a preserved proposal, not main.

## Production evidence

- At **13:17:07 UTC**, Argo `verdify-prod-dark` was **Synced/Healthy** at
  `ca9120fa308c77b2c71054758d0ca143428bdea6`, with no operation. The documentation
  merge required no sync or rollout.
- Ingestor `verdify-ingestor-7bd9494655-4kzcw`, UID
  `955fec0b-7ee0-4872-bc13-78e428d0e659`, remained Ready on **schedulable node5**
  with zero container restarts and image
  `sha256:c10b34418daf526d33c1b0f3b1e59cddf4837814faa40e2fe6084bf4653d6aaf`.
  One existing established TCP/6053 socket was observed through `/proc/net/tcp`,
  inode `1451546504`. No new device socket was opened by this review.
- Writer stage is **halted: 12 completed, zero staged records; handoff unsettled**.
  Reason: `unapproved candidate appeared`; run `0ae45182806445649458568603c32fdb`.
  Exact file SHA256:
  `5fee179703f55cdd31d106bbcd4c9bcc1b8aa9512a756411f23ad269fb997663`.
  The ordinary policy hold is generation5, 15 fields, `bounded_hold_no_dispatch`.
  Fresh telemetry does not establish recovery or authorize clearing either hold.
- At **13:18:00 UTC**, climate and action rows were **3.6 seconds old**;
  setpoint snapshot was at13:17:46. At13:18:03 `/health` and `/health/detailed`
  returned HTTP200, `ok`/`ready`, DB reachable, climate/action age4seconds.
  `compliance_pct=0` is not physical/crop acceptance. API reports source
  `183af7a93fe236ab97f4556173a07195515b732a`; no new provenance claim is made.
- Firmware observed at13:16:40 remains `2026.10.2.0637.620a218a-wifi-bound`.
  All desired application replicas were Ready in `shutdown-workloads.json`.
  Firmware builder/flasher remain suspended. Existing backups/publisher schedules
  and collectors are untouched. Control-plane cordons are preexisting; no cordon
  was added or removed.

## Completed versus pending source work

1. **#1086 merged** at `ca9120fa308c77b2c71054758d0ca143428bdea6` (13:08:29UTC).
   The release checklist now follows the current native CI/GitOps/signed-artifact
   path and records survey-later/no-reference-meter. This is documentation only;
   #390 still needs a selected build, recovery and physical acceptance.
2. **#1091 remains open**, exact head
   `d7c25b0c620d537d56f938302d8be236a4d46fbb`, branch
   `codex/finite-contributors-20261026`. It changes12 aggregation predicates from
   NaN-only to finite checks. **Strict native qualification succeeded** at
   **13:17:56UTC**:140/140 actual-YAML cases, firmware tests, strict invariants,
   committed zero-threshold replay and expected causal baseline all passed.
   The replay traces match byte-for-byte. The baseline had32expected failures and
   38passes.18 custody files totaling412,448bytes are preserved here.
   Ordinary required PR CI was still pending at its13:16:50 snapshot. Full
   ESPHome secretless compilation was prepared but **not submitted**; both firmware
   targets still need that compiler receipt before source merge/release decisions.
   No firmware flash, physical release or jump/flatline diagnosis is claimed.
3. **#587/#639 WIP source preserved**, branch
   `codex/positive-exposure-fixture-20261026`, commit `c616e4702db5f3b4723f68e39ee7a408e2c4599b`.
   Only two files changed. Three new positive native SQL cases are implemented
   (multiple exposure, expired work, stale observation). Expected22cases/13of16
   reasons remain **unverified**, with no PR or new CI submission. Existing
   accepted result stays19cases/10of16. Real existing LOGINs, immutable guards,
   exact effects and disposable-cluster teardown are required. Review and real
   source-bound native CI are next, after resume. No live/runtime/historical-prefix
   or physical acceptance follows from these private synthetic cases.
4. **Full audit and ownership corrections preserved on this branch**, not main.
   All41open issues were reviewed. Live GitHub graph has25blocking edges and zero
   closed blockers after #1009's exact passive network prerequisite moved555→839
   and #1010's closed743 edge was removed. #846 is unrelated options-only work.
   Product OWNERSHIP now labels retired-rehearsal snapshot/A2–A4 as historical;
   all8retained PV/Longhorn/B2 recovery artifacts remain protected.43planning
   tests and rendering/integrity checks passed. No infrastructure change occurred.

## Cluster work to leave alone

At13:18UTC these existing workflows were active in `vallery/agent-fleet-ci`:

| Workflow | UID | Scope |
|---|---|---|
| `verdify-platform-pr-ci-q9rzq` | `68b41140-3e11-4abe-a961-d4b4102665d8` | #1091 required PR CI |
| `verdify-platform-pr-ci-lv49z` | `b49b9cf6-a7d5-48a5-9602-650daf6cdf80` | main ca9120fa validation |
| `verdify-platform-ci-sl7bh` | `92632257-9351-4b7e-852e-6d0318351185` | main ca9120fa automatic build/classification |

The separately submitted `verdify-finite-native-d7c25b0c-20261026-01`, UID
`88700998-b28a-4303-8363-3cb053dfbd00`, **completed successfully on its own**.
No job was cancelled, restarted, suspended, deleted or altered for shutdown.
Automatic GitHub-status/candidate updates may complete after this snapshot;
read current status on resume. Keep all unrelated cluster processing active.

## Resume order and constraints

1. Read current main/PR heads and the above existing workflow results. Collect
   durable outputs before pod TTL cleanup; do not duplicate a job because local
   monitoring stopped. Recheck #1091 required CI and the admitted compiler path.
2. Review the WIP positive fixture and submit its normal source-bound CI only
   after resume; preserve the explicit source/SQL/physical boundaries.
3. Review and merge this documentation branch via normal checks if still current.
   Candidate PRs1089,1087,1082,1075,1071,1065,1062,1061,1058 remain open and are
   **not approved for release**. Await the newest relevant candidate; compare
   exact source/coverage before superseding old candidates. Never promote a
   candidate merely to clean up the queue. Draft855 stays parked.
4. Continue #1010's actual trusted-header chain trace; its prior external blocker
   is closed but Verdify provenance is not verified. Passive #386/#410 evidence
   remains eligible without manufacturing an event.
5. Shared greenhouse survey remains **later**. No reference CO2 meter exists.
   #298→#398→#45→#297, genuine scientific inputs, analyst identity, physical #641,
   release #390 and unsettled writer handoff remain their own gates. The full
   review gives every issue's action and acceptance limits.

No extra ESP32 connection, unapproved node cordon, writer hold release, new
credential/RBAC, Slack send, production experiment, firmware upload or recovery
storage deletion. All original checkouts, branches and unrelated `.kube/` remain.
The Mac may stop; existing cluster execution continues independently.

`manifest.json` binds this sanitized evidence. Native runtime receipts and the
source branches are retained independently of `/private/tmp`; no Secret values,
raw production rows or credentials are part of this packet.
