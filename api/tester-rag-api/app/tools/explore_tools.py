"""Read-only tools for the agentic code explorer.

The explorer runs before a plan exists, so — unlike the dev tools — these are
bound to the real project directory and never write. They mirror the way an
engineer (or Cursor's agent) locates code: grep, list a directory, read a file,
follow the references, repeat.
"""

import json
import os
from typing import Any, Callable

from app.tools.grep import grep_codebase, read_file as _grep_read_file
from app.tools.path_guard import PathGuardError, resolve_read_path

_READ_CAP = 6000
_SEARCH_CAP = 20

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_codebase",
            "description": (
                "Search the repository with ripgrep. Use class/symbol names, feature "
                "keywords, or file-name fragments. Returns up to 20 matches (file, line, text)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file (relative path) to understand structure and find related paths.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List the entries of a directory (relative path) to discover module layout.",
            "parameters": {
                "type": "object",
                "properties": {"directory": {"type": "string", "default": "."}},
                "required": [],
            },
        },
    },
]


def make_explore_tools(project_path: str) -> tuple[dict[str, Callable[..., str]], list[dict[str, Any]]]:
    """Return (functions, schemas) for read-only exploration of *project_path*."""

    # Each tool absorbs unexpected kwargs (**_) — models often pass extras like
    # start_line/end_line/max_results, and an unknown kwarg must not crash the loop.
    def search_codebase(query: str, max_results: int = _SEARCH_CAP, **_: Any) -> str:
        try:
            results = grep_codebase(project_path, query, max_results=int(max_results or _SEARCH_CAP))
            return json.dumps(
                [
                    {"file": m.file_path, "line": m.line_number, "text": m.line_text[:200]}
                    for m in results[:_SEARCH_CAP]
                ]
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error searching for {query!r}: {exc}"

    def read_file(path: str, **_: Any) -> str:
        try:
            resolve_read_path(project_path, path)  # traversal guard
            return _grep_read_file(project_path, path, max_chars=_READ_CAP)
        except PathGuardError as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001
            return f"Error reading {path}: {exc}"

    def list_dir(directory: str = ".", **_: Any) -> str:
        try:
            abs_dir = resolve_read_path(project_path, directory or ".")
            if not os.path.isdir(abs_dir):
                return f"Not a directory: {directory}"
            entries = []
            for name in sorted(os.listdir(abs_dir)):
                if name.startswith("."):
                    continue
                entries.append(name + ("/" if os.path.isdir(os.path.join(abs_dir, name)) else ""))
            return json.dumps(entries)
        except Exception as exc:  # noqa: BLE001
            return f"Error listing {directory}: {exc}"

    return (
        {"search_codebase": search_codebase, "read_file": read_file, "list_dir": list_dir},
        TOOL_SCHEMAS,
    )
