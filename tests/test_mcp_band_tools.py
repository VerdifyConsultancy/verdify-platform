"""#1334 follow-up: the MCP band reads, band_profile and band_profile_preview, with no database.

mcp/server.py loads under the FastMCP stub of tests/test_mcp_audience_auth.py; each tool runs API-23's service against
the stand-in connection of tests/test_band_profile.py. The band is operator-owned (decisions 2026-11-05): no MCP tool
writes it, and Iris cannot call either read. tests/test_mcp_transport_runtime.py lists them through the real MCP SDK.
"""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest
from test_band_profile import LIVE, Conn, openapi_errors
from test_mcp_audience_auth import AGENT_TOOLS, clean_auth_env, mcp_server, mcp_types  # noqa: F401

from verdify_services import band_profile as bp

BAND_TOOLS = ("band_profile", "band_profile_preview")
PHASES = [index * 4.0 / 144 for index in range(144)]


def payload(result) -> dict:
    [content] = result.content
    return json.loads(content.text)


class ToolConn(Conn):
    def __init__(self):
        super().__init__(PHASES)
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.fixture()
def conn(mcp_server, monkeypatch):  # noqa: F811
    connection = ToolConn()

    async def db():
        return connection

    async def bias(_conn):
        return 0.1

    async def no_write(*_args, **_kwargs):
        raise AssertionError("an MCP band tool reached the band Save")

    monkeypatch.setattr(mcp_server, "_db", db)
    monkeypatch.setattr(bp, "_night_bias", bias)
    monkeypatch.setattr(bp, "write", no_write)
    return connection


def test_the_band_reads_are_for_agents_and_admin_never_iris_and_are_read_only(mcp_server):  # noqa: F811
    for tool in BAND_TOOLS:
        assert mcp_server.TOOL_AUDIENCES[tool] == frozenset({"agent", "admin"}), tool
        assert tool in mcp_server._TOOL_READ_ONLY and tool in AGENT_TOOLS
        assert tool not in mcp_server.audience_allowlist("iris") and tool not in mcp_server.HERMES_REQUIRED_TOOLS
        assert inspect.signature(getattr(mcp_server, tool)).return_annotation is inspect.Signature.empty


def test_no_mcp_tool_writes_the_band(mcp_server):  # noqa: F811
    """The planner may not write the band: the only band tools are the two reads, and Iris has neither."""
    band = {name for name in mcp_server.TOOL_AUDIENCES if "band" in name}
    assert band == set(BAND_TOOLS)
    assert not {name for name in mcp_server.audience_allowlist("iris") if "band" in name}
    source = inspect.getsource(mcp_server.band_profile) + inspect.getsource(mcp_server.band_profile_preview)
    assert "band_service.write" not in source and "recorded_write" not in source


def test_band_profile_is_api_23_with_no_draft(mcp_server, mcp_types, conn):  # noqa: F811
    result = asyncio.run(mcp_server.band_profile(day="2026-11-05"))
    assert result.isError is False and conn.closed
    body = payload(result)
    assert openapi_errors("BandProfilePreview", body) == []
    assert body["current"]["anchors"] == LIVE and body["draft"] == body["current"] and body["changed"] == []
    assert len(body["measured"]["temp"]) == len(body["t"]) == 144
    assert body["forecast"]["vpd"] == [0.4] * 144


def test_band_profile_preview_evaluates_a_draft_without_saving(mcp_server, mcp_types, conn):  # noqa: F811
    result = asyncio.run(
        mcp_server.band_profile_preview(temp_night=67.7, anchors={"band_vpd_high_sm": 1.3}, day="2026-11-05")
    )
    assert result.isError is False
    body = payload(result)
    assert body["draft"]["anchors"]["band_temp_target_mid"] == 67.7
    assert body["draft"]["anchors"]["band_vpd_high_sm"] == 1.3
    assert "band_vpd_high_sm" in body["changed"] and "band_temp_target_mid" in body["changed"]
    assert body["current"]["anchors"] == LIVE


@pytest.mark.parametrize(
    ("arguments", "code", "field"),
    [
        ({"day": "tomorrow"}, "invalid_request", "day"),
        ({"anchors": {"band_temp_low": 60.0}}, "unknown_target", "anchor"),
    ],
)
def test_a_bad_argument_is_problem_json(mcp_server, mcp_types, conn, arguments, code, field):  # noqa: F811
    result = asyncio.run(mcp_server.band_profile_preview(**arguments))
    assert result.isError is True
    body = payload(result)
    assert (body["code"], body["field"]) == (code, field)
