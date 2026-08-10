"""SDK-driven dev loop.

Drives the Claude Agent SDK instead of hand-rolling the tool-calling
conversation in app.agents.roles.dev.run_dev. The SDK owns context management,
compaction, and the turn loop; this module adapts its message stream into the
same activity-log and return-value contract run_dev already produces (plus one
new key, dev_sdk_session_id), so app.orchestration.graph.dev_node needs no
changes to consume either path — only app.agents.roles.dev.run_dev's own
dispatch at the top decides which one runs.

Tool surface: app.mcp.dev_tools_server wraps the same path_guard-enforced
functions the legacy loop calls, as in-process MCP tools. Those are the ONLY
tools granted — every SDK built-in (Read/Write/Edit/Bash/Glob/Grep/...) is
explicitly denied, both via allowed_tools/disallowed_tools and via a
can_use_tool permission callback that hard-denies anything not in this run's
own MCP namespace. Two independent gates on the same boundary, deliberately
redundant — the same shape as saga's tool_policy.py: a denylist that only
ever narrows, never widens, what a tool grant permits.

Soft stop vs hard failure: a turn/budget cap ends the SDK run with
terminal_reason == "max_turns" (or the budget equivalent) — real work
happened, so the caller should still proceed to verification, and the next
retry resumes this exact session (`resume=`) instead of restarting cold and
losing all prior context. Anything else that ends in error — a crashed
subprocess, an auth/rate-limit rejection — is raised as DevSdkProviderError
and must never be silently treated as a normal, if truncated, completion.
This mirrors the distinction saga's soft_stop.py draws for its own SDK-driven
agents, and the same distinction that's missing from the legacy loop's flat
`max_steps` cap (which has no soft-stop/resume path at all).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from app.agents.roles.dev import (
    DEV_SYSTEM_LOOP,
    _build_iteration_brief,
    _collect_required_fixes,
    _finalize_dev_changes,
    _format_skills_block,
    _plan_path_set,
)
from app.agents.sdk_common import (
    as_streaming_prompt,
    log_sdk_auth_mode,
    resolve_sdk_env,
    sdk_supports_option,
)
from app.core.config import settings
from app.core.logging_config import get_logger
from app.mcp.dev_tools_server import MCP_SERVER_NAME, build_dev_mcp_server, mcp_tool_names
from app.services.run_activity import append_activity
from app.tools.worktree_sync import sync_planned_files_from_project

logger = get_logger("dev_sdk")

DevSdkOutcome = Literal["success", "soft_stop", "hard_failure"]

# terminal_reason values the SDK reports for a turn or budget cap. Treated as
# recoverable: the run proceeds to verification with truncated=True, exactly
# like the legacy loop's step-limit path, but — unlike the legacy loop —
# carries a session_id forward so the next retry resumes instead of
# rebuilding the conversation from scratch.
_SOFT_STOP_REASONS = frozenset(
    {"max_turns", "error_max_turns", "max_budget_usd", "error_max_budget_usd"}
)

# Every SDK built-in tool this dev loop must never use — file/shell access
# goes exclusively through the plan-scoped, path_guard-enforced MCP tools.
_DENIED_BUILTIN_TOOLS = [
    "Read", "Write", "Edit", "Bash", "Glob", "Grep",
    "WebSearch", "WebFetch", "NotebookEdit", "Task",
]


class DevSdkProviderError(RuntimeError):
    """A hard failure from the Claude Agent SDK run — missing/invalid auth, a
    provider rejection, a crashed subprocess, or a terminal_reason that isn't
    a recognized soft stop. Never raised for a turn/budget cap."""


def classify_dev_sdk_result(terminal_reason: str | None, is_error: bool) -> DevSdkOutcome:
    """Classify how one Claude Agent SDK dev run ended.

    Pure and SDK-independent so it's testable without the package installed:
    takes the two fields off ResultMessage the classification actually needs,
    not the message object itself.
    """
    if terminal_reason in _SOFT_STOP_REASONS:
        return "soft_stop"
    if is_error:
        return "hard_failure"
    return "success"


async def _dev_tool_permission(tool_name: str, _input_data: dict, _context: Any) -> Any:
    """Hard gate: only this run's own devtools MCP tools may execute — belt
    and suspenders alongside allowed_tools/disallowed_tools, independent of
    how the SDK resolves precedence between those lists."""
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    if tool_name.startswith(f"mcp__{MCP_SERVER_NAME}__"):
        return PermissionResultAllow()
    return PermissionResultDeny(
        message="Only the devtools MCP tools are permitted in the SDK dev loop.",
        interrupt=False,
    )


def _walk_blocks(message: Any):
    """Yield (block_type, block) for any block-carrying SDK message.

    Walks generically off `.content` rather than isinstance-checking specific
    wrapper classes for text/tool_use/tool_result/thinking, so it degrades to
    a no-op on an unrecognized message shape instead of raising."""
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type:
            yield block_type, block


def _log_usage(run_id: str | None, step: int, usage: Any) -> None:
    if not run_id or usage is None:
        return
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    total = input_tokens + output_tokens
    if total <= 0:
        return
    append_activity(
        run_id,
        type="token",
        phase="dev",
        title=f"Token usage (dev step {step})",
        detail=f"{total} tokens",
        meta={
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": total,
            "cost_usd": getattr(usage, "total_cost_usd", None),
        },
    )


def _build_prompt(
    *,
    continuing: bool,
    plan: dict[str, Any],
    context_bundle: dict[str, Any],
    brief: str,
) -> str:
    if continuing:
        return brief
    guide = context_bundle.get("project_guide") or {}
    project_guide_text = (
        f"Project guide ({guide.get('path', 'AGENTS.md')}):\n{guide.get('content', '')}"
        if guide.get("found") else ""
    )
    project_context_text = (
        f"Project context:\n{context_bundle.get('project_context', '')}\n\n"
        if context_bundle.get("project_context") else ""
    )
    dev_skills_text = _format_skills_block(context_bundle.get("dev_skills"))
    prior_work = context_bundle.get("prior_work") or {}
    prior_work_text = (
        "Prior implementation attempts exist for this ticket — see the plan's discovery "
        "notes for what's already covered. Read the relevant existing files before writing "
        "new ones to avoid duplicating completed work.\n\n"
        if prior_work.get("found") else ""
    )
    dev_plan = {k: v for k, v in plan.items() if k != "plan_markdown"}
    return (
        f"Approved plan:\n{json.dumps(dev_plan, indent=2)}\n\n"
        + (f"{project_guide_text}\n\n" if project_guide_text else "")
        + project_context_text
        + dev_skills_text
        + prior_work_text
        + brief
    )


def _build_options(
    *,
    worktree_path: str,
    mcp_server: Any,
    prior_session_id: str | None,
) -> Any:
    from claude_agent_sdk import ClaudeAgentOptions

    option_kwargs: dict[str, Any] = dict(
        cwd=worktree_path,
        system_prompt=DEV_SYSTEM_LOOP,
        mcp_servers={MCP_SERVER_NAME: mcp_server},
        allowed_tools=mcp_tool_names(),
        disallowed_tools=list(_DENIED_BUILTIN_TOOLS),
        can_use_tool=_dev_tool_permission,
        permission_mode="acceptEdits",
        model=settings.CODE_AGENT_DEV_SDK_MODEL,
        # No filesystem-loaded settings/skills/commands from this machine's
        # ~/.claude or the target project's .claude/ — this run's behavior
        # must come entirely from what this module configures, not from
        # whatever happens to be on the operator's local machine.
        setting_sources=[],
    )
    # 0 = unbounded (omit the field). Unlike the planner, a cap here is
    # defensible rather than destructive: hitting it is a recoverable soft
    # stop — the code written so far is on disk and the next dev iteration
    # resumes this same session — so the default keeps a real ceiling.
    if settings.CODE_AGENT_DEV_SDK_MAX_TURNS > 0:
        option_kwargs["max_turns"] = settings.CODE_AGENT_DEV_SDK_MAX_TURNS
    # See sdk_common.resolve_sdk_env — empty when unconfigured, on purpose,
    # so the Claude Code CLI login fallback isn't shadowed by an explicit
    # present-but-empty ANTHROPIC_API_KEY.
    env = resolve_sdk_env()
    if env:
        option_kwargs["env"] = env
    if prior_session_id:
        option_kwargs["resume"] = prior_session_id
    # Pinned explicitly even where it's already the field's own default, so a
    # future SDK version changing that default can't silently change this
    # loop's behavior underneath it.
    if sdk_supports_option("thinking"):
        option_kwargs["thinking"] = {"type": "adaptive"}
    if sdk_supports_option("effort") and settings.CODE_AGENT_DEV_SDK_EFFORT:
        option_kwargs["effort"] = settings.CODE_AGENT_DEV_SDK_EFFORT
    if settings.CODE_AGENT_DEV_SDK_MAX_BUDGET_USD > 0:
        option_kwargs["max_budget_usd"] = settings.CODE_AGENT_DEV_SDK_MAX_BUDGET_USD

    return ClaudeAgentOptions(**option_kwargs)


async def run_dev_sdk(
    *,
    plan: dict[str, Any],
    context_bundle: dict[str, Any],
    worktree_path: str,
    project_path: str,
    verifier_report: dict[str, Any] | None = None,
    run_id: str | None = None,
    prior_session_id: str | None = None,
) -> dict[str, Any]:
    continuing = bool(prior_session_id)
    logger.info(
        "Dev(SDK) starting worktree=%s model=%s resume=%s",
        worktree_path, settings.CODE_AGENT_DEV_SDK_MODEL, continuing,
    )
    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="dev",
            title="Development started (Claude Agent SDK)",
            meta={"runtime": "sdk", "resumed": continuing},
        )

    # No hard requirement on ANTHROPIC_API_KEY: the SDK's own auth resolution
    # (env var, then ~/.claude/ Claude Code login state, then ADC) decides
    # this, not code_flow — see _build_options for why the key is only
    # injected into the subprocess env when one is actually configured. If
    # neither path has credentials, the SDK/CLI subprocess itself reports
    # that; it surfaces here as the generic hard-failure path below rather
    # than a pre-flight check that can't actually see ~/.claude/ state.
    log_sdk_auth_mode(logger, "Dev(SDK)")

    synced = await asyncio.to_thread(
        sync_planned_files_from_project, project_path, worktree_path, plan
    )
    if synced and run_id:
        append_activity(
            run_id,
            type="tool",
            phase="dev",
            title=f"Synced {len(synced)} file(s) from project",
            files=synced,
            meta={"tool": "sync_planned_files"},
        )

    allowed_paths = _plan_path_set(plan)
    analyze_targets = sorted(
        p for p in allowed_paths if p.endswith(".dart") and p.startswith("lib/")
    )
    planned_test_files = sorted(
        p for p in allowed_paths if p.startswith("test/") or p.endswith("_test.dart")
    )
    allow_flutter_test = bool(planned_test_files)

    required_fixes, analyze_errors = _collect_required_fixes(verifier_report)
    brief = _build_iteration_brief(
        worktree_path, allowed_paths, required_fixes, analyze_errors, continuing=continuing
    )
    if run_id and (required_fixes or analyze_errors):
        append_activity(
            run_id,
            type="status",
            phase="dev",
            title=f"Fix mode: {len(required_fixes)} required fix(es), "
            f"{len(analyze_errors)} analyzer error(s) carried into dev loop",
        )

    prompt = _build_prompt(
        continuing=continuing, plan=plan, context_bundle=context_bundle, brief=brief
    )

    baseline_dir = (
        f"{settings.CODE_AGENT_DATA_DIR}/baselines/{run_id}" if run_id else None
    )
    mcp_server = build_dev_mcp_server(
        worktree_path=worktree_path,
        allowed_paths=allowed_paths,
        analyze_targets=analyze_targets,
        allow_flutter_test=allow_flutter_test,
        planned_test_files=planned_test_files,
        baseline_dir=baseline_dir,
    )
    options = _build_options(
        worktree_path=worktree_path, mcp_server=mcp_server, prior_session_id=prior_session_id
    )

    from claude_agent_sdk import AssistantMessage, ResultMessage, query

    dev_summary = "Development finished."
    session_id: str | None = prior_session_id
    outcome: DevSdkOutcome = "success"
    step = 0
    # Initialized before the try so an exception raised before any
    # ResultMessage arrives reports the real error, not UnboundLocalError.
    terminal_reason: str | None = None
    result_text: str | None = None

    # can_use_tool (set in _build_options) requires streaming input mode —
    # a plain string prompt raises ValueError. See sdk_common.as_streaming_prompt.
    try:
        async for message in query(prompt=as_streaming_prompt(prompt), options=options):
            if isinstance(message, AssistantMessage):
                _log_usage(run_id, step + 1, getattr(message, "usage", None))
                for block_type, block in _walk_blocks(message):
                    if block_type == "text":
                        text = (getattr(block, "text", "") or "").strip()
                        if text:
                            dev_summary = text
                            if run_id:
                                append_activity(
                                    run_id,
                                    type="llm",
                                    phase="dev",
                                    title=f"Reasoning (step {step + 1})",
                                    detail=text[:1000],
                                )
                    elif block_type == "tool_use":
                        step += 1
                        name = getattr(block, "name", "unknown")
                        tool_input = getattr(block, "input", {}) or {}
                        logger.info("Dev(SDK) tool call: %s args=%s", name, str(tool_input)[:200])
                        if run_id:
                            append_activity(
                                run_id,
                                type="tool",
                                phase="dev",
                                title=f"Called tool: {name}",
                                detail=f"Arguments: {json.dumps(tool_input, default=str)[:500]}",
                                meta={"tool": name},
                            )
                    elif block_type == "tool_result" and run_id:
                        is_error = bool(getattr(block, "is_error", False))
                        append_activity(
                            run_id,
                            type="tool",
                            phase="dev",
                            title="Tool result" + (" (error)" if is_error else ""),
                            detail=str(getattr(block, "content", ""))[:1000],
                            meta={"is_error": is_error},
                        )
            elif isinstance(message, ResultMessage):
                session_id = getattr(message, "session_id", None) or session_id
                terminal_reason = getattr(message, "terminal_reason", None)
                # ResultMessage.is_error is a top-level bool and .result is the
                # CLI's human-readable text (str) — NOT a dict carrying an
                # is_error key. Reading is_error out of .result made it always
                # False, so a genuine provider failure (bad auth, rate limit)
                # classified as "success" and the run silently continued to
                # finalize with zero file changes.
                is_error = bool(getattr(message, "is_error", False))
                raw_result = getattr(message, "result", None)
                result_text = raw_result if isinstance(raw_result, str) else None
                outcome = classify_dev_sdk_result(terminal_reason, is_error)
                logger.info(
                    "Dev(SDK) result run_id=%s terminal_reason=%s outcome=%s "
                    "session_id=%s result=%s",
                    run_id, terminal_reason, outcome, session_id, (result_text or "")[:300],
                )
                if is_error and result_text and run_id:
                    append_activity(
                        run_id,
                        type="status",
                        phase="dev",
                        title="Claude Agent SDK reported an error",
                        detail=result_text[:1000],
                        meta={"terminal_reason": terminal_reason},
                    )
    except DevSdkProviderError:
        raise
    except Exception as exc:  # SDK/subprocess/auth failure — never a soft stop
        logger.exception("Dev(SDK) provider error run_id=%s", run_id)
        detail = result_text or str(exc)
        raise DevSdkProviderError(f"Claude Agent SDK dev run failed: {detail}") from exc

    if outcome == "hard_failure":
        raise DevSdkProviderError(
            "Claude Agent SDK dev run ended in an unrecoverable error: "
            f"{result_text or 'no error detail reported'} "
            f"(terminal_reason={terminal_reason!r})."
        )

    truncated = outcome == "soft_stop"
    if truncated:
        dev_summary = dev_summary or "Development paused at the turn limit — will resume on retry."
        logger.warning(
            "Dev(SDK) soft stop (turn/budget limit) run_id=%s session_id=%s", run_id, session_id
        )
        if run_id:
            append_activity(
                run_id,
                type="status",
                phase="dev",
                title="Turn limit reached — resuming this session on the next retry",
                meta={"truncated": True, "soft_stop": True, "session_id": session_id},
            )

    logger.info(
        "Dev(SDK) loop finished run_id=%s truncated=%s session_id=%s", run_id, truncated, session_id
    )

    file_changes, build_runner = await asyncio.to_thread(_finalize_dev_changes, worktree_path, plan)

    return {
        "file_changes": file_changes,
        "summary": dev_summary,
        "truncated": truncated,
        "build_runner": build_runner,
        "synced_from_project": synced,
        "messages": [{"role": "dev", "content": dev_summary}],
        "dev_sdk_session_id": session_id,
        "dev_build_runner": {
            "passed": bool(build_runner.get("passed", True)),
            "skipped": bool(build_runner.get("skipped", False)),
            "signature": build_runner.get("signature", ""),
        },
    }
