# Prod Promotion Runbook

**Status:** reconciled against the live Agent Fleet templates on 2026-08-02;
promotion is `BLOCKED_PLATFORM` until the central mismatches below are fixed.

`main` is the canonical source branch. The retired `live/platform-main`,
`verdify-dev`, and `verdify-staging` promotion chain is not part of the deploy
path anymore.

**ZOT MIGRATION (2026-07-11, ADR-0021):** publishing moved OFF GHCR to the
in-cluster zot origin (`registry.vallery.net`). Repository GitHub Actions are
absent; centrally rendered Argo Workflows validate, build no-push archives with
Kaniko, and publish them with pinned Crane.

## Current rendered behavior and blockers

The live `verdify-platform-ci` generation 33 path does not yet implement the
approved promotion policy:

1. A pull request calls its `validate` template for the exact head. A main-push
   classification of `build` runs validation and the complete seven-image core
   matrix; it does not select individual changed images. All four Lab image
   builds run whenever classification is anything other than `skip`.
2. Those build steps call `repo-build/build` generation 18 with
   `allowed_publish_scopes=verdifyconsultancy`. Kaniko writes an isolated
   no-push image tar and pinned Crane publishes it. The direct template's
   volume mounts resolve against the generation-33 caller, which supplies the
   owner-scoped `zot-origin-verdifyconsultancy-ci-dockerconfig` binding. That
   bespoke publisher declaration matches the requested scope, although no
   exact-SHA push can be proved while checkout fails.
3. The repo self-service helper is not a workaround: it always emits the
   unsupported `push_secret` argument, its `.agent-fleet/ci.yaml` parser drops
   the `docker_target` and source-metadata build arguments that three Lab images
   require, and it submits the default `ci` profile whose allowed scopes are
   only `jvallery vallery` and whose fixed publisher is the generic
   `zot-svc-jvallery-ci-dockerconfig`. The repo ServiceAccount also cannot
   create Workflows. Do not hand-create a Workflow, add RBAC, or invent a
   repository credential.
4. After core builds, the live `pin-digests` step commits and pushes prod pins
   directly to `main`. Only the Lab-stage path opens a reviewed pin PR. A direct
   prod pin is not the required reviewed desired-state change and must not be
   treated as promotion approval.

These are central Agent Fleet contract gaps tracked from repository issue #561
into `jvallery/agents#3088`. The source checkout path is also blocked by the
missing standard CI App installation for `VerdifyConsultancy`.

## Required flow after the platform contract is corrected

1. Merge to `main` only after local `make ci` and the exact-head
   `Verdify Platform / Argo PR CI` status are green. `scripts/ci-local.sh` is
   the source gate run by the in-cluster workflow.
2. The corrected main-push workflow checks out the exact main SHA, has Kaniko
   create the applicable isolated image archives, and publishes them through a
   centrally bound Verdify Zot publisher profile. Each successful build returns an immutable
   `registry.vallery.net/verdifyconsultancy/<image>@sha256:…` reference.
3. Collect the returned digests and open a digest-only change to
   `deploy/k8s/overlays/prod/kustomization.yaml`; never push that change directly
   to protected `main`.
4. A human reviews and merges the digest-only PR at its exact green head.
5. The prod ArgoCD app remains OutOfSync until an operator performs the explicit
   manual sync and completes the post-sync probes.

The manual ArgoCD sync is the device-write gate. No CI path should sync the
live cluster or restart the ESP32 writer automatically.

## Promotable Images

The CI-built prod desired-state set is:

- `verdify-api`
- `verdify-mcp`
- `verdify-ingestor`
- `verdify-migrate`
- `verdify-planner`
- `verdify-setpoint-server`
- `verdify-lab-publisher-k3s`

The four Lab-stage images are pinned through their separate reviewed stage
desired-state change. Every prod digest change remains inert until the manual
device-write-gated Argo sync.

## Operator Checks

Before syncing ArgoCD, verify the gate and the in-cluster CI workflow are green:

```sh
make ci
kubectl get workflows -n agent-fleet-ci -l agent-fleet.vallery.net/repo=verdify-platform
```

After the prod ArgoCD app is green, run read-only smoke checks from a host with a
scoped kubeconfig:

```sh
KUBECONFIG=/path/to/verdify-agent.config scripts/k3s-smoke.sh smoke
KUBECONFIG=/path/to/verdify-agent.config scripts/k3s-smoke.sh device-monitor
```

`smoke` checks API, MCP, and DB health. `device-monitor` checks that exactly one
pod holds the ESP32 native API connection.
