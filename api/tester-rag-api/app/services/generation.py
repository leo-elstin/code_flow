import json
import re
from typing import Any

from app.services.llm_config import resolve_llm_config
from app.services.llm_providers import get_client
from app.services.run_activity import append_activity

# Config is resolved per call, not at import: the Settings page can change the
# provider or key while the server is running and the next call must pick it up.


def get_generation_model() -> str:
    return resolve_llm_config().chat_model or ""


def get_dev_model() -> str:
    """Model for the dev/codegen loop, falling back to the chat model."""
    return resolve_llm_config().resolved_dev_model or ""


async def acompletion(
    *,
    model: str,
    messages: list,
    **kwargs: Any,
) -> Any:
    """Provider-agnostic completion for tool-calling and plain-text callers.

    Always returns the OpenAI chat-completion shape, whichever provider served
    it. Request timeout and retries are configured on the provider client, so a
    stalled response can never hang a run indefinitely.
    """
    config = resolve_llm_config()
    return await get_client(config).acompletion(model=model, messages=messages, **kwargs)


def extract_cached_tokens(usage: Any) -> int:
    """Read cache-hit token count regardless of provider/path shape.

    Anthropic and the OpenAI Responses path are normalized into our own
    ``Usage`` model, which already has a flat ``cached_tokens``. OpenAI Chat
    Completions responses pass through as the raw SDK object, whose usage
    nests it at ``prompt_tokens_details.cached_tokens`` instead — this checks
    both shapes so callers don't need to know which provider served a call.
    """
    if usage is None:
        return 0
    flat = getattr(usage, "cached_tokens", None)
    if flat:
        return int(flat)
    details = getattr(usage, "prompt_tokens_details", None)
    return int(getattr(details, "cached_tokens", 0) or 0) if details else 0


def _parse_json_content(content: str) -> dict[str, Any]:
    """Parse a model's JSON reply, tolerating prose around the object.

    OpenAI's ``response_format`` guarantees a bare object; Anthropic's prefill
    approach is close but not enforced, so a stray trailing sentence should not
    fail a whole run.
    """
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


async def chat_completion_json(
    *,
    messages: list,
    run_id: str | None = None,
    phase: str = "system",
    label: str = "LLM call",
) -> tuple[dict[str, Any], dict[str, int]]:
    """Run a JSON-object chat completion and optionally record activity + tokens."""
    config = resolve_llm_config()
    model = config.chat_model or ""
    if run_id:
        append_activity(
            run_id,
            type="llm",
            phase=phase,  # type: ignore[arg-type]
            title=f"Starting {label}",
            meta={"model": model, "provider": config.provider, "state": "started"},
        )

    response = await get_client(config).acompletion(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        # A stable key nudges repeated calls in the same run toward the same
        # cache-holding backend shard. OpenAI-only — the Anthropic client
        # drops unrecognized kwargs, so this is harmless there.
        **({"prompt_cache_key": run_id} if run_id else {}),
    )

    content = response.choices[0].message.content or "{}"
    usage = response.usage
    usage_dict = {
        "prompt_tokens": int(usage.prompt_tokens or 0) if usage else 0,
        "completion_tokens": int(usage.completion_tokens or 0) if usage else 0,
        "total_tokens": int(usage.total_tokens or 0) if usage else 0,
        "cached_tokens": extract_cached_tokens(usage),
    }

    if run_id:
        append_activity(
            run_id,
            type="llm",
            phase=phase,  # type: ignore[arg-type]
            title=f"Completed {label}",
            meta={"model": model, "provider": config.provider, "state": "completed"},
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

    return _parse_json_content(content), usage_dict
