import pytest

from app.services.run_activity import (
    append_activity,
    get_current_action,
    get_token_totals,
    list_activity,
)


@pytest.fixture
def activity_db(tmp_path, monkeypatch):
    db_path = tmp_path / "activity.db"
    monkeypatch.setattr("app.services.run_activity._DB_PATH", db_path)
    return db_path


def test_append_and_list_incremental(activity_db):
    run_id = "run-activity-1"

    e1 = append_activity(
        run_id,
        type="grep",
        phase="planner",
        title="Grepping",
        files=["lib/foo.dart"],
    )
    e2 = append_activity(
        run_id,
        type="tool",
        phase="dev",
        title="write_file",
        files=["lib/foo.dart"],
        meta={"tool": "write_file"},
    )

    assert e1["seq"] == 1
    assert e2["seq"] == 2

    all_events = list_activity(run_id)
    assert len(all_events) == 2

    delta = list_activity(run_id, after_seq=1)
    assert len(delta) == 1
    assert delta[0]["title"] == "write_file"


def test_current_action_skips_token_events(activity_db):
    run_id = "run-activity-2"
    append_activity(run_id, type="status", phase="planner", title="Planning")
    append_activity(
        run_id,
        type="token",
        phase="planner",
        title="tokens",
        meta={"total_tokens": 100},
    )
    append_activity(run_id, type="grep", phase="planner", title="Grepped files")

    current = get_current_action(run_id)
    assert current is not None
    assert current["title"] == "Grepped files"


def test_discover_context_emits_grep_activity(tmp_path, monkeypatch):
    from app.services import run_activity
    from app.services.feature_discovery import discover_context
    from app.tools.grep import GrepMatch

    db_path = tmp_path / "discover_activity.db"
    monkeypatch.setattr(run_activity, "_DB_PATH", db_path)

    project = tmp_path / "proj"
    project.mkdir()
    (project / "lib").mkdir()
    (project / "lib" / "foo.dart").write_text("class Foo {}", encoding="utf-8")

    def fake_grep(_path, _terms, *, max_results=100):
        return [GrepMatch(file_path="lib/foo.dart", line_number=1, line_text="class Foo")]

    monkeypatch.setattr("app.services.feature_discovery.grep_terms", fake_grep)
    monkeypatch.setattr("app.services.feature_discovery._expand_imports", lambda *_a, **_k: set())
    monkeypatch.setattr(
        "app.services.feature_discovery.expand_via_lsp",
        lambda **_k: (set(), []),
    )
    monkeypatch.setattr(
        "app.services.feature_discovery.load_project_guide",
        lambda _p: {"found": False},
    )
    monkeypatch.setattr(
        "app.services.feature_discovery.ProjectExplorer",
        lambda _p: type("E", (), {"explore": lambda self, _x: {}})(),
    )

    discover_context("add foo feature", str(project), run_id="run-disc")

    events = list_activity("run-disc")
    grep_events = [e for e in events if e["type"] == "grep"]
    assert len(grep_events) >= 2
    assert any("lib/foo.dart" in (e.get("files") or []) for e in grep_events)


def test_token_totals_aggregate(activity_db):
    run_id = "run-activity-3"
    append_activity(
        run_id,
        type="token",
        phase="planner",
        title="t1",
        meta={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )
    append_activity(
        run_id,
        type="token",
        phase="dev",
        title="t2",
        meta={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    )

    totals = get_token_totals(run_id)
    assert totals["prompt_tokens"] == 30
    assert totals["completion_tokens"] == 15
    assert totals["total_tokens"] == 45
