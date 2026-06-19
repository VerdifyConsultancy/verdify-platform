# Prod Promotion Runbook

**Status:** current for the single-environment model as of 2026-06-19.

`main` is the canonical source branch. The retired `live/platform-main`,
`verdify-dev`, and `verdify-staging` promotion chain is not part of the deploy
path anymore.

## Flow

1. Merge to `main`.
2. GitHub Actions runs:
   - `CI`
   - `K8s Manifests`
   - `Container Publish`
3. `Container Publish` builds and publishes changed images to GHCR with immutable
   digests and the mutable `:branch-main` tag.
4. A maintainer runs `.github/workflows/prod-promote.yml`.
5. `prod-promote` resolves the latest published `:branch-main` digest for each
   promotable prod image and opens a digest-only PR against
   `deploy/k8s/overlays/prod/kustomization.yaml`.
6. `promote-diff-guard.yml` verifies the PR changed only the allowed prod image
   digest surface.
7. A human reviews and merges the promotion PR.
8. The prod ArgoCD app remains OutOfSync until an operator performs the explicit
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
