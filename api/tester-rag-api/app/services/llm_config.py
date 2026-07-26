"""Resolves which LLM configuration a call should use.

Precedence, highest first:

1. The active project's override (set per run via :func:`set_active_project`)
2. The saved global config
3. ``.env`` via :pymod:`app.core.config` — unchanged fallback, so a checkout
   that has never opened the Settings page behaves exactly as it did before.

The active project travels in a ContextVar rather than a parameter: the value
is copied into ``asyncio.create_task`` and ``asyncio.to_thread``, so setting it
once at the run entrypoint reaches every agent role without threading an
argument through ten call sites.
"""

from __future__ import annotations

from contextvars import ContextVar

from app.core.config import settings
from app.core.logging_config import get_logger
from app.services.llm_providers.base import LLMConfig
from app.services.llm_settings_store import (
    PROVIDER_OPENAI,
    PROVIDER_OPENAI_GATEWAY,
    get_global_config,
    get_project_override,
)
from app.services.project_ticket_store import get_project_by_path

logger = get_logger("llm_config")

_active_project_path: ContextVar[str | None] = ContextVar("active_project_path", default=None)


def set_active_project(project_path: str | None) -> None:
    """Bind a project to the current async context for override resolution."""
    _active_project_path.set(project_path)


def get_active_project() -> str | None:
    return _active_project_path.get()


def env_config() -> LLMConfig:
    """The pre-migration configuration, read from ``.env``.

    ``LITELLM_API_BASE`` being set is what used to mean "route OpenAI through a
    gateway", so it maps to the gateway provider.
    """
    base_url = settings.LITELLM_API_BASE
    return LLMConfig(
        provider=PROVIDER_OPENAI_GATEWAY if base_url else PROVIDER_OPENAI,
        api_key=settings.OPENAI_API_KEY,
        base_url=base_url,
        chat_model=settings.OPENAI_CHAT_MODEL,
        dev_model=settings.CODE_AGENT_DEV_MODEL,
    )


def _to_config(raw: dict) -> LLMConfig:
    return LLMConfig(
        provider=raw["provider"],
        api_key=raw.get("api_key"),
        base_url=raw.get("base_url"),
        chat_model=raw.get("chat_model"),
        dev_model=raw.get("dev_model"),
        reasoning_effort=raw.get("reasoning_effort"),
        reasoning_mode=raw.get("reasoning_mode"),
    )


def _merge(base: LLMConfig, override: LLMConfig) -> LLMConfig:
    """Layer an override onto a base config.

    Blank override fields inherit from the base only when both sides name the
    same provider — inheriting an OpenAI key into an Anthropic override would
    just produce a confusing auth failure.
    """
    if override.provider != base.provider:
        return override
    return LLMConfig(
        provider=override.provider,
        api_key=override.api_key or base.api_key,
        base_url=override.base_url or base.base_url,
        chat_model=override.chat_model or base.chat_model,
        dev_model=override.dev_model or base.dev_model,
        reasoning_effort=override.reasoning_effort or base.reasoning_effort,
        reasoning_mode=override.reasoning_mode or base.reasoning_mode,
    )


def global_config() -> LLMConfig:
    """The saved global config, or the ``.env`` config when none is saved."""
    saved = get_global_config()
    if not saved:
        return env_config()
    return _merge(env_config(), _to_config(saved))


def resolve_llm_config() -> LLMConfig:
    """The config for the current call, honouring any active project override."""
    base = global_config()

    project_path = _active_project_path.get()
    if not project_path:
        return base

    try:
        project = get_project_by_path(project_path)
        if not project:
            return base
        raw_override = get_project_override(int(project["id"]))
        if not raw_override:
            return base
        return _merge(base, _to_config(raw_override))
    except Exception:  # noqa: BLE001 — an override lookup must never fail a run
        logger.warning(
            "LLM override lookup failed for project_path=%s; using global config",
            project_path,
            exc_info=True,
        )
        return base


__all__ = [
    "LLMConfig",
    "PROVIDER_OPENAI",
    "env_config",
    "get_active_project",
    "global_config",
    "resolve_llm_config",
    "set_active_project",
]
