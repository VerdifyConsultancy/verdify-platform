# Sprint W6: finish all software-only work (verdify-platform)

Set by Jason on 2026-11-06 after the full triage (#1423). This plan covers software-only work. Hardware, replant and walk-throughs that need Jason are listed at the end and are not sprint scope.

## Decisions in force (Jason, 2026-11-06)
- **Planners:** Hermes is authoritative. The Cortex planner shadow keeps running beside it, with no cutover planned. #1350 and #1366 are closed as parked.
- **Study:** #1360 is left as it is, with no new work.
- **Local-only (#1351):** applies to everything except the planner, which uses OpenAI through Hermes. The WAN drill is out of scope here; Jason runs it estate-wide.
- **Slack (#1352):** removal is finished by turning off Hermes's Slack output. The token is revoked after 2026-11-13, through the coordinator.
- **Band compliance (#1358):** the definition is confirmed. Mist & fog is the primary water limit (#1339). Stop buttons appear only on running rows, plus one Stop-all (#1349). Every signed-in user sees the snapshot (#1346).
- **Backup misting:** the L04 slots are re-enabled (#1271). L07/L08 wait for the replant (#1336). The OTA password is rotated at the next flash (#1009). #31 is retired.

## Rules for every lane
- Use a fresh worktree of origin/main and make few, larger PRs. Follow AGENTS.md: heavy work runs in-cluster, and no make test or make check on the Mac.
- Migrations are serialized and numbered in this order: **303 = #1345, 304 = #1283, 305 = #1365.** Each one is qualified against prod before merge.
- **Transfer freeze:** from DASH TRANSFER GO until the required check moves to GitHub Actions (#1397), no verdify merges. Lanes keep building and push when they're ready.
- Releases go through the release train (lane H). An independent verifier closes issues against live evidence.

## Lanes (all run in parallel)
| Lane | Scope | Issues |
|---|---|---|
| A, Dashboard UX | `--cause-prep` colour, phone separator, soil targets and slot rename (migration 303), primary-limit copy, running-row Stop and Stop-all, snapshot shown to every signed-in user, decisions.md rows for every decision above, regenerated screenshot sets in-cluster | #1354 #1322 #1345 #1339 #1349 #1346 #1337 #1338 #1347 #1355 #1356 |
| B, DB and read model | Day end at local date with a 25-hour fixture (migration 304); nightly dried-out flag in the 290 rollup and the ADR-0003/0004 amendments (migration 305); lab publisher reads plantings and gets repinned | #1283 #1365 #1329 #1332 |
| C, Firmware | Replay with an unrounded compare at threshold 0, aborted-OTA accounting, C1 pin; rebase #1095 for min-off/min-on parity; jump and flatline detection; removal of retired tunables; deny checks; then one flash with the OTA password rotation (Jason seals the value) | #1312 #1313 #1277 #299 #367 #368 #369 #370 #390 #386 #1009 |
| D, Platform | Experiment pods' idle CPU loop and their requests/limits; Hermes and descheduler images through the local registry (#802); real client IP; planner and lab-publisher pin bump (release 4) | #1389 #1363 #1010 |
| E, Local-only and Slack | Turn off Hermes Slack output; ask COORD to remove KSOPS verdify-slack; check a critical page reaches the dashboard; alert on planning failure; forward vision analyze stderr to logs; re-enable the L04 slots and read them back; Cortex egress fix and re-embed (#1422) | #1352 #1351 #1118 #1271 |
| F, Agent live checks | Every T1 check an agent can do without Jason: band Save confirmation, light hold and Auto, agent planting test, BFF circuit, irrigation-run and Graphs checks, daytime Clear, server resync | #1334 #1335 #1342 #1327 #1328 #1330 #1336 #1348 #1353 #1357 #1300 #1162 #1125 #1361 #1340 #1344 |
| G, CI and transfer | Cutover runbook, Argo re-key, GHA required checks, pg runner job, retire native CI | #1397 #1402 #1421 |
| H, Release train and verifier | Releases 4+ with pins, gated sync and checks; independent closes; time-gated checks (#1364 on 11-07, #1346 on 11-07, #1365 and #1352 on 11-13) | all |

## Out of sprint scope (Jason's physical tasks or walk-throughs)
- **Walk-throughs and attended runs:** #1130, #1132, #1135, #1143, #1181, #1186, #1341, #1343, #1370.
- **Sensors and meter:** #51, #751, #1184, #1183.
- **Replant chain:** #298, #398, #45, #297, #52, then #1166 and #1161.
- **Physical fix:** #1424 (heat1 clamp).
- **Other owners:** #1001, #1113.

## Done means
Every software-only issue above is closed against live evidence, or carried to an OPEN issue labelled `deferred`. Everything is released to prod and verified. The repo runs on GitHub Actions under jvallery. The only open items left are Jason's physical tasks and the dated soaks.
