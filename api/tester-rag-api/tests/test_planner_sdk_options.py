"""Tests for planner_sdk's ClaudeAgentOptions construction — the read-only
tool restriction and the auth env behavior are the two things that would be
silently wrong (never raise, just misbehave) if they regressed."""
import pytest

from app.agents.roles.planner_sdk import _ALLOWED_TOOLS, _DENIED_BUILTIN_TOOLS, _build_options
from app.core.config import settings


def test_only_read_only_tools_are_allowed():
    assert set(_ALLOWED_TOOLS) == {"Read", "Grep", "Glob"}
    # Anything that could mutate the project directory must be explicitly
    # denied — planning must never write, regardless of what a prompt asks.
    assert "Write" in _DENIED_BUILTIN_TOOLS
    assert "Edit" in _DENIED_BUILTIN_TOOLS
    assert "Bash" in _DENIED_BUILTIN_TOOLS


def test_no_env_override_when_no_api_key_configured(monkeypatch):
    pytest.importorskip("claude_agent_sdk")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)

    options = _build_options(project_path="/tmp/project")

    assert options.env == {}
    assert options.cwd == "/tmp/project"
    assert options.output_format["type"] == "json_schema"
    assert set(options.allowed_tools) == {"Read", "Grep", "Glob"}


def test_configured_key_is_passed_through(monkeypatch):
    pytest.importorskip("claude_agent_sdk")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test-key")

    options = _build_options(project_path="/tmp/project")

    assert options.env == {"ANTHROPIC_API_KEY": "sk-ant-test-key"}


def test_turn_cap_is_unbounded_by_default(monkeypatch):
    """A planner that runs out of turns produces no plan at all — every token
    spent exploring is wasted, with nothing to resume from (unlike the dev
    runtime's recoverable soft stop). So the default must be unbounded: the
    0 sentinel omits max_turns entirely and lets Claude Code's own loop run
    to completion."""
    pytest.importorskip("claude_agent_sdk")
    monkeypatch.setattr(settings, "CODE_AGENT_PLANNER_SDK_MAX_TURNS", 0)

    options = _build_options(project_path="/tmp/x")

    assert options.max_turns is None


def test_turn_cap_is_applied_when_explicitly_configured(monkeypatch):
    pytest.importorskip("claude_agent_sdk")
    monkeypatch.setattr(settings, "CODE_AGENT_PLANNER_SDK_MAX_TURNS", 25)

    options = _build_options(project_path="/tmp/x")

    assert options.max_turns == 25
