"""Tests that CODE_AGENT_DEV_RUNTIME correctly switches run_dev between the
legacy hand-rolled loop and the SDK-driven one.

Deliberately does NOT assert on settings.CODE_AGENT_DEV_RUNTIME's ambient
value — that's this machine's live .env, not something a unit test should
depend on (a local .env with CODE_AGENT_DEV_RUNTIME=sdk already set is a
legitimate, expected state, not a bug). The two tests below each pin the
setting explicitly via monkeypatch instead, so they're correct regardless of
what's actually configured in the running environment. The source default
itself — os.getenv("CODE_AGENT_DEV_RUNTIME", "legacy") in config.py — is a
one-line, self-evident fact not worth a runtime assertion."""
import asyncio

from app.agents.roles import dev as dev_mod
from app.core.config import settings


def test_run_dev_dispatches_to_sdk_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "CODE_AGENT_DEV_RUNTIME", "sdk")

    captured: dict = {}
    sentinel = {"file_changes": [], "summary": "ok", "dev_sdk_session_id": "sess_123"}

    async def fake_run_dev_sdk(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr("app.agents.roles.dev_sdk.run_dev_sdk", fake_run_dev_sdk)

    result = asyncio.run(
        dev_mod.run_dev(
            plan={"files_to_create": []},
            context_bundle={},
            worktree_path="/tmp/worktree",
            project_path="/tmp/project",
            run_id="run-1",
            prior_sdk_session_id="sess_prior",
        )
    )

    assert result is sentinel
    assert captured["worktree_path"] == "/tmp/worktree"
    assert captured["run_id"] == "run-1"
    assert captured["prior_session_id"] == "sess_prior"


def test_run_dev_stays_on_legacy_loop_when_runtime_is_legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CODE_AGENT_DEV_RUNTIME", "legacy")

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    project = tmp_path / "project"
    project.mkdir()

    called = {"sdk": False}

    async def fake_run_dev_sdk(**kwargs):
        called["sdk"] = True
        return {}

    monkeypatch.setattr("app.agents.roles.dev_sdk.run_dev_sdk", fake_run_dev_sdk)

    # Empty plan -> no files -> the legacy loop's natural-completion path
    # exits without ever needing a real model call, since there's nothing to
    # implement and the brief is the plain "begin implementation" default.
    # We only care that the SDK path was never touched.
    async def fake_acompletion(*, model, messages, tools, tool_choice, **kwargs):
        from app.services.llm_providers.base import (
            AssistantMessage,
            ChatCompletionResponse,
            Choice,
        )

        return ChatCompletionResponse(
            choices=[Choice(message=AssistantMessage(role="assistant", content="done"))]
        )

    monkeypatch.setattr(dev_mod, "acompletion", fake_acompletion)

    asyncio.run(
        dev_mod.run_dev(
            plan={},
            context_bundle={},
            worktree_path=str(worktree),
            project_path=str(project),
        )
    )

    assert called["sdk"] is False
