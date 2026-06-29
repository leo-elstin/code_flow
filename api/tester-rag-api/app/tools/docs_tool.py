import json
import os
from typing import Any

from app.core.config import settings
from app.tools.lsp_client import DartLspSession, uri_to_abs_path


def _read_lines_around(file_path: str, center_line: int, context_lines: int = 15) -> str:
    """Read a window of lines from a file around a specific line number."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        return f"Error reading file {file_path}: {e}"

    start_line = max(0, center_line - context_lines)
    end_line = min(len(lines), center_line + context_lines + 1)

    snippet = []
    for i in range(start_line, end_line):
        prefix = ">>" if i == center_line else "  "
        snippet.append(f"{prefix} {i + 1:4d} | {lines[i].rstrip()}")

    return "\n".join(snippet)


def lookup_sdk_symbol(worktree_path: str, query: str) -> str:
    """
    Use LSP to find the SDK source definition of a given symbol (e.g., Color.withValues)
    and return the source code snippet containing its signature and docstring.
    """
    # Quick sanity check that the worktree exists
    if not os.path.isdir(worktree_path):
        return f"Error: Worktree path {worktree_path} does not exist."

    evidence = []
    try:
        with DartLspSession(worktree_path) as session:
            # Query workspace symbols
            symbols = session.workspace_symbols(query)
            
            for symbol in symbols:
                location = symbol.get("location", {})
                uri = location.get("uri")
                if not uri:
                    continue
                
                abs_path = uri_to_abs_path(uri)
                if not abs_path:
                    continue
                
                # Check if this file is part of the Flutter/Dart SDK rather than the local project
                # Typical SDK paths contain "sky_engine" or "packages/flutter/lib"
                # If the path starts with the project root, it's not the SDK.
                project_root = os.path.realpath(worktree_path)
                abs_path_real = os.path.realpath(abs_path)
                
                is_sdk = False
                if not abs_path_real.startswith(project_root + os.sep):
                    if "sky_engine" in abs_path_real or os.path.join("packages", "flutter", "lib") in abs_path_real:
                        is_sdk = True
                    # Alternatively, if it's anywhere in the DART_BIN or FLUTTER_BIN tree
                    dart_sdk_root = os.path.dirname(os.path.dirname(settings.DART_BIN))
                    if dart_sdk_root and abs_path_real.startswith(dart_sdk_root):
                        is_sdk = True
                
                if is_sdk:
                    range_info = location.get("range", {})
                    start = range_info.get("start", {})
                    line = start.get("line", 0)  # 0-indexed
                    
                    snippet = _read_lines_around(abs_path_real, line, context_lines=15)
                    evidence.append(
                        f"Found symbol: {symbol.get('name')}\n"
                        f"File: {abs_path_real}\n"
                        f"Line: {line + 1}\n\n"
                        f"```dart\n{snippet}\n```\n"
                    )
                    
                    # Return the first good SDK match we find
                    if evidence:
                        return "\n---\n".join(evidence)

    except Exception as e:
        error_msg = str(e)

    if not evidence:
        # Fallback to simple grep if LSP failed or found nothing
        try:
            import shutil
            import subprocess
            flutter_bin = shutil.which(settings.FLUTTER_BIN)
            if flutter_bin:
                flutter_bin_real = os.path.realpath(flutter_bin)
                flutter_root = os.path.dirname(os.path.dirname(flutter_bin_real))
                search_paths = [
                    os.path.join(flutter_root, "packages", "flutter", "lib"),
                    os.path.join(flutter_root, "bin", "cache", "pkg", "sky_engine", "lib")
                ]
                for sp in search_paths:
                    if not os.path.isdir(sp):
                        continue
                    # run a quick grep for the symbol
                    proc = subprocess.run(
                        ["grep", "-rn", f"{query}", sp],
                        capture_output=True, text=True, timeout=5
                    )
                    if proc.returncode == 0:
                        lines = proc.stdout.splitlines()
                        for line in lines[:3]: # take first few matches
                            parts = line.split(":", 2)
                            if len(parts) >= 3:
                                file_path = parts[0]
                                line_num = int(parts[1]) - 1
                                snippet = _read_lines_around(file_path, line_num, context_lines=15)
                                evidence.append(
                                    f"Found symbol (via grep): {query}\n"
                                    f"File: {file_path}\n"
                                    f"Line: {line_num + 1}\n\n"
                                    f"```dart\n{snippet}\n```\n"
                                )
                        if evidence:
                            return "\n---\n".join(evidence)
        except Exception:
            pass
            
        return f"Could not find SDK symbol matching '{query}' (LSP error: {error_msg if 'error_msg' in locals() else 'None'}). Try a more specific or broader query, or check if the API exists."

    return "\n---\n".join(evidence)
