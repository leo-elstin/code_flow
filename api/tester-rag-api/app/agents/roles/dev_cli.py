"""Pilot dev engine: runs the dev node through the Claude Code CLI's own
headless agent loop instead of app.agents.roles.dev's hand-rolled tool-calling
loop. Opt-in per project via project_ticket_store.dev_engine — see
app/orchestration/graph.py's dev_node for the branch point.

Mirrors run_dev()'s signature and return contract exactly so downstream nodes
(verifier, qa) don't need to know which engine produced the changes. Reuses
dev.py's plan-scoped prompt helpers rather than duplicating them.
"""

import asyncio
import json
import shutil
from typing import Any

from app.agents.roles.dev import (
    _build_iteration_brief,
    _collect_required_fixes,
    _finalize_dev_changes,
    _format_skills_block,
    _plan_path_set,
)
from app.core.config import settings
from app.core.logging_config import get_logger
from app.services.run_activity import append_activity
from app.tools.worktree_sync import sync_planned_files_from_project

logger = get_logger("dev_cli")

_CLAUDE_BIN = "claude"

_DEV_CLI_INSTRUCTIONS = """You are a senior Flutter developer implementing an approved plan in a git worktree (this directory).
ONLY create or modify files listed under files_to_create / files_to_modify below — nothing else, even if you
notice unrelated issues while working. When the implementation is complete and everything compiles, reply with
a brief plain-text summary of what you created and modified. Do not ask clarifying questions — if something is
ambiguous, make the most idiomatic choice consistent with the rest of the codebase and note the choice in your
summary.

"""


class ClaudeCliUnavailableError(RuntimeError):
    """Raised when the `claude` binary isn't on PATH for this process."""


async def _invoke_claude_cli(
    *,
    prompt: str,
    worktree_path: str,
    resume_session_id: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Run one headless Claude Code CLI turn and return its parsed --output-format
    json result: {..., is_error, result, session_id, usage: {input_tokens,
    output_tokens, cache_read_input_tokens, ...}, total_cost_usd, ...}.

    Isolated behind this seam (rather than inlined in run_dev_cli) so tests can
    monkeypatch it directly instead of mocking asyncio.create_subprocess_exec."""
    if shutil.which(_CLAUDE_BIN) is None:
        raise ClaudeCliUnavailableError(
            f"'{_CLAUDE_BIN}' CLI not found on PATH — install/configure Claude Code "
            "to use the claude_code_cli dev engine, or switch this project back to "
            "the api engine in Settings."
        )

    args = [
        _CLAUDE_BIN, "-p", prompt,
        "--output-format", "json",
        "--permission-mode", "acceptEdits",
    ]
    if resume_session_id:
        args += ["--resume", resume_session_id]

    # No shell=True: args go straight to execve, so the prompt content (which
    # may contain file contents, error output, etc.) can never be interpreted
    # as shell syntax.
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {
            "is_error": True,
            "result": f"Claude CLI timed out after {timeout_seconds}s",
            "session_id": resume_session_id,
        }

    if proc.returncode != 0:
        return {
            "is_error": True,
            "result": f"Claude CLI exited {proc.returncode}: {stderr.decode(errors='replace')[:2000]}",
            "session_id": resume_session_id,
        }

    try:
        return json.loads(stdout.decode(errors="replace"))
    except json.JSONDecodeError:
        return {
            "is_error": True,
            "result": f"Claude CLI returned non-JSON output: {stdout.decode(errors='replace')[:2000]}",
            "session_id": resume_session_id,
        }


def _build_prompt(
    *,
    plan: dict[str, Any],
    context_bundle: dict[str, Any],
    worktree_path: str,
    allowed_paths: set[str],
    verifier_report: dict[str, Any] | None,
    continuing: bool,
) -> str:
    required_fixes, analyze_errors = _collect_required_fixes(verifier_report)
    brief = _build_iteration_brief(
        worktree_path, allowed_paths, required_fixes, analyze_errors, continuing=continuing
    )

    if continuing:
        # The CLI's own session (--resume) already has the plan/guide/skills
        # from turn one in context — only the new brief needs sending.
        return brief

    guide = context_bundle.get("project_guide") or {}
    project_guide_text = (
        f"Project guide ({guide.get('path', 'AGENTS.md')}):\n{guide.get('content', '')}\n\n"
        if guide.get("found") else ""
    )
    dev_skills_text = _format_skills_block(context_bundle.get("dev_skills"))
    prior_work = context_bundle.get("prior_work") or {}
    prior_work_text = (
        "Prior implementation attempts exist for this ticket — see the plan's discovery "
        "notes for what's already covered. Read the relevant existing files before writing "
        "new ones to avoid duplicating completed work.\n\n"
        if prior_work.get("found") else ""
    )
    # plan_markdown is a prose restatement of fields already present elsewhere
    # in this same dict — see dev.py's identical strip for the rationale.
    dev_plan = {k: v for k, v in plan.items() if k != "plan_markdown"}

    return (
        _DEV_CLI_INSTRUCTIONS
        + f"Approved plan:\n{json.dumps(dev_plan, indent=2)}\n\n"
        + project_guide_text
        + dev_skills_text
        + prior_work_text
        + brief
    )


def _log_token_usage(run_id: str, usage: dict[str, Any], total_cost_usd: Any) -> None:
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cached_tokens = int(usage.get("cache_read_input_tokens") or 0)
    total = input_tokens + output_tokens
    if total <= 0:
        return
    append_activity(
        run_id,
        type="token",
        phase="dev",
        title="Token usage (dev CLI)",
        detail=f"{total} tokens" + (f" (${total_cost_usd})" if total_cost_usd else ""),
        meta={
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": total,
            "cached_tokens": cached_tokens,
        },
    )


async def run_dev_cli(
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
    logger.info("Dev(cli) starting worktree=%s resume=%s", worktree_path, continuing)
    if run_id:
        append_activity(
            run_id, type="status", phase="dev",
            title="Development started (Claude Code CLI, pilot)",
        )

    synced = await asyncio.to_thread(
        sync_planned_files_from_project, project_path, worktree_path, plan
    )
    if synced and run_id:
        append_activity(
            run_id, type="tool", phase="dev",
            title=f"Synced {len(synced)} file(s) from project",
            files=synced, meta={"tool": "sync_planned_files"},
        )

    allowed_paths = _plan_path_set(plan)
    if run_id:
        required_fixes, analyze_errors = _collect_required_fixes(verifier_report)
        if required_fixes or analyze_errors:
            append_activity(
                run_id, type="status", phase="dev",
                title=f"Fix mode: {len(required_fixes)} required fix(es), "
                f"{len(analyze_errors)} analyzer error(s) carried into CLI dev loop",
            )

    prompt = _build_prompt(
        plan=plan,
        context_bundle=context_bundle,
        worktree_path=worktree_path,
        allowed_paths=allowed_paths,
        verifier_report=verifier_report,
        continuing=continuing,
    )

    try:
        result = await _invoke_claude_cli(
            prompt=prompt,
            worktree_path=worktree_path,
            resume_session_id=prior_session_id,
            timeout_seconds=settings.CODE_AGENT_CLI_TIMEOUT_SECONDS,
        )
    except ClaudeCliUnavailableError as exc:
        logger.error("Dev(cli) unavailable run_id=%s: %s", run_id, exc)
        if run_id:
            append_activity(run_id, type="status", phase="dev", title=str(exc))
        return {
            "file_changes": [],
            "summary": str(exc),
            "truncated": True,
            "build_runner": {"passed": True, "skipped": True, "targets": [], "signature": ""},
            "synced_from_project": synced,
            "messages": [{"role": "dev", "content": str(exc)}],
            "dev_cli_session_id": prior_session_id,
            "dev_build_runner": {"passed": True, "skipped": True, "signature": ""},
        }

    is_error = bool(result.get("is_error"))
    session_id = result.get("session_id") or prior_session_id
    dev_summary = (result.get("result") or "").strip() or (
        "Claude CLI run failed." if is_error else "Development finished."
    )

    if run_id:
        append_activity(
            run_id, type="status", phase="dev",
            title="Claude CLI run failed" if is_error else "Claude CLI run finished",
            detail=dev_summary[:500],
        )
        usage = result.get("usage") or {}
        if usage:
            _log_token_usage(run_id, usage, result.get("total_cost_usd"))

    # Post-processing is engine-agnostic: git-status-based staging + build_runner.
    file_changes, build_runner = await asyncio.to_thread(_finalize_dev_changes, worktree_path, plan)

    # Soft scope check for this pilot — log, don't block. See plan notes on
    # why a hard PreToolUse-hook gate is deferred to a follow-up.
    out_of_scope = sorted({f["path"] for f in file_changes} - allowed_paths)
    if out_of_scope and run_id:
        append_activity(
            run_id, type="status", phase="dev",
            title=f"Claude CLI touched {len(out_of_scope)} out-of-plan file(s)",
            detail=", ".join(out_of_scope[:10]),
            meta={"out_of_scope_files": out_of_scope},
        )

    logger.info(
        "Dev(cli) finished run_id=%s is_error=%s session_id=%s file_changes=%d",
        run_id, is_error, session_id, len(file_changes),
    )

    return {
        "file_changes": file_changes,
        "summary": dev_summary,
        "truncated": is_error,
        "build_runner": build_runner,
        "synced_from_project": synced,
        "messages": [{"role": "dev", "content": dev_summary}],
        "dev_cli_session_id": session_id,
        "dev_build_runner": {
            "passed": bool(build_runner.get("passed", True)),
            "skipped": bool(build_runner.get("skipped", False)),
            "signature": build_runner.get("signature", ""),
        },
    }
