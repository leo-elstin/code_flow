"""Tests for the in-process MCP tool adapter (app.mcp.dev_tools_server).

Only build_dev_mcp_server itself needs claude-agent-sdk installed (it calls
create_sdk_mcp_server/tool); everything else — the name-sync assertion, the
naming scheme, and the _wrap adapter's behavior — has zero SDK dependency and
runs unconditionally."""
import asyncio
import os

import pytest

from app.mcp.dev_tools_server import (
    MCP_SERVER_NAME,
    _TOOL_SPECS,
    _wrap,
    build_dev_mcp_server,
    mcp_tool_names,
)
from app.tools.dev_tools import TOOL_SCHEMAS, make_dev_tools


def test_tool_specs_match_dev_tools_schemas():
    schema_names = {spec["function"]["name"] for spec in TOOL_SCHEMAS}
    spec_names = {name for name, _desc, _schema in _TOOL_SPECS}
    assert schema_names == spec_names


def test_mcp_tool_names_are_namespaced_to_this_server():
    names = mcp_tool_names()
    assert len(names) == len(TOOL_SCHEMAS)
    assert all(name.startswith(f"mcp__{MCP_SERVER_NAME}__") for name in names)


def test_wrap_returns_sdk_content_shape_and_enforces_scope(tmp_path):
    worktree = str(tmp_path)
    functions, _schemas = make_dev_tools(
        worktree_path=worktree,
        allowed_paths={"lib/foo.dart"},
        analyze_targets=[],
        allow_flutter_test=False,
        planned_test_files=[],
    )
    handler = _wrap(functions["write_file_tool"])

    # In-scope write succeeds and the SDK content envelope is well-formed.
    result = asyncio.run(handler({"path": "lib/foo.dart", "content": "// ok"}))
    assert result["content"][0]["type"] == "text"
    assert "Successfully wrote" in result["content"][0]["text"]
    assert os.path.isfile(os.path.join(worktree, "lib/foo.dart"))

    # Out-of-plan write is blocked by the same guard the legacy loop relies
    # on — the adapter doesn't weaken enforcement, it just changes the
    # calling convention.
    blocked = asyncio.run(handler({"path": "lib/bar.dart", "content": "// no"}))
    assert "BLOCKED" in blocked["content"][0]["text"]
    assert not os.path.isfile(os.path.join(worktree, "lib/bar.dart"))


def test_wrap_reports_invalid_arguments_instead_of_raising():
    handler = _wrap(lambda path: f"read {path}")
    result = asyncio.run(handler({"wrong_kwarg": "x"}))
    assert "Invalid arguments" in result["content"][0]["text"]


def test_build_dev_mcp_server_requires_sdk_but_degrades_cleanly(tmp_path):
    pytest.importorskip("claude_agent_sdk")
    server = build_dev_mcp_server(
        worktree_path=str(tmp_path),
        allowed_paths=set(),
        analyze_targets=[],
        allow_flutter_test=False,
        planned_test_files=[],
    )
    assert server is not None
