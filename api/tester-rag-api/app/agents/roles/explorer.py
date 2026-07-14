"""Agentic code explorer — locates the concrete files a plan needs before the
planner writes anything.

Instead of relying on a single one-shot grep pass, the explorer drives its own
retrieval: it searches, lists directories, and reads files in a tool-calling
loop (the way Cursor's agent does), following imports and references until it
has found the real screens/cubits/models/DI/routing files. Its findings are fed
to the planner so it can produce a file-accurate plan without stopping to ask
"which files?".
"""

import json
from typing import Any

from app.core.config import settings
from app.core.logging_config import get_logger
from app.services.generation import acompletion
from app.services.run_activity import append_activity
from app.tools.explore_tools import make_explore_tools

logger = get_logger("explorer")

EXPLORER_SYSTEM = """You are a code explorer for a Flutter codebase. Your job is to locate the exact
files the planner needs to implement a ticket — BEFORE any plan is written.

Work agentically: call tools to search, list directories, and read files. Start broad
(feature keywords, class/symbol names, file-name fragments), then follow imports and
references into the concrete implementation. Keep going until you have located the real
files that implement or must change for this feature: screens/pages, cubits/blocs/state,
models/DTOs, API requests, dependency-injection registration, and routing.

Do NOT guess paths — open files to confirm. Prefer citing files you actually read.

When you have enough, respond with JSON ONLY (and make no further tool calls):
{
  "relevant_files": [{"path": "lib/...", "role": "screen|cubit|state|model|request|di|routing|test|other", "why": "one line"}],
  "entry_points": ["lib/..."],
  "key_findings": ["short factual notes on how this area is structured"],
  "gaps": ["anything you genuinely could not find"]
}
Be concise. Only list files that exist and that you have evidence for."""


def _parse_json(text: str) -> dict[str, Any]:
    """Best-effort parse of the model's final JSON message."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1].lstrip("json").strip() if "```" in text[3:] else text.strip("`")
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {}


async def run_explorer(
    user_request: str,
    project_path: str,
    *,
    run_id: str | None = None,
    seed_terms: list[str] | None = None,
    seed_files: list[str] | None = None,
    max_steps: int | None = None,
) -> dict[str, Any]:
    """Drive a read-only tool loop to locate the files relevant to *user_request*.

    Returns the findings dict (``relevant_files`` etc.); an empty dict on any
    failure, so the planner falls back to plain discovery rather than erroring."""
    import asyncio

    max_steps = max_steps or settings.CODE_AGENT_EXPLORER_MAX_STEPS
    functions, schemas = make_explore_tools(project_path)

    seed = ""
    if seed_terms:
        seed += f"\nSearch terms from the ticket: {', '.join(seed_terms[:20])}"
    if seed_files:
        seed += f"\nFiles grep already surfaced (starting points, not exhaustive):\n" + "\n".join(
            f"- {f}" for f in seed_files[:20]
        )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": EXPLORER_SYSTEM},
        {"role": "user", "content": f"Ticket:\n{user_request}\n{seed}\n\nExplore the codebase and report the relevant files."},
    ]

    if run_id:
        append_activity(
            run_id, type="status", phase="planner", title="Agentic exploration started",
        )

    findings: dict[str, Any] = {}
    step = 0
    while step < max_steps:
        step += 1
        try:
            response = await acompletion(model=settings.OPENAI_CHAT_MODEL, messages=messages,
                                         tools=schemas, tool_choice="auto")
        except Exception as exc:  # noqa: BLE001 — bounded by acompletion timeout
            logger.warning("Explorer LLM call failed run_id=%s step=%d: %s", run_id, step, exc)
            break

        msg = response.choices[0].message
        messages.append(msg.model_dump(exclude_unset=False))

        if not msg.tool_calls:
            findings = _parse_json(msg.content or "")
            break

        for tc in msg.tool_calls:
            fn = functions.get(tc.function.name)
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            try:
                result = (
                    await asyncio.to_thread(fn, **args)
                    if fn
                    else f"Unknown tool: {tc.function.name}"
                )
            except Exception as exc:  # noqa: BLE001 — a bad tool call must not kill the loop
                result = f"Tool {tc.function.name} error: {exc}"
            if run_id:
                append_activity(
                    run_id, type="tool", phase="planner",
                    title=f"Explore: {tc.function.name}",
                    detail=(json.dumps(args, default=str)[:200] + " -> " + str(result)[:400]),
                    meta={"tool": tc.function.name},
                )
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})

    if not findings:
        # The model explored to the step budget without wrapping up. Force a final
        # answer from what it has already read — no more tools.
        messages.append({
            "role": "user",
            "content": (
                "Stop exploring now. Based only on the files you have already read, respond "
                "with the findings JSON described earlier — no more tool calls."
            ),
        })
        try:
            response = await acompletion(model=settings.OPENAI_CHAT_MODEL, messages=messages)
            findings = _parse_json(response.choices[0].message.content or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Explorer finalization failed run_id=%s: %s", run_id, exc)

    files = findings.get("relevant_files") or []
    if run_id:
        append_activity(
            run_id, type="status", phase="planner",
            title=f"Agentic exploration found {len(files)} relevant file(s)",
            detail="; ".join(f.get("path", "") for f in files[:10] if isinstance(f, dict)),
            files=[f.get("path") for f in files if isinstance(f, dict) and f.get("path")][:20],
        )
    logger.info("Explorer done run_id=%s steps=%d relevant_files=%d", run_id, step, len(files))
    return findings
