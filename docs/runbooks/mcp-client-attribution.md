# Trusted MCP client attribution (#1010)

The per-tool MCP event records its actual transport peer. A public client is
attributed by joining that event to the isolated Cloudflare ingress record;
forwarded headers never replace the MCP peer or audience identity.

## Source and delivery boundary

The source-prepared path is:

`Cloudflare -> cloudflared -> tunnelclient:9444 -> verdify-mcp-tunnel-forward -> verdify-traefik:81/8001 -> verdify-t2-mcp-tunnel-0@file -> MCP:8000`.

The shared `tunnelclient` listener already exists in network-infra. Its
NetworkPolicy admits only cloudflared Pods in the Cloudflare namespace. It is
ClusterIP-only, trusts no X-Forwarded-* claims, and is absent from the apps VIP.
The new Verdify 8001 listener permits only the tier-1 Traefik workload in its
namespace; the namespace and Pod selectors are in the same policy peer. The
file renderer accepts only the exact MCP host, path, backend and middleware
contract on this listener.

Ordinary web/websecure, Verdify 8000 and direct internal MCP remain available.
The ordinary MCP router removes CF-Connecting-IP and CF-Ray before forwarding.
An internal client may still submit its own CF-Ray claim directly to MCP; that
does not make it an external or trusted call.

This source does not move the public tunnel row. Network-infra owns
`deploy/networking/cloudflared/cloudflared-config.yaml`: only the exact
`mcp.verdify.ai` row moves to
`https://traefik-tunnel-client.traefik-apps.svc.cluster.local:443`, preserving the
qualified TLS origin name, CA and original Host. The fleet control-plane owns
the catalog declaration and its reviewed source pin at
`platform/gitops/applications/local-staging/cloudflare-edge.yaml`. Its
coordinator advances that child through the consolidated pin change; the
SHA-pinned parent is never blanket- or prune-synced. Register the new routes
and boundary in the central ownership record in the same delivery packet.

Post-merge restart: verdify-mcp, when its new image is explicitly promoted.
The source and content-hashed routing changes also roll verdify-traefik with
zero unavailable replicas. This change has no migration or ingestor change.
Promote only the reviewed MCP candidate using the supported named-image mode
of `scripts/promote-release-pins.py`; do not promote all five images by default.
Before every delivery, compare the entire intended production render and stop
if it would replace the ingestor or apply unrelated drift.

## Log contract

Tier-2 JSON access logs default-drop both fields and headers. Retained fields
are timestamp, duration, unqualified Traefik ClientHost, request host/method, router,
entrypoint and HTTP status. Only CF-Connecting-IP and CF-Ray headers remain.
ClientHost may derive from X-Forwarded-For and is logging context, never transport
proof. The join uses the MCP event peer and exact emitting proxy Pod metadata.
RequestPath is deliberately absent: Traefik 3.7.1 includes its query string.
Authorization, Cookie, bodies, arguments and results are not logged.

The MCP event adds only `edge_request_id_claim`: one ASCII CF-Ray-shaped value
of 16–32 hexadecimal characters followed by a hyphen and a three-letter uppercase
datacenter code. Duplicate, malformed or oversized values are omitted. This
claim never changes `peer_ip`, `client_ip_source`, authorization or dispatch.

Only all of the following together qualify an external attribution receipt:

1. The access record has `entryPointName="verdify-tunnel"`,
   `RouterName="verdify-t2-mcp-tunnel-0@file"` and
   `RequestHost="mcp.verdify.ai"`. Its canonical CF address equals the actual
   test client's public egress; a CF header on another router is untrusted.
2. The access record's CF-Ray exactly matches the MCP event's bounded
   `edge_request_id_claim`. The event's actual `peer_ip` matches the exact tier-2
   Pod emitting that access record, using captured Pod UID/IP metadata, and
   `client_ip_source` remains `transport_peer`. An internal
   caller copying the Ray fails this transport check.
3. The records fall within the one bounded test request's time window and show
   the expected audience, read-only tool, HTTP response and dispatch result.
   Preserve event timestamps from Loki. Do not assume one event per HTTP request
   for arbitrary clients: a protocol request may batch multiple tool calls.

An IP is connection attribution, not a person's identity. Audience remains
separate. Check the selected Cloudflare host's Worker and pseudo-IPv4 behavior
before treating its canonical address as the original visitor address.

## Live verification and inverse

Before an edge write, read current coordinator steer/inflight state and take
the edge-write lock through delivery, probes and the applicable soak. Deliver
the Verdify receiver first. Prove 8001 is rejected from an existing unauthorized
Pod and LAN source, accepted from the exact tier-1 workload, and inaccessible
through the ordinary public listener. Preserve the current internal MCP path
and all unrelated ingress behavior.

After the exact Network revision and catalog child are delivered, verify all
three cloudflared connectors and the complete hostname-map diff. Use an existing
approved audience and a single bounded public DB-backed read-only tool call.
Read credentials in memory; never print or persist them. Collect the exact join
above from Loki and test forged CF/XFF/Ray claims through the ordinary ingress
and the public Cloudflare path. Invalid claims must not change attribution.
No actuator call, new credential, firmware action, ESP32 connection or ingestor
restart is required. If a usable existing public credential/path is unavailable,
record that actual blocker rather than weakening authentication.

On regression restore the exact MCP tunnel row and catalog pin first. The new
receiver then becomes inert. Remove the new Verdify route/listener/port/policy
only after no active row uses them. Keep the delivered per-tool audit event and
all other host rows. #1010 stays open until external attribution, spoof rejection
and Loki custody have current live evidence and independent verification.

References: [Cloudflare headers](https://developers.cloudflare.com/fundamentals/reference/http-headers/),
[Traefik access logs](https://doc.traefik.io/traefik/v3.7/reference/install-configuration/observability/logs-and-accesslogs/),
[existing trusted-listener pattern](https://github.com/jvallery/network-infra/blob/main/docs/runbooks/ha-tunnel-attribution-743.md).
