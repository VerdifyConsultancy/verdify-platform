# Git Cleanup Inventory - 2026-06-12

This records the branch/worktree cleanup requested on 2026-06-12. It is an
audit trail, not an active backlog. The follow-up tracker item is
[#330](https://github.com/VerdifyConsultancy/verdify-platform/issues/330).

## Starting state

- Repo: `VerdifyConsultancy/verdify-platform`.
- Main worktree: `/Users/jason/repos/verdify-platform`.
- Local branch: `main`.
- `origin/main` advanced by one commit before cleanup:
  `2c1c766962d0999a2d7b6a88da53ad2b0ab95eb9`.
- Dirty main-worktree files before this cleanup commit:
  - `CLAUDE.md`
  - `README.md`
  - `ACCESS_MATRIX.md`
  - `AGENT_LANE.md`
  - `APP_INVENTORY.md`
  - `COORDINATION_REQUESTS.md`
  - `FINAL_REPORT.md`
  - `SECRETS_AUDIT.md`
  - `docs/AGENT_STATE.md`
  - `docs/CODEX_WORKFLOW.md`

## Worktrees

No dirty auxiliary worktrees were found. Clean auxiliary worktrees were removed
after their branch heads were preserved either in archive refs or existing
remote refs.

Removed clean worktrees:

| Path | Branch | Disposition |
| --- | --- | --- |
| `.claude/worktrees/agent-a2bc64caef3ef4a7c` | `worktree-agent-a2bc64caef3ef4a7c` | archived to `origin/archive/2026-06-12/worktree-agent-a2bc64caef3ef4a7c` |
| `.claude/worktrees/agent-afe6f2ec4c9cdacad` | `worktree-agent-afe6f2ec4c9cdacad` | archived to `origin/archive/2026-06-12/worktree-agent-afe6f2ec4c9cdacad` |
| `.claude/worktrees/ha-1-lane-a` | `ha-1-lane-a-resource-governance` | archived to `origin/archive/2026-06-12/ha-1-lane-a-resource-governance` |
| `.claude/worktrees/lane220-prod-promotion` | `lane220-prod-promotion` | archived to `origin/archive/2026-06-12/lane220-prod-promotion` |

Pruned stale worktree metadata for missing `/private/tmp` paths:

| Path | Branch | Disposition |
| --- | --- | --- |
| `/private/tmp/wt-ha3` | `laptop-root/ha-3-singleton-lease-fence` | archived to `origin/archive/2026-06-12/laptop-root/ha-3-singleton-lease-fence` |
| `/private/tmp/wt-ha4-cnpg` | `ha-4-cnpg-dev` | archived to `origin/archive/2026-06-12/ha-4-cnpg-dev` |
| `/private/tmp/wt-ha6-cnpg` | `ha-6-cnpg-prod` | not re-archived; active remote `origin/ha-6-cnpg-prod` already exists |
| `/private/tmp/wt-verdify-traefik` | `laptop-root/verdify-two-tier-traefik-prod` | archived to `origin/archive/2026-06-12/laptop-root/verdify-two-tier-traefik-prod` |

After cleanup, `git worktree list --porcelain` showed only the main worktree.

## Local branches

All local non-`main` branch refs were deleted after preserving unique branch
heads under `origin/archive/2026-06-12/*` where needed. After cleanup,
`git branch --list` showed only `main`.

## Archived remote refs

The following archive refs were pushed to origin. Counts are from:

```sh
git rev-list --left-right --count origin/main...<ref>
```

`branch-only` is the number of commits reachable from the branch and not
`origin/main` at cleanup time.

| Archive ref | branch-only | Tip | Date | Subject |
| --- | ---: | --- | --- | --- |
| `origin/archive/2026-06-12/chore/staging-db-iscsi-ssd-migration` | 1 | `560e844` | 2026-05-31 | feat(staging): migrate verdify-db PVC local-path -> synology-iscsi-ssd (worker, frees node1) |
| `origin/archive/2026-06-12/ha-1-lane-a-resource-governance` | 1 | `028f0bf` | 2026-06-07 | feat(ha-1/lane-a): resource governance - PriorityClasses, CPU limits, LimitRange (#226/#227/#229) |
| `origin/archive/2026-06-12/ha-1-stateless-multireplica` | 1 | `266da04` | 2026-06-07 | ha(verdify-prod): Sprint ha-1 / LANE B - stateless multi-replica + hard spread + PDBs (#230) |
| `origin/archive/2026-06-12/ha-4-cnpg-dev` | 1 | `e707c40` | 2026-06-07 | HA-4: TimescaleDB-on-CNPG dev cluster + WAL/PITR + gated prod migration runbook (#243/#244/#245) |
| `origin/archive/2026-06-12/lane-219-vault-lab-pipeline` | 1 | `d5eed68` | 2026-06-07 | feat(lab): automate vault->lab.verdify.ai content/build pipeline (#124/#219) |
| `origin/archive/2026-06-12/lane-221-firmware-iteration` | 1 | `d55505f` | 2026-06-07 | lane #221: safe firmware/band iteration loop + band-viz dashboard + tuning tooling |
| `origin/archive/2026-06-12/lane-c-firmware-bands` | 1 | `9c4542d` | 2026-06-07 | feat(db): band redesign proposal + season-resolver guard (#250 #251 #253) |
| `origin/archive/2026-06-12/lane-c-firmware-landingzone` | 5 | `db1c515` | 2026-06-07 | style: ruff format the kube-exec backend tests |
| `origin/archive/2026-06-12/lane220-prod-promotion` | 2 | `400ec31` | 2026-06-07 | docs(prod-promotion): credit the existing ci.yml code-level Device-Write Safety Gate |
| `origin/archive/2026-06-12/lane3/planner-db-netpol` | 2 | `ad0e37f` | 2026-06-07 | fix(hermes-iris): use args not command so image ENTRYPOINT is preserved |
| `origin/archive/2026-06-12/laptop-root/graphs-grafana-prod-498` | 1 | `79ae1f2` | 2026-06-07 | grafana(#498): OUR OWN graphs.verdify.ai in k3s vs in-cluster TimescaleDB |
| `origin/archive/2026-06-12/laptop-root/ha-3-singleton-lease-fence` | 3 | `eb3c1dc` | 2026-06-07 | docs(ha-3): gated live-arm runbook for the writer fence + fast-failover |
| `origin/archive/2026-06-12/laptop-root/ha-7-descheduler` | 1 | `50b374b` | 2026-06-07 | HA-1.9 (#234): conservative descheduler CronJob (dry-run) + 4-layer ingestor/singleton exclusion + gated arm |
| `origin/archive/2026-06-12/laptop-root/verdify-ai-ingressroute` | 1 | `4281c0f` | 2026-05-31 | feat(staging): host-route verdify through shared apps Traefik (.7.10) for *.vallery.net + api.verdify.ai |
| `origin/archive/2026-06-12/laptop-root/verdify-edge-ingressroute` | 1 | `9e0a6f9` | 2026-05-31 | feat(staging): host-route verdify through shared apps Traefik (.7.10) for *.vallery.net + api.verdify.ai |
| `origin/archive/2026-06-12/laptop-root/verdify-ingressroute-port-fix` | 1 | `bbd4cc6` | 2026-05-31 | fix(staging): verdify-api IngressRoute target Service port 8080 -> 80 (was 404 at .7.10) |
| `origin/archive/2026-06-12/laptop-root/verdify-two-tier-traefik-prod` | 1 | `13d8509` | 2026-06-07 | D7 two-tier Verdify Traefik: apps .7.10 -> verdify-traefik -> services |
| `origin/archive/2026-06-12/worktree-agent-a2bc64caef3ef4a7c` | 1 | `c81bb69` | 2026-06-10 | db(migrations): firmware-v2 DB contract - crop_band_anchors + cannabis/lime activation + zone audit columns + solar fn_zone_vpd_targets re-point (161-164) |
| `origin/archive/2026-06-12/worktree-agent-afe6f2ec4c9cdacad` | 1 | `2f720fa` | 2026-06-10 | dispatcher: deterministic crop+solar band - anchor sync, per-zone audit, solar ephemeris, lighting differentiation (firmware-v2 section B1/B2/B6/B7) |

## Active remote non-main refs left in place

Remote refs that already existed on origin were not deleted. Owner review should
decide whether to merge, rebase, archive, or delete each one.

| Remote ref | branch-only | Tip | Date | Subject |
| --- | ---: | --- | --- | --- |
| `origin/codex/verdify-platform-codeowners-baseline` | 1 | `9c59a04` | 2026-06-07 | governance: add verdify platform codeowners |
| `origin/codex/verdify-route-classification` | 1 | `33f20bb` | 2026-06-07 | Classify Verdify route exposure metadata |
| `origin/coordinator/migrate-idempotent-restore-aware` | 0 | `58f8911` | 2026-05-31 | staging+prod: repin verdify-migrate to idempotent restore-aware image sha-0537abc1 (#83) |
| `origin/coordinator/pin-staging-canonical-digests` | 0 | `f19a35c` | 2026-05-31 | staging: pin overlay to real published in-org digests (unblock ArgoCD repoint) |
| `origin/coordinator/repin-appcode-images` | 0 | `95df5bd` | 2026-05-31 | staging+prod: repin api/mcp/ingestor to the app-code build sha-881d4d8 (#58/#79 image==source) |
| `origin/coordinator/sprint1-followups-82-89` | 0 | `f332265` | 2026-05-31 | ci: drop CODEOWNERS human gate - verdify agent prod-promotes autonomously (Jason 2026-05-31) |
| `origin/coordinator/sprint1-staging-green` | 0 | `f339673` | 2026-05-31 | staging: canonical sealed secret is agent-fleet-control (already in-cluster); drop redundant secrets.sops.yaml (#66) |
| `origin/coordinator/sprint2-unblocked` | 5 | `ac622fe` | 2026-05-31 | #86 prod overlay completeness: volume1/SSD DB PVC + promote-same-digest |
| `origin/coordinator/sprint5-build` | 0 | `5403555` | 2026-05-31 | feat(#116): verdify-www into k3s (GHCR image + component + dev/prod IngressRoutes) |
| `origin/coordinator/sprint6-staging-buildout` | 2 | `887543c` | 2026-05-31 | sprint6 staging-buildout: relocate dev/prod ArgoCD App handover to canonical path + per-env syncPolicy + runbook |
| `origin/coordinator/state-of-the-union-2026-06-01` | 2 | `7093c3c` | 2026-06-01 | docs(sotu): Appendix A - full cross-board backlog reconciliation (agents Theme-C + network-infra area:verdify + new items) |
| `origin/dashboards/drop-flaky-cronjob` | 1 | `e515d8f` | 2026-06-10 | grafana: drop the band-curve refresh CronJob - fresh pods cannot connect to verdify-db |
| `origin/dashboards/v2-solar-bands` | 9 | `beabea2` | 2026-06-10 | fix: move 16 firmware-v2 fields from ClimateActionLogRow to ClimateRow (drift guard) |
| `origin/deploy/firmware-ota-secret-artifacts` | 1 | `719551e` | 2026-06-09 | deploy(firmware-ota): SealedSecret shape + sealing runbook for k3s OTA (#301) |
| `origin/docs/backlog-canonical-pointer` | 1 | `9470060` | 2026-06-09 | docs(backlog): point cycle index at the 2026-06-09 unified replan (epics #286/#287/#288 + Project #1) |
| `origin/docs/replan-followup-2026-06-09` | 1 | `6efcd45` | 2026-06-09 | docs: replan follow-up (2026-06-09 PM) - R4 recurring, OTA=backlog, sub-issues, landing rule |
| `origin/firmware/cicd-golden-path` | 0 | `e213b66` | 2026-05-31 | Merge pull request #68 from VerdifyConsultancy/coordinator/fix-container-publish-reusable-wf |
| `origin/firmware/v2-solar-bands` | 13 | `3a740dc` | 2026-06-10 | registry: drop fw_clamp from the 5 firmware-v2-retired stress-wet params |
| `origin/firmware/vanda-band-compliance-rearch` | 0 | `aa6518c` | 2026-05-30 | Vanda sprint-3: software backlog close-out (146/149/150 staged, M1-M14 hygiene, P3a heat pre-cool, P1a, N1, P3 software) |
| `origin/fix-ingestor-hermes-key-213` | 2 | `b143afb` | 2026-06-07 | fix(ingestor): wire HERMES_IRIS_API_KEY so AI-plan delivery authenticates to Hermes (#213) |
| `origin/fix/ingestor-ha-token-energy-hydro` | 1 | `f2bd88e` | 2026-06-07 | fix(ingestor): mount verdify-ha-token so energy + hydro telemetry resumes |
| `origin/fix/verdify-db-backup-netpol-race-and-rpo-alert` | 1 | `84bd54f` | 2026-06-07 | fix(db-backup): wait out the CNI netpol-program race + add RPO freshness alarm |
| `origin/ha-1/probe-health-prestop` | 1 | `b81f3f0` | 2026-06-07 | fix(ha): decouple api liveness from DB + add graceful drain to serving surfaces (#231) |
| `origin/ha-6-cnpg-prod` | 1 | `b32bf42` | 2026-06-07 | HA-6: prod-target CNPG TimescaleDB cluster (1+2 sync, GHCR-pinned, WAL/PITR) + cutover runbook (#244/#245) |
| `origin/iris/19-g9-prompt-copy` | 1 | `0f0c717` | 2026-06-01 | copy(G9): name compliance_v2_attributable_pct as the scored compliance in planner prompt + MCP scorecard docstring (#19) |
| `origin/iris/34-twin-mvp-dashboard` | 1 | `1bc6253` | 2026-06-01 | feat(twin): prod-vs-reality divergence dashboard + local offline twin overlay (#34) |
| `origin/iris/g2-hypertables` | 1 | `59d37c8` | 2026-06-04 | db(g2): convert the 15 missing telemetry tables to hypertables (parity 19) |
| `origin/iris/g3-compression-retention` | 1 | `84a4631` | 2026-06-04 | db(g3): recreate compression + retention background-job policies (parity 5 compressed) |
| `origin/iris/migrate-impact-fix` | 1 | `cf7e138` | 2026-06-01 | ci(container-publish): treat db/** as migrate-impacting; repin dev planner (#99) |
| `origin/lane5/hermes-iris-api-server-host` | 1 | `96db678` | 2026-06-07 | fix(verdify-prod): bind hermes-iris api_server on 0.0.0.0 (CrashLoop fix) |
| `origin/laptop-root/prod-overlay-cutover-reconcile` | 1 | `25c72bb` | 2026-06-07 | verdify-prod: reconcile overlay to PROVEN live cutover shape (egress + state vol) |
| `origin/laptop-root/verdify-k3s-context-docs` | 1 | `1235b7f` | 2026-06-07 | docs: update agent guide to k3s reality (cutover + single-writer fence + HA M7) |
| `origin/live/platform-main` | 0 | `e96f60d` | 2026-06-10 | dev env buildout + #312 netpol fix: graphs-dev, nightly prod->dev DB restore, setpoint-server DB allow |
| `origin/plan-delivery-fix-210-213` | 2 | `6c63dcf` | 2026-06-07 | fix(ingestor): wire HERMES_IRIS_API_KEY so AI-plan delivery authenticates to Hermes (#213) (#224) |

## Tracker and project board status

- Created GitHub issue
  [#330](https://github.com/VerdifyConsultancy/verdify-platform/issues/330)
  for archive review.
- The GitHub connector could not create the issue (`403 Resource not accessible
  by integration`), so `gh` was run with the existing local `GH_TOKEN` handoff.
- Adding the issue to GitHub Projects was not completed because the available
  token lacks `read:project`; `gh project list --owner VerdifyConsultancy`
  returned that missing-scope error.

## Safety notes

- No production sync, firmware OTA, device VLAN action, secret rotation, or
  destructive prod DB action was performed.
- No remote branch was deleted.
- Raw secret values were not printed or committed.

## Verification

Passed locally before commit:

- `git diff --check`
- Secret-pattern scan over changed docs
- `kustomize build deploy/k8s/overlays/{dev,prod,prod-dark,staging}` with
  `0` rendered `kind: Secret` objects per overlay
- `make lint`
- `.venv/bin/ruff format --check ingestor/ api/ mcp/ scripts/*.py tests/ verdify_schemas/`
- `.venv/bin/python -m pytest tests/test_migration_rollback_safety.py -q`
- `.venv/bin/python -m pytest tests/test_device_write_gate.py -v`

Attempted but not locally green:

- `make test` failed because this Mac does not have the live-stack smoke-test
  prerequisites running (`verdify-timescaledb` Docker container, `systemctl`,
  and `/srv/greenhouse/.venv`).
- Local schema/drift suites that query Docker Postgres were blocked by the
  missing `verdify-timescaledb` container.
