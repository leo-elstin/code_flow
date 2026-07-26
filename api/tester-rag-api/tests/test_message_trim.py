"""Tests for the shared tool-message trimmer used by both the dev loop and
the explorer loop (app.agents.message_trim.trim_tool_messages)."""

from app.agents.message_trim import trim_tool_messages


def _messages_with_n_tool_calls(n: int) -> list[dict]:
    messages = [{"role": "system", "content": "sys"}]
    for i in range(n):
        messages.append({"role": "assistant", "content": f"call {i}", "tool_calls": [{"id": str(i)}]})
        messages.append({"role": "tool", "tool_call_id": str(i), "content": "X" * 2000})
    return messages


def test_below_keep_recent_is_untouched():
    messages = _messages_with_n_tool_calls(3)
    trimmed = trim_tool_messages(messages, keep_recent=4, stub_len=400, batch_size=4)
    assert trimmed == messages


def test_stubs_old_tool_results_in_batches_and_preserves_pairing():
    # 10 tool results, keep_recent=4, batch_size=4:
    # eligible = 10 - 4 = 6, floors to one batch of 4 -> 4 stale, 6 full.
    messages = _messages_with_n_tool_calls(10)

    trimmed = trim_tool_messages(messages, keep_recent=4, stub_len=400, batch_size=4)

    # Same message count -> every tool result still has its assistant tool_call.
    assert len(trimmed) == len(messages)
    tool_msgs = [m for m in trimmed if m.get("role") == "tool"]
    stubbed = [m for m in tool_msgs if "older tool output trimmed" in m["content"]]
    kept_full = [m for m in tool_msgs if len(m["content"]) == 2000]
    assert len(stubbed) == 4
    assert len(kept_full) == 6


def test_cutoff_holds_steady_within_a_batch():
    baseline = trim_tool_messages(
        _messages_with_n_tool_calls(8), keep_recent=4, stub_len=400, batch_size=4
    )
    for n in (9, 10, 11):
        trimmed = trim_tool_messages(
            _messages_with_n_tool_calls(n), keep_recent=4, stub_len=400, batch_size=4
        )
        assert trimmed[: len(baseline)] == baseline
