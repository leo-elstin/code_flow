import os
import subprocess
from pathlib import Path

import pytest

from app.services import project_ticket_store as store
from app.services.project_agent_context import get_project_agent_config
from app.services.project_skills import discover_project_skills, load_skill_contents, normalize_skill_ids


@pytest.fixture
def isolated_store(monkeypatch):
    with pytest.MonkeyPatch.context() as mp:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "projects.db"
            monkeypatch.setattr(store, "_DB_PATH", db_path)
            yield tmp


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("# test", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)
    return repo


def test_discover_project_skills(git_repo):
    skill_dir = git_repo / ".cursor" / "skills" / "routing"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# Routing\nUse go_router for navigation.",
        encoding="utf-8",
    )

    skills = discover_project_skills(str(git_repo))
    assert len(skills) == 1
    assert skills[0]["name"] == "routing"
    assert skills[0]["id"] == ".cursor/skills/routing/SKILL.md"


def test_discover_project_skills_nested_and_case_insensitive(git_repo):
    skill_dir = git_repo / ".tester" / "skills" / "nested" / "deep"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("Nested skill body.", encoding="utf-8")

    skills = discover_project_skills(str(git_repo))
    assert len(skills) == 1
    assert skills[0]["name"] == "deep"
    assert skills[0]["id"] == ".tester/skills/nested/deep/skill.md"


def test_load_skill_contents(git_repo):
    skill_dir = git_repo / ".tester" / "skills" / "state"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("Use bloc pattern.", encoding="utf-8")

    loaded = load_skill_contents(str(git_repo), [".tester/skills/state/SKILL.md"])
    assert len(loaded) == 1
    assert "bloc" in loaded[0]["content"]


def test_normalize_skill_ids_rejects_traversal():
    assert normalize_skill_ids(["../secrets/SKILL.md", ".cursor/skills/a/SKILL.md"]) == [
        ".cursor/skills/a/SKILL.md"
    ]


def test_update_project_context_persists(isolated_store, git_repo):
    project = store.upsert_project(str(git_repo))
    updated = store.update_project_context(
        project["id"],
        context_text="Use feature-first layout.",
        planner_skill_ids=[".cursor/skills/routing/SKILL.md"],
        dev_skill_ids=[".cursor/skills/routing/SKILL.md"],
    )
    assert updated is not None
    assert updated["context_text"] == "Use feature-first layout."

    refreshed = store.get_project(project["id"])
    assert refreshed["context_text"] == "Use feature-first layout."
    assert refreshed["planner_skill_ids"] is not None


def test_get_project_agent_config_loads_skills(isolated_store, git_repo):
    skill_dir = git_repo / ".cursor" / "skills" / "routing"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Router conventions.", encoding="utf-8")

    project = store.upsert_project(str(git_repo))
    store.update_project_context(
        project["id"],
        context_text="Project notes.",
        planner_skill_ids=[".cursor/skills/routing/SKILL.md"],
        dev_skill_ids=[],
    )

    cfg = get_project_agent_config(str(git_repo))
    assert cfg["context_text"] == "Project notes."
    assert len(cfg["planner_skills"]) == 1
    assert cfg["planner_skills"][0]["name"] == "routing"
    assert cfg["dev_skills"] == []


def test_get_project_by_path(isolated_store, git_repo):
    project = store.upsert_project(str(git_repo))
    found = store.get_project_by_path(str(git_repo))
    assert found is not None
    assert found["id"] == project["id"]
    assert store.get_project_by_path("/nonexistent/path") is None
