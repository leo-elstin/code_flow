"""Guards the ANTHROPIC_API_KEY fallback: when no key is configured, the SDK
dev loop must not inject an empty-string key into the subprocess env (which
could shadow the Claude Code CLI login fallback ~/.claude/ resolves) — it
should pass nothing at all and let the SDK's own auth resolution run."""
import pytest

from app.agents.roles.dev_sdk import _build_options
from app.core.config import settings


def test_no_env_override_when_no_api_key_configured(monkeypatch):
    pytest.importorskip("claude_agent_sdk")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)

    options = _build_options(worktree_path="/tmp/x", mcp_server=object(), prior_session_id=None)

    # dataclass default is {} — nothing was merged in, so the subprocess
    # inherits this process's environment untouched and the SDK can fall
    # through to local Claude Code login state on its own.
    assert options.env == {}


def test_configured_key_is_passed_through(monkeypatch):
    pytest.importorskip("claude_agent_sdk")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test-key")

    options = _build_options(worktree_path="/tmp/x", mcp_server=object(), prior_session_id=None)

    assert options.env == {"ANTHROPIC_API_KEY": "sk-ant-test-key"}
