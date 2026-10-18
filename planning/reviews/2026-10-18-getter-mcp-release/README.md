# Getter diagnosis and MCP release — October 18, 2026

The MCP false safety-PASS correction is running on both production replicas. [PR #987](https://github.com/VerdifyConsultancy/verdify-platform/pull/987) merged at `7b48a210e8c44620320b929df6ad2ed8cf0d43c1` after exact-head native validation. It promotes only MCP digest `sha256:264f5fcb4da78a068c5bc64ec2b70d9efe122a473a1f92bb5d923565fb47a393`, built from `d298f1ecca8249a3898339045e69dc08e7a85ab1`. The [render check](render-verification.json) proves one Deployment image is the only semantic change; other pins and schema are fixed.

Both actual replicas are Ready with the expected imageID, baked source and exact server-file hash. [Installed runtime checks](runtime-verification.json) passed ten clearly labeled synthetic projection cases per replica: empty pending, failure precedence, explicit-all-pass only, and incomplete/unknown/null retained as incomplete. These execute the hash-bound installed projection without creating DB fixtures or importing the service. Actual HTTP checks separately passed readiness, existing Iris authentication and the exact23-tool inventory, and confirmed Iris still cannot call admin-only `outcome_kpi`. Smoke passed9/9 and device-monitor observed exactly one ESP32 writer connection. The real getter produced no episode result, so no genuine episode projection or positive admin dispatch is claimed; no admin route is configured and Iris/planner consumption remains absent.

The [recorded selective operation](release-operation.json) succeeded with exactly `apps/Deployment verdify-prod/verdify-mcp`, no prune and no hook launches. This reviewed migration-independent fallback was necessary because fresh directlaunch status was shadow/closed/feature-off, lease19, recovered attempt3; its settled-emergency predicate failed and full sync could replay unrelated study configuration. That original failed predicate is retained. The selective path preserves this exact status/attempt byte-for-byte. All15 Deployment UIDs survive; only MCP spec changed. API, ingestor and database Pod UIDs/container states are unchanged. Selective operations do not populate normal Argo sync history; actual operation/source/live custody is the deployment proof.

## Getter diagnosis

Six explicitly bounded read-only sessions used the same October16 adjacent-day window,2-second statement and500ms lock fences,4MiB work memory and no parallel workers. The original pilot remains unchanged. [Sealed diagnosis](getter-diagnosis.json) rehashes every query, output, error and receipt.

| Read | Actual result |
|---|---|
| Installed-function plan, no ANALYZE | Constant bounds materialize;53 climate chunks and14 setpoint chunks twice remain behind time join filters; planning660.984ms |
| Original/candidate body plans, no ANALYZE | Sole `bounds AS NOT MATERIALIZED` candidate prunes each windowed table to one chunk;252→101 nodes, estimated cost77632.82→5380.99; planning657.626→12.436ms |
| One full candidate body | Also timed out2s; guard passed, no episode result. This is not a runtime cure or installed getter change |
| Raw climate/action aggregation + setpoint intervals |214.317ms;2785 climate minutes,2785 action minutes,5628 intervals; every source column forced, counts only returned |
| Resolved prefix before episode/response analysis | Timed out2s; interval joins and helper fanout remain in this stage |
| First64 real-minute helper microread |149.637ms,64 selected and64 result rows; no interval/response join |

Materialized date bounds are a confirmed planning defect. Remaining execution cost is reproducible before episode/response logic in the resolved stage. The exact interval-versus-helper share is unresolved; the64-row timing is not extrapolated to2785 minutes. There is no old successful result for byte parity, and existing timestamp-tie/floating-order nondeterminism is not qualified. No dates/timeouts were widened, no migration installed, and no further getter queries are queued.

## Remaining acceptance and next execution

#410 stays OPEN with its original three clauses. There is no actual episode envelope, measured-zero daytime held admission, efficacy, crop-safety or planner-consumption proof. Legacy empty/null aggregate counts0 are not measured absence. Narrow safety pass is separate from episode disposition, confounds and evidence status.

Next source work is a separately selected immutable migration combining proven date-bound pruning with a narrowly justified resolved-stage optimization, preserving lateral cardinality, historical/current fallback, ties, all solar/response/safety/disposition semantics and actual parity. Positive outcome transport needs an existing authorized admin route or a separately scoped ordinary-planner contract; no credentials/audience expansion are manufactured here. Historical #639 prefix remains27/48 UNPROVEN, and physical/recovery prerequisites remain intact.

Canonical issue baselines and next actions are reconciled without altering acceptance, status, priorities, parents, estimates or dependencies:47open issues,143open clauses,26local active edges plus HA#878→#981. Original S3B40h /22–34h packet estimates remain historical planning, not measured effort or remaining hours. Final CI/custody and remote publication readback are linked from the [current campaign handoff](https://github.com/VerdifyConsultancy/verdify-platform/issues/775#issuecomment-6251679645). Full raw receipts are retained separately to avoid publication-triggered CI loops.

## Final custody publication

The release PR, release-main and planning-main validation completed successfully with direct terminal validation Pod/container exit0/restarts0 and full `ALL GATES GREEN` main logs. Both automatic main validations selected mode `validate`, all eight image-build flags false; no additional images or production change followed the documentation publication. [Release-main receipt](custody/release-main-verification.json), [planning-main receipt](custody/planning-main-verification.json), [canonical/native graph readback](custody/github-graph-verification.json).

The [complete byte-preserving archive](custody/full-custody.tar.gz) and [per-member hash manifest](custody/archive-manifest.json) retain original queries, successes/timeouts, chronological CI/rollout captures, service/projection and smoke/device outputs, source reviews and verifier scripts. Jason explicitly approved public publication of this sprint's report and full raw evidence. Credential scanning and the remote exact-byte archive readback are recorded in the final campaign handoff; credential values and Kubernetes Secret data are not exported. This custody-only branch creates no additional main validation or production release.
