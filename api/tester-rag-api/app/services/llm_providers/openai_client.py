"""OpenAI provider client — covers both direct api.openai.com and a
self-hosted / gateway OpenAI-compatible endpoint.

The only difference between the two is whether ``base_url`` is set, so one
client serves both.

Two request paths live here:

- **Chat Completions** (default): responses pass straight through — the
  OpenAI SDK's objects are already the OpenAI chat-completion shape the rest
  of the codebase expects.
- **Responses API** (opt-in, via ``reasoning_effort``/``reasoning_mode``):
  reasoning-effort models reject function tools combined with
  ``reasoning_effort`` on ``/v1/chat/completions`` — they require
  ``/v1/responses`` instead. That endpoint uses a different request/response
  shape (``input`` items instead of ``messages``, ``function_call`` /
  ``function_call_output`` items instead of a ``tool`` role), so this path
  translates in both directions and keeps OpenAI chat-completion shape as the
  contract everything else in the codebase sees — the same approach as
  ``anthropic_client.py``.
"""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

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


def translate_tool_choice_to_responses(tool_choice: Any) -> Any:
    """Chat Completions' ``{"type":"function","function":{"name":X}}`` →
    Responses' flat ``{"type":"function","name":X}``. Everything else
    (``"auto"``, ``"required"``, ``"none"``) is already shared vocabulary."""
    if isinstance(tool_choice, dict) and "function" in tool_choice:
        name = (tool_choice.get("function") or {}).get("name")
        if name:
            return {"type": "function", "name": name}
    return tool_choice


def translate_tools_to_responses(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Chat Completions' ``{"type":"function","function":{...}}`` → the
    Responses API's flat tool shape (name/description/parameters at the top
    level, no ``function`` wrapper)."""
    if not tools:
        return None
    translated = []
    for tool in tools:
        function = tool.get("function") if "function" in tool else tool
        translated.append(
            {
                "type": "function",
                "name": function.get("name"),
                "description": function.get("description") or "",
                "parameters": function.get("parameters") or {"type": "object", "properties": {}},
                # Structured Outputs' strict mode enforces closed schemas; the
                # dev tool schemas aren't guaranteed to satisfy that, so leave
                # it off rather than reject calls that would otherwise work.
                "strict": False,
            }
        )
    return translated


def translate_messages_to_responses_input(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """OpenAI chat messages → Responses API ``input`` items.

    The Responses API accepts role-shaped ``{"role": ..., "content": ...}``
    items directly for plain user/system/assistant turns — this is the one
    genuine shortcut Responses gives us over Anthropic's translation. Only two
    shapes need real conversion: an assistant message carrying ``tool_calls``
    (→ a ``function_call`` item per call), and a ``tool`` role message (→ a
    ``function_call_output`` item, correlated by ``call_id``).
    """
    items: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")

        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id") or "",
                    "output": str(message.get("content") or ""),
                }
            )
            continue

        tool_calls = message.get("tool_calls") if role == "assistant" else None
        if tool_calls:
            content = message.get("content")
            if content:
                items.append({"role": "assistant", "content": content})
            for call in tool_calls:
                function = call.get("function") or {}
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.get("id") or "",
                        "name": function.get("name") or "",
                        "arguments": function.get("arguments") or "{}",
                    }
                )
            continue

        content = message.get("content")
        if content is None:
            continue
        items.append({"role": role, "content": content})

    return items


def _extract_message_text(item: Any) -> str:
    parts = getattr(item, "content", None) or []
    texts = [getattr(part, "text", "") for part in parts if getattr(part, "type", None) == "output_text"]
    return "".join(texts)


def to_chat_completion_response(response: Any) -> ChatCompletionResponse:
    """Normalise a Responses API ``Response`` into OpenAI chat-completion shape.

    Reasoning items (``type: "reasoning"``) are intentionally dropped rather
    than threaded through our internal message format: keeping that format
    provider-neutral (established for the Anthropic adapter) matters more than
    the modest efficiency Responses gains from replaying encrypted reasoning
    context turn to turn. The model still works without it — it just spends a
    few more tokens re-establishing context on the next call.
    """
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []

    for item in response.output or []:
        item_type = getattr(item, "type", None)
        if item_type == "message":
            text = _extract_message_text(item)
            if text:
                text_parts.append(text)
        elif item_type == "function_call":
            tool_calls.append(
                ToolCall(
                    # call_id — not id — is what a function_call_output must echo
                    # back, and what dev.py will carry forward as tool_call_id.
                    id=getattr(item, "call_id", "") or "",
                    function=FunctionCall(
                        name=getattr(item, "name", "") or "",
                        arguments=getattr(item, "arguments", "") or "{}",
                    ),
                )
            )
        # "reasoning" items: dropped, see docstring.

    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
    completion_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
    total_tokens = int(getattr(usage, "total_tokens", prompt_tokens + completion_tokens) or 0) if usage else 0
    # Automatic caching (no code needed to enable it — OpenAI caches any
    # repeated ≥1024-token prefix on its own) reports the hit here.
    details = getattr(usage, "input_tokens_details", None) if usage else None
    cached_tokens = int(getattr(details, "cached_tokens", 0) or 0) if details else 0

    return ChatCompletionResponse(
        model=getattr(response, "model", None),
        choices=[
            Choice(
                message=AssistantMessage(
                    content="".join(text_parts) or None,
                    tool_calls=tool_calls or None,
                ),
                finish_reason="tool_calls" if tool_calls else "stop",
            )
        ],
        usage=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
        ),
    )


class OpenAIProviderClient:
    def __init__(self, config: LLMConfig) -> None:
        self._client = AsyncOpenAI(
            api_key=config.api_key or "",
            # None keeps the SDK default (api.openai.com); a value points at the gateway.
            base_url=config.base_url or None,
            timeout=settings.CODE_AGENT_LLM_TIMEOUT,
            max_retries=settings.CODE_AGENT_LLM_MAX_RETRIES,
        )
        self._reasoning_effort = config.reasoning_effort
        self._reasoning_mode = config.reasoning_mode

    async def acompletion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        response_format: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if self._reasoning_effort or self._reasoning_mode:
            return await self._responses_acompletion(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                response_format=response_format,
                **kwargs,
            )

        params: dict[str, Any] = {"model": model, "messages": messages, **kwargs}
        if tools:
            params["tools"] = tools
            if tool_choice is not None:
                params["tool_choice"] = tool_choice
        if response_format is not None:
            params["response_format"] = response_format

        return await self._client.chat.completions.create(**params)

    async def _responses_acompletion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        response_format: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ChatCompletionResponse:
        reasoning: dict[str, Any] = {}
        if self._reasoning_effort:
            reasoning["effort"] = self._reasoning_effort
        if self._reasoning_mode:
            # Not yet in every installed SDK's type stubs, but the request
            # param is a plain dict — an extra key still serialises on the wire.
            reasoning["mode"] = self._reasoning_mode

        params: dict[str, Any] = {
            "model": model,
            "input": translate_messages_to_responses_input(messages),
            "store": False,
            **kwargs,
        }
        if reasoning:
            params["reasoning"] = reasoning

        translated_tools = translate_tools_to_responses(tools)
        if translated_tools:
            params["tools"] = translated_tools
            if tool_choice is not None:
                params["tool_choice"] = translate_tool_choice_to_responses(tool_choice)

        if response_format is not None:
            params["text"] = {"format": response_format}

        # Chat Completions names this max_tokens; Responses renamed it.
        if "max_tokens" in params:
            params["max_output_tokens"] = params.pop("max_tokens")

        response = await self._client.responses.create(**params)
        return to_chat_completion_response(response)
