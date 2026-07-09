# Software recovery change proposal

## Contract change

The recovery turns five approved black-box contracts into one release transaction:

1. `device-writer-reconcile`: connection generation, canonical readback identity, normalized desired/observed comparison, bounded fair delivery, and truthful terminal lifecycle.
2. `planner-delivery`: tool-level health, actual terminal action, strict bound intersection, one effective expiring plan, neutral failure, and correct outdoor forecast scoring.
3. `firmware-control-policy`: one relay resolver, center-only climate wetting, intentional disabled dormant zones, wall-only commissioning-gated feed, solar-night safety, and preservation of already-effective dwell behavior.
4. `evidence-contracts`: unavailable DLI, device/DB solar/VPD parity, raw-transition cycling, realized dry-out episodes, and provenance-bearing equipment/resource evidence.
5. `runtime-release-verification`: schema→services→intent cleanup→one OTA, with immutable artifacts, rollback, immediate probes, and settled re-probe.

Issue #438 adds a release invariant: source may reference credential locations and injection modes only; the exposed application credential must be rotated and old authentication rejected before deployment.

## Proposed decomposition boundary

- `security-controller`: #438 source cleanup, caller inventory, protected rotation runbook and release gate. No product behavior edits.
- `data-evidence`: #293, #424, #389, #410 evidence, #435 non-firmware consumers, and #437. Owns serialized migrations, DB/MCP/API/site evidence, and no firmware control files.
- `device-writer`: #433. Owns ingestor writer/readback/dispatch/confirmation paths; coordinates registry contract but not planner or firmware behavior.
- `planner-delivery`: #427. Owns Hermes/MCP/planner lifecycle/materializer/forecast/context paths; depends on availability and writer cadence.
- `firmware-recovery`: #419, #428, #434, firmware slice of #435/#410, and preservation/regression slices of #299/#383/#386. Exclusive owner of firmware source/tests/corpus exporter for the combined image; no speculative new anti-chatter tunables.
- `release-controller`: #390 and #377 plus integration/deployment verification. Owns no product implementation paths.

Prompt 06 must turn these into path-exclusive contracts and split shared issue acceptance explicitly; it may merge tightly coupled firmware work but must not create parallel edits to the same firmware YAML/header/test files.

## Deployment and rollback

Database migrations are numbered and applied serially, with migration 189 reserved. Services are digest-pinned and verified before the stale plan row is touched. The stale row is retired atomically after fixed consumers are live. The firmware package is compiled and replayed from the exact integration head, one rollback binary is retained, and OTA occurs only after critical alerts, heap, cycling, weekly, and bake gates pass. If any immediate or settled invariant fails, revert the affected service/intent/image using the pre-recorded artifact and preserve evidence.

## Verification boundary

Repo-green is insufficient. Acceptance requires GitHub checks, Argo/digest truth, pod/service health, DB contract/version truth, writer/planner logs, device readbacks, relay attribution, alert state, heap/reset state, cycle/runtime deltas, and a settled re-probe from the production vantage.
