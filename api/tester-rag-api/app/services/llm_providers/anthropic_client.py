"""Anthropic provider client.

Anthropic's Messages API differs from OpenAI's chat completions in three ways
that matter here: system prompts are a top-level parameter rather than a
message, tool results travel as content blocks inside a *user* turn rather than
as a distinct ``tool`` role, and there is no ``response_format`` JSON mode.

This module absorbs all three so callers keep speaking OpenAI shape. See
``base.py`` for why that shape is the internal contract.
"""

from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic

from app.core.config import settings
from app.services.llm_providers.base import (
    AssistantMessage,
    ChatCompletionResponse,
    Choice,
    FunctionCall,
    LLMConfig,
    ToolCall,
    Usage,
)

# Anthropic maps its own stop reasons; translate to OpenAI's vocabulary so
# anything inspecting finish_reason behaves the same across providers.
_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
}


def _as_blocks(content: Any) -> list[dict[str, Any]]:
    """Normalise an OpenAI message ``content`` into Anthropic content blocks."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, list):
        # Already block-shaped (e.g. a message we produced on a previous turn).
        return content
    return [{"type": "text", "text": str(content)}]


def translate_messages(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert OpenAI-shaped messages into ``(system, anthropic_messages)``.

    Consecutive same-role turns are coalesced. This matters for tool calls: the
    dev loop appends one ``{"role": "tool"}`` message per tool call, and
    Anthropic rejects two user turns in a row — all tool results for one
    assistant turn must arrive in a single user message.
    """
    system_parts: list[str] = []
    turns: list[tuple[str, list[dict[str, Any]]]] = []

    def push(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if turns and turns[-1][0] == role:
            turns[-1][1].extend(blocks)
        else:
            turns.append((role, blocks))

    for message in messages:
        role = message.get("role")

        if role == "system":
            content = message.get("content")
            if content:
                system_parts.append(content if isinstance(content, str) else str(content))

        elif role == "tool":
            push(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": message.get("tool_call_id") or "",
                        "content": str(message.get("content") or ""),
                    }
                ],
            )

        elif role == "assistant":
            blocks = _as_blocks(message.get("content"))
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                raw_args = function.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    # A malformed arguments blob must not kill the conversation;
                    # send an empty input and let the tool report the error.
                    arguments = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id") or "",
                        "name": function.get("name") or "",
                        "input": arguments,
                    }
                )
            push("assistant", blocks)

        else:  # user, or anything unrecognised
            push("user", _as_blocks(message.get("content")))

    system = "\n\n".join(system_parts) if system_parts else None
    return system, [{"role": role, "content": blocks} for role, blocks in turns]


def translate_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """OpenAI ``{"type": "function", "function": {...}}`` → Anthropic tool specs."""
    if not tools:
        return None
    translated = []
    for tool in tools:
        function = tool.get("function") if "function" in tool else tool
        translated.append(
            {
                "name": function.get("name"),
                "description": function.get("description") or "",
                "input_schema": function.get("parameters")
                or {"type": "object", "properties": {}},
            }
        )
    return translated


def translate_tool_choice(tool_choice: Any) -> dict[str, Any] | None:
    if tool_choice in (None, "auto"):
        return {"type": "auto"} if tool_choice == "auto" else None
    if tool_choice in ("required", "any"):
        return {"type": "any"}
    if tool_choice == "none":
        return {"type": "none"}
    if isinstance(tool_choice, dict):
        name = (tool_choice.get("function") or {}).get("name") or tool_choice.get("name")
        if name:
            return {"type": "tool", "name": name}
    return None


def to_openai_response(response: Any, *, json_prefill: bool = False) -> ChatCompletionResponse:
    """Normalise an Anthropic Message into the OpenAI chat-completion shape."""
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []

    for block in response.content or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_parts.append(getattr(block, "text", "") or "")
        elif block_type == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=getattr(block, "id", "") or "",
                    function=FunctionCall(
                        name=getattr(block, "name", "") or "",
                        # OpenAI callers json.loads() this, so it must be a string.
                        arguments=json.dumps(getattr(block, "input", {}) or {}),
                    ),
                )
            )

    content = "".join(text_parts)
    if json_prefill:
        # The prefilled "{" is not echoed back in the response, so restore it.
        content = "{" + content

    usage = getattr(response, "usage", None)
    # Anthropic's `input_tokens` counts only the *uncached* portion of the
    # prompt — cache_read/cache_creation tokens are reported separately. For
    # our purposes (measuring actual context volume) the full prompt size is
    # all three combined; cached_tokens is called out on top so it's visible
    # how much of that was cheap (~10% of base price) rather than full price.
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
    cache_read_tokens = int(getattr(usage, "cache_read_input_tokens", 0) or 0) if usage else 0
    cache_creation_tokens = int(getattr(usage, "cache_creation_input_tokens", 0) or 0) if usage else 0
    prompt_tokens = input_tokens + cache_read_tokens + cache_creation_tokens

    return ChatCompletionResponse(
        model=getattr(response, "model", None),
        choices=[
            Choice(
                message=AssistantMessage(
                    content=content or None,
                    tool_calls=tool_calls or None,
                ),
                finish_reason=_STOP_REASONS.get(
                    getattr(response, "stop_reason", "") or "", "stop"
                ),
            )
        ],
        usage=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=output_tokens,
            total_tokens=prompt_tokens + output_tokens,
            cached_tokens=cache_read_tokens,
        ),
    )


class AnthropicProviderClient:
    def __init__(self, config: LLMConfig) -> None:
        self._client = AsyncAnthropic(
            api_key=config.api_key or "",
            base_url=config.base_url or None,
            timeout=settings.CODE_AGENT_LLM_TIMEOUT,
            max_retries=settings.CODE_AGENT_LLM_MAX_RETRIES,
        )

    async def acompletion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        response_format: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ChatCompletionResponse:
        system, anthropic_messages = translate_messages(messages)

        # Anthropic has no JSON response_format. Prefill an opening brace so the
        # model has no room to preamble; callers already instruct JSON in their
        # system prompt. Prefill is incompatible with tools, so only do it when
        # no tools are in play — which is the case for every JSON caller.
        json_prefill = bool(
            response_format
            and response_format.get("type") == "json_object"
            and not tools
        )
        if json_prefill:
            anthropic_messages.append({"role": "assistant", "content": [{"type": "text", "text": "{"}]})

        params: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            # Required by Anthropic — OpenAI treats it as optional.
            "max_tokens": kwargs.pop("max_tokens", settings.CODE_AGENT_LLM_MAX_TOKENS),
            # Automatic prompt caching: Anthropic places the breakpoint at the
            # last cacheable block and moves it forward as the conversation
            # grows, so a multi-step tool-calling loop only pays full price
            # for what's new each turn instead of resending its whole history
            # at full price every time. Below the per-model minimum (512-4096
            # tokens) this is silently a no-op — no error, just no discount.
            "cache_control": {"type": "ephemeral"},
        }
        if system:
            params["system"] = system

        translated_tools = translate_tools(tools)
        if translated_tools:
            params["tools"] = translated_tools
            choice = translate_tool_choice(tool_choice)
            if choice:
                params["tool_choice"] = choice

        response = await self._client.messages.create(**params)
        return to_openai_response(response, json_prefill=json_prefill)
