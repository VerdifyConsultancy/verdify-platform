# MCP protocol availability observer

The `observer` audience authenticates MCP transport but has an empty tool
allow-list. It can initialize the stateless protocol and list zero tools.
All operational tool calls remain denied in the production `enforce` mode.
Existing Iris, experiment and admin audience privileges are unchanged.

The shared `mcp-observer` Kustomize component is enabled by both production
shapes. It references only `verdify-prod/verdify-mcp-observer`, key `token`.
Agents owns the encrypted Secret under its existing Secret-only, non-pruning
`verdify-prod-secrets-local-prod` Application. No credential is stored here.

Delivery order:

1. Reconcile the encrypted provider Secret and verify name/key metadata only.
2. Publish and pin an image built from the observer source. The existing image
   does not recognize this audience; do not deploy the component with old code.
3. Reconcile only the intended MCP Deployment through its owning Argo app;
   preserve the device-dark workload and all unrelated resources.
4. Verify both replicas, pinned image digest, transport authentication and
   audience denial, locally and through explicit Cloudflare addresses.
5. Only after qualification, publish the separate encrypted consumer profile
   and activate source-owned endpoint cells.

Probe `POST /mcp` with `Content-Type: application/json` and
`Accept: application/json, text/event-stream`, body:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"vallery-endpoint-prober","version":"1"}}}
```

Anonymous and invalid credentials must return401. The observer must return200
with the expected protocol initialization result and no session ID. A tools/list
request must return an empty list. No operational tool execution is necessary
for live availability probing; source tests prove all registered tools deny
the observer and the real SDK test exercises an attempted tool call safely.
Root404 is a route miss, not MCP availability. The internal `/readyz` route
remains separate and is not exposed as an external authentication bypass.

Rollback reverts the MCP image/component together while retaining the sealed
Secret. No existing credential is revoked or rotated. This probe proves MCP
transport/protocol availability, not greenhouse device execution or tool data.

Qualified image source: b09a298d29dfb512ad5859e477d5a9da450708b3.
Native build: `repo-build-verdify-mcp-1b5fa910a865f87807f8bda5`, Succeeded.
Digest: `sha256:56b1c48bdf3416e336243cf238f31a3402efbc11ae8258989e49b2fd790dcae6`.
Post-merge restart: verdify-mcp, via the owning Argo Deployment rollout only.
The initial source CI failed its restart-documentation check; that failure is
retained and this explicit delivery statement corrects it without bypassing CI.
