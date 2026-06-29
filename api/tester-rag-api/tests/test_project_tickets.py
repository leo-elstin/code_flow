import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.services import project_ticket_store as store


@pytest.fixture
def isolated_store(monkeypatch):
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


def test_upsert_project_creates_and_updates(isolated_store, git_repo):
    project = store.upsert_project(str(git_repo))
    assert project["id"] > 0
    assert project["path"] == os.path.abspath(str(git_repo))
    assert project["name"] == "repo"

    updated = store.upsert_project(str(git_repo), name="My App")
    assert updated["id"] == project["id"]
    assert updated["name"] == "My App"


def test_create_ticket_and_link_run(isolated_store, git_repo):
    project = store.upsert_project(str(git_repo))
    ticket = store.create_ticket(
        project_id=project["id"],
        title="Add login screen",
        description="OAuth flow",
        ticket_type="feature",
    )
    assert ticket["status"] == "pending"
    assert ticket["run_id"] is None

    linked = store.set_ticket_run(ticket["id"], "run-abc", status="planning")
    assert linked["run_id"] == "run-abc"
    assert linked["status"] == "planning"

    store.update_ticket_status_by_run("run-abc", "awaiting_approval")
    refreshed = store.get_ticket(ticket["id"])
    assert refreshed["status"] == "awaiting_approval"


def test_create_ticket_rejects_invalid_type(isolated_store, git_repo):
    project = store.upsert_project(str(git_repo))
    with pytest.raises(ValueError, match="Unsupported ticket_type"):
        store.create_ticket(
            project_id=project["id"],
            title="Bad",
            description=None,
            ticket_type="chore",
        )


def test_list_tickets_for_project(isolated_store, git_repo):
    project = store.upsert_project(str(git_repo))
    store.create_ticket(project["id"], "Bug fix", None, "bug")
    store.create_ticket(project["id"], "New widget", None, "feature")

    tickets = store.list_tickets(project["id"])
    assert len(tickets) == 2
    types = {ticket["ticket_type"] for ticket in tickets}
    assert types == {"bug", "feature"}


def test_delete_ticket(isolated_store, git_repo):
    project = store.upsert_project(str(git_repo))
    ticket = store.create_ticket(project["id"], "Remove me", None, "feature")

    deleted = store.delete_ticket(ticket["id"])
    assert deleted is not None
    assert deleted["title"] == "Remove me"
    assert store.get_ticket(ticket["id"]) is None
    assert store.list_tickets(project["id"]) == []


def test_delete_ticket_not_found(isolated_store):
    assert store.delete_ticket(9999) is None


def test_delete_project_removes_tickets(isolated_store, git_repo):
    project = store.upsert_project(str(git_repo))
    store.create_ticket(project["id"], "Ticket A", None, "feature")
    store.create_ticket(project["id"], "Ticket B", None, "bug")

    deleted = store.delete_project(project["id"])
    assert deleted is not None
    assert deleted["name"] == project["name"]
    assert store.get_project(project["id"]) is None
    assert store.list_tickets(project["id"]) == []


def test_delete_project_not_found(isolated_store):
    assert store.delete_project(9999) is None
