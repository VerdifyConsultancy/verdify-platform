# Fleet CI/CD convergence evidence — 2026-08-02

**Verdict: `BLOCKED_PLATFORM`**

**Lifecycle: `active-delivery`**

**Estate lane:** `jvallery/agents#3088`

**Repository lane:** `VerdifyConsultancy/verdify-platform#561`

**Proof PR:** `VerdifyConsultancy/verdify-platform#559`

**Broadcast:** `[[codex-broadcast:fleet-cicd-convergence-20260802T180658Z-88f1eab5]]`

This is a point-in-time reconciliation of the default branch, GitHub, the
centrally rendered Agent Fleet contract, the live CI WorkflowTemplates, and the
two Argo-delivered runtime surfaces. It does not authorize or perform a
production sync. Exact command output and the immutable final PR head are
recorded on issue #561 and PR #559.

## Scope and safety boundary

- Remote default branch re-fetched at `2026-08-02T20:59:40Z`: `main` at
  `eb3ac9cf33001fa6c5271412284382b36c6abf9e`.
- Issue branch: `docs/fleet-cicd-e2e-2026-07-31` in dedicated worktree
  `/workspace/verdify-platform/scratch/worktrees/fleet-cicd-e2e-20260731`.
- The shared checkout was intentionally not used: it was on
  `auth/uniform-github-app-7570d1c`, ahead of `origin/main`, with an unrelated
  untracked `.mcp.json`.
- Filesystem and network access were available. Approval policy was
  non-interactive. The pod had namespace reads in `agent-fleet-ci`,
  `verdify-platform`, and `verdify-prod`, but could not create CI Workflows or
  read/patch Argo `Application` objects. No Secret object, value, or annotation
  was read; only names and key names were handled through the safe inventory
  surface.
- Production is the sole greenhouse environment and includes the only device
  writer. Its Argo application is manual-sync and `prune:false`. No firmware,
  device, database, DNS, storage, Secret, workload, or Argo mutation was made.
- PR #559 has no merge SHA. The required context remains red, no protection
  bypass was used, and no GitHub PR review had been submitted during this
  evidence pass. Independent read-only agent audits were resolved in-tree
  before the final head was produced.

The lifecycle is `active-delivery`, not validate-only: this repository owns
the running greenhouse API/MCP/ingestor/planner stack and publishes multiple
Zot images, plus an isolated static Lab stage.

## Live workflow and enforcement inventory

There are no repository-authored GitHub Actions workflows on `main`.
`.github/workflows/` is absent and repository tests require it to stay empty.
GitHub registers only its synthetic `Dependency Graph` workflow at
`dynamic/dependabot/update-graph`; its trigger and runner are GitHub-managed,
it has no repository caller, and it consumes no repository Actions runner.
The eight retired workflow files and their replacements remain inventoried in
[`zero-paid-runner-ledger.md`](zero-paid-runner-ledger.md).

Actual validation and publishing run in namespace `agent-fleet-ci`. The
generations and storage/credential-name bindings below were re-probed at
`2026-08-02T20:59:03Z`:

| WorkflowTemplate | Caller / trigger | Work performed | Runner identity | Deadline / cleanup | Artifact and cache behavior | Credential references (names only) |
| --- | --- | --- | --- | --- | --- | --- |
| `verdify-platform-pr-ci` generation 13 | The centrally rendered PR sensor filters this repository, base `main`, and actions `opened`, `reopened`, `synchronize`; exact live Sensor reads are RBAC-forbidden | Reports pending, performs trusted precheck, calls `verdify-platform-ci/validate` for the exact head, then reports the terminal required context | Kubernetes SA `argo-ci-workflow`; no node selector; observed failed pod on `vm-k3s-node4`; no ARC runner | 3,600 s; `podGC: OnPodSuccess`; TTL 24 h success / 48 h failure; no supersession/cancel synchronization | No GitHub artifact/cache and no Argo artifact repository | `agent-fleet-ci-github-app` for precheck/read; `agent-fleet-repo-github-app` for status |
| `verdify-platform-ci` generation 33 | The centrally rendered push sensor filters non-deleted pushes to `refs/heads/main`; PR CI also calls its `validate` template | Classifies push; runs `make ci`; on `build`, runs the full seven-image core matrix and pushes prod pins directly to `main`; on every non-`skip`, runs all four Lab builds/probes and opens a Lab-stage pin PR. All eleven direct build calls pass `allowed_publish_scopes=verdifyconsultancy` | Kubernetes SA `argo-ci-workflow`; validate has no node selector; build tasks call `repo-build/build` | 7,200 s; `podGC: OnPodSuccess`; TTL 24 h success / 48 h failure; no top-level synchronization | The push/build caller declares one shared 20 Gi rebuildable Longhorn RWO `workdir` plus `publisher-state` `emptyDir`; no `volumeClaimGC`, Argo artifact repository, or Kaniko cache is declared. Build outputs are parameters. The PR caller has no PVC and calls only `validate` | `agent-fleet-ci-github-app` for classify/read/validate; `agent-fleet-repo-github-app` for pin writes; `zot-origin-cluster-pull` for base pulls; owner-scoped publisher `zot-origin-verdifyconsultancy-ci-dockerconfig` |
| `repo-build` generation 18 | Called eleven times at its direct `build` template by `verdify-platform-ci`; intended self-service caller `agent-ci-build` instead submits the default `ci` entrypoint | Exact-revision checkout and optional test; Kaniko produces an isolated no-push image tar; pinned Crane validates the allowed image scope and publishes the tar to Zot | Kubernetes SA `argo-ci-workflow`; build selector `agentfleet.vallery.net/runner-eligible=true` | 3,600 s; `podGC: OnPodCompletion`; TTL 24 h success / 48 h failure | Its standalone/default invocation declares a 20 Gi rebuildable Longhorn RWO `workdir` and `publisher-state` `emptyDir`; no `volumeClaimGC`, Argo artifact repository, or Kaniko cache is declared. Direct template callers instead supply same-named volumes. It emits digest, canonical image ref, short SHA, source revision and release time | `agent-fleet-ci-github-app` for source; `zot-origin-cluster-pull` for base pulls; the standalone/default profile binds generic `zot-svc-jvallery-ci-dockerconfig`; no declared `push_secret` input |
| `repo-validate` generation 24 | Intended self-service caller is `agent-ci-validate`; this repo has no active caller because its config is absent | Executes declared checks and emits conclusion plus per-check report | Kubernetes SA `argo-ci-workflow`; no node selector; default runtime is the pinned Agent Fleet dev runtime | 3,600 s; per-check 900 s and one retry; `podGC: OnPodCompletion`; TTL 24 h success / 48 h failure | No GitHub artifact/cache and no Argo artifact repository; result is Workflow output parameters | `agent-fleet-ci-github-app` |

The live immutable build/validation tooling includes:

- Kaniko:
  `gcr.io/kaniko-project/executor@sha256:c3109d5926a997b100c4343944e06c6b30a6804b2f9abe0994d3de6ef92b028e`.
- Crane publisher:
  `gcr.io/go-containerregistry/crane@sha256:82b7ed493481c78f20c80ecbc082d1cc61c78ad248bce1e58980ed47959055a7`.
- Generic validation runtime:
  `registry.vallery.net/jvallery/agents-agent-dev-runtime@sha256:d3c2f8c47010a964e7f43dda467f8babd90ae63366bfe206ac1fb61c964cf316`.
- Generic control steps:
  `mirror.gcr.io/library/python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de`.
- Verdify validation:
  `mirror.gcr.io/library/python@sha256:353cf2106d143e1d28f5d7c10c5f5c0387085bba22ef0f7f7e52c2c330fb1779`.
- Workflow scripting:
  `ruby@sha256:2f763b37070564bb00b736f1d4dba6e8f8d203b5f93b94463879fd8d79966f28`.
- Lab verification:
  `mirror.gcr.io/library/node@sha256:7725a5c2c83eed1d36258c66efae14b1ceccd021db9ed1d9559d3335ed3d68ed`.

### Required check and runner profile

`main` is protected and requires one context:
`Verdify Platform / Argo PR CI`. The observable enforcement level is
`non_admins`, and the required context has `app_id: null`. Rulesets and
environments are empty. Current App scope returns 403 for the full protection,
runner, and Actions-policy endpoints, so review requirements and the July
ledger's `strict: true` and linear-history settings could not be freshly
re-proven.

The check is therefore advisory in two important ways: administrators can
bypass non-admin enforcement, and the null App binding lets another repository
writer spoof the context name. All observed status payloads also have
`target_url: null`, so the check has no durable run-details link.

No GitHub Actions job or repo ARC runner performs the gate. The rendered
`validation-standard` label is only a prospective rule for any future Actions
workflow; it does not match current runtime reality. Actual execution is the
Argo service account and Kubernetes node recorded above.

## Repository gate and image matrix

The authoritative source gate is `make ci` (`scripts/ci-local.sh`). It runs
Ruff check/format, the complete portable `make test` suite, migration rollback
classification, generated-config checks, twin syntax compilation, production
Kustomize rendering, and diff-sensitive firmware gates when `CI_BASE_REF` is
provided. Pytest uses strict registered markers so a misspelled external marker
cannot silently enter the portable suite. Collection-time database/container
reachability checks are also fail-closed behind explicit opt-in.

The gate has one visibly optional integration step: the 151 DB-backed drift and
relationship checks run only with `VERDIFY_TEST_DISPOSABLE_DB=1` and an
acknowledged `POSTGRES_HOST`. This pod had no disposable database provisioned,
so final `make ci` printed an explicit `SKIP`; its terminal message means all
**required portable** gates passed, not that DB integration was proven. This is
a remaining disposable-test-platform provision gap, never evidence from prod.

The repaired split excludes live, writable, device, vault, legacy-host and
disposable-container probes without dropping static contracts from mixed
modules. `make test-live` is an explicit six-probe, transaction-read-only prod
suite. `make test-container` owns the disposable G5 PostgreSQL check. No safe
current `make test-writable` target exists: the two old mutation E2Es still
assume the retired Docker host and must be ported to a disposable database
before such a target can be exposed.

Final pre-commit results for that split were `make test`: **1,683 passed, 4
skipped, 280 deselected, 1 strict xfailed** (plus one pre-existing pytest
deprecation warning); `make lint`: **passed**;
`make test-live`: **6 passed**; and `make test-container`: **3 skipped** because
this agent has no Docker socket. The container fixture never started, so there
was no disposable resource to clean up.

`CI_BASE_REF=origin/main make ci` also completed **ALL REQUIRED PORTABLE GATES
GREEN**; the immutable final-head repetition and UTC timestamp are recorded on
#561/#559 for the same non-self-referential reason as the remote run below.

The live push workflow builds these exact image definitions:

| Image | Dockerfile | Context | Special test / build behavior | Desired-state destination |
| --- | --- | --- | --- | --- |
| `verdify-api` | `api/Dockerfile` | `.` | Standard build | prod overlay |
| `verdify-mcp` | `mcp/Dockerfile` | `.` | Standard build | prod overlay |
| `verdify-ingestor` | `ingestor/Dockerfile` | `.` | Standard build; live sole writer | prod overlay |
| `verdify-planner` | `planner_graph/Dockerfile` | `.` | Standard build | prod overlay |
| `verdify-migrate` | `db/Dockerfile.migrate` | `.` | Standard build | prod overlay / migration Job |
| `verdify-setpoint-server` | `scripts/Dockerfile.setpoint-server` | `.` | Standard build | prod overlay |
| `verdify-lab-publisher-k3s` | `scripts/Dockerfile.lab-publisher` | `.` | Standard build | prod overlay |
| `verdify-lab-astro` | `site-astro/Dockerfile` | `site-astro` | `npm ci`, snapshot hydration, pinned-kubectl verification, full site tests | Lab-stage overlay |
| `verdify-lab-release-agent` | `site-astro/Dockerfile.release-runtime` | `site-astro` | target `agent`; source SHA/time build arguments | Lab-stage overlay, dormant runtime |
| `verdify-lab-release-nginx` | `site-astro/Dockerfile.release-runtime` | `site-astro` | target `site`; source SHA/time build arguments | Lab-stage overlay, dormant runtime |
| `verdify-lab-occurrence-exporter` | `site-astro/Dockerfile.occurrence-exporter` | `site-astro` | source SHA/time build arguments | pinned in Lab-stage overlay |

The Git tree and filesystem independently contain exactly thirteen Dockerfiles
and no Containerfiles. Their complete disposition is:

| Dockerfile | Build / publish disposition | Deployment disposition |
| --- | --- | --- |
| `api/Dockerfile` | Built as `verdify-api` | Prod API Deployment |
| `db/Dockerfile.migrate` | Built as `verdify-migrate` | Prod Argo `PreSync` fresh-schema/verification Job |
| `deploy/k8s/cnpg/image/Dockerfile` | **No current caller or artifact**; the named GitHub workflow does not exist | Retired dev/CNPG candidate only; prod uses upstream TimescaleDB |
| `ingestor/Dockerfile` | Built as `verdify-ingestor` | Prod sole-writer Deployment plus HA-gap and vision CronJobs |
| `mcp/Dockerfile` | Built as `verdify-mcp` | Prod MCP Deployment |
| `planner_graph/Dockerfile` | Built as `verdify-planner` | Prod planner Deployment |
| `scripts/Dockerfile.lab-publisher` | Built as `verdify-lab-publisher-k3s` | Prod publisher CronJob and publisher sidecar |
| `scripts/Dockerfile.setpoint-server` | Built as `verdify-setpoint-server` | Prod-only, device-writing setpoint Deployment |
| `site-astro/Dockerfile` | Built as `verdify-lab-astro`; developer-only `fixture-runtime` is not published | Active Lab-stage Deployment |
| `site-astro/Dockerfile.occurrence-exporter` | Built and pinned as `verdify-lab-occurrence-exporter` | **No workload consumes it**; publish-only contract artifact |
| `site-astro/Dockerfile.production` | **No current caller or artifact** | Disconnected zero-digest candidate, absent from every overlay |
| `site-astro/Dockerfile.release-runtime` | Built twice, targets `agent` and `site` | Digests pinned in Lab-stage; candidate Deployment deliberately remains at zero replicas |
| `twin/Dockerfile` | **No current caller or artifact**; `make ci` compiles the source but does not build this image | No live twin Deployment; the disconnected component would compile inline instead |

The seven core prod image tasks have an empty per-image test command: their
required proof is the shared `make ci`, a successful no-push Kaniko archive
build, and Crane publication with immutable digest output. That build/publish
proof was not reached in this lane. The Astro task runs its full npm suite; its
release and occurrence tasks additionally hydrate the exact snapshot and
enforce source/release metadata in their Dockerfiles. PR CI validates only and
never builds or publishes.

The rendered prod overlay also consumes images this repository does not build:
Traefik `v3.7.1`, Mosquitto `2`, ESPHome `2025.6.3`, the legacy immutable
Quartz Lab GHCR digest, immutable Grafana `12.4.5` and renderer `5.10.0`, an
immutable unprivileged nginx `1.29` image, immutable Hermes Agent, and the
upstream `postgres:16`, `python:3.12-alpine`, `python:3.13-alpine`, and
TimescaleDB `2.25.2-pg16` images. The unpinned tags are a desired-state supply
chain gap; none has a hidden repo build or Zot publication path.

The intended publish destination is the in-cluster
`registry-origin.registry-origin.svc.cluster.local:5000` and yields immutable
external references under
`registry.vallery.net/verdifyconsultancy/<image>@sha256:<digest>`. The closed
helper mapping contains the intended `verdifyconsultancy` Zot scope and
`zot-origin-verdifyconsultancy-ci-dockerconfig` name, but the helper always
emits an unsupported `push_secret` Workflow argument. Live `repo-build`
generation 18 isolates publication behind pinned Crane and a fixed publisher;
its default `ci` entrypoint allows only `jvallery vallery`, while the bespoke
generation-33 caller bypasses that entrypoint and passes
`allowed_publish_scopes=verdifyconsultancy` to `build`. Because a direct Argo
`templateRef` resolves the referenced template's volume mounts against the
caller Workflow, those eleven builds also use generation 33's owner-scoped
`zot-origin-verdifyconsultancy-ci-dockerconfig` binding. The generic
`zot-svc-jvallery-ci-dockerconfig` binding belongs to `repo-build`'s standalone
default profile, which the incompatible self-service helper would invoke. A new
exact-SHA registry digest was not produced because checkout fails before code
runs, the self-service contract is incompatible, and this pod cannot create a
Workflow.

## Non-image delivery and publication inventory

Image promotion is only one of this repository's delivery surfaces. The active
or operator-addressable non-image paths are:

| Surface | Caller / trigger and target | Gate and credential references (names only) | Verification and rollback |
| --- | --- | --- | --- |
| Firmware OTA, worktree path | Operator runs `make firmware-deploy`; ESPHome compiles the exact worktree and uploads to the ESP32 | Jason/device gate; alert, 48-hour bake, weekly limit, clean-tree, replay, invariant, unit and compile gates; `verdify-firmware-ota`, `verdify-app-secrets`; preflight requires both last-good binary and nonempty version metadata | Firmware-version wait plus sensor-health sweep and atomic expected-version pin on the current live ingestor state mount; failure attempts the last-good flash first, stops explicitly if it fails, then verifies exact version plus sensor health while metadata is available; `make firmware-rollback` is the manual handle. The mount is temporary `emptyDir` under #382, so the pin is not durable across pod replacement |
| Firmware build/OTA, in-cluster path | Explicit Job created from suspended `verdify-firmware-builder`; `FLASH=0` compiles and archives, `FLASH=1` contacts the device | Flash remains Jason-gated; `verdify-github-token`, `verdify-firmware-ota`, `verdify-app-secrets`, `verdify-firmware-wifi` | Durable artifact/cache PVCs and a last-good binary are present, but the Job always compiles/uploads `FW_REVISION`; no first-class in-cluster action currently flashes the stored last-good artifact |
| Plans and tunables | Ingestor trigger to Hermes to MCP `set_plan` / `set_tunable`, or an audited manual MCP trigger; DB plan rows reach the sole-writer dispatcher and ESP32 | Bounded registry, trigger ID, planner identity and lifecycle fences; `verdify-hermes`, `verdify-app-secrets`, `verdify-ha-token` | `cfg_*` device readback closes the loop; a later audited bounded plan/tunable compensates or supersedes, never a raw device write |
| Incremental migrations | Reviewed operator pipes one serialized `db/migrations/*.sql` file through `psql` in `verdify-db-0` | Prod DB gate; Kubernetes operator identity; workloads use `verdify-app-secrets` | Classify with `make migration-rollback-safety`, run the migration-specific proof, and use migration-specific down/compensating SQL or gated restore; never outer-wrap a self-committing migration |
| Fresh schema bootstrap | Manual prod Argo sync invokes `verdify-migrate` as a `PreSync` hook | Same prod/device sync gate; `verdify-app-secrets`, `zot-origin-cluster-pull`, transitional `ghcr-jvallery-readonly` | Builds a fresh DB or verifies core objects; **deliberately no-ops on populated prod and does not apply the sequential incremental migrations**; data restore is a separate human-gated runbook |
| Quartz Lab content | `verdify-lab-publisher` runs every ten minutes or as an explicit one-shot Job; it generates content, builds a guarded private candidate, then updates cache/S3 public, content and state | Forbid concurrency, bounded query/step timeouts and public-output guard; `verdify-lab-publisher-s3`, `verdify-app-secrets`, image pull names | Failed generation/build leaves the served tree unchanged. Content rollback restores a prior S3/cache generation and republishes; code rollback re-pins the publisher image. There is no first-class current/previous content selector |
| Planner Lab refresh / manual Quartz wrapper | `make planner-publish` and `make site-rebuild` address the old absolute host path; the live k3s authority is the ten-minute publisher CronJob | Same Lab credentials; these wrappers are compatibility paths, not independent k3s delivery proof | Same guarded candidate/content recovery; the host trigger is classified stale rather than silently treated as live |
| Hermes profile | Git changes canonical config plus its environment-specific ConfigMap mirror and declarative profile checksum; a gated prod Argo sync rolls the Deployment and the init container reseeds the PVC | `verdify-hermes`, `verdify-hermes-slack`, image pull names | Revert both copies and checksum, validate, gated sync, then `make hermes-smoke`, which requires the live ConfigMap data hash and Deployment checksum to equal the exact reviewed checksum before waiting for rollout and Availability. `hermes-restart` is an emergency imperative handle that leaves `restartedAt` drift until the next sync; `SOUL.md`/`slack.yaml` still lack a current repo-to-runtime delivery adapter |
| Grafana dashboards | Edit JSON, regenerate committed ConfigMaps, then gated Argo sync; a documented targeted server-side apply is an imperative fast-iteration path | `make grafana-cm-check`, render/brand checks; `verdify-grafana-secrets`, `verdify-app-secrets` | Provisioner reload is 300 seconds or a guarded rollout restart; rollback reverts JSON/generated ConfigMaps and re-syncs/reloads |
| Schema consumer rollout | Schema-first merge, compatible consumer image pins, then gated Argo reconcile; MCP/ingestor restarts must be documented | Service-restart drift guard; normal workload credential names | Re-pin compatible images. DB rollback remains migration-specific. This branch replaces the stale systemd ingestor helper with a confirmation-gated k3s rollout and status wait |
| Secret material | SOPS-encrypted, out-of-kustomization manifests are applied by an authorized operator | Rotation/apply is explicitly human-gated; only Secret names are inventoried here | Provider-specific rotation/restore runbook; no CI decrypt, ambient credential, or autonomous revocation path |

Generic Cloud Run planner scaffolding, the retired cross-repo vault-to-GHCR
publisher, local Astro release-manager contracts, retired CNPG dev fixtures,
and direct systemd/Docker host commands have no live caller. Quiet-mode and
retained-MQTT cleanup commands are operator mutations, not releases; they retain
their explicit confirmation/expiry rules and are not represented as CI/CD.

### Scheduled delivery and mutation callers

Live schedules were reconciled at `2026-08-02T19:03:05Z`:

| CronJob | Trigger and output | Image/build authority | Credential names and containment / rollback |
| --- | --- | --- | --- |
| `verdify-band-curve-refresh` | Every 10 min; refreshes derived materialized views | Upstream TimescaleDB image | `verdify-app-secrets`; repair source and refresh again, or revert/suspend |
| `verdify-db-backup` | Daily 02:17; atomic dump to backup PVC with retention | Upstream TimescaleDB image | `verdify-app-secrets`, pull names; restore is separate, destructive and human-gated |
| `verdify-db-watchdog` | Every 2 min; narrowly recycles a proven-unhealthy DB pod | Upstream Python image | Kubernetes SA `verdify-db-watchdog`; StatefulSet recovery; revert/suspend disables it |
| `verdify-writer-watchdog` | Every 2 min; emits Events and manages writer/telemetry alert rows | Upstream Python and TimescaleDB images | SA `verdify-writer-watchdog`, `verdify-app-secrets`; health resolves alerts; revert/suspend |
| `verdify-ha-gap-backfill` | Hourly at :23; bounded historical HA backfill | Repo-built ingestor image | `verdify-app-secrets`, `verdify-ha-token`, pull names; data undo/restore is separately DB-gated |
| `verdify-lab-publisher` | Every 10 min; Quartz/S3/PVC/public-site publication | Repo-built publisher image | Lab/DB credential names above; staged fail-closed publish and content recovery above |
| `verdify-vision` | 00:00, 15:00, 18:00 and 21:00 UTC; captures/analyzes observations | Repo-built ingestor image | `verdify-vision-key`, `verdify-app-secrets`, `zot-origin-cluster-pull`; revert/suspend/repin, with DB deletion separately gated |
| `verdify-firmware-builder` | Suspended with impossible Feb-31 schedule; manual Job template only | Upstream ESPHome image | Firmware names above; `FLASH=0` default; last-good artifact retained, but in-cluster rollback actuation is absent |
| `descheduler` (`verdify-descheduler`) | Every 30 min; currently `--dry-run`, so it only reports candidates | Immutable upstream descheduler image | SA `descheduler`, no Secret; enforcement needs its separate arm gate; rollback retains/restores dry-run |

The old CNPG daily-backup source under `deploy/k8s/cnpg/dev` has no live target
because the dev environment was decommissioned; it is not a hidden scheduler.

## Contract-to-reality reconciliation

| Contract surface | Live finding | Match? |
| --- | --- | --- |
| `.agent-fleet/ci.yaml` | Absent on `main`; both helpers fail closed | **No** |
| Standard build schema/helper | Live `repo-build/build` accepts target/build arguments and requires `allowed_publish_scopes`, but the `.agent-fleet/ci.yaml` helper drops target/build arguments, emits undeclared `push_secret`, and submits the default `ci` profile limited to `jvallery vallery` | **No** — a partial config is lossy and every Verdify helper submission is currently invalid |
| Registered runner profile | Runner API is 403 and no approved repo ARC profile is evidenced; actual gate is Argo on `vm-k3s-node4` | **No / advisory only** |
| Interactive repo App | Generated `gh`/Git auth works and the installation is scoped to exactly this repository | **Yes** |
| CI checkout App/broker | Read/precheck paths use `agent-fleet-ci-github-app`, which reports no VerdifyConsultancy installation; status/write paths use the newer repo App successfully | **No** |
| Zot scope | The bespoke generation-33 caller supplies both `allowed_publish_scopes=verdifyconsultancy` and the fixed owner-scoped publisher volume, so its declared binding matches. The standard self-service helper still emits an unsupported Secret-name argument and targets the default generic profile limited to `jvallery vallery` | **Bespoke declaration yes; self-service no; new push unproved** |
| Argo applications | Repo declares prod `verdify-prod-dark`, `main`, prod overlay, manual sync, `prune:false`; metrics show Healthy/OutOfSync. Lab-stage metrics show Healthy/Synced and manual sync | **Runtime exists; direct Application read is RBAC-blocked** |
| Repo self-service | `agent-ci-build` and `agent-ci-validate` exist, config is absent, the build helper/template parameters disagree, and this SA has `create workflows = no` | **No** |
| Managed branch identity | Live Agent Fleet inventory still configures agents for retired `live/platform-main`; GitHub and this repo use `main` | **No** |
| Managed CI prose | Claims GitHub Actions validates, while no repo workflow or Actions run exists; actual required status comes from Argo | **No** |
| Pin-review contract | Lab-stage opens a reviewed pin PR, but the prod pin step pushes directly to `main`; commit `ae13b911...` has no associated PR | **No** |
| Incremental migration authority | The handoff claimed the `PreSync` Job applies sequential migrations, but `db/migrate.sh` proves it verify-no-ops on populated prod | **Fixed on this branch** — the handoff now names serialized pod-local `psql`; a standard automated incremental path remains absent |
| Repo runtime/test helpers | Hermes copied to retired host paths; ingestor used systemd/journalctl; firmware post-checks could fall back to the wrong DB backend or mask archive/pin failure; `make test` ran retired live-host tests | **Fixed on this branch** — checksum-driven GitOps rollout validation, confirmation-gated k3s restart/log helpers, end-to-end `FIRMWARE_DB_BACKEND` propagation, authoritative pre-OTA state check and fail-closed acceptance/rollback verification, strict marker-based portable `make test`, explicit transaction-read-only `make test-live` |

The generic schema and Workflow-create authorization are central interfaces.
This repository must not invent target/build-argument extensions, credentials,
RBAC, or a Verdify-specific broker to compensate.

## Repository-owned corrections in this lane

The evidence pass found locally correctable contract defects. This PR:

- carries `FIRMWARE_DB_BACKEND` through the post-OTA version wait and
  sensor-health path, preventing a non-laptop run from querying a nonexistent
  Docker DB and falsely rolling a healthy device back;
- requires the firmware state path to exist and be writable before an OTA and
  requires a nonempty rollback credential before upload; the rollback helper
  passes credential/target data through the environment into a quoted Python
  heredoc so secret punctuation cannot become source or leak through a syntax
  error; it makes archive plus expected-version pin failures fail the acceptance chain
  instead of being hidden by a final successful `echo`; it pins the accepted
  version on the current live ingestor state mount and verifies last-good
  version plus sensor health after an automatic rollback; the mount's temporary
  #382 `emptyDir` limitation is recorded rather than called durable; recursive
  calls no longer make `make -n firmware-deploy` execute the OTA acceptance or
  rollback shell, and a no-execution regression test covers that hazard;
- replaces retired host-copy/systemd/journalctl Hermes and ingestor helpers
  with drift-checked, non-mutating GitOps validation, a declarative Hermes
  profile-checksum rollout handle, explicit confirmation-gated Kubernetes
  restarts, generation-aware rollout waits, and Kubernetes logs;
- corrects the handoff to distinguish serialized incremental migration apply
  from the `PreSync` fresh-schema/verify Job; and
- restores the promised portable/current-live/container separation without
  dropping mixed-file static safety contracts, with a bounded TLS/public-route
  plus pod-local transaction-read-only `SELECT` suite that excludes
  `/setpoints` and every device-affecting path; collection-time Docker probing
  now requires explicit disposable-DB opt-in and unknown markers fail closed;
  and
- retires a test that opened an obsolete Anthropic key file, updates stale
  static expectations to the current firmware/storage/dashboard contracts,
  and preserves the strict #382 PVC-recovery xfail.

The changed surfaces are the root agent guide, lane/access/Argo/history indexes,
README and service map; Make/Pytest and local-CI contracts; mixed legacy and
schema test modules plus container/live suites;
firmware preflight/rollback helpers; Hermes canonical/mirrored config,
Deployment, Secret-name contract, and NetworkPolicy commentary; active
release/control/k3s/laptop/promotion/Slack/web operator docs; and this report.
Fidelity tests lock the portable and delivery helper invariants. No target that
restarts a workload, writes the DB, syncs Argo, or contacts the device was run.

**Restart: none** — the files changed under `verdify_schemas/` are test-only
marker/collection contract updates. No runtime schema, `ingestor/entity_map.py`,
or `mcp/server.py` consumer changed, so bouncing `verdify-mcp` or
`verdify-ingestor` would create unnecessary production churn.

## Credential reference inventory (names only)

CI and publishing references:

- Agent runtime profile: `github-app-installation`, `repo-agent-standard`,
  activation `enabled`.
- Source checkout/trusted precheck: `agent-fleet-ci-github-app`.
- Status and repository pin writes: `agent-fleet-repo-github-app`.
- Zot publish: `zot-origin-verdifyconsultancy-ci-dockerconfig`.
- Generic `repo-build` default push reference:
  `zot-svc-jvallery-ci-dockerconfig` (used only by its standalone/default
  profile, not the bespoke Verdify direct caller).
- Zot workload pull: `zot-origin-cluster-pull`.
- Legacy GHCR workload pull: `ghcr-jvallery-readonly`.

Runtime manifests also reference, by name only:
`verdify-app-secrets`, `verdify-ha-token`, `verdify-hermes`,
`verdify-hermes-slack`, `verdify-lab-publisher-s3`,
`verdify-grafana-secrets`, `verdify-firmware-ota`,
`verdify-firmware-wifi`, `verdify-vision-key`, and the legacy
`verdify-github-token`. The last name is still referenced by the firmware
builder despite the rendered no-ambient-token statement; its removal rollout
is incomplete. No value or Secret annotation was inspected.

The complete workload key-name wiring is maintained in
[`deploy/k8s/SECRETS.md`](../../deploy/k8s/SECRETS.md). In particular, the
current `verdify-hermes` contract requires `OPENAI_API_KEY`,
`VERDIFY_MCP_TOKEN`, `API_SERVER_KEY`, and `HERMES_IRIS_API_KEY`; the latter two
are the differently named gateway/caller auth pair and must be coordinated only
by the authorized secret-delivery workflow. An already-delivered
`HERMES_MCP_URL` key is legacy and unused because the MCP URL is ConfigMap-owned.

Inactive or disconnected candidates name `verdify-twin-secrets`,
`verdify-umami-secrets`, `minio-dev-creds`, and `verdify-agent-secrets`; none is
an active CI credential or a live target proven by this lane.

## Exact-SHA proof and failure classification

The following representative exact-head retry failed before checkout under
`verdify-platform-pr-ci` generation 13 and `verdify-platform-ci` generation 31.
It did not reach `repo-build`. The latter has since advanced to generation 33,
and separately inventoried `repo-build` has advanced to generation 18, without
changing the failing `agent-fleet-ci-github-app` checkout route:

- Workflow: `verdify-platform-pr-ci-bvhpw` (`event-action=synchronize`).
- Head: `b567b052742f770e1673559402f8744267c47621` (PR #559).
- Started: `2026-08-02T19:21:07Z`; terminal failure at
  `2026-08-02T19:21:57Z`.
- Runner: Kubernetes service account `argo-ci-workflow`; validation pod
  `verdify-platform-pr-ci-bvhpw-validate-1158878924` on `vm-k3s-node4`.
- Literal error: `no CI App installation for owner VerdifyConsultancy`.
- Required status: terminal `failure`, with `target_url: null`; the status
  reporter succeeded and GitHub Actions check-runs remained zero.

The supported automatic `synchronize` trigger exercised the normal PR path.
The specs retained `agent-fleet-ci-github-app` for checkout and
`agent-fleet-repo-github-app` for status/write. Reproducing the same error while
the separate status writer succeeded isolates the fault to the standard
read/checkout installation contract rather than repo code.

This is the intentional terminal-failure path: it exercises the supported
`synchronize` trigger and status reporter after a real code diff. The live templates
declare neither superseded-run cancellation nor a repository-specific timeout
acceptance contract, so no artificial cancel/timeout was manufactured. The
failed validation pod is terminal, not running, and is retained only by the
declared 48-hour failure TTL for diagnosis.

Self-service was probed at `2026-08-02T18:16:15Z` against exact default SHA
`eb3ac9cf33001fa6c5271412284382b36c6abf9e`:

```text
agent-ci-validate ... --print --output json
  exit 2: no CI spec at <worktree>/.agent-fleet/ci.yaml
agent-ci-build ... --output json
  exit 2: no build spec at <worktree>/.agent-fleet/ci.yaml
kubectl auth can-i create workflows.argoproj.io -n agent-fleet-ci
  no
kubectl auth can-i get applications.argoproj.io -n argocd
  no
kubectl auth can-i patch applications.argoproj.io -n argocd
  no
```

The final report commit's immutable head, local gates, and automatic platform
result are recorded on #561/#559. Recording a final run in this file would
change the tested SHA, so this section is intentionally representative rather
than recursively claiming to be the latest. A platform failure is not retried
without a platform diff.

## Desired state, runtime, user path, and rollback

At `2026-08-02T18:23:06Z`, Argo metrics reported
`verdify-prod-dark` Healthy but **OutOfSync**, with autosync disabled and zero
orphans. Running prod is a mixed selectively synced digest set rather than one
Git revision:

| Workload | Running immutable digest | `main` desired digest | Ready |
| --- | --- | --- | --- |
| API | `sha256:984ba4936864851f375fdd53041e65c606c6aeb5dbedb7223c5fdafe0736bb6c` | `sha256:d28c45ce14c425cc06ef2a64430fa390602b93995d51b42fba2ac2719705b5e2` | 2/2 |
| MCP | `sha256:8cc8d7472f63b6dc8ecea47ab67d488f6c8b950dd8fd4388b7a42670d164354a` | `sha256:e3f37fc9ced69bfde0290c6aa8f6b662a983a77d42742f82e06c4df9b4ad5e3c` | 2/2 |
| Ingestor | `sha256:fcd13ad7aa91650db371b92f72bcf60caae61b6b1b9f1f79f69e6718541025e3` | `sha256:083386240ff684ac81d53eec58f89e57f095e4f390052bf1a7901f71cec14090` | 1/1 |
| Planner | `sha256:ff7de32d7de6b6eee73ad2dfc11f4bd140180b35a0fef6325803bbee0b1aa1b6` | `sha256:735cb005d436e067b013d606e037aa1a1b8cbee6a6af96187bc316e29cd68d73` | 1/1 |
| Setpoint server | `sha256:f5ac817f42f9a0f306692e1fb0434eb1b600c2ba5fa35da0dccdc618bbb3690a` | `sha256:7278d148e39818ec6098d76a88fff6289670a8811e6cd61cf44372eb9d5b8e06` | 1/1 |
| Lab publisher | `sha256:c6085836d0cb3a5bd083add258989502c84f5a07a6182f0e501aee0ffac4cee0` | `sha256:57c3ae05aaa1671f024ccf30af984e0d7a127e77a0a45ae02d8bf3fe51c3642d` | scheduled job path |
| Quartz Lab | `sha256:98ac23b6affa3b8af3ebb5c7f1c7abe31d00e2379c070f4d61b409130dae9845` | same | 2/2 |

Blindly syncing current `main` would roll multiple greenhouse workloads,
including the sole writer and setpoint server, so it is not a safe proof or
rollback. The mixed running digest set above is recovery evidence, not proof of
one coherent known-good release. Recovery requires reconstructing a reviewed
desired-state commit from known-good historical pins, followed by the explicit
non-pruning, device-gated Argo sync and post-sync verification.

Lab stage is coherent: at `2026-08-02T18:24:19Z`, metrics reported
`verdify-platform-lab-stage` Synced and Healthy, autosync disabled, with two
ready pods whose image ID exactly matches the `main` pin
`registry.vallery.net/verdifyconsultancy/verdify-lab-astro@sha256:1852cf2523041ef840b3eb1092050e4b3b19d2027494fc6b6c9809b930de93b7`.

Read-only public probes at `2026-08-02T18:23:56Z` returned:

```text
https://api.verdify.ai/health                 200
https://lab.verdify.ai/                       200
https://graphs.verdify.ai/api/health          200
https://mcp.verdify.ai/mcp                    406 (expected unauthenticated GET; route/TLS proven)
https://lab-stage.verdify.ai/                 200
```

The new bounded `make test-live` repeated those five TLS-verified route checks
and a pod-local `BEGIN READ ONLY` transaction that verifies both
`current_database()` and `transaction_read_only=on` at
`2026-08-02T19:46:59Z`: **6 passed**. It deliberately contains no `/setpoints`,
device, writable API, alert-emitting, or mutation probe.

No `/setpoints` or device-path probe was attempted because it can emit an
operational alert. No runtime was changed, so there is no new-live-delivery
`GREEN at <UTC>, re-verified at <UTC+10>` claim. The stage baseline was
observed, not promoted.

## Required standard fixes

1. Reconcile every common read, trusted-precheck, validation, and build
   checkout consumer onto the standard repo-scoped read broker/installation
   contract while retaining a separately scoped status/write authority. Audit
   all uses of `agent-fleet-ci-github-app`; do not add a Verdify-specific
   Secret. Re-render/reconcile centrally, then retrigger each exact PR head
   once.
2. Reconcile the standard `.agent-fleet/ci.yaml` parser, helper, and live
   `repo-build` profiles: preserve controlled multi-stage targets/build
   arguments, remove the helper's caller-supplied Secret-name parameter, and
   make the standard entrypoint select a centrally fixed owner publisher plus
   allowed scope, preserving the already-correct bespoke Verdify binding. Then
   add a complete repo-owned Verdify spec. Restore standard repo-agent
   Workflow-create capability through the fleet registry/broker contract, not
   a hand-applied Role.
3. Bind `Verdify Platform / Argo PR CI` to the approved App, supply a durable
   non-null run URL, and make branch protection fully inspectable to the
   repo-scoped agent. Add correct superseded-run cancellation centrally.
4. Make production promotion create a reviewed digest-only PR instead of
   pushing a pin commit directly to `main`; align the promotable image set and
   repair the current mixed prod pin state before any sync.
5. Correct the centrally rendered `live/platform-main`, GitHub Actions, and
   unavailable-auth claims. Remove the obsolete runtime GitHub-token and GHCR
   references through their owner-approved standard migrations.
6. After this branch lands, separately close the remaining repo-owned delivery
   gaps: make Hermes config one generated source instead of drift-checked
   duplicated YAML, add an in-cluster last-good firmware rollback actuator,
   define an auditable incremental-migration delivery mechanism, add a
   first-class Quartz content generation selector, and pin the remaining
   upstream runtime tags. The stale restart and post-OTA backend defects are
   fixed in this PR but cannot merge while the required platform check is red.

Until the checkout identity, standard config schema, submission capability,
and protected-check provenance are repaired, this repository cannot prove the
required source-to-Zot-to-reviewed-pin-to-Argo chain. The unambiguous current
verdict is **`BLOCKED_PLATFORM`**.
