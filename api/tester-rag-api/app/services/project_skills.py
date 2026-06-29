import re
from pathlib import Path

_SKILL_ROOTS = (".cursor/skills", ".tester/skills", "skills")
_MAX_SKILL_CONTENT_CHARS = 12_000


def _first_summary_line(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:240]
    return ""


def _skill_name(skill_md: Path, base: Path) -> str:
    parent = skill_md.parent
    if parent == base:
        return skill_md.stem
    return parent.name


def discover_project_skills(project_path: str) -> list[dict[str, str]]:
    """List SKILL.md files under common skill directories in a project."""
    root = Path(project_path)
    if not root.is_dir():
        return []

    seen: set[str] = set()
    skills: list[dict[str, str]] = []
    for rel_root in _SKILL_ROOTS:
        base = root / rel_root
        if not base.is_dir():
            continue
        candidates: list[Path] = []
        for path in base.rglob("*"):
            if path.is_file() and path.name.lower() == "skill.md":
                candidates.append(path)
        for skill_md in sorted(candidates, key=lambda path: path.as_posix().lower()):
            rel_path = skill_md.relative_to(root).as_posix()
            if rel_path in seen:
                continue
            seen.add(rel_path)
            try:
                content = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            skills.append(
                {
                    "id": rel_path,
                    "name": _skill_name(skill_md, base),
                    "path": rel_path,
                    "description": _first_summary_line(content),
                }
            )
    return skills


def load_skill_contents(
    project_path: str,
    skill_ids: list[str],
    *,
    max_chars: int = _MAX_SKILL_CONTENT_CHARS,
) -> list[dict[str, str]]:
    """Load full skill bodies for the given skill ids (relative paths)."""
    root = Path(project_path)
    loaded: list[dict[str, str]] = []
    for skill_id in skill_ids:
        rel = skill_id.strip().lstrip("/")
        if not rel or ".." in Path(rel).parts:
            continue
        skill_path = root / rel
        if not skill_path.is_file():
            continue
        try:
            content = skill_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(content) > max_chars:
            content = content[:max_chars] + "\n…[truncated]"
        name = skill_path.parent.name
        loaded.append({"id": rel, "name": name, "path": rel, "content": content})
    return loaded


def normalize_skill_ids(skill_ids: list[str] | None) -> list[str]:
    if not skill_ids:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in skill_ids:
        skill_id = str(raw).strip().lstrip("/")
        if not skill_id or skill_id in seen:
            continue
        if ".." in Path(skill_id).parts:
            continue
        if not re.search(r"SKILL\.md$", skill_id, re.IGNORECASE):
            continue
        seen.add(skill_id)
        normalized.append(skill_id)
    return normalized
