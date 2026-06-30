import tempfile
from pathlib import Path

import pytest

from app.services import epic_run_store as store
from app.services import project_ticket_store as tickets_store


@pytest.fixture
def isolated_epic_store(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(store, "_DB_PATH", Path(tmp) / "epic_runs.db")
        yield tmp


@pytest.fixture
def isolated_ticket_store(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(tickets_store, "_DB_PATH", Path(tmp) / "projects.db")
        yield tmp


def test_create_and_get_epic_run(isolated_epic_store):
    row = store.create_epic_run(
        "epic-1", epic_ticket_id=10, project_id=1, project_path="/repo", epic_jira_key="E-1"
    )
    assert row["status"] == "planning"
    assert row["epic_ticket_id"] == 10
    assert row["child_runs"] == {}

    fetched = store.get_epic_run("epic-1")
    assert fetched["epic_jira_key"] == "E-1"


def test_update_epic_run_status_and_plan(isolated_epic_store):
    store.create_epic_run("epic-1", epic_ticket_id=10, project_id=1)
    plan = {"levels": [[1, 2], [3]], "edges": {"3": [1, 2]}}
    updated = store.update_epic_run("epic-1", status="awaiting_approval", plan=plan)
    assert updated["status"] == "awaiting_approval"
    assert updated["plan"]["levels"] == [[1, 2], [3]]


def test_set_child_run_merges(isolated_epic_store):
    store.create_epic_run("epic-1", epic_ticket_id=10, project_id=1)
    store.set_child_run("epic-1", 1, status="pending", jira_key="K-1")
    store.set_child_run("epic-1", 1, run_id="run-1", status="planning")
    children = store.get_epic_run("epic-1")["child_runs"]
    assert children["1"] == {
        "ticket_id": 1, "jira_key": "K-1", "run_id": "run-1", "status": "planning",
    }


def test_list_epic_runs_filters_by_project(isolated_epic_store):
    store.create_epic_run("e1", epic_ticket_id=1, project_id=1)
    store.create_epic_run("e2", epic_ticket_id=2, project_id=2)
    rows = store.list_epic_runs(project_id=1)
    assert [r["epic_run_id"] for r in rows] == ["e1"]


def test_ensure_schema_is_idempotent(isolated_epic_store):
    store.ensure_schema()
    store.ensure_schema()  # second call must not error
    assert store.list_epic_runs() == []


def test_list_children_returns_epic_stories(isolated_ticket_store, tmp_path):
    # Build a project, then a Jira epic with two child stories.
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    project = tickets_store.upsert_project(str(repo))
    pid = project["id"]

    tickets_store.upsert_jira_ticket(pid, "E-1", "Epic", None, "feature", jira_issue_type="Epic")
    tickets_store.upsert_jira_ticket(pid, "S-1", "Story 1", None, "feature",
                                     jira_issue_type="Story", jira_parent_key="E-1")
    tickets_store.upsert_jira_ticket(pid, "S-2", "Story 2", None, "feature",
                                     jira_issue_type="Story", jira_parent_key="E-1")
    # An unrelated ticket under a different parent must not leak in.
    tickets_store.upsert_jira_ticket(pid, "S-9", "Other", None, "feature",
                                     jira_issue_type="Story", jira_parent_key="E-9")

    children = tickets_store.list_children(pid, "E-1")
    assert {c["jira_key"] for c in children} == {"S-1", "S-2"}
    # A child under a different parent stays scoped to that parent.
    assert {c["jira_key"] for c in tickets_store.list_children(pid, "E-9")} == {"S-9"}
    assert tickets_store.list_children(pid, "E-404") == []
