import json
from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.core.config import settings
from app.services.run_activity import append_activity

_client: AsyncOpenAI | None = None


def get_generation_client() -> AsyncOpenAI:
    """Return the OpenAI client used for chat completions."""
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def get_generation_model() -> str:
    return settings.OPENAI_CHAT_MODEL


async def chat_completion_json(
    *,
    messages: list[ChatCompletionMessageParam],
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

    client = get_generation_client()
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
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
