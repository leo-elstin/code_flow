"""Dev agent tools: plain Python functions + OpenAI function schemas.

No LangChain dependency. Use make_dev_tools() to get (functions_dict, openai_schemas)
pre-bound to a specific worktree and plan-scoped allowed paths for a single run.
"""
from __future__ import annotations

import json
import os
import shlex
from typing import Any

from app.core.logging_config import get_logger
from app.tools.dart_tools import run_dart_analyze
from app.tools.docs_tool import lookup_sdk_symbol
from app.tools.edit_file import EditFileError, edit_file
from app.tools.filesystem import roll_back_file, snapshot_baseline, write_file
from app.tools.grep import grep_codebase, read_file
from app.tools.safe_shell import run_safe_command

logger = get_logger("dev_tools")

_RUN_COMMAND_STDOUT_TAIL = 2000
_RUN_COMMAND_STDERR_TAIL = 1000


def _norm_rel(path: str) -> str:
    return os.path.normpath(str(path).strip().lstrip("/")).replace("\\", "/")


def _truncate_tail(text: str, limit: int) -> str:
    if not text or len(text) <= limit:
        return text or ""
    return f"...[truncated {len(text) - limit} chars]...\n{text[-limit:]}"


def _scoped_analyze(worktree_path: str, analyze_targets: list[str]) -> str:
    targets = [
        rel for rel in analyze_targets if os.path.isfile(os.path.join(worktree_path, rel))
    ]
    if not targets:
        return "No planned lib/*.dart files exist yet — create them before analyzing."
    result = run_dart_analyze(worktree_path, targets)
    payload: dict[str, Any] = {
        "passed": result.get("passed", False),
        "error_count": result.get("error_count", 0),
        "error_lines": result.get("error_lines", [])[:25],
        "targets": targets,
    }
    if not payload["passed"] and not payload["error_lines"]:
        payload["output_tail"] = _truncate_tail(
            result.get("stdout") or result.get("stderr") or "", 1500
        )
    return json.dumps(payload, indent=2)


def _guard_run_command(
    command: str,
    worktree_path: str,
    analyze_targets: list[str],
    allow_flutter_test: bool,
    planned_test_files: list[str],
) -> tuple[str | None, str]:
    """Returns (block_result, command). If block_result is not None it is the
    final tool output and nothing should be executed."""
    try:
        tokens = shlex.split(command.strip())
    except ValueError:
        return None, command
    if len(tokens) >= 2 and tokens[0] in ("flutter", "dart"):
        if tokens[1] == "analyze":
            return (
                "Redirected to analyze_changed_files_tool (full-project analyze is disabled):\n"
                + _scoped_analyze(worktree_path, analyze_targets),
                command,
            )
        if tokens[1] == "test":
            if not allow_flutter_test:
                return (
                    "BLOCKED: the approved plan contains no test files, so flutter test "
                    "is disabled. Use analyze_changed_files_tool instead.",
                    command,
                )
            requested = [tok for tok in tokens[2:] if not tok.startswith("-")]
            invalid = [
                rel for rel in (_norm_rel(tok) for tok in requested)
                if rel not in planned_test_files
            ]
            if invalid:
                return (
                    f"BLOCKED: flutter test may only target planned test files. "
                    f"Not in plan: {', '.join(invalid)}",
                    command,
                )
            if not requested and planned_test_files:
                return None, " ".join(tokens[:2] + planned_test_files)
    return None, command


# ---------------------------------------------------------------------------
# OpenAI tool schemas
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file_tool",
            "description": "Read the contents of a file at the given relative path in the worktree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file_tool",
            "description": (
                "Create a NEW file with the given content. "
                "Fails if the file already exists — use edit_file_tool for existing files. "
                "Only pass overwrite=true if the file is corrupted beyond repair."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean", "default": False},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file_tool",
            "description": (
                "Surgically replace an exact block of code in an existing file. "
                "target_content must match exactly one occurrence unless allow_multiple=true. "
                "Prefer this over write_file_tool for all changes to existing files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "target_content": {"type": "string", "description": "Exact code block to find"},
                    "replacement_content": {"type": "string", "description": "Replacement code"},
                    "allow_multiple": {"type": "boolean", "default": False},
                },
                "required": ["path", "target_content", "replacement_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_edits_tool",
            "description": (
                "Apply MANY surgical edits in ONE call — strongly preferred over calling "
                "edit_file_tool repeatedly. Each edit targets a file and replaces an exact "
                "block (whitespace-tolerant). Edits may span multiple files and are applied "
                "in order; if one fails the rest still apply and you get a per-edit report, "
                "so batch every independent change you know you need instead of one per turn."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "target_content": {
                                    "type": "string",
                                    "description": "Exact code block to find",
                                },
                                "replacement_content": {
                                    "type": "string",
                                    "description": "Replacement code",
                                },
                                "allow_multiple": {"type": "boolean", "default": False},
                            },
                            "required": ["path", "target_content", "replacement_content"],
                        },
                    },
                },
                "required": ["edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "roll_back_file_tool",
            "description": "Discard any local uncommitted edits to a specific file, restoring it to HEAD.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_codebase_tool",
            "description": "Search for strings or patterns across the codebase. Returns up to 15 matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_sdk_symbol_tool",
            "description": (
                "Look up the exact signature and docstring of a Flutter/Dart SDK API. "
                "Use this before writing any fix for deprecated or unknown APIs. "
                "Example: query='Color.withValues' to get its exact parameter signature."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_changed_files_tool",
            "description": (
                "Run flutter analyze scoped ONLY to planned lib/*.dart files. "
                "Use after every meaningful change to verify compilation. "
                "Do NOT call run_command_tool with 'flutter analyze' — use this instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command_tool",
            "description": (
                "Run a whitelisted shell command (e.g. 'dart run build_runner build'). "
                "Do NOT use for 'flutter analyze' — use analyze_changed_files_tool instead. "
                "'flutter test' is only allowed when the plan includes test files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------

def make_dev_tools(
    *,
    worktree_path: str,
    allowed_paths: set[str],
    analyze_targets: list[str],
    allow_flutter_test: bool,
    planned_test_files: list[str],
    baseline_dir: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (functions_dict, openai_schemas) pre-bound to this run's worktree and plan scope.

    functions_dict maps tool name → callable(**kwargs) -> str
    openai_schemas is the list to pass directly to openai client as tools=
    """

    def _scope_error(path: str) -> str | None:
        if _norm_rel(path) in allowed_paths:
            return None
        return (
            f"BLOCKED: {path} is not in the approved plan "
            f"(allowed: {', '.join(sorted(allowed_paths)) or 'none'}). "
            "Do not modify out-of-scope files."
        )

    def read_file_tool(path: str) -> str:
        try:
            return read_file(worktree_path, path)
        except Exception as exc:
            return f"Error reading file {path}: {exc}"

    def write_file_tool(path: str, content: str, overwrite: bool = False) -> str:
        blocked = _scope_error(path)
        if blocked:
            return blocked
        abs_target = os.path.join(worktree_path, _norm_rel(path))
        if os.path.isfile(abs_target) and not overwrite:
            return (
                f"BLOCKED: {path} already exists. Use edit_file_tool to change it; "
                "only pass overwrite=true if the file is corrupted beyond repair."
            )
        try:
            snapshot_baseline(baseline_dir, worktree_path, path)
            write_file(worktree_path, path, content)
            return f"Successfully wrote file to {path}."
        except Exception as exc:
            return f"Error writing file {path}: {exc}"

    def edit_file_tool(
        path: str,
        target_content: str,
        replacement_content: str,
        allow_multiple: bool = False,
    ) -> str:
        blocked = _scope_error(path)
        if blocked:
            return blocked
        try:
            snapshot_baseline(baseline_dir, worktree_path, path)
            edit_file(worktree_path, path, target_content, replacement_content, allow_multiple=allow_multiple)
            return f"Successfully replaced content in {path}."
        except EditFileError as exc:
            return f"Edit failed: {exc}"
        except Exception as exc:
            return f"Error editing file {path}: {exc}"

    def apply_edits_tool(edits: list[dict[str, Any]]) -> str:
        if not edits:
            return "No edits provided."
        results: list[str] = []
        ok = 0
        for idx, spec in enumerate(edits, 1):
            path = spec.get("path", "")
            blocked = _scope_error(path)
            if blocked:
                results.append(f"[{idx}] {path}: {blocked}")
                continue
            try:
                snapshot_baseline(baseline_dir, worktree_path, path)
                edit_file(
                    worktree_path,
                    path,
                    spec.get("target_content", ""),
                    spec.get("replacement_content", ""),
                    allow_multiple=spec.get("allow_multiple", False),
                )
                ok += 1
                results.append(f"[{idx}] {path}: OK")
            except EditFileError as exc:
                results.append(f"[{idx}] {path}: Edit failed: {exc}")
            except Exception as exc:
                results.append(f"[{idx}] {path}: Error: {exc}")
        header = f"Applied {ok}/{len(edits)} edit(s)."
        return header + "\n" + "\n".join(results)

    def roll_back_file_tool(path: str) -> str:
        blocked = _scope_error(path)
        if blocked:
            return blocked
        try:
            roll_back_file(worktree_path, path, baseline_dir=baseline_dir)
            return f"Successfully rolled back uncommitted changes to {path}."
        except Exception as exc:
            return f"Error rolling back {path}: {exc}"

    def search_codebase_tool(query: str) -> str:
        try:
            results = grep_codebase(worktree_path, query)
            return json.dumps(
                [
                    {"file": m.file_path, "line": m.line_number, "text": m.line_text}
                    for m in results[:15]
                ]
            )
        except Exception as exc:
            return f"Error searching codebase: {exc}"

    def lookup_sdk_symbol_tool(query: str) -> str:
        try:
            return lookup_sdk_symbol(worktree_path, query)
        except Exception as exc:
            return f"Error looking up SDK symbol {query}: {exc}"

    def analyze_changed_files_tool() -> str:
        return _scoped_analyze(worktree_path, analyze_targets)

    def run_command_tool(command: str) -> str:
        guard_result, safe_command = _guard_run_command(
            command, worktree_path, analyze_targets, allow_flutter_test, planned_test_files
        )
        if guard_result is not None:
            return guard_result
        try:
            res = run_safe_command(worktree_path, safe_command)
            return (
                f"Return Code: {res.returncode}\n"
                f"STDOUT:\n{_truncate_tail(res.stdout, _RUN_COMMAND_STDOUT_TAIL)}\n"
                f"STDERR:\n{_truncate_tail(res.stderr, _RUN_COMMAND_STDERR_TAIL)}"
            )
        except Exception as exc:
            return f"Error running command: {exc}"

    functions: dict[str, Any] = {
        "read_file_tool": read_file_tool,
        "write_file_tool": write_file_tool,
        "edit_file_tool": edit_file_tool,
        "apply_edits_tool": apply_edits_tool,
        "roll_back_file_tool": roll_back_file_tool,
        "search_codebase_tool": search_codebase_tool,
        "lookup_sdk_symbol_tool": lookup_sdk_symbol_tool,
        "analyze_changed_files_tool": analyze_changed_files_tool,
        "run_command_tool": run_command_tool,
    }

    return functions, TOOL_SCHEMAS
