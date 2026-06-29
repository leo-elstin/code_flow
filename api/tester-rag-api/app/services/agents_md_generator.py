import os
import re
from typing import Any

from app.services.generation import get_generation_client, get_generation_model
from app.services.project_guide import load_project_guide

_AGENTS_MD_FILENAME = "AGENTS.md"

_AGENTS_MD_SYSTEM = """You are a technical writer creating an AGENTS.md guide for AI coding agents working on a Flutter/Dart project.
The file must describe the REAL project structure evidenced in the repository materials — do not invent folders, packages, or patterns that are not supported by the materials.
Write valid Markdown suitable for saving as AGENTS.md at the project root.

Required sections (use these headings):
# Project overview
## Folder layout
## State management
## Routing / navigation
## Naming conventions
## Patterns to follow
## Do not
## Reference files

Be specific about observed paths under lib/. List anti-patterns (e.g. do not create lib/controllers/ unless it exists).
Return Markdown only — no JSON, no code fences wrapping the whole document."""


def agents_md_exists(project_path: str) -> bool:
    guide = load_project_guide(project_path)
    return bool(guide.get("found"))


def get_agents_md_status(project_path: str) -> dict[str, Any]:
    guide = load_project_guide(project_path)
    if guide.get("found"):
        content = guide.get("content") or ""
        return {
            "exists": True,
            "filename": guide.get("path") or _AGENTS_MD_FILENAME,
            "path": guide.get("path") or _AGENTS_MD_FILENAME,
            "content_preview": content[:500],
        }
    return {
        "exists": False,
        "filename": "",
        "path": "",
        "content_preview": "",
    }


def _collect_lib_tree(project_path: str, *, max_entries: int = 50) -> list[str]:
    lib_tree: list[str] = []
    lib_dir = os.path.join(project_path, "lib")
    if not os.path.isdir(lib_dir):
        return lib_tree
    for dirpath, dirnames, filenames in os.walk(lib_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel = os.path.relpath(dirpath, project_path)
        depth = rel.count(os.sep)
        if depth > 4:
            dirnames.clear()
            continue
        dart_files = sorted(f for f in filenames if f.endswith(".dart"))
        if dart_files:
            sample = ", ".join(dart_files[:4])
            suffix = "…" if len(dart_files) > 4 else ""
            lib_tree.append(f"{rel}/ [{len(dart_files)} files: {sample}{suffix}]")
        if len(lib_tree) >= max_entries:
            break
    return lib_tree


def _sample_dart_files(project_path: str, *, max_files: int = 5, max_chars: int = 3000) -> list[dict[str, str]]:
    samples: list[dict[str, str]] = []
    lib_dir = os.path.join(project_path, "lib")
    if not os.path.isdir(lib_dir):
        return samples
    candidates: list[str] = []
    for dirpath, _, filenames in os.walk(lib_dir):
        for name in filenames:
            if name.endswith(".dart"):
                candidates.append(os.path.relpath(os.path.join(dirpath, name), project_path))
    candidates.sort(key=lambda path: (path.count("/"), path))
    priority = [p for p in candidates if p.endswith("main.dart") or "/feature/" in p]
    ordered = priority + [p for p in candidates if p not in priority]
    for rel in ordered[:max_files]:
        abs_path = os.path.join(project_path, rel)
        try:
            with open(abs_path, encoding="utf-8", errors="replace") as handle:
                content = handle.read(max_chars)
        except OSError:
            continue
        samples.append({"path": rel, "content": content})
    return samples


def _strip_markdown_fences(text: str) -> str:
    stripped = text.strip()
    match = re.match(r"^```(?:markdown|md)?\s*\n(.*)\n```\s*$", stripped, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return stripped


async def generate_agents_md_content(project_path: str, hints: str = "") -> str:
    """Draft AGENTS.md content from repository artifacts."""
    pubspec_excerpt = ""
    pubspec_path = os.path.join(project_path, "pubspec.yaml")
    if os.path.isfile(pubspec_path):
        with open(pubspec_path, encoding="utf-8", errors="replace") as handle:
            pubspec_excerpt = handle.read(6000)

    materials: dict[str, Any] = {
        "pubspec_yaml": pubspec_excerpt,
        "lib_tree": _collect_lib_tree(project_path),
        "dart_samples": _sample_dart_files(project_path),
    }

    user_parts = [
        f"Project path: {project_path}",
        f"Repository materials:\n{materials}",
    ]
    if hints.strip():
        user_parts.append(f"User hints:\n{hints.strip()}")
    user_parts.append("Write AGENTS.md for coding agents implementing features in this project.")

    client = get_generation_client()
    response = await client.chat.completions.create(
        model=get_generation_model(),
        messages=[
            {"role": "system", "content": _AGENTS_MD_SYSTEM},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ],
    )
    return _strip_markdown_fences(response.choices[0].message.content or "")


def write_agents_md(project_path: str, content: str) -> str:
    """Write AGENTS.md to the project root. Raises FileExistsError if a guide already exists."""
    if agents_md_exists(project_path):
        status = get_agents_md_status(project_path)
        raise FileExistsError(status.get("path") or _AGENTS_MD_FILENAME)

    abs_project = os.path.abspath(project_path)
    target = os.path.join(abs_project, _AGENTS_MD_FILENAME)
    if not target.startswith(abs_project + os.sep) and target != abs_project:
        raise ValueError("Invalid project path")

    body = content.strip()
    if not body:
        raise ValueError("AGENTS.md content is empty")

    with open(target, "w", encoding="utf-8") as handle:
        handle.write(body)
        if not body.endswith("\n"):
            handle.write("\n")
    return _AGENTS_MD_FILENAME
