"""In-process MCP server exposing the dev agent's tools to the Claude Agent SDK.

Wraps app.tools.dev_tools.make_dev_tools() — the same path_guard-enforced,
plan-scoped Python functions the legacy hand-rolled dev loop already calls —
as SDK @tool-decorated functions, so both dev-loop implementations share one
tool surface instead of maintaining two. Scope enforcement (allowed_paths,
blocked commands) lives inside those functions regardless of which loop calls
them; this module is purely an adapter.

The claude_agent_sdk import is deferred into build_dev_mcp_server() so this
package stays importable — and the legacy dev loop stays fully functional —
without the optional claude-agent-sdk dependency installed.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.tools.dev_tools import TOOL_SCHEMAS, make_dev_tools

MCP_SERVER_NAME = "devtools"

# Mirrors TOOL_SCHEMAS' names/descriptions; the SDK's @tool decorator takes a
# simplified {param_name: python_type} schema rather than full JSON Schema.
# Optionality isn't expressed here — the wrapped functions already default
# missing/None kwargs correctly (see _wrap), so this is best-effort guidance
# for the model, not an enforcement layer.
_TOOL_SPECS: list[tuple[str, str, dict[str, Any]]] = [
    (
        "read_file_tool",
        "Read the contents of a file at the given relative path in the worktree. "
        "Reads the whole file by default. For a large file where you only need one "
        "part (e.g. one method, one widget), pass offset (1-based line number) and "
        "limit (number of lines) to read just that range instead of paying the token "
        "cost of the entire file.",
        {"path": str, "offset": int, "limit": int},
    ),
    (
        "write_file_tool",
        "Create a NEW file with the given content. Fails if the file already exists "
        "— use edit_file_tool for existing files. Only pass overwrite=true if the "
        "file is corrupted beyond repair.",
        {"path": str, "content": str, "overwrite": bool},
    ),
    (
        "edit_file_tool",
        "Surgically replace an exact block of code in an existing file. "
        "target_content must match exactly one occurrence unless allow_multiple=true. "
        "Prefer this over write_file_tool for all changes to existing files.",
        {"path": str, "target_content": str, "replacement_content": str, "allow_multiple": bool},
    ),
    (
        "apply_edits_tool",
        "Apply MANY surgical edits in ONE call — strongly preferred over calling "
        "edit_file_tool repeatedly. Each edit targets a file and replaces an exact "
        "block. Edits may span multiple files and are applied in order.",
        {"edits": list},
    ),
    (
        "roll_back_file_tool",
        "Discard any local uncommitted edits to a specific file, restoring it to HEAD.",
        {"path": str},
    ),
    (
        "search_codebase_tool",
        "Search for strings or patterns across the codebase. Returns up to 15 matches.",
        {"query": str},
    ),
    (
        "lookup_sdk_symbol_tool",
        "Look up the exact signature and docstring of a Flutter/Dart SDK API. "
        "Use this before writing any fix for deprecated or unknown APIs.",
        {"query": str},
    ),
    (
        "analyze_changed_files_tool",
        "Run flutter analyze scoped ONLY to planned lib/*.dart files. Use after "
        "every meaningful change to verify compilation.",
        {},
    ),
    (
        "run_command_tool",
        "Run a whitelisted shell command (e.g. 'dart run build_runner build'). "
        "Do NOT use for 'flutter analyze' — use analyze_changed_files_tool instead. "
        "'flutter test' is only allowed when the plan includes test files.",
        {"command": str},
    ),
]

# TOOL_SCHEMAS is the source of truth for tool names — this assertion catches
# the two lists drifting apart (a tool added/renamed in one but not the other)
# at import time rather than as a silent missing-tool gap at runtime.
_SCHEMA_NAMES = {spec["function"]["name"] for spec in TOOL_SCHEMAS}
_SPEC_NAMES = {name for name, _desc, _schema in _TOOL_SPECS}
assert _SCHEMA_NAMES == _SPEC_NAMES, (
    f"dev_tools_server._TOOL_SPECS out of sync with dev_tools.TOOL_SCHEMAS: "
    f"missing={_SCHEMA_NAMES - _SPEC_NAMES} extra={_SPEC_NAMES - _SCHEMA_NAMES}"
)


def mcp_tool_names() -> list[str]:
    """Full mcp__{server}__{tool} names for every dev tool, for allowed_tools."""
    return [f"mcp__{MCP_SERVER_NAME}__{name}" for name in _SCHEMA_NAMES]


def _wrap(fn: Any) -> Any:
    """Adapt a make_dev_tools() function (sync, returns str) into an async SDK
    tool handler. Runs the underlying call off-thread — several of these tools
    shell out (dart analyze, build_runner) and can take up to two minutes; a
    blocking call here would stall the whole FastAPI event loop, not just this
    run, exactly as the legacy loop is careful to avoid via asyncio.to_thread."""

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(fn, **(args or {}))
        except TypeError as exc:
            result = f"Invalid arguments for tool: {exc}"
        return {"content": [{"type": "text", "text": str(result)}]}

    return handler


def build_dev_mcp_server(
    *,
    worktree_path: str,
    allowed_paths: set[str],
    analyze_targets: list[str],
    allow_flutter_test: bool,
    planned_test_files: list[str],
    baseline_dir: str | None = None,
) -> Any:
    """Build the in-process MCP server for one dev run, pre-bound to its
    worktree and plan scope. Deferred claude_agent_sdk import — see module
    docstring."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    functions, _openai_schemas = make_dev_tools(
        worktree_path=worktree_path,
        allowed_paths=allowed_paths,
        analyze_targets=analyze_targets,
        allow_flutter_test=allow_flutter_test,
        planned_test_files=planned_test_files,
        baseline_dir=baseline_dir,
    )

    tool_defs = [
        tool(name, description, schema)(_wrap(functions[name]))
        for name, description, schema in _TOOL_SPECS
    ]

    return create_sdk_mcp_server(name=MCP_SERVER_NAME, version="1.0.0", tools=tool_defs)
