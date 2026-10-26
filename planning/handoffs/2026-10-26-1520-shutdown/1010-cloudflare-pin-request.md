# Coordinator pin request — Verdify platform #1010

**STAGED REQUEST ONLY. Do not merge/deliver before the Verdify receiver receipt.**

Please consolidate the Cloudflare child advance with the existing pin train after:

1. Verdify's reviewed source, MCP-only image promotion and exact receiver delivery are complete.
2. The new8001 admission boundary is proved from permitted tier-1 and denied ordinary Pod/LAN sources; ordinary/direct/internal headers remain untrusted.
3. Network's exact MCP tunnel-row companion has required CI and is merged to main. Its full40-character merge revision is the eventual pin value; no feature-branch or placeholder pin is authorized.
4. The complete current/live-to-candidate Cloudflare tree diff has been reviewed, including the pending TeslaMate row described below. The same source-revision annotation must equal targetRevision. Every other pin is preserved.

Owning file: `platform/gitops/applications/local-staging/cloudflare-edge.yaml`.
Current source/annotation: `c5ee6266deca80e216678b5030f5cd0ae44d1289`.
Content owner/path: `jvallery/network-infra:deploy/networking/cloudflared`.
The coordinator alone owns its merge/pin delivery. Use an exact named-child reconciliation with no parent blanket/prune sync and hold the edge lock through probes/soak. Capture current comparison again immediately before actuation.

No consolidated pin PR is currently OPEN in the read snapshot. Last consolidated [#5697](https://github.com/jvallery/agents/pull/5697) merged2026-10-26T02:39:09Z; parent-only [#5705](https://github.com/jvallery/agents/pull/5705) merged04:34:38Z. The important existing overlap is OPEN [#5708](https://github.com/jvallery/agents/pull/5708), `claude/teslamate-pins`, head `5fe8a29ab101bd2cedfd22903dc51d68f6dc019f`. It proposes:

- Cloudflare child `c5ee6266...` -> Network `f131eef4f1bc36c57db4f21c8189c03c5b0f541b` for the TeslaMate row.
- An unrelated AppProjects adoption pin and canonical-revision guard adjustment.

It is TeslaMate-specific, not a general consolidated train. Do not create a competing Cloudflare catalog PR or silently replace its target. Resolve under the coordinator: either finish that reviewed pin first, or combine its qualified Cloudflare delta with this later MCP delta in the single train. Do not consume its unrelated changes by copying or editing its branch here.

Verified source difference from currently pinned `c5ee6266` to this Network preparation's base178512e is only the already-declared `teslamate.vallery.net` tunnel row and its checksum. This preparation adds only the exact MCP service change and checksum. Therefore a direct jump from the current pin would carry **two hostname changes**, not solely MCP, until the TeslaMate pin has delivered. Preserve this in the comparison and do not claim an MCP-only live delta prematurely.

The existing MCP Access application is declared Block Everyone; no current external client credential is provisioned by design (#752/#1007). This request does not alter that policy. Genuine public authenticated MCP acceptance waits for an approved usable existing identity/path; synthetic internal headers do not establish that acceptance.

No pin file was edited, no PR/comment was posted, and no shared write was performed for this request.
