# November 7 full triage: what W6 shipped, what's left, and the replanned tracks

Five independent verifiers triaged all **82 open issues** between about 02:55Z and 03:30Z on 2026-11-07, after release 4. Each one read every issue with its comments, checked the latest acceptance against live production with read-only evidence (prod DB, cluster, merged PRs, running digests), and left one dated comment on each issue.

- **Baseline:**
  - Release 4 runs at `9517acb6`, with migrations through 305: 303 = #1345, 304 = #1283, 305 = #1365.
  - Smoke passed 12/0, and device-monitor reports exactly one ESP32.
  - The controller still runs firmware `2026.11.6.0938.36c7630c`. The W6 build `2026.11.7.0133.9517acb6` is built, signed, verified and parity-checked for the #1009 OTA rotation, but not yet flashed.
- **Closed today:**
  - Verified live: #1143, #1271, #1283, #1312 and #1389.
  - Already closed on GitHub since the 11-06 triage: #31, #1125, #1300, #1330, #1334, #1340, #1344, #1348, #1353, #1357 and #1361.
  - Nothing was superseded.
- **Still open:** 77 issues, all in `planning/backlog.yaml` with a 2026-11-07 baseline. Seven are new records: #1424, #1431–#1435 and #1450.

## Findings that change the plan

1. **The lab publisher has failed since release 4.** The public-output guard refuses `/reference/ai-tunables`, because the #1392 setting help names a non-public crop. lab.verdify.ai stays on the 01:17Z build. Fixed in #1451, which also adds a regression test. Blocks #1329, #1332 and #1325.
2. **The dashboard web pin is behind main.** Live web is `c8b752c5`, which predates #1425 and the web part of #1437. That blocks #1322, #1339, #1345, #1349, #1354 and part of #1342. The catch-up build waits on #1443.
3. **The planner has written no plans since 2026-11-06 15:54Z.** The context gather took 115–120 s against its 120 s cap while CI saturated node7. With node7 capped it takes 97.8 s. A critical required-plan-missed alert also blocks the firmware preflight. Tracked in #1450.
4. **#1346 (brief cadence) can't pass its 11-07 gate.** Cortex `llm.primary.longctx` has returned 404 since 11-07 00:02Z, and the number check rejects "12". Both need a fix before the 24 h window starts again.
5. **#1352 (Slack) needs a code sweep.** Live code under `ingestor/` and `planner_graph/` still references Slack. That is on top of the 11-13 gate.
6. **Stale fields from the old `codex-ssa` field manager** keep `VERDIFY_SLACK_CONFIG` and the hermes-slack volume on `verdify-hermes-iris`, and `OPENAI_API_KEY` on `verdify-planner`. Blocks #1351 and #1352.
7. **The #1364 NTP soak window closes at about 09:55Z on 11-07.** The W6 flash reboots the controller, so it is scheduled after that.

## Tracks

Each track is the `execution_plan` sprint of the same id.

- **U0: unblock releases (software, now).**
  - Merge #1451, then repin and run the lab publisher. Covers #1329 and #1332.
  - Merge #1443, then build planner and dashboard-web, repin and sync. Covers #1322, #1339, #1345, #1349 and #1354.
  - Merge #1438, which carries the tail-prefix range fix (`EAST-SHELF-T1..T2`). Covers #1327 and #1328.
  - Sync #1449's NetworkPolicy.
  - Release the stale field managers (#1351).
  - Fix the brief's Cortex 404 and the number check (#1346).
  - Give the gather margin (#1450).
- **U1: the single W6 firmware flash and OTA rotation, the morning of 11-07.**
  - Run it after #1364's window closes and a required plan clears the preflight. Jason runs `promote` afterwards.
  - Then verify #299, #359, #367, #368, #369, #386, #390, #430, #1009, #1277, #1313, #1335 and #1342. #370 follows #367.
- **U2: live verification walk.** One signed-in Jason walk, plus in-cluster operator looks: #1130, #1132, #1135, #1181, #1186, #1333, #1337, #1338, #1341, #1347, #1355, #1356 and #1358. #1343 needs an attended L04 run-once.
- **U3: time-gated soaks.**
  - #1364 on 11-07.
  - #1346 24 h after its fix.
  - #1352, #1365 and #1434 on 11-13.
  - #1435 in mid-December.
- **U4: hardware, replant and irrigation bring-up (Jason, on site).**
  - Directly physical: #16, #45, #51, #52, #75, #298, #751, #1118 (camera greenhouse_2 returns HTTP 500), #1161, #1162, #1164, #1165, #1183, #1184, #1336, #1370, #1424, #1431 and #1432.
  - Behind those: #297, #398 and #1166.
- **U5: external owners.** #1001 (agents#4468), #1010 (network-infra tunnel row), #1113 (upstream renderer), #1363 (agents#802 descheduler) and #1397 (removing the native CI wiring).
- **U6: engineering backlog.** #1433 (read-model band envelope). #1360 stays parked by Jason's 11-06 decision and resumes only on his word.
- **Roll-ups:** #775, #1120 and #1325 close when their children close or carry.

The open-issue graph is [DEPENDENCIES.md](DEPENDENCIES.md), with 22 edges between open issues. The full 190-record graph is `planning/DEPENDENCIES.md`.

## What only Jason can do

- **The W6 flash window (U1):** be reachable, then run `promote`.
- **One signed-in dashboard walk (U2)** and **one attended L04 run (#1343).**
- **On-site work (U4).**
- **Resuming #1360**, if and when he chooses.
