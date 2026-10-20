# MCP dispatch audit (#1010)

Each authorized dispatch produces one `mcp_tool_call` JSON event after completion, exception or cancellation, with the authenticated audience, registered tool, conservative read/write-capable classification, result class, duration and transport peer. Mixed-action tools remain write-capable even for their read actions. A completed dispatch is not a claim that a requested business action succeeded. Existing authorization denials remain separate and do not produce an allowed-call event.

Tokens, arguments, results, exception messages, session identifiers and forwarded-header values are excluded. The production entry point installs one dedicated JSON handler; the root/Uvicorn logging level cannot silence or duplicate these events. Existing authorization, tool bodies, inputs and outputs are unchanged.

`peer_ip` is explicitly the transport peer. Forwarded-header claims are ignored, including when a caller forges CF-Connecting-IP or X-Forwarded-For. No pod-wide CIDR trust or Uvicorn forwarded-header bypass is enabled. Public external-client attribution remains open until the network-owned trusted-origin path in network-infra#743 is accepted; #1007 still requires confirmation of the named external client before issuing its Access/audience credentials. This source packet does not close those acceptance criteria.

Delivery selects only the reviewed MCP image, preserving the other release pins and the ingestor. Verify the exact image on both ready MCP replicas, normal Hermes calls in both Pod logs and Loki, and unchanged authenticated 23-tool inventory. Read-only operator probes may confirm the audit event; no synthetic setpoint, plan, Slack, or other write is required.
