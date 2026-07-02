import json
from typing import Any

import litellm

from app.core.config import settings
from app.services.run_activity import append_activity

# Configure litellm once at import time
litellm.api_key = settings.OPENAI_API_KEY
if settings.LITELLM_API_BASE:
    litellm.api_base = settings.LITELLM_API_BASE
litellm.set_verbose = settings.LITELLM_VERBOSE


def get_generation_model() -> str:
    return settings.OPENAI_CHAT_MODEL


async def acompletion(
    *,
    model: str,
    messages: list,
    **kwargs: Any,
) -> Any:
    """Thin async wrapper around litellm.acompletion for tool-calling and streaming callers.

    Applies a default request timeout/retry so a stalled provider response can
    never hang a run indefinitely; explicit caller values win."""
    kwargs.setdefault("timeout", settings.CODE_AGENT_LLM_TIMEOUT)
    kwargs.setdefault("num_retries", settings.CODE_AGENT_LLM_MAX_RETRIES)
    return await litellm.acompletion(model=model, messages=messages, **kwargs)


async def chat_completion_json(
    *,
    messages: list,
    run_id: str | None = None,
    phase: str = "system",
    label: str = "LLM call",
) -> tuple[dict[str, Any], dict[str, int]]:
    """Run a JSON-object chat completion and optionally record activity + tokens."""
    model = get_generation_model()
    if run_id:
        append_activity(
            run_id,
            type="llm",
            phase=phase,  # type: ignore[arg-type]
            title=f"Starting {label}",
            meta={"model": model, "state": "started"},
        )

    response = await litellm.acompletion(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        timeout=settings.CODE_AGENT_LLM_TIMEOUT,
        num_retries=settings.CODE_AGENT_LLM_MAX_RETRIES,
    )

    content = response.choices[0].message.content or "{}"
    usage = response.usage
    usage_dict = {
        "prompt_tokens": int(usage.prompt_tokens or 0) if usage else 0,
        "completion_tokens": int(usage.completion_tokens or 0) if usage else 0,
        "total_tokens": int(usage.total_tokens or 0) if usage else 0,
    }

    if run_id:
        append_activity(
            run_id,
            type="llm",
            phase=phase,  # type: ignore[arg-type]
            title=f"Completed {label}",
            meta={"model": model, "state": "completed"},
        )
        if usage_dict["total_tokens"] > 0:
            append_activity(
                run_id,
                type="token",
                phase=phase,  # type: ignore[arg-type]
                title=f"Token usage ({label})",
                detail=f"{usage_dict['total_tokens']} tokens",
                meta=usage_dict,
            )

    return json.loads(content), usage_dict
