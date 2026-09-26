# MCP protocol availability observer

The `observer` audience authenticates MCP transport but has an empty tool
allow-list. It can initialize the stateless protocol and list zero tools.
All operational tool calls remain denied in the production `enforce` mode.
Existing Iris, experiment and admin audience privileges are unchanged.

The shared `mcp-observer` Kustomize component references only
`verdify-prod/verdify-mcp-observer`, key `token`. The active `overlays/prod`
includes it after the Secret was reconciled by the Agents-owned, Secret-only,
non-pruning `verdify-prod-secrets-local-prod` Application. No credential is
stored here. `overlays/prod-dark` is not an Argo source.

Delivery order:

1. The encrypted provider Secret is reconciled through its owning Agents
   application; verify name, key names and tracking metadata only.
2. Build MCP from the integrated current source and pin its new Zot digest.
   The earlier `b09a298d` observer build predates later MCP changes and is not
   a valid pin for this branch. Preserve the old running digest for rollback.
3. Review the entire Argo diff and coordinate with any pending C0 migration or
   hook. Use a full hook-running sync for a combined release. An MCP-only
   selective sync is valid only when no migration or hook part is involved and
   the full pending diff is reviewed.
4. Verify both replicas, source and running digest, transport authentication and
   audience denial locally and through the public Cloudflare route.
5. After qualification, publish the separate encrypted consumer profile and
   activate source-owned endpoint cells.

Probe `POST /mcp` with `Content-Type: application/json` and
`Accept: application/json, text/event-stream`, body:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"vallery-endpoint-prober","version":"1"}}}
```

Anonymous and invalid credentials must return 401. The observer must return 200
with the expected protocol initialization result and no session ID. A tools/list
request must return an empty list. No operational tool execution is necessary
for live availability probing; source tests prove all registered tools deny
the observer and the real SDK test exercises an attempted tool call safely.
Root 404 is a route miss, not MCP availability. The internal `/readyz` route
remains separate and is not exposed as an external authentication bypass.

Rollback reverts the MCP image/component together while retaining the sealed
Secret. No existing credential is revoked or rotated. This probe proves MCP
transport/protocol availability, not greenhouse device execution or tool data.

Post-merge restart: verdify-mcp, through its owning Argo Deployment after
provider Secret reconciliation and the integrated image pin.
