# Prod Promotion Runbook

**Status:** current for the single-environment model as of 2026-06-19.

`main` is the canonical source branch. The retired `live/platform-main`,
`verdify-dev`, and `verdify-staging` promotion chain is not part of the deploy
path anymore.

**ZOT MIGRATION (2026-07-11, ADR-0021):** publishing moved OFF GHCR to the
in-cluster zot origin (`registry.vallery.net`). GitHub Actions now VALIDATES
builds only (`Container Publish` runs every Dockerfile with `push: false`).

## Flow

1. Merge to `main`.
2. GitHub Actions runs (validation only):
   - `CI`
   - `K8s Manifests`
   - `Container Publish` (build-without-push; a Dockerfile/COPY break fails)
3. Publish happens IN-CLUSTER: submit one `repo-build` Argo Workflow per
   changed image in namespace `agent-fleet-ci` (Kaniko builds the exact main
   revision and pushes `registry.vallery.net/verdifyconsultancy/<image>@sha256:…`
   using the org push secret `zot-origin-verdifyconsultancy-ci-dockerconfig`):

   ```sh
   kubectl create -n agent-fleet-ci -f - <<'WF'
   apiVersion: argoproj.io/v1alpha1
   kind: Workflow
   metadata:
     generateName: verdify-platform-build-<image>-
     labels: {agent-fleet.vallery.net/repo: verdify-platform}
   spec:
     workflowTemplateRef: {name: repo-build}
     arguments:
       parameters:
         - {name: repo, value: https://github.com/VerdifyConsultancy/verdify-platform.git}
         - {name: revision, value: <sha>}
         - {name: dockerfile, value: <path/to/Dockerfile>}
         - {name: context, value: "."}
         - {name: image, value: verdifyconsultancy/verdify-<image>}
         - {name: push_secret, value: zot-origin-verdifyconsultancy-ci-dockerconfig}
   WF
   ```
4. Collect each workflow's `digest` output parameter and commit a digest-only
   change to `deploy/k8s/overlays/prod/kustomization.yaml`
   (`newName: registry.vallery.net/verdifyconsultancy/<image>` + `digest:`).
5. A human reviews the digest-only commit/PR.
6. The prod ArgoCD app remains OutOfSync until an operator performs the explicit
   manual sync.

The manual ArgoCD sync is the device-write gate. Nothing in GitHub Actions should
sync the live cluster or restart the ESP32 writer automatically.

## Promotable Images

`prod-promote` manages the CI-built greenhouse service images:

- `verdify-api`
- `verdify-mcp`
- `verdify-ingestor`
- `verdify-migrate`
- `verdify-planner`

Device-affecting or external images remain hand-pinned unless a specific reviewed
change says otherwise:

- `verdify-setpoint-server`
- `verdify-lab`
- `verdify-lab-publisher-k3s`

## Operator Checks

Before syncing ArgoCD, verify the GitHub checks for the merge and promotion PR are
green:

```sh
gh run list --repo VerdifyConsultancy/verdify-platform --branch main --limit 10
```

After the prod ArgoCD app is green, run read-only smoke checks from a host with a
scoped kubeconfig:

```sh
KUBECONFIG=/path/to/verdify-agent.config scripts/k3s-smoke.sh smoke
KUBECONFIG=/path/to/verdify-agent.config scripts/k3s-smoke.sh device-monitor
```

`smoke` checks API, MCP, and DB health. `device-monitor` checks that exactly one
pod holds the ESP32 native API connection.
