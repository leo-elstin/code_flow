"""The Anthropic adapter must produce exactly what the dev/explorer loops read.

Those loops were written against LiteLLM's OpenAI-shaped objects and push
``message.model_dump()`` back onto a conversation that gets checkpointed to
SQLite and replayed on retry. If this adapter drifts from that shape, runs break
at the point where the model first calls a tool.
"""

import json
from types import SimpleNamespace

import pytest

from app.services.llm_providers.anthropic_client import (
    to_openai_response,
    translate_messages,
    translate_tool_choice,
    translate_tools,
)
from app.services.llm_providers.openai_client import (
    to_chat_completion_response,
    translate_messages_to_responses_input,
    translate_tool_choice_to_responses,
    translate_tools_to_responses,
)


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(block_id, name, payload):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=payload)


def _anthropic_response(content, *, stop_reason="end_turn", input_tokens=10, output_tokens=5):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        model="claude-sonnet-5",
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


# --- response normalisation -------------------------------------------------


def test_text_response_maps_to_openai_shape():
    response = to_openai_response(_anthropic_response([_text_block("hello world")]))

    assert response.choices[0].message.content == "hello world"
    assert response.choices[0].message.tool_calls is None
    assert response.choices[0].finish_reason == "stop"


def test_tool_use_becomes_openai_tool_calls():
    response = to_openai_response(
        _anthropic_response(
            [_tool_use_block("toolu_01", "read_file", {"path": "lib/main.dart"})],
            stop_reason="tool_use",
        )
    )

    message = response.choices[0].message
    assert message.tool_calls is not None
    call = message.tool_calls[0]
    assert call.id == "toolu_01"
    assert call.type == "function"
    assert call.function.name == "read_file"
    # dev.py does json.loads(tc.function.arguments) — it must be a *string*.
    assert isinstance(call.function.arguments, str)
    assert json.loads(call.function.arguments) == {"path": "lib/main.dart"}
    assert response.choices[0].finish_reason == "tool_calls"


def test_message_model_dump_round_trips_as_a_conversation_turn():
    """dev.py appends message.model_dump() straight back onto `messages`."""
    response = to_openai_response(
        _anthropic_response(
            [_text_block("Reading the file."), _tool_use_block("toolu_7", "grep", {"q": "foo"})],
            stop_reason="tool_use",
        )
    )
    dumped = response.choices[0].message.model_dump(exclude_unset=False)

    assert dumped["role"] == "assistant"
    assert dumped["content"] == "Reading the file."
    assert dumped["tool_calls"][0]["function"]["name"] == "grep"
    assert dumped["tool_calls"][0]["id"] == "toolu_7"

    # And that dumped turn must survive a trip back through the translator.
    _system, translated = translate_messages([dumped])
    blocks = translated[0]["content"]
    assert {"type": "text", "text": "Reading the file."} in blocks
    assert any(b["type"] == "tool_use" and b["input"] == {"q": "foo"} for b in blocks)


def test_usage_maps_to_openai_token_names():
    response = to_openai_response(
        _anthropic_response([_text_block("hi")], input_tokens=120, output_tokens=30)
    )
    assert response.usage.prompt_tokens == 120
    assert response.usage.completion_tokens == 30
    assert response.usage.total_tokens == 150


def test_json_prefill_restores_the_opening_brace():
    response = to_openai_response(
        _anthropic_response([_text_block('"plan": []}')]), json_prefill=True
    )
    assert json.loads(response.choices[0].message.content) == {"plan": []}


def test_max_tokens_stop_reason_maps_to_length():
    response = to_openai_response(
        _anthropic_response([_text_block("truncated")], stop_reason="max_tokens")
    )
    assert response.choices[0].finish_reason == "length"


# --- message translation ----------------------------------------------------


def test_system_message_is_hoisted_out():
    system, messages = translate_messages(
        [
            {"role": "system", "content": "You are a dev agent."},
            {"role": "user", "content": "Implement the login screen."},
        ]
    )
    assert system == "You are a dev agent."
    assert [m["role"] for m in messages] == ["user"]


def test_multiple_system_messages_are_joined():
    system, _ = translate_messages(
        [{"role": "system", "content": "A"}, {"role": "system", "content": "B"}]
    )
    assert system == "A\n\nB"


def test_tool_message_becomes_a_user_tool_result():
    _system, messages = translate_messages(
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "toolu_1", "type": "function",
                     "function": {"name": "read_file", "arguments": '{"path": "a.dart"}'}}
                ],
            },
            {"role": "tool", "tool_call_id": "toolu_1", "content": "file contents"},
        ]
    )

    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    result_block = messages[2]["content"][0]
    assert result_block["type"] == "tool_result"
    assert result_block["tool_use_id"] == "toolu_1"
    assert result_block["content"] == "file contents"


def test_consecutive_tool_results_are_coalesced_into_one_turn():
    """Anthropic rejects two user turns in a row, but the dev loop appends one
    `tool` message per tool call — so parallel calls must merge."""
    _system, messages = translate_messages(
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "t1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
                    {"id": "t2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "t1", "content": "one"},
            {"role": "tool", "tool_call_id": "t2", "content": "two"},
        ]
    )

    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant", "user"], "tool results must share one user turn"
    assert [b["tool_use_id"] for b in messages[2]["content"]] == ["t1", "t2"]


def test_roles_always_alternate():
    _system, messages = translate_messages(
        [
            {"role": "user", "content": "one"},
            {"role": "user", "content": "two"},
            {"role": "assistant", "content": "ok"},
            {"role": "assistant", "content": "still me"},
        ]
    )
    roles = [m["role"] for m in messages]
    assert all(a != b for a, b in zip(roles, roles[1:]))


def test_malformed_tool_arguments_do_not_raise():
    _system, messages = translate_messages(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "t1", "type": "function",
                     "function": {"name": "a", "arguments": "not json{"}}
                ],
            }
        ]
    )
    assert messages[0]["content"][0]["input"] == {}


def test_empty_content_messages_are_dropped():
    _system, messages = translate_messages(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": None}]
    )
    assert len(messages) == 1


# --- tool + tool_choice translation -----------------------------------------


def test_tools_translate_to_input_schema():
    schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    translated = translate_tools(
        [{"type": "function",
          "function": {"name": "read_file", "description": "Read a file", "parameters": schema}}]
    )
    assert translated == [
        {"name": "read_file", "description": "Read a file", "input_schema": schema}
    ]


def test_tools_without_parameters_get_an_empty_schema():
    translated = translate_tools([{"type": "function", "function": {"name": "noop"}}])
    assert translated[0]["input_schema"] == {"type": "object", "properties": {}}


@pytest.mark.parametrize(
    "given,expected",
    [
        ("auto", {"type": "auto"}),
        ("required", {"type": "any"}),
        ("none", {"type": "none"}),
        ({"type": "function", "function": {"name": "grep"}}, {"type": "tool", "name": "grep"}),
        (None, None),
    ],
)
def test_tool_choice_translation(given, expected):
    assert translate_tool_choice(given) == expected


# ============================================================================
# OpenAI Responses API adapter
#
# Reasoning-effort models reject function tools on /v1/chat/completions, so
# this path routes through /v1/responses instead. Same requirement as the
# Anthropic adapter above: whatever comes out must be exactly what dev.py's
# tool-calling loop consumes.
# ============================================================================


def _responses_text_item(text):
    return SimpleNamespace(
        type="message",
        content=[SimpleNamespace(type="output_text", text=text)],
    )


def _responses_function_call_item(call_id, name, arguments):
    return SimpleNamespace(type="function_call", call_id=call_id, name=name, arguments=arguments)


def _responses_reasoning_item():
    return SimpleNamespace(type="reasoning", id="rs_1", encrypted_content="opaque")


def _responses_response(output, *, input_tokens=10, output_tokens=5, total_tokens=None):
    return SimpleNamespace(
        output=output,
        model="gpt-5.6",
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens if total_tokens is not None else input_tokens + output_tokens,
        ),
    )


def test_responses_text_message_maps_to_openai_shape():
    response = to_chat_completion_response(_responses_response([_responses_text_item("hello")]))
    assert response.choices[0].message.content == "hello"
    assert response.choices[0].message.tool_calls is None
    assert response.choices[0].finish_reason == "stop"


def test_responses_function_call_becomes_openai_tool_call():
    response = to_chat_completion_response(
        _responses_response(
            [_responses_function_call_item("call_abc123", "read_file", '{"path": "lib/main.dart"}')]
        )
    )
    call = response.choices[0].message.tool_calls[0]
    # dev.py sends this back as tool_call_id — it must be call_id, not the item id.
    assert call.id == "call_abc123"
    assert call.function.name == "read_file"
    assert isinstance(call.function.arguments, str)
    assert json.loads(call.function.arguments) == {"path": "lib/main.dart"}
    assert response.choices[0].finish_reason == "tool_calls"


def test_responses_reasoning_items_are_dropped_not_raised():
    response = to_chat_completion_response(
        _responses_response(
            [
                _responses_reasoning_item(),
                _responses_function_call_item("call_1", "grep", "{}"),
            ]
        )
    )
    assert len(response.choices[0].message.tool_calls) == 1


def test_responses_message_model_dump_round_trips():
    response = to_chat_completion_response(
        _responses_response(
            [
                _responses_text_item("Reading the file."),
                _responses_function_call_item("call_7", "grep", '{"q": "foo"}'),
            ]
        )
    )
    dumped = response.choices[0].message.model_dump(exclude_unset=False)
    assert dumped["tool_calls"][0]["id"] == "call_7"
    assert dumped["tool_calls"][0]["function"]["name"] == "grep"

    # And that dumped turn must translate back into a valid function_call item.
    translated = translate_messages_to_responses_input([dumped])
    call_items = [item for item in translated if item.get("type") == "function_call"]
    assert call_items[0]["call_id"] == "call_7"
    assert json.loads(call_items[0]["arguments"]) == {"q": "foo"}


def test_responses_usage_maps_token_names():
    response = to_chat_completion_response(
        _responses_response([_responses_text_item("hi")], input_tokens=120, output_tokens=30)
    )
    assert response.usage.prompt_tokens == 120
    assert response.usage.completion_tokens == 30
    assert response.usage.total_tokens == 150


# --- input translation -------------------------------------------------


def test_plain_messages_pass_through_by_role():
    items = translate_messages_to_responses_input(
        [
            {"role": "system", "content": "You are a dev agent."},
            {"role": "user", "content": "Implement the login screen."},
        ]
    )
    assert items == [
        {"role": "system", "content": "You are a dev agent."},
        {"role": "user", "content": "Implement the login screen."},
    ]


def test_tool_message_becomes_function_call_output():
    items = translate_messages_to_responses_input(
        [{"role": "tool", "tool_call_id": "call_1", "content": "file contents"}]
    )
    assert items == [
        {"type": "function_call_output", "call_id": "call_1", "output": "file contents"}
    ]


def test_assistant_tool_calls_become_separate_function_call_items():
    items = translate_messages_to_responses_input(
        [
            {
                "role": "assistant",
                "content": "Let me check.",
                "tool_calls": [
                    {"id": "call_1", "type": "function",
                     "function": {"name": "read_file", "arguments": '{"path": "a.dart"}'}},
                    {"id": "call_2", "type": "function",
                     "function": {"name": "grep", "arguments": '{"q": "TODO"}'}},
                ],
            }
        ]
    )
    assert items[0] == {"role": "assistant", "content": "Let me check."}
    assert items[1] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "read_file",
        "arguments": '{"path": "a.dart"}',
    }
    assert items[2]["call_id"] == "call_2"


def test_assistant_tool_calls_without_text_content_emit_no_message_item():
    items = translate_messages_to_responses_input(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "type": "function",
                     "function": {"name": "noop", "arguments": "{}"}}
                ],
            }
        ]
    )
    assert items == [{"type": "function_call", "call_id": "call_1", "name": "noop", "arguments": "{}"}]


def test_none_content_messages_are_dropped():
    items = translate_messages_to_responses_input(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": None}]
    )
    assert items == [{"role": "user", "content": "hi"}]


# --- tools + tool_choice translation ------------------------------------


def test_tools_translate_to_flat_responses_shape():
    schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    translated = translate_tools_to_responses(
        [{"type": "function",
          "function": {"name": "read_file", "description": "Read a file", "parameters": schema}}]
    )
    assert translated == [
        {
            "type": "function",
            "name": "read_file",
            "description": "Read a file",
            "parameters": schema,
            "strict": False,
        }
    ]


def test_tools_without_parameters_get_an_empty_schema():
    translated = translate_tools_to_responses([{"type": "function", "function": {"name": "noop"}}])
    assert translated[0]["parameters"] == {"type": "object", "properties": {}}


def test_named_tool_choice_flattens_out_of_the_function_wrapper():
    result = translate_tool_choice_to_responses(
        {"type": "function", "function": {"name": "grep"}}
    )
    assert result == {"type": "function", "name": "grep"}


@pytest.mark.parametrize("shared", ["auto", "required", "none"])
def test_shared_tool_choice_vocabulary_passes_through(shared):
    assert translate_tool_choice_to_responses(shared) == shared


# ============================================================================
# Prompt caching
#
# Anthropic needs an explicit cache_control breakpoint; OpenAI's caching is
# automatic (no request param needed) so there's nothing to send there — only
# something to read back (cached_tokens) once it happens.
# ============================================================================


def test_anthropic_request_carries_the_cache_control_breakpoint():
    """The top-level cache_control param is what makes Anthropic place (and
    move forward, as the conversation grows) a cache breakpoint automatically
    — without it, every turn of a multi-step tool-calling loop pays full
    price for the whole resent history."""
    from app.services.llm_providers.anthropic_client import AnthropicProviderClient
    from app.services.llm_providers.base import LLMConfig

    client = AnthropicProviderClient(LLMConfig(provider="anthropic", api_key="sk-test"))

    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            content=[_text_block("ok")],
            stop_reason="end_turn",
            model="claude-sonnet-5",
            usage=SimpleNamespace(
                input_tokens=1, output_tokens=1,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
            ),
        )

    client._client.messages.create = fake_create

    import asyncio
    asyncio.run(client.acompletion(model="claude-sonnet-5", messages=[{"role": "user", "content": "hi"}]))

    assert captured["cache_control"] == {"type": "ephemeral"}


def test_anthropic_usage_folds_cache_reads_into_prompt_tokens_and_reports_them():
    response = to_openai_response(
        SimpleNamespace(
            content=[_text_block("ok")],
            stop_reason="end_turn",
            model="claude-sonnet-5",
            usage=SimpleNamespace(
                input_tokens=50,
                output_tokens=10,
                cache_read_input_tokens=9000,
                cache_creation_input_tokens=0,
            ),
        )
    )
    # Full prompt volume is uncached + cache-read + cache-creation tokens...
    assert response.usage.prompt_tokens == 50 + 9000
    # ...with the cache-read portion also called out on its own.
    assert response.usage.cached_tokens == 9000
    assert response.usage.total_tokens == 50 + 9000 + 10


def test_openai_responses_usage_reports_cached_tokens():
    from app.services.llm_providers.openai_client import to_chat_completion_response

    response = to_chat_completion_response(
        SimpleNamespace(
            output=[],
            model="gpt-5.6-terra",
            usage=SimpleNamespace(
                input_tokens=5000,
                output_tokens=20,
                total_tokens=5020,
                input_tokens_details=SimpleNamespace(cached_tokens=4800),
            ),
        )
    )
    assert response.usage.cached_tokens == 4800


def test_extract_cached_tokens_reads_our_normalized_usage():
    from app.services.generation import extract_cached_tokens
    from app.services.llm_providers.base import Usage

    assert extract_cached_tokens(Usage(cached_tokens=123)) == 123
    assert extract_cached_tokens(Usage()) == 0


def test_extract_cached_tokens_reads_raw_openai_chat_completions_shape():
    """Chat Completions responses pass through un-normalized — cached_tokens
    lives nested at prompt_tokens_details.cached_tokens instead of flat."""
    from app.services.generation import extract_cached_tokens

    raw_usage = SimpleNamespace(prompt_tokens_details=SimpleNamespace(cached_tokens=777))
    assert extract_cached_tokens(raw_usage) == 777
    assert extract_cached_tokens(SimpleNamespace(prompt_tokens_details=None)) == 0
    assert extract_cached_tokens(None) == 0
