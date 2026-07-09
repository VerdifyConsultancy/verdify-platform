# State of union — July 9 software recovery

Status: approved. Canonical record: `state-of-union.yaml`.

## Verdict

The repository foundations, 17-issue sprint, and eight-lane topology are approved for execution, but production is not green. Main CI/publish/manifests pass, core pods run, and API/lab/graphs return 200. Separately, the stable ESPHome connection still receives 69-value batches every five to six minutes, the required planner is tool-dead with critical alert 7676, the stale active band plan remains 0.25, climate mist waters south/west, numeric DLI is fabricated from invalid sensing, live DB solar semantics lag migration 186, replay has no source-backed outdoor age, the water-event ledger is stale, and dry-out lacks durable realized-effectiveness evidence. Argo is Healthy/OutOfSync and must be diff-reconciled before broad sync. Source credential fallbacks are removed, but issue #438 and the protected rotation gate block release.

## GitHub reconciliation

- Executable: #293, #299, #377, #383, #386, #389, #390, #410, #419, #424, #427, #428, #433, #434, #435, #437, and #438.
- Parent/follow-up: #430, #350, #365, #367, and #371.
- Closed as superseded: #37, #323, #397, and mixed PR #409.
- Deferred health issue: #436 vision CronJob/source/image pull.

## Execution sequence

1. Merge source credential cleanup while holding the protected production rotation gate; in parallel repair writer truth and core solar/cycle/night/replay evidence.
2. Serialize resource-accounting and DLI migrations/consumers, then repair planner delivery against the final shared contracts.
3. Integrate one firmware package for center-only climate, explicit irrigation, wall-only fail-closed feed, DLI unavailable baseline, and measured heap protection. Preserve/test existing mister/light/night behavior instead of adding unproven tunables.
4. With rotation explicitly authorized and verified, deploy schema/services, prove writer/planner, then atomically retire stale 0.25 intent.
5. Capture the topology-aware cycling baseline, pass every firmware/heap/alert gate, perform one combined OTA, and verify immediate plus settled behavior/rollback.

Exact fertilizer chemistry remains a commissioning step. It does not block software because automatic actuation must remain disabled until measured inputs exist.

Next stage: compile/dispatch the approved lane contracts; production release remains gated on explicit credential rotation authorization.
