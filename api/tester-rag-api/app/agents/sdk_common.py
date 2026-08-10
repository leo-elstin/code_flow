"""Shared helpers between every Claude Agent SDK-driven role
(app.agents.roles.dev_sdk, app.agents.roles.planner_sdk, and any future one).

Kept separate from either role module so behavior fixed once — e.g. the
Claude Code CLI login fallback — applies to all of them, rather than each
role module carrying its own copy that can drift.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.core.config import settings


def sdk_supports_option(field_name: str) -> bool:
    """Feature-detect a ClaudeAgentOptions field so an installed SDK version
    without it can't crash a spawn, and so a role module never silently
    relies on a field's default changing underneath it — mirrors saga's
    _detect_sdk_supports_effort/_detect_sdk_supports_thinking."""
    try:
        from claude_agent_sdk import ClaudeAgentOptions
    except ImportError:
        return False
    return field_name in getattr(ClaudeAgentOptions, "__dataclass_fields__", {})


def resolve_sdk_env() -> dict[str, str]:
    """The env dict to merge onto the spawned Claude Code subprocess.

    Empty when no ANTHROPIC_API_KEY is configured — critically, this must
    NOT set the key to an empty string. env is MERGED onto the subprocess's
    inherited environment (confirmed against the SDK's own docs), so setting
    nothing here means the subprocess sees exactly what this process's own
    environment already carries, letting the SDK's own auth resolution fall
    through to Claude Code's local login state (~/.claude/) on its own. An
    explicit ANTHROPIC_API_KEY="" would inject a present-but-empty value
    instead, and whether the SDK's precedence check treats that the same as
    absent isn't documented — so this doesn't risk finding out.
    """
    if settings.ANTHROPIC_API_KEY:
        return {"ANTHROPIC_API_KEY": settings.ANTHROPIC_API_KEY}
    return {}


async def as_streaming_prompt(
    content: str | list[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    """Wrap a single turn's content into the AsyncIterable query() requires
    whenever `can_use_tool` is set on options.

    Both dev_sdk and planner_sdk pass a permission callback (their own
    denylist gate on top of allowed_tools/disallowed_tools), and the SDK
    hard-requires streaming input mode for that — a plain string prompt
    raises `ValueError: can_use_tool callback requires streaming mode`
    (confirmed by reading claude_agent_sdk/_internal/client.py directly: the
    check is unconditional whenever options.can_use_tool is set). The dict
    shape below is copied from that same file's own string-prompt handling
    (client.py's `isinstance(prompt, str)` branch) — this reproduces exactly
    what the SDK does internally for a plain string, just satisfying the
    AsyncIterable type check the can_use_tool path additionally demands.

    `content` may be a plain string or a list of Messages-API-shaped content
    blocks (text/image/...) for a multimodal turn — this dict's `content`
    field is passed straight through to the underlying CLI/API, the same as
    a raw Messages API user turn.
    """
    yield {
        "type": "user",
        "session_id": "",
        "message": {"role": "user", "content": content},
        "parent_tool_use_id": None,
    }


# Substrings that mark an SDK/CLI failure as transient — a dropped stream, a
# provider hiccup, an overload — rather than something a retry can't fix (bad
# auth, an invalid request, a refusal). Matched case-insensitively against
# ResultMessage.result, the CLI's own human-readable error text.
#
# Deliberately an ALLOWLIST of known-retryable causes, not a denylist of fatal
# ones: an unrecognized error retries zero times and surfaces immediately,
# which is the safe direction. Retrying a genuine auth failure or a malformed
# request just burns tokens and delays a real error reaching the user.
_TRANSIENT_ERROR_MARKERS = (
    "connection closed",
    "connection error",
    "connection reset",
    "econnreset",
    "socket hang up",
    "stream error",
    "timeout",
    "timed out",
    "overloaded",
    "rate limit",
    "429",
    "500",
    "502",
    "503",
    "529",
    "internal server error",
    "service unavailable",
    "bad gateway",
)


def is_transient_sdk_error(result_text: str | None) -> bool:
    """Whether an SDK run's failure text names a cause worth retrying.

    Takes the text rather than the ResultMessage so it stays a pure function,
    testable without claude-agent-sdk installed.
    """
    if not result_text:
        return False
    lowered = result_text.lower()
    return any(marker in lowered for marker in _TRANSIENT_ERROR_MARKERS)


def retry_backoff_seconds(attempt: int, *, base: float = 2.0, ceiling: float = 30.0) -> float:
    """Exponential backoff for retry *attempt* (1-based), capped at *ceiling*."""
    return min(base * (2 ** max(attempt - 1, 0)), ceiling)


def log_sdk_auth_mode(logger: Any, label: str) -> None:
    logger.info(
        "%s auth: %s",
        label,
        "explicit ANTHROPIC_API_KEY configured" if settings.ANTHROPIC_API_KEY
        else "no ANTHROPIC_API_KEY set — relying on local Claude Code login state",
    )
