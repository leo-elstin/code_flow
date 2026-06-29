import json
import os

from app.services.project_skills import discover_project_skills, load_skill_contents, normalize_skill_ids
from app.services.project_ticket_store import get_project_by_path

_MAX_CONTEXT_CHARS = 12_000


def _parse_skill_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return normalize_skill_ids([str(item) for item in parsed])


def get_project_agent_config(project_path: str) -> dict:
    """Load persisted context and skill selections for a project path."""
    project = get_project_by_path(project_path)
    if not project:
        return {
            "project_id": None,
            "context_text": "",
            "planner_skill_ids": [],
            "dev_skill_ids": [],
            "planner_skills": [],
            "dev_skills": [],
        }

    context_text = (project.get("context_text") or "").strip()
    if len(context_text) > _MAX_CONTEXT_CHARS:
        context_text = context_text[:_MAX_CONTEXT_CHARS]

    planner_skill_ids = _parse_skill_ids(project.get("planner_skill_ids"))
    dev_skill_ids = _parse_skill_ids(project.get("dev_skill_ids"))

    abs_path = os.path.abspath(project_path)
    return {
        "project_id": project["id"],
        "context_text": context_text,
        "planner_skill_ids": planner_skill_ids,
        "dev_skill_ids": dev_skill_ids,
        "planner_skills": load_skill_contents(abs_path, planner_skill_ids),
        "dev_skills": load_skill_contents(abs_path, dev_skill_ids),
    }


def list_available_skills(project_path: str) -> list[dict[str, str]]:
    return discover_project_skills(os.path.abspath(project_path))
