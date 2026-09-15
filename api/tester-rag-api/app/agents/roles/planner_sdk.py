"""SDK-driven planner.

Drives the Claude Agent SDK instead of the legacy run_planner's two-stage
design (a hand-rolled explorer tool loop, then a single structured chat
completion). Both stages collapse into one SDK session here: the agent
explores the target project live via the SDK's own built-in read-only tools
(Read/Grep/Glob — see the tool-surface note below) and produces the plan in
the same turn, schema-validated by the SDK itself via `output_format` rather
than the legacy path's prose contract + tolerant JSON parser.

Tool surface: unlike app.agents.roles.dev_sdk, this grants the SDK's OWN
built-in Read/Grep/Glob rather than wrapping custom MCP tools. That's a
deliberate, narrower risk call than it looks: app.tools.explore_tools (the
legacy explorer's tool set) is itself just ripgrep search + read + list-dir —
capability-equivalent to the built-ins, so wrapping it would add an MCP layer
for no behavioral gain. Planning is also read-only by nature (no write/edit),
so the higher-risk case that justified path_guard-enforced custom tools for
the dev loop — an agent modifying files outside its plan's declared scope —
doesn't apply here. Write/Edit/Bash/NotebookEdit are still explicitly denied
via both allowed_tools/disallowed_tools and a can_use_tool callback, the same
defense-in-depth shape as dev_sdk.py, because a planning session has no
business changing anything on disk regardless.

Output contract: `output_format={"type": "json_schema", "schema": ...}` on
ClaudeAgentOptions, confirmed against the installed claude-agent-sdk source
(app.agents.roles.planner_sdk._PLAN_JSON_SCHEMA below) — the CLI validates
the final turn against it and the API surfaces the parsed result directly on
ResultMessage.structured_output (no manual JSON extraction needed, unlike the
legacy path's chat_completion_json). There's no separate "questions mode"
schema: `questions` is just one more (optional) array in the same schema,
matching how PLANNER_SYSTEM already describes a single JSON shape where
`questions` is populated only when genuinely ambiguous — the schema doesn't
need a oneOf/union for that, it only needs the field to be present.

No turn/budget soft-stop-and-resume mechanism (contrast dev_sdk.py): unlike
the dev↔verifier retry loop, nothing in the graph retries a stalled planner
call, so there is no session to usefully resume. A turn/budget cap that ends
without a structured_output is therefore a hard failure here — not a
recoverable soft stop — because there is no partial artifact (no files on
disk) to hand back the way a truncated dev run still has real diffs.
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

from app.agents.roles.planner import PLANNER_SYSTEM, _format_prior_work_block
from app.agents.sdk_common import (
    as_streaming_prompt,
    is_transient_sdk_error,
    log_sdk_auth_mode,
    resolve_sdk_env,
    retry_backoff_seconds,
    sdk_supports_option,
)
from app.core.config import settings
from app.core.logging_config import get_logger
from app.services.feature_discovery import discover_context
from app.services.prior_work_discovery import discover_prior_work, select_base_ref
from app.services.run_activity import append_activity

logger = get_logger("planner_sdk")

# Read-only surface only. No custom MCP server (see module docstring) — these
# are the SDK's own built-ins, scoped to `cwd` on ClaudeAgentOptions.
_ALLOWED_TOOLS = ["Read", "Grep", "Glob"]
_DENIED_BUILTIN_TOOLS = [
    "Write", "Edit", "Bash", "NotebookEdit", "Task", "WebSearch", "WebFetch",
]

# Mirrors PLANNER_SYSTEM's documented field list. additionalProperties:false
# on every object per the documented structured-outputs constraint; fields
# genuinely optional in the current prose contract (reasoning, architecture)
# are left out of `required` rather than forced, since that constraint is
# about object shape, not about every property being mandatory.
_FILE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "purpose": {"type": "string"},
    },
    "required": ["path"],
    "additionalProperties": False,
}

_PLAN_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "feature_summary": {"type": "string"},
        "reasoning": {"type": "string"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "context": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "label": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["id", "label"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "question", "options"],
                "additionalProperties": False,
            },
        },
        "files_to_create": {"type": "array", "items": _FILE_ITEM_SCHEMA},
        "files_to_modify": {"type": "array", "items": _FILE_ITEM_SCHEMA},
        "context_files": {"type": "array", "items": {"type": "string"}},
        "architecture": {
            "type": "object",
            "properties": {
                "layers": {"type": "string"},
                "state_management": {"type": "string"},
                "routing": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        "discovery_evidence": {"type": "array", "items": {"type": "string"}},
        "plan_markdown": {"type": "string"},
    },
    "required": [
        "feature_summary",
        "questions",
        "files_to_create",
        "files_to_modify",
        "context_files",
        "acceptance_criteria",
        "discovery_evidence",
        "plan_markdown",
    ],
    "additionalProperties": False,
}


class PlannerSdkError(RuntimeError):
    """A hard failure from the Claude Agent SDK planner run — missing/invalid
    auth, a provider rejection, a crashed subprocess, or a turn/budget cap
    reached with no structured_output produced. Always hard: there is no
    partial-plan artifact worth proceeding with, unlike a truncated dev run."""


async def _planner_tool_permission(tool_name: str, _input_data: dict, _context: Any) -> Any:
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    if tool_name in _ALLOWED_TOOLS:
        return PermissionResultAllow()
    return PermissionResultDeny(
        message="Planning is read-only — only Read/Grep/Glob are permitted.",
        interrupt=False,
    )


def _build_prompt_text(
    *,
    user_request: str,
    project_path: str,
    context_bundle: dict[str, Any],
    linked_issues_context: str | None,
    acceptance_criteria_hint: list[str] | None,
    prior_work: dict[str, Any],
    clarification_answers: list[dict[str, Any]] | None,
) -> str:
    guide = context_bundle.get("project_guide") or {}
    text = f"User request:\n{user_request}\n\nProject path: {project_path}\n\n"

    if guide.get("found") and guide.get("content"):
        text += (
            "=== PROJECT ARCHITECTURE GUIDE (AGENTS.md) — authoritative reference ===\n"
            "Read this before making any decisions about folder layout, naming, state management, "
            "dependency injection, routing, or which services/packages to use. "
            "All plan decisions MUST conform to the conventions described here.\n\n"
            + guide["content"]
            + "\n=== END AGENTS.md ===\n\n"
        )

    if linked_issues_context:
        text += f"Linked Jira issues:\n{linked_issues_context}\n\n"
    if acceptance_criteria_hint:
        text += "Jira acceptance criteria (use as starting point):\n"
        text += "\n".join(f"- {ac}" for ac in acceptance_criteria_hint)
        text += "\n\n"

    manifest_summaries = context_bundle.get("manifest_summaries") or []
    if manifest_summaries:
        text += (
            "Declared dependencies (do not propose anything not listed here — see "
            "the dependency-hygiene rules above):\n"
            + json.dumps(manifest_summaries, indent=2)
            + "\n\n"
        )
    search_terms = context_bundle.get("search_terms") or []
    if search_terms:
        text += (
            "Starting search terms for your own exploration (not exhaustive — use "
            "Grep/Glob/Read to actually locate the files this ticket needs):\n"
            + ", ".join(search_terms)
            + "\n\n"
        )

    text += _format_prior_work_block(prior_work)

    if clarification_answers:
        text += "Clarification answers from the developer (incorporate these into the plan, do NOT emit questions):\n"
        for ans in clarification_answers:
            q = ans.get("question", "")
            label = ans.get("option_label", "")
            desc = ans.get("option_description", "")
            text += f"- Q: {q}\n  A: {label}"
            if desc:
                text += f" — {desc}"
            text += "\n"
        text += "\nExplore the codebase yourself using Read/Grep/Glob, then produce a full implementation plan JSON (no questions field needed).\n"
    else:
        text += "Explore the codebase yourself using Read/Grep/Glob, then produce an implementation plan JSON.\n"

    return text


def _build_options(*, project_path: str) -> Any:
    from claude_agent_sdk import ClaudeAgentOptions

    option_kwargs: dict[str, Any] = dict(
        cwd=project_path,
        system_prompt=PLANNER_SYSTEM,
        allowed_tools=list(_ALLOWED_TOOLS),
        disallowed_tools=list(_DENIED_BUILTIN_TOOLS),
        can_use_tool=_planner_tool_permission,
        model=settings.CODE_AGENT_PLANNER_SDK_MODEL,
        output_format={"type": "json_schema", "schema": _PLAN_JSON_SCHEMA},
        setting_sources=[],
    )
    # Omit max_turns entirely when unbounded (0) — the SDK treats the field's
    # own None default as "no limit", so passing nothing lets Claude Code run
    # its exploration loop to completion and report back when the plan is done.
    if settings.CODE_AGENT_PLANNER_SDK_MAX_TURNS > 0:
        option_kwargs["max_turns"] = settings.CODE_AGENT_PLANNER_SDK_MAX_TURNS
    if settings.CODE_AGENT_PLANNER_SDK_MAX_BUDGET_USD > 0:
        option_kwargs["max_budget_usd"] = settings.CODE_AGENT_PLANNER_SDK_MAX_BUDGET_USD
    env = resolve_sdk_env()
    if env:
        option_kwargs["env"] = env
    if sdk_supports_option("thinking"):
        option_kwargs["thinking"] = {"type": "adaptive"}
    if sdk_supports_option("effort") and settings.CODE_AGENT_PLANNER_SDK_EFFORT:
        option_kwargs["effort"] = settings.CODE_AGENT_PLANNER_SDK_EFFORT

    return ClaudeAgentOptions(**option_kwargs)


def _image_content_block(file_path: str) -> dict[str, Any] | None:
    """Anthropic-shaped image content block for a local attachment, or None
    if the file is missing/unreadable. Mirrors planner._encode_image_base64's
    format detection but emits the raw block instead of a data: URI, since the
    SDK's multimodal streaming-prompt path expects Messages API content
    blocks, not the OpenAI image_url shape the legacy path uses."""
    path = Path(file_path)
    if not path.exists():
        return None
    suffix = path.suffix.lower()
    mime_map = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp",
    }
    mime = mime_map.get(suffix, "image/png")
    data = base64.b64encode(path.read_bytes()).decode()
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": mime, "data": data},
    }


async def run_planner_sdk(
    *,
    user_request: str,
    project_path: str,
    run_id: str | None = None,
    ticket_id: int | None = None,
    attachment_paths: list[str] | None = None,
    linked_issues_context: str | None = None,
    acceptance_criteria_hint: list[str] | None = None,
    clarification_answers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="planner",
            title="Planning started (Claude Agent SDK)",
            detail=user_request[:200],
        )

    log_sdk_auth_mode(logger, "Planner(SDK)")

    context_bundle = discover_context(user_request, project_path, run_id=run_id)

    prior_work: dict[str, Any] = {"found": False, "candidates": []}
    if settings.CODE_AGENT_PRIOR_WORK_DISCOVERY:
        try:
            prior_work = discover_prior_work(ticket_id, project_path, current_run_id=run_id)
        except Exception:  # noqa: BLE001 — discovery must never block planning
            logger.warning("Prior-work discovery raised unexpectedly", exc_info=True)
    selected = select_base_ref(prior_work)
    prior_work["selected_branch"] = selected["branch"] if selected else None
    context_bundle["prior_work"] = prior_work
    if run_id and prior_work.get("found"):
        append_activity(
            run_id,
            type="status",
            phase="planner",
            title=f"Found {len(prior_work['candidates'])} prior attempt(s) for this ticket",
            detail=", ".join(c["branch"] for c in prior_work["candidates"]),
        )

    text = _build_prompt_text(
        user_request=user_request,
        project_path=project_path,
        context_bundle=context_bundle,
        linked_issues_context=linked_issues_context,
        acceptance_criteria_hint=acceptance_criteria_hint,
        prior_work=prior_work,
        clarification_answers=clarification_answers,
    )

    image_blocks = []
    if attachment_paths:
        for img_path in attachment_paths:
            block = _image_content_block(img_path)
            if block:
                image_blocks.append(block)

    options = _build_options(project_path=project_path)

    from claude_agent_sdk import AssistantMessage, ResultMessage, query

    structured_output: dict[str, Any] | None = None
    terminal_reason: str | None = None
    is_error = False
    # ResultMessage.result carries the CLI's own human-readable failure text
    # (e.g. "Not logged in · Please run /login"). Captured so it can be
    # surfaced in the raised error and the activity log — without it a real,
    # actionable cause is reduced to an opaque terminal_reason=api_error.
    result_text: str | None = None

    # can_use_tool (set in _build_options) requires streaming input mode —
    # a plain string prompt raises ValueError. Always go through
    # as_streaming_prompt, image attachments or not, rather than branching
    # on a "no images" fast path that would carry the same string-prompt bug.
    content: str | list[dict[str, Any]] = (
        [{"type": "text", "text": text}, *image_blocks] if image_blocks else text
    )
    prompt = as_streaming_prompt(content)

    # Retry loop for TRANSIENT failures only — a dropped stream mid-response,
    # a provider overload, a rate limit. Planning is read-only and produces no
    # partial artifact, so a fresh attempt is always safe and never duplicates
    # work; the only cost is the exploration tokens spent before the drop.
    # Anything not recognized as transient (bad auth, a malformed request)
    # breaks out immediately rather than burning attempts on an error a retry
    # cannot fix.
    max_attempts = max(1, settings.CODE_AGENT_PLANNER_SDK_MAX_RETRIES + 1)
    last_detail: str | None = None

    for attempt in range(1, max_attempts + 1):
        structured_output = None
        terminal_reason = None
        is_error = False
        result_text = None
        # The prompt is a one-shot async generator — it is consumed by the
        # first attempt, so each retry needs a fresh one.
        prompt = as_streaming_prompt(content)

        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    blocks = getattr(message, "content", None) or []
                    for block in blocks:
                        if getattr(block, "type", None) == "text":
                            block_text = (getattr(block, "text", "") or "").strip()
                            if block_text and run_id:
                                append_activity(
                                    run_id,
                                    type="llm",
                                    phase="planner",
                                    title="Planner reasoning",
                                    detail=block_text[:4000],
                                )
                elif isinstance(message, ResultMessage):
                    structured_output = getattr(message, "structured_output", None)
                    terminal_reason = getattr(message, "terminal_reason", None)
                    is_error = bool(getattr(message, "is_error", False))
                    raw_result = getattr(message, "result", None)
                    result_text = raw_result if isinstance(raw_result, str) else None
                    logger.info(
                        "Planner(SDK) result run_id=%s attempt=%d/%d terminal_reason=%s "
                        "is_error=%s has_plan=%s result=%s",
                        run_id, attempt, max_attempts, terminal_reason, is_error,
                        structured_output is not None, (result_text or "")[:300],
                    )
        except Exception as exc:  # SDK/subprocess/provider failure
            # Prefer the CLI's own message over the SDK wrapper's generic text —
            # "Claude Code returned an error result: success" says nothing, while
            # result_text names the actual cause.
            last_detail = result_text or str(exc)
            logger.warning(
                "Planner(SDK) attempt %d/%d failed run_id=%s: %s",
                attempt, max_attempts, run_id, last_detail,
            )
            if attempt >= max_attempts or not is_transient_sdk_error(last_detail):
                logger.exception("Planner(SDK) provider error run_id=%s", run_id)
                raise PlannerSdkError(
                    f"Claude Agent SDK planner run failed: {last_detail}"
                ) from exc
        else:
            if isinstance(structured_output, dict):
                break  # got a plan
            last_detail = result_text or "no error detail reported"
            logger.warning(
                "Planner(SDK) attempt %d/%d produced no plan run_id=%s: %s",
                attempt, max_attempts, run_id, last_detail,
            )
            if attempt >= max_attempts or not is_transient_sdk_error(last_detail):
                raise PlannerSdkError(
                    "Claude Agent SDK planner run ended without a valid structured plan: "
                    f"{last_detail} (terminal_reason={terminal_reason!r}, is_error={is_error})."
                )

        delay = retry_backoff_seconds(attempt)
        if run_id:
            append_activity(
                run_id,
                type="status",
                phase="planner",
                title=f"Transient error — retrying in {delay:.0f}s "
                f"(attempt {attempt + 1}/{max_attempts})",
                detail=(last_detail or "")[:1000],
            )
        await asyncio.sleep(delay)

    if not isinstance(structured_output, dict):  # pragma: no cover — loop raises first
        raise PlannerSdkError(
            f"Claude Agent SDK planner run failed after {max_attempts} attempt(s): "
            f"{last_detail or 'no error detail reported'}"
        )

    plan = structured_output

    reasoning = plan.get("reasoning")
    if run_id and reasoning:
        append_activity(
            run_id,
            type="thinking",
            phase="planner",
            title="Planner reasoning",
            detail=str(reasoning)[:4000],
        )

    questions = plan.get("questions") or []
    if not isinstance(questions, list):
        questions = []

    acceptance = plan.get("acceptance_criteria") or []
    if isinstance(acceptance, str):
        acceptance = [acceptance]

    if run_id:
        if questions:
            append_activity(
                run_id,
                type="status",
                phase="planner",
                title="Planner has clarification questions",
                detail=f"{len(questions)} question(s) before finalising plan",
            )
        else:
            append_activity(
                run_id,
                type="status",
                phase="planner",
                title="Plan ready for approval",
                detail=plan.get("feature_summary", "Plan created."),
            )

    return {
        "context_bundle": context_bundle,
        "plan": plan,
        "questions": questions,
        "acceptance_criteria": acceptance,
        "messages": [
            {"role": "planner", "content": plan.get("feature_summary", "Plan created.")}
        ],
    }
