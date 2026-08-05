"""Tests for the Claude Code CLI dev engine pilot (app/agents/roles/dev_cli.py).

_invoke_claude_cli is the seam: tests mock it directly instead of the
subprocess, mirroring how test_dev_loop_efficiency.py mocks acompletion for
the API engine."""

import asyncio

import pytest

from app.agents.roles import dev_cli
from app.services import run_activity


def _write(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def worktree(tmp_path):
    """A real (empty) git repo so list_changed_files/_finalize_dev_changes work."""
    import subprocess

    wt = tmp_path / "worktree"
    wt.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=wt, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=wt, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=wt, check=True)
    return wt


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "project"
    p.mkdir()
    return p


PLAN = {
    "feature_summary": "Add the frobnicator widget.",
    "files_to_create": [{"path": "lib/frobnicator.dart"}],
    "files_to_modify": [],
    "plan_markdown": "# Frobnicator\n\nA long human-readable restatement.",
}


def test_run_dev_cli_first_call_sends_full_prompt_no_resume(worktree, project, monkeypatch):
    _write(worktree / "lib" / "frobnicator.dart", "class Frobnicator {}\n")
    captured = {}

    async def fake_invoke(*, prompt, worktree_path, resume_session_id, timeout_seconds):
        captured["prompt"] = prompt
        captured["resume_session_id"] = resume_session_id
        return {"is_error": False, "result": "Created the widget.", "session_id": "sess-1", "usage": {}}

    monkeypatch.setattr(dev_cli, "_invoke_claude_cli", fake_invoke)

    result = asyncio.run(
        dev_cli.run_dev_cli(
            plan=PLAN, context_bundle={}, worktree_path=str(worktree), project_path=str(project),
        )
    )

    assert captured["resume_session_id"] is None
    assert "frobnicator widget" in captured["prompt"]  # structured field present
    assert "long human-readable restatement" not in captured["prompt"]  # plan_markdown dropped
    assert result["summary"] == "Created the widget."
    assert result["dev_cli_session_id"] == "sess-1"
    assert result["truncated"] is False
    assert {f["path"] for f in result["file_changes"]} == {"lib/frobnicator.dart"}


def test_run_dev_cli_retry_uses_resume_and_brief_only(worktree, project, monkeypatch):
    captured = {}

    async def fake_invoke(*, prompt, worktree_path, resume_session_id, timeout_seconds):
        captured["prompt"] = prompt
        captured["resume_session_id"] = resume_session_id
        return {"is_error": False, "result": "Fixed it.", "session_id": "sess-1", "usage": {}}

    monkeypatch.setattr(dev_cli, "_invoke_claude_cli", fake_invoke)

    verifier_report = {"passed": False, "required_fixes": ["Fix the null check in frobnicator.dart"]}
    result = asyncio.run(
        dev_cli.run_dev_cli(
            plan=PLAN, context_bundle={}, worktree_path=str(worktree), project_path=str(project),
            verifier_report=verifier_report, prior_session_id="sess-1",
        )
    )

    assert captured["resume_session_id"] == "sess-1"
    assert "FIX MODE" in captured["prompt"]
    assert "Fix the null check" in captured["prompt"]
    # The retry brief must NOT re-dump the whole plan (already in the CLI's own session).
    assert "Frobnicator widget" not in captured["prompt"]
    assert result["dev_cli_session_id"] == "sess-1"


def test_run_dev_cli_is_error_marks_truncated(worktree, project, monkeypatch):
    async def fake_invoke(*, prompt, worktree_path, resume_session_id, timeout_seconds):
        return {"is_error": True, "result": "API Error: 401 Invalid bearer token", "session_id": "sess-2"}

    monkeypatch.setattr(dev_cli, "_invoke_claude_cli", fake_invoke)

    result = asyncio.run(
        dev_cli.run_dev_cli(plan=PLAN, context_bundle={}, worktree_path=str(worktree), project_path=str(project))
    )

    assert result["truncated"] is True
    assert "401" in result["summary"]
    assert result["dev_cli_session_id"] == "sess-2"


def test_run_dev_cli_logs_out_of_scope_files_without_blocking(worktree, project, monkeypatch, tmp_path):
    monkeypatch.setattr(run_activity, "_DB_PATH", tmp_path / "activity.db")

    # Claude wrote the planned file AND an unplanned one.
    _write(worktree / "lib" / "frobnicator.dart", "class Frobnicator {}\n")
    _write(worktree / "lib" / "unrelated.dart", "class Unrelated {}\n")

    async def fake_invoke(*, prompt, worktree_path, resume_session_id, timeout_seconds):
        return {"is_error": False, "result": "Done.", "session_id": "sess-3", "usage": {}}

    monkeypatch.setattr(dev_cli, "_invoke_claude_cli", fake_invoke)

    run_id = "run-scope-test"
    result = asyncio.run(
        dev_cli.run_dev_cli(
            plan=PLAN, context_bundle={}, worktree_path=str(worktree), project_path=str(project), run_id=run_id,
        )
    )

    # Not blocked — both files land in file_changes, run still "succeeds".
    assert {f["path"] for f in result["file_changes"]} == {"lib/frobnicator.dart", "lib/unrelated.dart"}
    assert result["truncated"] is False

    events = run_activity.list_activity(run_id)
    warnings = [e for e in events if "out-of-plan" in (e.get("title") or "")]
    assert len(warnings) == 1
    assert "lib/unrelated.dart" in warnings[0]["detail"]


def test_run_dev_cli_missing_binary_returns_truncated_not_raise(worktree, project, monkeypatch):
    monkeypatch.setattr(dev_cli.shutil, "which", lambda _name: None)

    result = asyncio.run(
        dev_cli.run_dev_cli(plan=PLAN, context_bundle={}, worktree_path=str(worktree), project_path=str(project))
    )

    assert result["truncated"] is True
    assert "not found on PATH" in result["summary"]
    assert result["file_changes"] == []
