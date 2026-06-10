"""No-write MCP adapter used at the planner boundary.

This module stands in for write-capable tool execution in tests. Production
planner runs return one bounded payload; Verdify's MCP remains the only write
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MCPCall:
    tool_name: str
    payload: dict[str, object]


@dataclass
class MCPClient:
    calls: list[MCPCall] = field(default_factory=list)

    def call_tool(
        self, tool_name: str, payload: dict[str, object]
    ) -> dict[str, object]:
        self.calls.append(MCPCall(tool_name=tool_name, payload=payload))
        return {"tool_name": tool_name, "accepted": True}
