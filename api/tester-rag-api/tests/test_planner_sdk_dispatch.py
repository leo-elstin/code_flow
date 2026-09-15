"""Tests that CODE_AGENT_PLANNER_RUNTIME switches run_planner to the SDK path.

Deliberately does not assert on settings.CODE_AGENT_PLANNER_RUNTIME's ambient
value — see test_dev_sdk_dispatch.py's module docstring for why that's a live
.env fact, not something a unit test should depend on. The setting is pinned
explicitly via monkeypatch below instead."""
import asyncio

from app.agents.roles import planner as planner_mod
from app.core.config import settings


def test_run_planner_dispatches_to_sdk_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "CODE_AGENT_PLANNER_RUNTIME", "sdk")

    captured: dict = {}
    sentinel = {
        "context_bundle": {},
        "plan": {"feature_summary": "ok"},
        "questions": [],
        "acceptance_criteria": [],
        "messages": [],
    }

    async def fake_run_planner_sdk(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr("app.agents.roles.planner_sdk.run_planner_sdk", fake_run_planner_sdk)

    result = asyncio.run(
        planner_mod.run_planner(
            "add a login screen",
            "/tmp/project",
            run_id="run-1",
            ticket_id=42,
        )
    )

    assert result is sentinel
    assert captured["user_request"] == "add a login screen"
    assert captured["project_path"] == "/tmp/project"
    assert captured["run_id"] == "run-1"
    assert captured["ticket_id"] == 42
    # Dispatch must happen before any legacy setup (discover_context, explorer,
    # chat_completion_json) runs — proven by the fact this call never touched
    # any of that and still returned cleanly.
