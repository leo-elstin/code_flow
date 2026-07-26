"""Provider clients, keyed by provider id.

Clients are cached per credential so we reuse connection pools across calls;
saving new config clears the cache so the next request picks it up without a
process restart.
"""

from __future__ import annotations

from app.services.llm_providers.anthropic_client import AnthropicProviderClient
from app.services.llm_providers.base import LLMConfig, ProviderClient
from app.services.llm_providers.openai_client import OpenAIProviderClient
from app.services.llm_settings_store import (
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    PROVIDER_OPENAI_GATEWAY,
)

_CLIENTS: dict[tuple, ProviderClient] = {}


def get_client(config: LLMConfig) -> ProviderClient:
    # reasoning_effort/reasoning_mode change which endpoint OpenAIProviderClient
    # calls (Responses vs Chat Completions), not just connection details — they
    # must be part of the cache key or a client built before they were set gets
    # reused and silently keeps calling the wrong endpoint.
    key = (
        config.provider,
        config.api_key,
        config.base_url,
        config.reasoning_effort,
        config.reasoning_mode,
    )
    client = _CLIENTS.get(key)
    if client is None:
        if config.provider == PROVIDER_ANTHROPIC:
            client = AnthropicProviderClient(config)
        elif config.provider in (PROVIDER_OPENAI, PROVIDER_OPENAI_GATEWAY):
            client = OpenAIProviderClient(config)
        else:
            raise ValueError(f"Unknown LLM provider: {config.provider!r}")
        _CLIENTS[key] = client
    return client


def clear_client_cache() -> None:
    """Drop cached clients — call after config changes."""
    _CLIENTS.clear()


__all__ = ["LLMConfig", "ProviderClient", "get_client", "clear_client_cache"]
