# Combined-release live evidence harness

This scratch-only harness watches the four exact `verdify-prod` PreSync Jobs in
parallel so the 600-second migration/bootstrap evidence cannot expire before it
is captured:

1. `verdify-experiment-v2-restore-rehearsal` (wave -2)
2. `verdify-migrate` (wave 0)
3. `verdify-runtime-role-bootstrap` (wave 1)
4. `verdify-experiment-v2-credential-bootstrap` (wave 2)

It records only allowlisted Job/Pod/Deployment/StatefulSet fields, exact image
IDs, restricted-permission main-container logs, log SHA256s, fixed PASS/failure
marker assertions, four explicit non-secret `verdify-config` keys, and
allowlisted public API/MCP health fields. It never reads Secrets, never dumps a
ConfigMap, never invokes ArgoCD, and never changes the cluster.

Start it **before** the Argo sync. Fixed-name hooks that exist at startup are
treated as stale and a different UID is required. If the sync has already
started and the operator has independently matched the existing hook UID to the
new operation, add `--accept-existing`.

```bash
HARNESS=/workspace/verdify-platform/scratch/tmp/live-combined-evidence/capture-live-combined-evidence.sh
OUT=/workspace/verdify-platform/scratch/tmp/live-combined-evidence/run-<pin12>-$(date -u +%Y%m%dT%H%M%SZ)

"$HARNESS" \
  --release <exact-pin-commit-40-hex> \
  --output "$OUT" \
  --api-digest <64-hex> \
  --mcp-digest <64-hex> \
  --ingestor-digest <64-hex> \
  --migrate-digest <64-hex> \
  --orchestrator-digest <64-hex>
```

The five digests must come from the independently verified e0be-derived pin
commit. Do not infer them from a running pod.

Dry-run (digests are intentionally unnecessary):

```bash
"$HARNESS" --dry-run \
  --release e0be4e05edbbf54b954bb7f9f6a6a7bca91ffaaf \
  --output /workspace/verdify-platform/scratch/tmp/live-combined-evidence/dry-run-$(date -u +%Y%m%dT%H%M%SZ)
```

The terminal `summary.tsv` is PASS only after all four hooks, the exact pinned
API/MCP/ingestor and three feature-off worker rollouts, public health, and the
single-ingestor-pod invariant converge. Raw hook logs are mode 0600 and are
never emitted to the terminal. A protected-assignment heuristic quarantines a
suspicious raw log as mode 000 and fails the packet without printing the match.

Known limitation: this repository service account cannot read the
`verdify-ingestor-writer` Lease. The harness therefore proves the observable
single-writer boundary with `strategy: Recreate`, desired/updated/ready = 1,
exactly one non-terminating Ready zero-restart ingestor pod on the candidate
digest, and fresh public `service_ingestor`, climate, and climate-action-log
health. A fleet-Root operator can add the Lease holder/renew-time receipt as a
separate metadata-only artifact.
