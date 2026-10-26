# #1010 independent source/security review

Reviewed at 2026-10-26T14:38:57.455814+00:00. Result: **clear for the next source/CI step, with no blocking source or security finding**. This is not deployment authorization, live external attribution proof, or issue closure.

## Inspected evidence

Read the actual uncommitted diff and both new files in the MCP attribution worktree, the source-preparation and live-chain trace, registered renderer, prior/current full production renders, existing test fixture and 52-case JUnit receipt, and the retained Traefik 3.7.1 access-log implementation. All11 source hashes still match `1010-source-custody.json` at this review. No tests, CI, GitHub, cluster, provider, credential, device or scheduling writes were performed. One bounded independent `kustomize build` and YAML comparison were run locally.

## Source findings

- The new trusted port is isolated as intended: ClusterIP Service81 -> container8001; only the exact MCP route uses `verdify-tunnel`, and that entrypoint is explicitly nondefault. Its ingress peer combines namespace `traefik-apps` AND both exact tier-1 workload labels in one selector. The existing broader grants stop at8000. The focused tests enumerate every rendered policy selecting the proxy, including numeric ranges and all-port grants, and reject namespace-only, label-only, OR-peer and pod-CIDR alternatives. Actual live policies/CNI enforcement still require readback and positive/negative probes.
- The renderer admits the exact trusted MCP host/path/backend/middleware contract and rejects another route attaching to the trusted entrypoint. Existing ordinary and internal routes remain. Ordinary MCP strips both CF headers; application code does not promote CF/XFF into an identity or change authorization.
- Both access-log fields and headers default-drop. The allowlist omits RequestPath (whose pinned implementation includes query strings), Authorization, Cookie, bodies, arguments and results. Only explicit routing/status/time fields and two CF headers are enabled. The actual retained logger canonicalizes header names and case-folds field names, matching these flags. The live logger must still be checked for flag/config acceptance and unwanted payload leakage.
- `_tool_edge_request_id_claim` permits only a single bounded ASCII CF-Ray-shaped value; duplicate, invalid, control, non-ASCII and oversized values are omitted. It remains named a claim, while `peer_ip` and `client_ip_source=transport_peer` retain their original semantics. Tool results, arguments and exceptions are not added to the event.
- The receipt contract correctly requires the exact trusted router/entrypoint/host and request CF headers, bounded Ray/time/audience/tool/result join, and the exact emitting tier-2 Pod UID/IP matched to actual MCP `peer_ip`. A Ray alone, an ordinary-route CF header, or a direct internal caller carrying a copied Ray cannot qualify.
- Ownership and cross-owner delivery are explicit. The network companion changes only the exact MCP tunnel row; the Agents-owned catalog pin is a separate coordinated action. Receiver-first delivery, rollback ordering, edge lock, steer/inflight check, independent live verifier and applicable soak remain required. No broad root sync or ingestor restart is part of this design.

## Nonblocking documentation correction

The runbook calls retained `ClientHost` a “transport client host.” The actual pinned Traefik logger sets it from `req.RemoteAddr` and then overwrites it with `X-Forwarded-For` when present (`traefik371-accesslog-logger.go` lines256–260). Describe it as log context, never authenticated raw peer evidence. The acceptance logic already uses the independent MCP `peer_ip` plus exact emitting proxy Pod metadata, so this wording does not undermine the implementation. In the live receipt explicitly select `request_Cf-Connecting-Ip` and `request_Cf-Ray`, rather than similarly named origin/downstream header fields.

## Independently verified render boundary

-144 resources before,148 after; zero rendered Secret objects.
- Ingestor Pod template unchanged; canonical SHA256 `79b0b32638866d9f6d9c37de37c3354b0f87bfbc430b979f99615805fc8c852f`.
- Current rendered MCP Pod template unchanged: source-only until its image is explicitly promoted.
- `release-pins.yaml` bytes unchanged; SHA256 `af705be512d51f3fd8be0046d4101317a24513a071f855ff0e291911061d47fa`.
- Independent complete current render SHA256 `5b8fb81db0d64c91ae868d091c1dbaea2cdf91ecc953558a8414d8958a1afab5`,2484768bytes; custody `1010-independent-current-render.yaml`.

## Acceptance limits and remaining gates

The reviewed52-test receipt is focused source/pure-logic evidence, not a real Traefik startup, network denial, CI image provenance or public-client receipt. Native CI and exact MCP build/promotion custody remain pending. The public tunnel row/catalog pin is not changed in this source packet. Before delivery, refresh current main/live drift and every ingress policy affecting the new receiver; do not disturb running green CI merely because main moved.

Before #1010 can close, prove accepted tier-1 traffic and denied unauthorized Pod/LAN traffic on8001, receiver rollout health, unchanged ordinary/internal paths and authentication, exact reviewed Cloudflare child revision and all connectors, real canonical public egress with Worker/pseudo-IPv4 settings checked, a bounded authenticated DB-backed read-only tool call and precise edge/tool join in Loki, spoof negatives through ordinary and public paths, applicable soak, and independent acceptance. No new credentials, device calls, ESP32 connection, node action or all-five image promotion is necessary or justified.
