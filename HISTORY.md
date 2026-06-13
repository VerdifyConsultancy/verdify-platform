# Verdify Platform History

Last updated: 2026-06-13

Agent name: `verdify-platform`

This file is a compact index of historically completed work with evidence
links. Detailed historical handoffs and evidence artifacts live in
`/Users/jason/Orbit/context_dump/verdify-platform/`.

## Completed Milestones

| Date | Work | Evidence |
|---|---|---|
| 2026-05-31 to 2026-06-02 | CI/CD green path established: manifests, images, digest write-back, and gating repairs. | GitHub issues #69, #78, #81, #82, #92, #99, #126. |
| 2026-05-31 to 2026-06-07 | k3s app desired state authored and cutover path executed. | GitHub issues #70, #73, #86, #216. |
| 2026-06-07 | Single-writer cutover executed; Verdify prod became the live writer. | GitHub issue #216. |
| 2026-06-07 | Iris VM powered off / host units retired. | GitHub issue #217. |
| 2026-06-07 to 2026-06-08 | HA incident response: resource governance, spread/PDBs, backups, edge HA, descheduler, CNPG groundwork. | GitHub milestone #14; issues #226-#234, #236, #241, #243, #244, #264. |
| 2026-06-08 to 2026-06-11 | Firmware/control optimization wave and dashboard work. | GitHub issues #249-#256, #281-#285; PRs #270, #325, #328, #329. |
| 2026-06-12 | Repo cleanup archived historical context to Orbit and codified Codex operating docs. | Commits `ecad3dd` and `7a48a0b`; GitHub issue #330. |
| 2026-06-13 | Lane board normalization created current repo docs and GitHub fallback tracking blocks. | GitHub issues #331, #332, #333. |

## Important Incidents And Decisions

- Migration rollback safety was codified after the 2026-05-30 live-commit
  incident; see `AGENTS.md` and CI job `migration-rollback-safety`.
- `main` is the single canonical branch as of 2026-06-10; `live/platform-main`
  is retired.
- `verdify-prod-dark` is a legacy live App name; it currently points at the real
  prod writer overlay. Rename is cosmetic but gated because App deletion can
  prune live resources if done wrong.
- Staging is retired; `verdify-dev` is the proving environment and
  `verdify-prod` is manually synced behind the device-write gate.
