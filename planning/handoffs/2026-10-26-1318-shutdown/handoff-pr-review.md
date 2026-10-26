# Outstanding PR and CI review — 2026-10-26

Read-only GitHub/source/CI review at main `ccc4274cb790308685110a154831480f700e95d7`. No PR, branch, workload, credential or device mutation occurred. Root owns live production verification.

## Immediate recommendation

1. Merge documentation PR #1086 at exact head `b0a1020a8e74736f681281196c28b9e5f3b5b547` after a final head/status readback. Required status is success, and GitHub reports clean/mergeable. No rebase is necessary: neither edited document changed in the two commits since its base. AGENTS.md is byte-identical at the PR head and current main.
2. Let the existing main validation/build path run. The ingestor Dockerfile copies `docs/`; this docs merge naturally produces a fresher image candidate. Do not manufacture a build or production rollout.
3. Merge only that new, latest applicable candidate once its exact-head required CI and source/render applicability are verified. Keep release pins unchanged; no sync is implied.
4. Close superseded candidate PRs #1058, #1061, #1062, #1065, #1071, #1075, #1082, #1087 and then #1089 with a link to the accepted replacement, retaining branches and evidence. Do not serially merge stale candidate digests.
5. Keep #855 parked: its old green status does not authorize physical activation; it remains draft, conflicted and placeholder-bound.

## Current candidate applicability

- #1087 head `53dad33539bc4685a8cc3286b937811e47d9cd63`: required status success; clean. It proposes ingestor digest `8558ab651eb6e034c81354a369db6dc656652d050726a274c2f9da95b425ffdd` from source `2439c5f1ce2d2b578c6186829e683f6aecbef87f`, before the HA token/script changes. Superseded by #1089.
- #1089 head `c97b5196b9f2a20d65dfdb9398f88e1015dd4c6f`: required status success; clean. It proposes ingestor digest `c3cec27027b33ceef359b78891be358994268ff0f16fc14684df241b7427ee48` from source `3c9d6ad8ae3b826bfb552be3186b321980bd8a96`. Main's subsequent #1090 changes only setpoint-server manifest/pin, outside the ingestor copy inputs. Its one-line candidate delta remains applicable today, but #1086's docs merge would supersede it.
- Both candidates leave `release-pins.yaml` unchanged. The ingestor release pin remains `c10b34418daf526d33c1b0f3b1e59cddf4837814faa40e2fe6084bf4653d6aaf`. Candidate bookkeeping is not a promotion.
- Current main contains #1088/#1090 pod-template changes, including the ingestor config revision and HA token mount. A future full Argo sync must examine that existing drift. A render-identical docs/candidate delta relative to current main does not prove main is render-identical to live production.

## Retained CI handles

| Workflow | UID | Result | Bound revision | Finished UTC |
|---|---|---|---|---|
| `verdify-platform-pr-ci-qlfgm` | `338e7e08-7443-4e2f-ab75-0c186ce4c611` | Succeeded | `2439c5f1ce2d2b578c6186829e683f6aecbef87f` | 2026-10-25 17:09:21 |
| `verdify-platform-pr-ci-gps29` | `68dc76bc-0135-4872-9218-67a465ba4973` | Succeeded | `b0a1020a8e74736f681281196c28b9e5f3b5b547` | 2026-10-25 17:09:16 |
| `verdify-platform-ci-6gn6p` | `55158486-86e6-4a46-9505-847ad2d3cf1c` | Succeeded | `2439c5f1ce2d2b578c6186829e683f6aecbef87f` | 2026-10-25 17:15:00 |

The first lookup for `6gn6p` used the PR-CI prefix and correctly returned NotFound; its actual name is the build-workflow prefix shown above. No restart or re-submission was attempted.

## Evidence files

- `handoff-pr-inspection.json`: exact PR head/base, full diff metadata and commit statuses for all eleven open PRs.
- `source-comparisons.json`: changes from #1086 base to current main, #1087 source to #1089 source, and #1089 source to current main.
- `old-workflow-status.json`, `old-build-workflow-status.json`: sanitized named Workflow results and bound arguments.
- `main-pr-source-metadata.json`: main ref and #1088/#1090 merge identities.
- `main-AGENTS.md`, `1086-AGENTS.md`: identical SHA256 `0e2da55815e74094d6cd8e6fae5c9925181c1b3d49c2adeeeaff1b3177a53482`.
- `main-ingestor.Dockerfile`, `main-kustomization.yaml`, `main-release-pins.yaml`, `1089-kustomization.yaml`, `1086-RELEASE-CHECKLIST.md`, `main-firmware-signed-artifacts.md`: exact source used for applicability review.
