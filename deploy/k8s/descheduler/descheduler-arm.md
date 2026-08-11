# HA-1.9 (#234) — descheduler: deploy (dry-run), PROVE exclusion, gated enforce

**Status:** Step 1 (dry-run) is laptop-root-autonomous (evicts nothing). Steps 3–4
(relabel + enforce flip) are **GATED — root executor** (they mutate live pods).
**Tracks:** #234 · **Epic:** #225 · **Design:** §2.4.

The descheduler keeps the multi-replica stateless verdify surfaces
(www/lab/api/mcp/planner/traefik) spread after node events. It MUST NEVER evict the
device-writer ingestor or ANY singleton. It ships DRY-RUN and is only armed after a
dry-run run proves the eviction set excludes every singleton.

---

## Step 1 — Deploy DRY-RUN (autonomous; evicts NOTHING)

```bash
K="ssh jason@192.168.30.32 sudo k3s kubectl"
# Apply the cluster-scoped, dry-run descheduler (own namespace; no ArgoCD coupling):
$K apply -k deploy/k8s/descheduler/
$K -n verdify-descheduler get cronjob descheduler -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].args}'
#   EXPECT: contains "--dry-run"
```

## Step 2 — PROVE the ingestor + singletons are NOT in the eviction set

Trigger one dry-run job immediately and read its log:

```bash
$K -n verdify-descheduler create job --from=cronjob/descheduler desched-dryrun-proof
$K -n verdify-descheduler wait --for=condition=complete job/desched-dryrun-proof --timeout=120s
$K -n verdify-descheduler logs job/desched-dryrun-proof | tee /tmp/desched-dryrun.log

# HARD GATE — the proof. These MUST ALL hold:
# a) The ingestor is NEVER named as evicted/would-evict:
grep -iE 'verdify-ingestor' /tmp/desched-dryrun.log && echo "STOP: ingestor in set" || echo "PASS: ingestor never listed"
# b) No singleton is named:
grep -iE 'verdify-(grafana|hermes-iris|mqtt|db-0|db-backup-exporter)' /tmp/desched-dryrun.log \
  && echo "STOP: singleton in set" || echo "PASS: no singleton listed"
# c) Any 'would evict' lines reference ONLY www/lab/api/mcp/planner/traefik:
grep -iE 'evict' /tmp/desched-dryrun.log
$K -n verdify-descheduler delete job desched-dryrun-proof
```

NOTE on the FIRST dry-run (before the relabel of Step 3): the L1 allowlist
(`verdify.ai/rebalanceable=true`) matches NOTHING yet, so the evictor set is EMPTY
— the proof trivially passes (no pod is a candidate, the ingestor included). After
Step 3 the allowlist matches the 6 stateless deploys ONLY; re-run Step 2 and confirm
the eviction set is still singleton-free before Step 4.

## Step 3 — GATED: label the stateless surfaces rebalanceable (one rolling restart)

verify the exact target and prerequisites a window. This restarts ONLY www/lab/api/mcp/planner/traefik
(RollingUpdate, PDB-protected). It does NOT touch the ingestor or any singleton.

```bash
$K apply -f deploy/k8s/descheduler/STAGED-rebalanceable-labels-patch.yaml
# Confirm the ingestor did NOT get the label (must be empty):
$K -n verdify-prod get pod -l app.kubernetes.io/component=ingestor -o jsonpath='{.items[0].metadata.labels.verdify\.ai/rebalanceable}'
#   EXPECT: <empty>
# Re-run Step 2 — confirm dry-run now lists only stateless duplicates, no singleton.
```

## Step 4 — GATED: ARM enforcement (drop --dry-run) — verify the exact target and prerequisites

ONLY after Step 2 (post-relabel) is green.

```bash
$K -n verdify-descheduler patch cronjob descheduler --type=json \
  -p='[{"op":"replace","path":"/spec/jobTemplate/spec/template/spec/containers/0/args","value":["--policy-config-file=/policy-dir/policy.yaml","--descheduling-interval=0","--v=4"]}]'
# Force-stack test (design §2.4): cordon a worker, scale a stateless deploy so 2
# replicas land on one node, uncordon, run a job, assert it rebalances WITHOUT
# evicting the ingestor or any singleton and WITHOUT a PDB violation:
$K -n verdify-descheduler create job --from=cronjob/descheduler desched-enforce-test
$K -n verdify-descheduler logs job/desched-enforce-test
# Oracle MUST stay 1 throughout (the ingestor must never be evicted):
$K -n observability exec deploy/prometheus -c prometheus -- \
  wget -qO- 'http://localhost:9090/api/v1/query?query=sum(verdify_esp32_writer_estab)'
#   EXPECT: "1"  — re-probe ≥60 min for durability.
```

## Rollback (instant, any step)

```bash
# Back to dry-run:
$K -n verdify-descheduler patch cronjob descheduler --type=json \
  -p='[{"op":"add","path":"/spec/jobTemplate/spec/template/spec/containers/0/args/-","value":"--dry-run"}]'
# Remove the descheduler entirely (never touches verdify-prod):
$K delete -k deploy/k8s/descheduler/
# Remove the rebalanceable label (one more rolling restart of stateless only):
$K -n verdify-prod get deploy -l app.kubernetes.io/part-of=verdify \
  -o name | xargs -I{} $K -n verdify-prod patch {} --type=json \
  -p='[{"op":"remove","path":"/spec/template/metadata/labels/verdify.ai~1rebalanceable"}]' 2>/dev/null || true
```

The descheduler never touches firmware, the DB, the ESP32, or any singleton. The
exactly-one oracle is the life-safety backstop throughout.
