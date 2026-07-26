"""Shared types for LLM provider clients.

The OpenAI chat-completion shape is this codebase's internal wire format: the
dev and explorer loops read ``response.choices[0].message.tool_calls`` and push
``message.model_dump()`` straight back onto the conversation, and that
conversation is checkpointed to SQLite as ``dev_messages`` and replayed on
retry. Every provider client therefore returns that shape, whatever the
underlying API looks like.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class LLMConfig:
    """A fully-resolved LLM configuration — provider, credential, and models."""

    provider: str
    api_key: str | None = None
    base_url: str | None = None
    chat_model: str | None = None
    dev_model: str | None = None
    # OpenAI-family only. Unset (the default) keeps every call on Chat
    # Completions exactly as before. Setting either routes OpenAIProviderClient
    # through the Responses API instead — required for reasoning-effort models,
    # which reject function tools combined with reasoning_effort on
    # /v1/chat/completions.
    reasoning_effort: str | None = None  # none|minimal|low|medium|high|xhigh|max
    reasoning_mode: str | None = None  # standard|pro

    @property
    def resolved_dev_model(self) -> str | None:
        """The dev-loop model, falling back to the chat model.

        Mirrors the CODE_AGENT_DEV_MODEL default in config.py: an unset dev
        model must never silently degrade the codegen loop to a weaker model.
        """
        return self.dev_model or self.chat_model


# --- OpenAI-shaped response models -----------------------------------------
# Only non-OpenAI clients build these; the OpenAI client returns the SDK's own
# objects, which already have this shape.


class FunctionCall(BaseModel):
    name: str
    # JSON *string*, not a dict — callers do json.loads() on it.
    arguments: str


class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: FunctionCall


class AssistantMessage(BaseModel):
    role: str = "assistant"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None


class Choice(BaseModel):
    index: int = 0
    message: AssistantMessage
    finish_reason: str | None = None


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # Tokens served from a prompt cache instead of processed fresh — populated
    # from Anthropic's cache_read_input_tokens or OpenAI Responses'
    # input_tokens_details.cached_tokens. 0 doesn't necessarily mean caching is
    # off; it can just mean this particular call's prefix didn't hit the cache.
    cached_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    choices: list[Choice]
    usage: Usage | None = None
    model: str | None = None


class ProviderClient(Protocol):
    """What generation.py needs from any provider."""

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
        ...
