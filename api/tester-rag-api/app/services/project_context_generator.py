import os
from typing import Any

from app.services.generation import acompletion, get_generation_model
from app.services.project_guide import load_project_guide

_CONTEXT_SYSTEM = """You are a technical writer summarizing a Flutter/Dart project for AI coding agents.
Write concise, factual project context the planner and developer agents should follow.
Include: app purpose, folder layout, state management, routing, naming conventions, testing patterns, and gotchas.
Do not invent libraries or paths not evidenced in the supplied materials.
Return plain text only (no JSON, no markdown fences)."""


async def generate_project_context(project_path: str, hints: str = "") -> str:
    """Use the LLM to draft persistent project context from repo artifacts."""
    guide = load_project_guide(project_path)
    pubspec_excerpt = ""
    pubspec_path = os.path.join(project_path, "pubspec.yaml")
    if os.path.isfile(pubspec_path):
        with open(pubspec_path, encoding="utf-8", errors="replace") as handle:
            pubspec_excerpt = handle.read(6000)

    lib_tree: list[str] = []
    lib_dir = os.path.join(project_path, "lib")
    if os.path.isdir(lib_dir):
        for dirpath, dirnames, filenames in os.walk(lib_dir):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            rel = os.path.relpath(dirpath, project_path)
            depth = rel.count(os.sep)
            if depth > 3:
                dirnames.clear()
                continue
            dart_files = [f for f in filenames if f.endswith(".dart")]
            if dart_files:
                lib_tree.append(f"{rel}/ ({len(dart_files)} dart files)")

    materials: dict[str, Any] = {
        "project_guide_found": guide.get("found", False),
        "project_guide": (guide.get("content") or "")[:8000],
        "pubspec_yaml": pubspec_excerpt,
        "lib_tree_sample": lib_tree[:40],
    }

    user_parts = [
        f"Project path: {project_path}",
        f"Repository materials:\n{materials}",
    ]
    if hints.strip():
        user_parts.append(f"User hints:\n{hints.strip()}")
    user_parts.append("Produce project context for coding agents.")

    response = await acompletion(
        model=get_generation_model(),
        messages=[
            {"role": "system", "content": _CONTEXT_SYSTEM},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ],
    )
    return (response.choices[0].message.content or "").strip()
