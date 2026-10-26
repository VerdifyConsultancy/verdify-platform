# #1010 shutdown handoff

Read-only verification completed 2026-10-26T15:22:04.828220+00:00. All **11 checked worktrees are clean** and every same-named remote branch exactly matches its local HEAD. No commit, push, PR, merge, sync, job, scheduling, credential or device action was performed by this shutdown check. Running cluster work was left untouched.

## Retained #1010 source

| Owner | Remote branch | Exact SHA |
|---|---|---|
| VerdifyConsultancy/verdify-platform | [`codex/mcp-trusted-attribution-20261026`](https://github.com/VerdifyConsultancy/verdify-platform/tree/codex/mcp-trusted-attribution-20261026) | `33e5ad3beb8830b50c29877cd57cf7bd8de71506` |
| jvallery/network-infra | [`codex/mcp-tunnel-row-20261026`](https://github.com/jvallery/network-infra/tree/codex/mcp-tunnel-row-20261026) | `cefc6f5c2f1cfb8681c2d37caea243f7ea9036e1` |
| jvallery/agents | [`codex/verdify-mcp-attribution-owner-20261026`](https://github.com/jvallery/agents/tree/codex/verdify-mcp-attribution-owner-20261026) | `c4cda6a61ddb7c2d72bb60d83ca8c96cad509794` |

All three branches are remotely durable WIP source; fresh GitHub head queries found **no PR for any of these branches**. No #1010 CI/deployment/real external-client receipt is claimed. Product source had 52 focused source/pure tests plus renderer/Ruff/diff checks; Network had 18 focused contract tests and actual-source gate; Agents had full registered regen/write/check. These are the recorded preparation checks, not new shutdown test runs.

Product worktree: `/private/tmp/verdify-mcp-attribution-20261026`; Network: `/private/tmp/network-mcp-attribution-20261026`; central ownership: `/private/tmp/agents-mcp-attribution-20261026`. Detailed source plans, comparison hashes and CI commands remain in `1010-source-preparation.md` and `1010-adjacent-source-preparation.md`. The original source comparison proves unchanged ingestor Pod template and unchanged release-pins bytes; re-check the complete current render before any resumed delivery.

## Related retained work — do not replay completed packets

| Work | Retained branch | Exact remote/local SHA |
|---|---|---|
| prior guard refusals | `codex/private-guard-refusals-20261025` | `80c01fc08030afbf53522280afd1e9cc43fe2dbd` |
| prior release checklist | `codex/release-checklist-current-path-20261025` | `b0a1020a8e74736f681281196c28b9e5f3b5b547` |
| prior unblocked followthrough | `codex/unblocked-graph-followthrough-20261025` | `eda5daedfc01d4106047c5b801fa42cd5afb1f1e` |
| current positive fixture | `codex/positive-exposure-fixture-20261026` | `c616e4702db5f3b4723f68e39ee7a408e2c4599b` |
| current finite contributors | `codex/finite-contributors-20261026` | `d7c25b0c620d537d56f938302d8be236a4d46fbb` |
| current issue inventory | `codex/resume-review-inventory-20261026` | `f90263d186ffa5da76ea79ba49be34493f05b8b1` |
| codex/qualification-resume-20261026 | `codex/qualification-resume-20261026` | `6d2dccca89200b60eba916f22f8ef9169490f4f6` |
| codex/qualification-receipts-20261026 | `codex/qualification-receipts-20261026` | `129783ed68810ef9db1a170660cee20d0f2a8760` |

Fresh GitHub reads confirm #1083 merged 2026-10-25T15:43:32Z, #1085 merged 16:49:31Z, #1086 merged 2026-10-26T13:08:29Z, #1091 merged 14:25:02Z, and #1094 merged 14:59:41Z. The merged #1094 head is **6d2dccca89200b60eba916f22f8ef9169490f4f6**, on `codex/qualification-resume-20261026`; the older `codex/positive-exposure-fixture-20261026` at c616e470 is retained history, not the authoritative successor to resume. CI/background build terminal state and receipts belong to the main shutdown handoff; observe existing handles rather than submitting duplicates.

## Exact resume order

1. Read the current user hold, coordinator steer/inflight and actual GitHub/main/runtime state. Preserve the unresolved writer reconciliation hold; telemetry freshness never clears it. No new ESP32 connection, node cordon, experiment activation, credential or Access-policy change is authorized by this source packet.
2. Review the retained product + central-ownership source together and open/qualify source PRs only after the broader pause is explicitly lifted. Regenerate with the registered renderer on a genuine conflict or enforced stale-output refusal; a moved main alone is not a rebase trigger. Keep the ordinary/internal MCP path transport-only.
3. Receiver first: qualify source and the MCP image, promote only the reviewed MCP candidate through the supported named-image path, and compare the full production render. Stop if it would replace the ingestor or apply unrelated drift. Deliver the isolated tier-1 tunnel twin, tier-2 listener 81/8001, strict namespace-AND-Pod policy and minimal logging. Verify permitted tier-1 access and unauthorized Pod/LAN denial before changing the tunnel row.
4. Review/qualify/merge the exact Network MCP row/checksum companion. Preserve its existing SNI `mcp.verdify.ai`, Host, public CA, TLS verification, Access denial and every unrelated hostname. Merging its source does not advance the current pinned Cloudflare Application.
5. Ask the coordinator to consolidate the eventual reviewed Network main SHA into the existing pin train only after receiver proof. The staged request is `1010-cloudflare-pin-request.md`. The coordinator alone owns Agents merge/pin delivery; never blanket/prune-sync the SHA-pinned parent. Read source and live pin again, take the edge-write lock and retain the rollback through the applicable probe/soak window.
6. Finish source/live parity, spoof negatives, actual public authenticated DB-read evidence and Loki correlation only through an approved usable identity/path. A trusted join requires the exact named tunnel router/entrypoint, bounded CF-Ray/time match, and MCP peer matching the exact tier-2 Pod emitting the access row. Internal synthetic headers cannot substitute.

## Pin and Access blockers

- Fresh catalog source still pins both targetRevision and source-revision annotation to **c5ee6266deca80e216678b5030f5cd0ae44d1289**. Fresh [Agents #5708](https://github.com/jvallery/agents/pull/5708) remains OPEN, head **5fe8a29ab101bd2cedfd22903dc51d68f6dc019f**, proposing the same Cloudflare child advance to f131eef4 for TeslaMate. No currently open consolidated pin PR was found during preparation; last consolidated #5697 is merged. Do not create a competing pin or overwrite #5708.
- Network main already contains a TeslaMate hostname row absent from the current Cloudflare pin. A direct jump from c5ee6266 to the eventual MCP revision would therefore carry **two host changes** until the coordinator delivers/combines the qualified TeslaMate delta. Review the full current-to-candidate tree and disclose both.
- Existing declared `mcp.verdify.ai` Access policy is **deny/everyone**, with no current external client credential by the #1007 decision. The packet deliberately leaves it unchanged. Confirm actual provider/client state on resume; genuine authenticated public-tool acceptance may remain blocked until an approved external path exists. Do not weaken authentication, issue credentials or claim this criterion completed from an internal test.

Custody evidence: `1010-final-shutdown-custody.json`. Root handles GitHub issue updates and the canonical shutdown link. This lane is stopped after writing this handoff.
