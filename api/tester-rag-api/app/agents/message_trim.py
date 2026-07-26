from typing import Any


def trim_tool_messages(
    messages: list[dict[str, Any]],
    *,
    keep_recent: int,
    stub_len: int,
    batch_size: int,
) -> list[dict[str, Any]]:
    """Shrink a tool-calling conversation without breaking tool_call/tool_result
    pairing. Every message is kept (so each assistant tool_call keeps its
    matching tool result, which the API requires), but the *content* of all
    but the most recent `keep_recent` tool results is stubbed. That preserves
    the agent's reasoning and recent evidence while shedding the bulk of old
    file dumps, so carrying context across steps/iterations stays bounded.

    The stale/full cutoff advances in batches of `batch_size` tool results
    instead of shifting by one on every single call: recomputing "last N full"
    as a pure sliding window means almost every turn stubs exactly one message
    for the first time — a byte-level mutation partway through the
    conversation, which breaks prefix-based prompt caching (both Anthropic's
    cache_control and OpenAI's automatic longest-prefix match need that prefix
    to stay byte-identical call to call). Freezing the cutoff for a whole batch
    lets the cache actually hold across that stretch instead of invalidating
    every step."""
    tool_positions = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    eligible = len(tool_positions) - keep_recent
    if eligible <= 0:
        return messages
    batch_eligible = (eligible // batch_size) * batch_size
    if batch_eligible <= 0:
        return messages
    stale = set(tool_positions[:batch_eligible])
    trimmed: list[dict[str, Any]] = []
    for i, msg in enumerate(messages):
        content = msg.get("content")
        if i in stale and isinstance(content, str) and len(content) > stub_len:
            msg = {**msg, "content": content[:stub_len] + "\n...[older tool output trimmed]"}
        trimmed.append(msg)
    return trimmed
