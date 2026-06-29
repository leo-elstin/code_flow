import os

_GUIDE_FILENAMES = ("AGENTS.md", "agents.md")
_MAX_CHARS = 12_000


def load_project_guide(project_path: str) -> dict[str, str | bool]:
    """Load AGENTS.md from the Flutter project root for planner/dev context."""
    for name in _GUIDE_FILENAMES:
        path = os.path.join(project_path, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                content = handle.read()
        except OSError:
            continue
        if len(content) > _MAX_CHARS:
            content = content[:_MAX_CHARS] + "\n\n...(truncated)"
        return {"path": name, "content": content, "found": True}
    return {"path": "", "content": "", "found": False}
