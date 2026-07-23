"""Prior-work discovery: finding earlier attempts on the same ticket via
run history + git branches, so the planner can build on existing work."""

import subprocess
import tempfile
from pathlib import Path

import pytest

from app.services import prior_work_discovery, run_index
from app.tools import git_tools


# ---------------------------------------------------------------------------
# run_index.list_runs_by_ticket_id
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_run_index(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "run_index.db"
        monkeypatch.setattr(run_index, "_INDEX_PATH", db_path)
        yield tmp


def _state(run_id, ticket_id, *, root_run_id=None, attempt=1, status="completed", updated_at=None):
    s = {
        "run_id": run_id,
        "status": status,
        "user_request": "req",
        "project_path": "/tmp/proj",
        "ticket_id": ticket_id,
        "root_run_id": root_run_id or run_id,
        "parent_run_id": None,
        "attempt": attempt,
    }
    return s


def test_list_runs_by_ticket_id_returns_latest_per_root(isolated_run_index):
    run_index.upsert_run(_state("r1", ticket_id=5, root_run_id="r1", attempt=1))
    run_index.upsert_run(_state("r2", ticket_id=5, root_run_id="r2", attempt=1))
    # r2 gets retried as r3 (same root_run_id, attempt 2) — should supersede r2.
    run_index.upsert_run(_state("r3", ticket_id=5, root_run_id="r2", attempt=2))
    # Different ticket entirely — must not appear.
    run_index.upsert_run(_state("r4", ticket_id=9, root_run_id="r4", attempt=1))

    rows = run_index.list_runs_by_ticket_id(5)
    run_ids = {r["run_id"] for r in rows}
    assert run_ids == {"r1", "r3"}
    assert "r2" not in run_ids  # superseded by its retry r3


def test_list_runs_by_ticket_id_excludes_current_run(isolated_run_index):
    run_index.upsert_run(_state("r1", ticket_id=5, root_run_id="r1", attempt=1))
    run_index.upsert_run(_state("r2", ticket_id=5, root_run_id="r2", attempt=1))

    rows = run_index.list_runs_by_ticket_id(5, exclude_run_id="r2")
    assert {r["run_id"] for r in rows} == {"r1"}


def test_list_runs_by_ticket_id_empty_when_no_match(isolated_run_index):
    run_index.upsert_run(_state("r1", ticket_id=5, root_run_id="r1", attempt=1))
    assert run_index.list_runs_by_ticket_id(999) == []


# ---------------------------------------------------------------------------
# git_tools.branch_exists / get_branch_diff_summary
# ---------------------------------------------------------------------------

@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)
    return repo


def test_branch_exists(git_repo):
    subprocess.run(["git", "branch", "agent/abc123"], cwd=git_repo, check=True)
    assert git_tools.branch_exists(str(git_repo), "agent/abc123") is True
    assert git_tools.branch_exists(str(git_repo), "agent/does-not-exist") is False


def test_get_branch_diff_summary(git_repo):
    subprocess.run(["git", "checkout", "-b", "agent/abc123"], cwd=git_repo, check=True, capture_output=True)
    (git_repo / "lib" ).mkdir()
    (git_repo / "lib" / "feature.dart").write_text("class Feature {}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "add feature"], cwd=git_repo, check=True)
    subprocess.run(["git", "checkout", "main"], cwd=git_repo, check=True, capture_output=True)

    summary = git_tools.get_branch_diff_summary(str(git_repo), "agent/abc123")
    assert summary["base"] == "main"
    assert summary["changed_files"] == ["lib/feature.dart"]
    assert "class Feature" in summary["diff"]
    assert summary["diff_truncated"] is False


def test_get_branch_diff_summary_truncates(git_repo):
    subprocess.run(["git", "checkout", "-b", "agent/abc123"], cwd=git_repo, check=True, capture_output=True)
    (git_repo / "big.txt").write_text("x" * 20_000, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "big file"], cwd=git_repo, check=True)
    subprocess.run(["git", "checkout", "main"], cwd=git_repo, check=True, capture_output=True)

    summary = git_tools.get_branch_diff_summary(str(git_repo), "agent/abc123", max_chars=500)
    assert summary["diff_truncated"] is True
    assert len(summary["diff"]) == 500


def test_resolve_default_branch_falls_back_to_common_names(git_repo):
    # No origin/HEAD in a bare local repo → falls through to the "main" candidate.
    assert git_tools.resolve_default_branch(str(git_repo)) == "main"


def test_list_branches_matching_finds_by_token_and_excludes_agent(git_repo):
    subprocess.run(["git", "branch", "feature/MMA-3480-multi-container"], cwd=git_repo, check=True)
    subprocess.run(["git", "branch", "feature/MMA-9999-unrelated"], cwd=git_repo, check=True)
    subprocess.run(["git", "branch", "agent/MMA-3480-should-be-ignored"], cwd=git_repo, check=True)

    matches = git_tools.list_branches_matching(str(git_repo), ["MMA-3480"])
    assert "feature/MMA-3480-multi-container" in matches
    assert "feature/MMA-9999-unrelated" not in matches
    assert all(not m.startswith("agent/") for m in matches)


def test_get_branch_diff_summary_defaults_base_to_resolved_default(git_repo):
    subprocess.run(["git", "checkout", "-b", "feature/MMA-1-x"], cwd=git_repo, check=True, capture_output=True)
    (git_repo / "f.dart").write_text("class X {}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "x"], cwd=git_repo, check=True)
    subprocess.run(["git", "checkout", "main"], cwd=git_repo, check=True, capture_output=True)

    summary = git_tools.get_branch_diff_summary(str(git_repo), "feature/MMA-1-x")
    assert summary["base"] == "main"
    assert summary["branch"] == "feature/MMA-1-x"
    assert summary["changed_files"] == ["f.dart"]


# ---------------------------------------------------------------------------
# prior_work_discovery.discover_prior_work (wiring test)
# ---------------------------------------------------------------------------

def test_discover_prior_work_returns_empty_without_ticket_id():
    result = prior_work_discovery.discover_prior_work(None, "/tmp/proj")
    assert result == {"found": False, "candidates": []}


def test_discover_prior_work_returns_empty_when_ticket_missing(monkeypatch):
    monkeypatch.setattr(
        prior_work_discovery.project_ticket_store, "get_ticket", lambda tid: None
    )
    result = prior_work_discovery.discover_prior_work(42, "/tmp/proj")
    assert result == {"found": False, "candidates": []}


def test_discover_prior_work_finds_branch_and_diffs_it(monkeypatch):
    monkeypatch.setattr(
        prior_work_discovery.project_ticket_store,
        "get_ticket",
        lambda tid: {"id": tid, "project_id": 1, "title": "Add login screen"},
    )
    monkeypatch.setattr(
        prior_work_discovery.project_ticket_store, "list_tickets", lambda pid: []
    )
    monkeypatch.setattr(
        prior_work_discovery.run_index,
        "list_runs_by_ticket_id",
        lambda tid, exclude_run_id=None, limit=3: [
            {
                "run_id": "r1", "root_run_id": "r1", "attempt": 1,
                "status": "failed", "updated_at": "2026-01-01T00:00:00",
            }
        ],
    )
    monkeypatch.setattr(prior_work_discovery.git_tools, "branch_exists", lambda p, b: True)
    monkeypatch.setattr(
        prior_work_discovery.git_tools,
        "get_branch_diff_summary",
        lambda p, b: {"base": "main", "changed_files": ["lib/login.dart"], "diff": "+class Login", "diff_truncated": False},
    )

    result = prior_work_discovery.discover_prior_work(42, "/tmp/proj", current_run_id="r2")
    assert result["found"] is True
    cand = result["candidates"][0]
    assert cand["branch"] == "agent/r1"
    assert cand["branch_exists"] is True
    assert cand["changed_files"] == ["lib/login.dart"]
    assert cand["match_reason"] == "ticket_id"


def test_discover_prior_work_matches_by_title_fallback(monkeypatch):
    def fake_get_ticket(tid):
        return {"id": tid, "project_id": 1, "title": "Add login screen"}

    monkeypatch.setattr(prior_work_discovery.project_ticket_store, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(
        prior_work_discovery.project_ticket_store,
        "list_tickets",
        lambda pid: [{"id": 99, "title": "add   LOGIN screen"}],
    )

    seen_ticket_ids = []

    def fake_list_runs(tid, exclude_run_id=None, limit=3):
        seen_ticket_ids.append(tid)
        if tid == 99:
            return [{
                "run_id": "r9", "root_run_id": "r9", "attempt": 1,
                "status": "completed", "updated_at": "2026-01-01T00:00:00",
            }]
        return []

    monkeypatch.setattr(prior_work_discovery.run_index, "list_runs_by_ticket_id", fake_list_runs)
    monkeypatch.setattr(prior_work_discovery.git_tools, "branch_exists", lambda p, b: False)

    result = prior_work_discovery.discover_prior_work(42, "/tmp/proj")
    assert 99 in seen_ticket_ids
    assert result["found"] is True
    assert result["candidates"][0]["match_reason"] == "title"
    assert result["candidates"][0]["branch_exists"] is False


def test_discover_prior_work_finds_epic_branch_for_child_story(monkeypatch):
    # Child story MMA-3481 whose parent epic is MMA-3480; a human branch exists
    # for the epic but nothing in run history references the child.
    monkeypatch.setattr(
        prior_work_discovery.project_ticket_store,
        "get_ticket",
        lambda tid: {
            "id": tid, "project_id": 1, "title": "Select multiple container types",
            "jira_key": "MMA-3481", "jira_parent_key": "MMA-3480",
        },
    )
    monkeypatch.setattr(prior_work_discovery.project_ticket_store, "list_tickets", lambda pid: [])
    monkeypatch.setattr(
        prior_work_discovery.run_index, "list_runs_by_ticket_id",
        lambda tid, exclude_run_id=None, limit=3: [],
    )

    seen_tokens = {}

    def fake_list_branches(project_path, tokens):
        seen_tokens["tokens"] = tokens
        return ["feature/MMA-3480-multi-container"]

    monkeypatch.setattr(prior_work_discovery.git_tools, "list_branches_matching", fake_list_branches)
    monkeypatch.setattr(
        prior_work_discovery.git_tools, "get_branch_diff_summary",
        lambda p, b: {
            "base": "development", "branch": b,
            "changed_files": ["lib/features/book_module/multi_container/ui/vm/multi_container_cubit.dart"],
            "diff": "+class MultiContainerCubit", "diff_truncated": False,
        },
    )

    result = prior_work_discovery.discover_prior_work(15, "/tmp/proj")
    # Both the ticket's own key and the parent epic key are offered as scan tokens.
    assert seen_tokens["tokens"] == ["MMA-3481", "MMA-3480"]
    assert result["found"] is True
    cand = result["candidates"][0]
    assert cand["branch"] == "feature/MMA-3480-multi-container"
    assert cand["match_reason"] == "epic_branch"
    assert cand["run_id"] is None
    assert "multi_container_cubit.dart" in cand["changed_files"][0]


def test_discover_prior_work_never_raises_on_failure(monkeypatch):
    def boom(tid):
        raise RuntimeError("db down")

    monkeypatch.setattr(prior_work_discovery.project_ticket_store, "get_ticket", boom)
    result = prior_work_discovery.discover_prior_work(42, "/tmp/proj")
    assert result["found"] is False
    assert result["candidates"] == []
