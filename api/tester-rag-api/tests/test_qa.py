"""Tests for the QA review node."""

import asyncio
import json

from app.agents.roles import qa as qa_mod


def test_run_qa_drops_plan_markdown_but_keeps_structured_fields(monkeypatch):
    """plan_markdown is a prose restatement of fields already in the same plan
    dict — it's for the human plan review UI, not the QA review, and was being
    sent in full on every run's final review call."""
    plan = {
        "feature_summary": "Add the frobnicator widget.",
        "files_to_create": [],
        "plan_markdown": "# Frobnicator\n\nA VERY long human-readable restatement",
    }

    captured = {}

    async def fake_chat_completion_json(*, messages, **kwargs):
        captured["payload"] = json.loads(messages[1]["content"])
        return {"scenarios": [], "regression_scope": [], "manual_checklist": []}, {}

    monkeypatch.setattr(qa_mod, "chat_completion_json", fake_chat_completion_json)
    monkeypatch.setattr(qa_mod, "get_widget_tree", lambda *a, **k: [])

    asyncio.run(
        qa_mod.run_qa(
            plan=plan,
            acceptance_criteria=["AC1"],
            diffs=[],
            project_path="/tmp/project",
            worktree_path="/tmp/worktree",
            run_id=None,
        )
    )

    sent_plan = captured["payload"]["plan"]
    assert "plan_markdown" not in sent_plan
    assert sent_plan["feature_summary"] == "Add the frobnicator widget."
