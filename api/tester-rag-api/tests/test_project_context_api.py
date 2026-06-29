import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services import project_ticket_store as store
from main import app


@pytest.fixture
def isolated_store(monkeypatch, tmp_path):
    db_path = tmp_path / "projects.db"
    monkeypatch.setattr(store, "_DB_PATH", db_path)
    store.ensure_schema()
    yield db_path


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


@pytest.fixture
def client():
    return TestClient(app)


def test_project_context_crud(client, isolated_store, git_repo):
    create_resp = client.post(
        "/api/code-agent/projects",
        json={"path": str(git_repo)},
    )
    assert create_resp.status_code == 200
    project_id = create_resp.json()["id"]

    get_resp = client.get(f"/api/code-agent/projects/{project_id}/context")
    assert get_resp.status_code == 200
    assert get_resp.json()["context_text"] == ""

    put_resp = client.put(
        f"/api/code-agent/projects/{project_id}/context",
        json={
            "context_text": "Always use package imports.",
            "planner_skill_ids": [],
            "dev_skill_ids": [],
        },
    )
    assert put_resp.status_code == 200
    body = put_resp.json()
    assert body["context_text"] == "Always use package imports."


def test_save_skill_selection_via_api(client, isolated_store, git_repo):
    skill_dir = git_repo / ".cursor" / "skills" / "bloc"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Bloc patterns.", encoding="utf-8")

    create_resp = client.post("/api/code-agent/projects", json={"path": str(git_repo)})
    project_id = create_resp.json()["id"]
    skill_id = ".cursor/skills/bloc/SKILL.md"

    put_resp = client.put(
        f"/api/code-agent/projects/{project_id}/context",
        json={
            "context_text": "notes",
            "planner_skill_ids": [skill_id],
            "dev_skill_ids": [skill_id],
        },
    )
    assert put_resp.status_code == 200
    body = put_resp.json()
    assert body["planner_skill_ids"] == [skill_id]
    assert body["dev_skill_ids"] == [skill_id]

    get_resp = client.get(f"/api/code-agent/projects/{project_id}/context")
    assert get_resp.status_code == 200
    loaded = get_resp.json()
    assert loaded["planner_skill_ids"] == [skill_id]
    assert loaded["dev_skill_ids"] == [skill_id]


def test_agents_md_status_missing(client, isolated_store, git_repo):
    create_resp = client.post("/api/code-agent/projects", json={"path": str(git_repo)})
    project_id = create_resp.json()["id"]

    status_resp = client.get(f"/api/code-agent/projects/{project_id}/agents-md")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["exists"] is False


def test_delete_ticket_api(client, isolated_store, git_repo):
    create_resp = client.post("/api/code-agent/projects", json={"path": str(git_repo)})
    project_id = create_resp.json()["id"]
    ticket_resp = client.post(
        f"/api/code-agent/projects/{project_id}/tickets",
        json={"title": "Temp", "ticket_type": "feature"},
    )
    ticket_id = ticket_resp.json()["id"]

    delete_resp = client.delete(f"/api/code-agent/tickets/{ticket_id}")
    assert delete_resp.status_code == 204

    get_resp = client.get(f"/api/code-agent/tickets/{ticket_id}")
    assert get_resp.status_code == 404


def test_agents_md_generate_conflict_when_exists(client, isolated_store, git_repo):
    (git_repo / "AGENTS.md").write_text("# Existing guide", encoding="utf-8")
    create_resp = client.post("/api/code-agent/projects", json={"path": str(git_repo)})
    project_id = create_resp.json()["id"]

    status_resp = client.get(f"/api/code-agent/projects/{project_id}/agents-md")
    assert status_resp.json()["exists"] is True

    gen_resp = client.post(
        f"/api/code-agent/projects/{project_id}/agents-md/generate",
        json={"hints": "test"},
    )
    assert gen_resp.status_code == 409


def test_list_project_skills(client, isolated_store, git_repo):
    skill_dir = git_repo / ".cursor" / "skills" / "bloc"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Bloc patterns.", encoding="utf-8")

    create_resp = client.post("/api/code-agent/projects", json={"path": str(git_repo)})
    project_id = create_resp.json()["id"]

    skills_resp = client.get(f"/api/code-agent/projects/{project_id}/skills")
    assert skills_resp.status_code == 200
    skills = skills_resp.json()["skills"]
    assert len(skills) == 1
    assert skills[0]["name"] == "bloc"
