# November 7 afternoon triage: the last 24 hours, what's left, and the replan

Four independent verifiers checked every open issue against live production, read-only, between 14:20Z and 14:35Z on 2026-11-07. None of them did the work they checked (L9). Each issue gets one dated comment with its evidence.

- **Baseline:**
  - `verdify-prod-dark` runs release 6 (`d925d764`, synced 09:53Z).
  - The controller runs firmware `2026.11.7.0133.9517acb6` (since 10:27Z, verified and pinned as last-good).
  - Release 7 is merged but not synced: #1478, #1479 and #1421. Pin PR #1481 and fix PR #1482 are open.
- **Closed by this triage:** #1313 (aborted-OTA drip accounting; the fix is on the device) and #1118 (vision analysis; the natural runs at 04:00Z and 07:00Z succeeded).
- **Reopened:** #1277. GitHub closed it automatically when #1478 merged, with no live check. It needs release 7 plus one C1 check against 9517acb6.
- **New records:** #1477 (deferred carry from #369) and #1480 (Cortex shadow reasoning truncation; fix in #1482).
- **Still open:** 72 issues.

## The last 24 hours (2026-11-06 14:00Z to 2026-11-07 14:20Z)

### Delivered to production

- **Release 4** (01:29Z): migrations 303–305 (#1345, #1283, #1365), plus W6 lanes A–E.
- **Release 5** (07:38Z): api, mcp, ingestor and planner at `00b53fe3`, carrying the parallel context gather (#1450), the read model for the achievable VPD band (#1433), the brief fixes (#1346) and the third-party CVE pins (#1458).
- **Release 6** (10:24Z): ingestor and cortex-planner `f08a0d17`, alert auto-resolve (#1472), planner `e7cc9183`, the hermes-agent CVE rebuild `1fa1f16e`, and the renderer with Chromium 156 `c297463e` (#1113).
- **W6 firmware flash** (10:26Z, verified 10:48Z): one signed flash carrying the #1430 batch and the #1009 OTA password rotation image. The sensor sweep was 26 PASS / 0 FAIL, and the device runs it as last-good.
- **Field managers** (13:43Z): the stale `codex-ssa` field manager was released (#1351/#1352). Hermes has no Slack or OpenAI env left; the planner has no `OPENAI_API_KEY`.
- **Dashboard web** `9c94c80c` (source `00b53fe3`) and **lab publisher** `219144b2` (the #1451 guard fix).

### Delivered to the repo

- 37 PRs merged.
- The repository moved to `jvallery/verdify-platform`, and CI moved to GitHub Actions on self-hosted runners (#1402, #1442, #1443, #1446, #1448 and #1456).
- 23 issues closed. The verifiers re-checked a sample and found one closed without evidence, #1277, which is now reopened.

### Highlights

- Three gated releases and one firmware flash in 12 hours. Each passed smoke 12/12, device-monitor (one writer) and a 30-minute soak.
- **New firmware health:** heap minimum 44 kB, no `esp32_push_failed` since the flash, relay chatter unchanged, and the #390 topology, cycling and outdoor checks passed with no override.
- **NTP soak met for #1364:** 1,343 rows, 0 invalid, longest gap between syncs 885 s.
- **Planner:** recovered from the 11-06 outage. MIDNIGHT and SUNRISE plans landed on time.

### Lowlights

- **The W6 flash ran unattended at 03:26 MST.** COORD had moved it to the attended morning at 02:48Z, but that ruling was lost when the session context was compacted. The device was healthy and COORD accepted no rollback. The fix is in place: re-read steer before any device write, and mark attended-only work with `ATTENDED-ONLY` inflight markers.
- **#1277 closed automatically** when its PR merged, with no verifier.
- **The Cortex shadow planner rarely answers:** one ok proposal in 24 h. SUNRISE ran out of tokens inside its reasoning, and a 00:01–00:15Z burst of transport failures is carried to cortex-ai-compute#487. Filed as #1480.
- **Release 7 stopped before its sync** when Jason paused the loops.
- **#299:** the mister re-fire fence does not survive a reset, and no follow-up issue existed. Now listed as a decision.
- **#1365:** no night reached the dry-out target in 5 nights. Minutes at or above the floor were 55, 0, 20, 115 and 155, against 240 needed.

## Tracks

Each track is the `execution_plan` sprint of the same id. The graph is [DEPENDENCIES.md](DEPENDENCIES.md), with 21 edges between open issues, mirrored as native GitHub blocked-by links.

- **R7: Release 7 (software, next attended session).** #1277, #1351 and #1480. Merge #1481 and #1482, promote the five release pins, then run the gated full sync, smoke, device-monitor, the ingestor watch and a 30-minute soak. #1479's ConfigMap change rolls api, mcp, the ingestor (Recreate), the planner, setpoint-server and the workers.
- **S1: Software backlog (agents).**
  - #1352: the Slack remainder. 52 non-doc mentions, the `slack_alert_runbooks` rename migration, and the two lab pages still return 200. The test-page receipt waits on Jason.
  - #1458: R26 exception rows, then the 11-08 02:15Z rescan.
  - #299: depends on Jason's decision about the fence.
  - #1432: the circulation fan spec.
- **V1: Jason signed-in walk and attended runs.**
  - The walk covers #1132, #1181, #1186, #1322, #1332, #1333, #1337, #1338, #1339, #1341, #1345, #1347, #1349, #1354, #1355, #1356 and #1358.
  - A light-hold test in the lights-on window covers #1335 and #1342.
  - One attended L04 run-once covers #1343.
  - One daytime Restart covers #1164.
  - #1009: Jason runs `promote` (D-71).
- **T1: Dated soaks.**

  | Issue | Gate |
  |---|---|
  | #386 | 11-08 05:00Z |
  | #1346 | 11-08 07:06Z |
  | #368 | 11-08 10:27Z |
  | #367, #369 (then close with carry #1477), #390 | 11-09 07:00Z |
  | #1365 | 11-13 |
  | #1435 | 12-15 |

- **H1: Jason on site.**
  - Sensors: #51 CO2, #751 south probe and hydro, #1184 PAR, #1183 YINMIK Wi-Fi and Secret seal.
  - Equipment: #1424 clamp.
  - Short checks: #1161 hose test, #1162 panel buttons, #1370 occupancy walk.
  - Replant and survey chain: #298 → #398 → #45 → #297, then #52.
  - Lines: #1165, #1336 and #1166.
  - #1431: the moisture-source experiment.
- **D1: Decision.** #1433 (see below).
- **X1: External owners.**
  - #1001: agents#4468, agents#6243, network-infra#602.
  - #1010: agents#6243.
  - #1363: depends on a decision.
  - #1364: network-infra `chronyc clients` readback, plus #864.
  - #1397: agents#6243, plus webhook and ConfigMap removal.
- **P1: Parked.** #370 (follows #367), #1360 (Jason's 11-06 decision), #1434, #1459 and #1477.
- **ROLLUP: Epics.** #16, #75, #359, #430, #775, #1120 and #1325.

## Decisions only Jason can make

1. **#299 mister fence after a reset:** accept boot time as the spacing after a reset (amend criterion 1), or require a zone fence that survives a reset in the next signed flash.
2. **#1132 setting ages:** accept the drawer and STALE ages as shipped for R1.1, or require an age on every row in the Settings list.
3. **#1346 brief cadence:** does an unchanged-facts skip count toward "an ok row every 15 min", or must the job write a row anyway?
4. **#1360 planner study:** resume it with Hermes as the AI arm, keep it parked, or close it as not planned.
5. **#1363 image IDs:** accept pod specs that pull the local registry copies (same digests), or require a node image purge and re-pull.
6. **#1432 circulation fans:** approve the fan spec once posted (count, CFM, switching, schedule). The dehumidifier pilot comes later.
7. **#1433 VPD envelope in SQL:** defer the SQL mirror until a SQL rollup needs it, or require the migration now.
8. **#52 seasonal care after the replant:** no seasonal change, or one reversible dormancy, light or dry-down setting for named species.
9. **#1181:** accept lane W's viewer look at `/settings` (03:24Z, all 247 rows) in place of a personal visit, or keep it on the walk.
10. **#1345:** let a named agent make the one real soil-target edit through MCP `soil_target_set`, or keep it on the walk.
11. **#75:** close the epic and carry #51 and #751 to #16, or keep it open.
12. **Release 7 timing:** when to run the gated sync. The sync is a device-write gate, and it Recreates the ingestor and rolls the planner.
