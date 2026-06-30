import os
from typing import Any

from app.agents.analyst import CodeAnalyst
from app.api.context import resolve_import_path, should_skip_file
from app.services.project_explorer import ProjectExplorer
from app.services.project_guide import load_project_guide
from app.services.run_activity import append_activity
from app.tools.grep import GrepMatch, grep_terms, normalize_search_terms, read_file
from app.tools.lsp_client import expand_via_lsp

_analyst = CodeAnalyst()
_SKIP_DIRS = (
    ".venv", "build", "node_modules", ".dart_tool", "Pods", ".symlinks", ".gradle", "Build", ".idea"
)


def _expand_imports(project_path: str, seed_files: set[str], depth: int = 2) -> set[str]:
    final_files = set(seed_files)
    to_process = list(seed_files)
    while to_process and depth > 0:
        next_batch: list[str] = []
        for file_path in to_process:
            if not os.path.isfile(file_path):
                continue
            for imp in _analyst.get_imports(file_path):
                resolved = resolve_import_path(file_path, imp, project_path)
                if resolved and resolved not in final_files and not should_skip_file(resolved):
                    final_files.add(resolved)
                    next_batch.append(resolved)
        to_process = next_batch
        depth -= 1
    return final_files


def _seed_files_from_request(project_path: str, terms: list[str], grep_matches: list[GrepMatch]) -> set[str]:
    seed_files: set[str] = set()
    for match in grep_matches:
        abs_path = os.path.join(project_path, match.file_path)
        if os.path.isfile(abs_path) and not should_skip_file(match.file_path):
            seed_files.add(abs_path)

    for root, dirs, files in os.walk(project_path):
        dirs[:] = [
            d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS
        ]
        for name in files:
            if not name.endswith((".dart", ".py")):
                continue
            rel = os.path.relpath(os.path.join(root, name), project_path)
            if should_skip_file(rel):
                continue
            lower = rel.lower()
            if any(term in lower for term in terms):
                seed_files.add(os.path.join(project_path, rel))
    return seed_files


def _collect_symbols(project_path: str, files: set[str]) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    for abs_path in sorted(files):
        if not abs_path.endswith((".dart", ".py")):
            continue
        rel = os.path.relpath(abs_path, project_path)
        if should_skip_file(rel):
            continue
        for symbol in _analyst.get_symbols(abs_path):
            symbols.append({"file_path": rel, **symbol})
    return symbols[:50]


def discover_context(
    user_request: str,
    project_path: str,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Cursor-style discovery: grep -> tree-sitter imports/symbols -> LSP navigation."""
    terms = normalize_search_terms(user_request)
    if run_id:
        append_activity(
            run_id,
            type="grep",
            phase="planner",
            title="Grepping codebase",
            detail=", ".join(terms[:12]) if terms else user_request[:120],
            meta={"search_terms": terms[:20]},
        )
    grep_matches = grep_terms(project_path, terms, max_results=80)
    grep_files = sorted({m.file_path for m in grep_matches})
    if run_id:
        append_activity(
            run_id,
            type="grep",
            phase="planner",
            title=f"Found {len(grep_files)} file(s) via grep",
            files=grep_files[:30],
            meta={"match_count": len(grep_matches)},
        )
    seed_files = _seed_files_from_request(project_path, terms, grep_matches)

    expanded = _expand_imports(project_path, seed_files)
    symbols = _collect_symbols(project_path, expanded)

    relative_seeds = {
        os.path.relpath(path, project_path)
        for path in expanded
        if os.path.isfile(path)
    }
    if run_id:
        append_activity(
            run_id,
            type="tool",
            phase="planner",
            title="Expanding imports and symbols",
            files=sorted(os.path.relpath(p, project_path) for p in expanded if os.path.isfile(p))[:25],
            meta={"tool": "tree_sitter", "seed_count": len(seed_files)},
        )
    lsp_files, lsp_hits = expand_via_lsp(
        project_path=project_path,
        grep_matches=grep_matches,
        search_terms=terms,
        seed_files=relative_seeds,
    )
    expanded.update(lsp_files)
    if run_id and lsp_hits:
        append_activity(
            run_id,
            type="tool",
            phase="planner",
            title=f"LSP navigation ({len(lsp_hits)} hit(s))",
            files=sorted(os.path.relpath(p, project_path) for p in lsp_files if os.path.isfile(p))[:20],
            meta={"tool": "dart_lsp"},
        )

    project_guide = load_project_guide(project_path)
    if run_id and project_guide.get("found"):
        append_activity(
            run_id,
            type="tool",
            phase="planner",
            title="Loaded project guide",
            files=[str(project_guide.get("path") or "AGENTS.md")],
            meta={"tool": "project_guide"},
        )

    # Always include dependency manifests so the LLM knows which packages exist.
    manifest_candidates = ["pubspec.yaml", "pubspec.yml", "package.json"]
    manifest_summaries: list[dict[str, Any]] = []
    for name in manifest_candidates:
        full = os.path.join(project_path, name)
        if os.path.isfile(full):
            try:
                content = read_file(project_path, name, max_chars=8000)
                manifest_summaries.append({"path": name, "preview": content, "lines": content.count("\n") + 1})
            except OSError:
                pass

    explorer = ProjectExplorer(project_path)
    explore_tree = explorer.explore("lib")

    file_summaries: list[dict[str, Any]] = []
    for abs_path in sorted(expanded)[:40]:
        rel = os.path.relpath(abs_path, project_path)
        if should_skip_file(rel):
            continue
        try:
            content = read_file(project_path, rel, max_chars=4000)
            file_summaries.append(
                {
                    "path": rel,
                    "preview": content[:1200],
                    "lines": content.count("\n") + 1,
                }
            )
        except OSError:
            continue

    return {
        "project_guide": project_guide,
        "manifest_summaries": manifest_summaries,
        "search_terms": terms,
        "grep_matches": [
            {
                "file_path": m.file_path,
                "line_number": m.line_number,
                "line_text": (m.line_text[:500] + "...") if len(m.line_text) > 500 else m.line_text,
            }
            for m in grep_matches[:50]
        ],
        "symbols": symbols,
        "lsp_hits": lsp_hits[:40],
        "explore_tree": explore_tree,
        "files": [item["path"] for item in file_summaries],
        "file_summaries": file_summaries,
    }
