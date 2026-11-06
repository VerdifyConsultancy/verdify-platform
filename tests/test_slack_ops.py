"""#1352: the MCP `slack_ops` command surface is retired; no agent profile lists it.

This file keeps its name because the in-cluster CI test list (.agent-fleet/ci.yaml, generated
in jvallery/agents) still names it.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILES = (
    "mcp/server.py",
    "hermes/iris/config.yaml",
    "deploy/k8s/components/hermes-iris/hermes-config.yaml",
)


def test_no_tool_surface_names_slack_ops():
    for name in PROFILES:
        assert "slack_ops" not in (ROOT / name).read_text(), name


def test_mcp_server_does_not_import_slack():
    text = (ROOT / "mcp/server.py").read_text()
    assert "slack_config" not in text and "handle_slack_command" not in text
