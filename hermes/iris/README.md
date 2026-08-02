# Hermes — Iris profile

Canonical config for the `hermes-iris` agent container that replaces OpenClaw
as Verdify's sole planner gateway. OpenClaw was decommissioned on 2026-05-11;
Hermes is now the only production route for Iris planning cycles.

## Files

- `config.yaml` — canonical Hermes profile: OpenAI GPT-5.6 Sol
  xhigh-reasoning, single profile, MCP-only tool surface. The k3s ConfigMap
  contains a mirror with only the MCP URL changed to ClusterIP DNS;
  `tests/test_17_planner_health_surface.py` enforces parsed equivalence.
  Merging config does not activate it; live rollout is separately gated.
- `SOUL.md` — durable identity and behavioral reference. Repo-root
  `slack.yaml` is the versioned Slack reference. Neither is copied by the
  current GitOps helper; runtime delivery remains a separately tracked
  reconciliation gap.
- Runtime state — **not in git**. The `verdify-hermes-iris-data` PVC is mounted
  at `/opt/data`; an init container seeds `config.yaml` from the
  `verdify-hermes-iris-config` ConfigMap on every pod start.
- Runtime secrets — **not in git**. The Deployment references
  `verdify-hermes` and `verdify-hermes-slack` by name. Never inspect or print
  their values.

## Deployment

```bash
# Validate canonical/mirrored config equivalence and the prod render.
# This performs no live mutation.
make hermes-deploy-config

# The profile checksum makes the reviewed, operator-gated Argo sync roll the
# Deployment and rerun its config-seed init container. Then prove Available.
make hermes-smoke
```

## Updating Config

Edit both canonical `hermes/iris/config.yaml` and the environment-specific
mirror under `deploy/k8s/components/hermes-iris/hermes-config.yaml`, then run
`make hermes-deploy-config`. Commit and merge the reviewed desired-state
change. An authorized operator performs the manual prod Argo sync. A restart
is declarative: the pod-template profile checksum changes with the ConfigMap,
so that sync rolls the pod and reseeds the PVC. `make hermes-restart` is only an
operator-gated emergency restart without a desired-state change; it creates a
live `restartedAt` drift that the next reviewed Argo sync must reconcile.
`make hermes-smoke` first requires the live ConfigMap data to hash to the exact
desired checksum, then waits for that checksum on the live Deployment and for
the resulting rollout and Availability. This prevents a selective sync of only
the Deployment from reporting a false green:

```bash
CONFIRM_PROD_RESTART=1 make hermes-restart
```

## Roll Forward

OpenClaw rollback is gone. Revert the desired-state commit (including both
config copies and checksum), run the validator, use the same reviewed gated Argo
sync, then run `make hermes-smoke`. Validate recovery through
`plan_delivery_log`; reserve `make hermes-restart` for emergency imperative
recovery and reconcile its `restartedAt` drift afterward.
