# Verdify Release Checklist

Updated 2026-10-25. Production is the single `verdify-prod` environment, delivered
by the manual-sync Argo application `verdify-prod-dark` from `main`. Use this
checklist with [the operating contract](../AGENTS.md),
[service delivery](runbooks/laptop-operator.md#2-cicd-merge-publishes-promotion-pins-the-gated-sync-deploys-single-env)
and [signed firmware operations](runbooks/firmware-signed-artifacts.md).

Apply only the sections relevant to the requested change and honor the active
device, scheduling and writer-reconciliation holds. Release preparation or fresh
telemetry does not settle a writer handoff or expand the requested device,
credential or physical-action scope. The linked field/release worksheet records
the current sprint's survey and release status.

## A. Services and production configuration

**Source and CI**

- [ ] Preserve unrelated work; prepare the smallest owner change from current
      `origin/main` in an isolated checkout when needed.
- [ ] Run the relevant checks through the supported in-cluster CI path. Full
      suites, image builds and other intensive work run in `vallery`, not on the
      Mac. `make test` and `make check` can touch production; they are not generic
      offline checks. Use the existing private fixtures for isolated qualification.
- [ ] Require the exact PR head's `Verdify Platform / Argo PR CI` commit status
      to pass, plus any other enforced branch checks. Read commit statuses with
      `gh api repos/VerdifyConsultancy/verdify-platform/commits/<sha>/status`;
      this repository has no hosted Actions workflows or check-runs. Retain the
      selected test results and their limits; a skipped case is not acceptance.
- [ ] Land schema contracts and numbered migrations before or with their
      consumers. Run the relevant drift guards. For `verdify_schemas/**`,
      `ingestor/entity_map.py` or `mcp/server.py`, name affected service rollouts
      or a justified `Restart: none` in the PR/commit as required by rule 7.
      This records the delivery need; it does not call for an extra blind restart.

**Build, promote and sync**

- [ ] Match the successful exact-main build to its published image digests.
      The registered merge pipeline builds with Kaniko and publishes to the
      zot origin `registry.vallery.net`. Its five actuator-managed entries in
      `deploy/k8s/overlays/prod/kustomization.yaml` are build candidates; the
      later `release-pins.yaml` transformer determines their production images.
      Do not hand-edit those candidate entries or treat their update as deployment.
- [ ] When the change requires promotion, use
      `scripts/promote-release-pins.py` and preserve its source/digest receipt.
      Its default promotes all five related images, including the migration
      hook; review that complete change. Planner, setpoint-server and
      lab-publisher pins use their exact successful build outputs in the
      overlay's separate `images:` entries. Follow the current owner runbook
      for an image outside the registered build set.
- [ ] Before a requested production sync, render and inspect the complete
      pending diff with `kustomize build deploy/k8s/overlays/prod` and
      `kubectl diff`. Check every pending resource, the single ingestor's
      `Recreate` strategy, release digests, hooks and zero rendered Secrets.
      A full sync applies other pending drift too. A render-identical source or
      candidate-only change needs no sync or workload restart.
- [ ] For an authorized sync, use the exact revision and the atomic operation
      procedure in [laptop-operator §2](runbooks/laptop-operator.md#2-cicd-merge-publishes-promotion-pins-the-gated-sync-deploys-single-env):
      clear the prior terminal operation state and submit a full sync with
      `prune: false` and no resource selector. Immediately confirm the new
      operation's `sync.resources` is absent or empty. Stop and preserve the
      result if a selector, stale hook result or unexpectedly short resource
      set appears. Do not prune the database StatefulSet or either retained PVC.
- [ ] Verify Argo `Synced` + `Healthy` at the intended revision, newly executed
      migration/bootstrap/readiness hooks when applicable, and actual running
      imageIDs/source identities against the release receipt. Run the
      authenticated API/MCP and freshness checks with the expected API SHA and
      digest. Use passive socket/readback evidence for the sole existing writer;
      do not open a device client. Observe schema-validation logs after any
      authorized ingestor rollout for five minutes. Do not restart healthy
      services merely to complete the checklist.

## B. Firmware release and recovery

**Candidate and nonphysical qualification**

- [ ] Select an exact reviewed source revision and intended configuration.
      Identify affected relay/topology, dwell, sensor validity and irrigation
      contracts. Reuse prior receipts only within their original source and
      test coverage; the shared [field/release worksheet](../planning/execution/2026-10-25-safe-followthrough/field-release-worksheet.md)
      does not itself select or qualify a binary.
- [ ] Run the required native unit/invariant, causal and replay checks in the
      supported cluster lane. Bind the old and new source revisions and the
      corpus identity. For band-curve changes also retain the dedicated band
      replay result; ordinary corpus parity alone does not exercise that change.
      Every new tunable needs its `cfg_*` readback. Secretless dummy-credential
      compiler outputs remain nondeployable.
- [ ] Build/archive/sign the selected full SHA with the suspended
      `verdify-firmware-builder` path described in
      [signed firmware operations](runbooks/firmware-signed-artifacts.md).
      Record `release.json`, its reviewed SHA256, source/configuration/toolchain
      identities, binary hashes and signature. Preserve partial/failed archives;
      do not overwrite them or rebuild during the flash step.
- [ ] Use the independent verification-only Job to check the archived bytes,
      pinned public key, signature and exact reviewed manifest hash without
      device contact. Build, signature and verification success establish
      artifact custody, not physical compatibility, bake or recovery acceptance.

**Recovery, preflight and runtime contract**

- [ ] Identify the independently available recovery binary and its full hash,
      provenance, configuration compatibility and actual recovery evidence.
      A filename, matching version, copied metadata or successful compile does
      not qualify last-good. A provisional recovery artifact stays explicitly
      unbaked and follows the existing reason-logged provisional path; it does
      not become accepted last-good by being selected.
- [ ] Before a release, declare its heap/free-block and fragmentation bounds,
      loop timing, software-reset and watchdog-reset reporting, representative
      load, canary/soak duration, relay/cfg checks and rollback triggers. Carry
      the applicable #390/#428 requirements and affected control contracts into
      that exact candidate's packet. Missing physical topology or recovery
      evidence remains missing; it is not replaced with a green software test.
- [ ] Run the unchanged `scripts/firmware-deploy-preflight.sh` with the kube DB
      backend in the attended release window: no unresolved critical/legacy-high
      alerts, fresh climate/action telemetry and complete action proof, existing
      weekly OTA limit, and verified available rollback bytes. Forecast heat is
      context, not a new blocker. Follow the existing reason-logged override
      semantics only when the requested scope covers the relevant exception.
- [ ] Distinguish the preflight's last-good file-age check from actual bake
      evidence. Passing its 48-hour mtime check proves neither 48 hours of clean
      device runtime nor recovery. Preserve the real observed bake interval and
      its reset, heap, timing, fault and configuration records separately.

**Attended flash and acceptance**

- [ ] Flash only when the requested scope covers the attended firmware change
      and the preceding candidate/recovery checks are satisfied. Use the
      independent flasher's exact archived artifact and reviewed manifest hash;
      never substitute an unreviewed compile-and-upload command. Its upload
      alone does not execute the operator's post-flash health or recovery checks.
- [ ] Keep `VERDIFY_DB_BACKEND=kube` for the database-backed version wait and
      sensor-health checks. Verify `diagnostics.firmware_version`, not
      `last-good.version`, then capture the required relay/cfg, safety and
      single-writer evidence without an additional native API client. Execute
      the predeclared recovery path if a failure requires it and the release
      scope covers that action; retain the failed candidate's evidence.
- [ ] Verify the intended candidate's `band_track_fraction`,
      `dehum_vent_hold_enabled` and related config through current readbacks.
      Record actual door/screen state and other envelope conditions. Do not
      infer them from July firmware defaults, widen tunable bounds, or inject a
      setpoint merely to match an old checklist. Preserve stable bake conditions;
      record any intervention and its effect on the observation window.
- [ ] Retain exact pre/post firmware, configuration and raw-transition evidence,
      including starts/dwell, separate software/watchdog reset counts and the
      declared runtime canaries. Incomplete coverage is not a pass. Promote
      last-good only after the actual required bake (at least 48 hours), runtime
      criteria and recovery qualification are met; no ordinary CI or signature
      result promotes it automatically.

## C. Dashboards (Grafana)

- [ ] Edit `grafana/dashboards/`, then regenerate with
      `scripts/gen-grafana-dashboard-cms.py` and check the generated ConfigMaps.
- [ ] Run relevant branding/render checks, then verify live labels, units,
      ranges and data after a deployment that actually changes them.
- [ ] Keep reconstructed/desired bands distinct from consumed observations.
      `setpoint_snapshot` alone is not raw device truth; use the source/runtime/
      connection lineage in [the band contract](band-traceability-contract.md).
- [ ] Shared alert rules and probes belong in `jvallery/monitoring-stack`;
      a local `PrometheusRule` is not loaded by production Prometheus.

## D. Lab site (lab.verdify.ai)

- [ ] Apply [the web lane's checks](agents/web.md) for changed generators,
      content, theme, plugins or routes. Run intensive Quartz builds and broad
      suites in the supported cluster lane, not on the Mac.
- [ ] Verify the actual S3 content → publisher → validated PVC generation →
      nginx path with [the publication runbook](runbooks/lab-content-pipeline.md),
      including the live generation and affected pages/embeds. Merging an image
      or source change alone does not prove publication.

## E. Schema and migrations

- [ ] Add a numbered migration; never edit an applied migration. Classify it
      with the existing rollback-safety check before any execution.
- [ ] Do not wrap a self-committing migration in an outer rollback transaction.
      Qualify rollback/forward recovery using the supported isolated fixture;
      a live `irrigation-migration-check` is not a generic test invocation.
- [ ] Keep schema contracts/snapshots aligned through the supported development
      path. Do not run a production `db-dump` that overwrites `db/schema.sql`
      simply to complete this checklist.
- [ ] Bind migration and consumer image digests, ledger outcomes and runtime
      adoption to the same release. Declare the required service rollouts as
      above; do not add an unconditional post-migration bounce.

## F. Documentation and evidence

- [ ] Update the owning runbook and relevant issue with delivered behavior,
      exact source/build/runtime evidence and remaining limitations.
- [ ] Edit `planning/backlog.yaml` and regenerate its board/dependency views;
      do not hand-edit generated planning files. Update actual ownership records
      when ownership changes.
- [ ] Review the final diff and preserve unrelated files and recovery evidence.
      A documentation correction does not itself deploy, release a hold or
      satisfy physical acceptance.
