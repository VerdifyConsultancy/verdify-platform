# Winter feasibility daily collector (#782)

This is the separate **observational** November 2–December 31, 2026 packet.
It does not draw assignments, write the database, contact the controller, or
claim hot/dry efficacy. The source-owned CronJob is suspended until a real
prospective target declaration, route-only panel, and registered instance are
staged. No physical inspection is a registration gate; NULL physical identities
remain explicit in the packet.

## Runtime contract

- The registered extractor and protocol are pinned to Git SHA
  `f77ef0005b8cf211130eebbd4b54d881d14c335a`. Stage only those exact
  committed file bytes and a `source-revision` file containing that full SHA.
  The extractor compares its own and the protocol's bytes to the registered
  hashes every run. The archived 48-field cfg source supplies the exact field
  order; it is not taken from a mutable runtime image.
- The daily Pod uses the existing `verdify-agent-secrets/AGENT_RO_DSN` secret.
  Live readback on 2026-09-27 showed its URL identifies login `agent` at
  `verdify-db.verdify-prod.svc/verdify`. `agent` is a non-superuser member of
  `agent_ro`/`pg_read_all_data`, with SELECT on all extractor relations. The
  extractor requires the registered login and a repeatable-read READ ONLY
  transaction. The Pod has no Kubernetes API token and egress only to DNS and
  the in-namespace DB. No secret value is copied into the archive.
- The 00:30 Denver CronJob collects the previous completed 06:00–24:00 local
  day. If a day was missed, each later run captures at most two missing days,
  including the latest due day. Late collections remain labeled late. Existing
  day files are never overwritten. The runner emits a 60-row manifest after
  collection; it validates prior artifact hashes. After day 60, the schedule
  can continue bounded catch-up until all days have an artifact.
- `verdify-winter-feasibility-archive` is a 10 GiB Longhorn workspace RWO PVC
  with two replicas and the storage lane's `standard` recurring group (daily
  snapshot and backup jobs). It holds the exact source snapshot, registration
  inputs, immutable day files, and manifests. The idempotent archive-init Sync
  hook binds the WaitForFirstConsumer PVC while the collector is suspended; it
  recreates on later full syncs, creates directories only, and carries no DB
  credential. Confirm actual backup objects
  and restore/readback separately before claiming off-cluster recovery.

## One-time staging before the first window

After migration 255 is live, generate the *new live* 4,320-bin crop-target
source and verify its production revision/readback. Use the current firmware
diagnostic and concrete route-only panel/cfg sources. Register from a detached
checkout of the exact collector SHA, not a moving `main` checkout:

```sh
git worktree add --detach /tmp/verdify-winter-f77 f77ef0005b8cf211130eebbd4b54d881d14c335a
cd /tmp/verdify-winter-f77
python3 research/planner-efficacy/winter_feasibility.py register \
  --output-dir /Users/jason/Documents/Codex/verdify-winter-package-2026/winter-2026-27 \
  --start 2026-11-02 \
  --panel-source /retained/panel-source.json \
  --crop-target-source /retained/crop-target-source.json \
  --cfg-schema-source /retained/cfg-schema-source.json \
  --firmware-revision '<current diagnostic version>' \
  --observer-role agent \
  --archive-id winter-2026-27-v1
mkdir -p /Users/jason/Documents/Codex/verdify-winter-package-2026/source/research/planner-efficacy/protocols
cp research/planner-efficacy/winter_feasibility.py \
  /Users/jason/Documents/Codex/verdify-winter-package-2026/source/research/planner-efficacy/
cp research/planner-efficacy/protocols/seasonal-decision-2026-09-27.md \
  /Users/jason/Documents/Codex/verdify-winter-package-2026/source/research/planner-efficacy/protocols/
git rev-parse HEAD > /Users/jason/Documents/Codex/verdify-winter-package-2026/source/source-revision
```

The PVC, archive-init hook and suspended CronJob are declared in
`deploy/k8s/components/winter-feasibility/`; apply them through the owning Argo
app after reviewing its full diff. Verify the init Job completed and the PVC
bound, then use the **unreferenced**
`stage-pod.example.yaml` once to copy `/Users/jason/Documents/Codex/verdify-winter-package-2026/source` and
`/Users/jason/Documents/Codex/verdify-winter-package-2026/winter-2026-27` into the PVC root:

```sh
kubectl --context vallery apply -f deploy/k8s/components/winter-feasibility/stage-pod.example.yaml
kubectl --context vallery -n verdify-prod wait --for=condition=Ready pod/verdify-winter-stage --timeout=120s
kubectl --context vallery -n verdify-prod cp /Users/jason/Documents/Codex/verdify-winter-package-2026/. verdify-winter-stage:/archive
kubectl --context vallery -n verdify-prod exec verdify-winter-stage -- \
  python /archive/source/research/planner-efficacy/winter_feasibility.py manifest \
    --instance /archive/winter-2026-27/instance.json \
    --output-dir /archive/winter-2026-27 \
    --as-of "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
kubectl --context vallery -n verdify-prod delete pod verdify-winter-stage
```

The manifest command checks the source revision and extractor/protocol bytes,
instance identity, and all three archived source hashes before writing its
60-row readback. Retain that receipt and the original private package with
hashes. Staging is a one-time operation; the Mac can disconnect for the 60
daily Jobs.

Unsuspend the CronJob in Git only after the exact staged source and instance
pass readback. Render/diff first; sync only the reviewed resources with prune
off and no unrelated migration/hook change. Verify Argo Synced/Healthy, the
live CronJob source pin, a first Job's read-only DB login, the day-file hash,
and Longhorn backup/readback. Do not mark missing or late days timely.
