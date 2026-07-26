import asyncio
import json
import os
import shlex
import subprocess
from typing import Any

from app.agents.message_trim import trim_tool_messages
from app.core.config import settings
from app.core.logging_config import get_logger
from app.services.generation import acompletion, extract_cached_tokens, get_dev_model
from app.services.run_activity import append_activity
from app.tools.dart_tools import (
    dart_source_signature,
    is_generated_dart_path,
    maybe_run_build_runner,
    run_dart_analyze,
)
from app.tools.filesystem import write_file, roll_back_file
from app.tools.edit_file import edit_file, EditFileError
from app.tools.safe_shell import run_safe_command
from app.tools.git_tools import list_changed_files, stage_files
from app.tools.grep import read_file, grep_codebase
from app.tools.worktree_sync import sync_planned_files_from_project
from app.tools.docs_tool import lookup_sdk_symbol
from app.tools.dev_tools import make_dev_tools

logger = get_logger("dev")

DEV_SYSTEM_LOOP = """You are a senior Flutter developer implementing an approved plan in a git worktree.
You work iteratively by calling tools. Analyze your progress after each tool result and decide what to do next.
When the implementation is complete and all files compile cleanly, respond with a plain-text summary of everything
you created and modified — do not call any more tools.

CRITICAL — Issue independent tool calls TOGETHER in a single turn:
- You can return SEVERAL tool calls in one response. Any calls that do not depend on each other's results MUST go out together.
- Creating planned files: when the plan lists several files to create and you know what goes in them, issue ALL those "write_file" calls in ONE turn. Do NOT write one file per turn.
- Exploring: issue all the "read_file" / "search_codebase" calls you need at once rather than one at a time.
- Only split across turns when a call genuinely needs an earlier call's result (e.g. read a file, then edit it based on what you read).
- Every extra turn re-sends the entire conversation so far, so one turn with five calls costs far less than five turns with one call.

Rules:
- Avoid overwriting entire files using "write_file" for small changes. Use "edit_file" instead to perform precise modifications.
- BATCH your edits: when you already know several edits you need to make (across one or more files), issue them together in a SINGLE "apply_edits" call instead of one "edit_file" call per turn. This is much faster. Only fall back to a single "edit_file" when an edit depends on the result of a previous one.
- HOWEVER, if a file becomes severely corrupted (e.g., edits keep failing because the code is duplicated or mangled), you SHOULD use "write_file" to rewrite the entire file with the correct content.
- Ensure "target_content" matches a block in the file. Whitespace differences (trailing spaces, indentation) are tolerated, but the block must be unique unless you pass allow_multiple.
- Verify your code compiles with the "analyze_changed_files" tool after a batch of changes — you do NOT need to analyze after every single edit.
- If compile errors are found, fix them iteratively or rollback files to undo mistakes using "roll_back_file".
- For a large file where you only need to see one method/widget/class, pass "offset"/"limit" to "read_file" to read just that range instead of the whole file.

CRITICAL — Scope discipline (ENFORCED: the tool layer rejects out-of-scope writes/edits and blocked commands):
- ONLY create or modify files that are listed in the approved plan (files_to_create, files_to_modify).
- Do NOT edit test files, config files, or any other files unless the plan explicitly requires it.
- If flutter analyze or flutter test reports errors in files OUTSIDE your plan scope, IGNORE those errors. They are pre-existing and not your responsibility.
- If a pre-existing test fails, do NOT try to fix it. Only address errors in YOUR planned files.

CRITICAL — Testing:
- "flutter test" is BLOCKED unless the approved plan includes test files; when allowed, it runs only the planned test files.
- Use "analyze_changed_files" (not "flutter test") to verify compilation of new widgets/components.

CRITICAL — Fix iterations:
- If the user message contains "FIX MODE", the implementation ALREADY EXISTS (current contents are provided in the message).
- Do NOT rewrite files from scratch. Apply ONLY the listed required fixes using "edit_file".
- After applying the fixes, run "analyze_changed_files", then complete.

CRITICAL — Deprecated API migration:
- When flutter analyze warns about a deprecated API (e.g., withOpacity), you MUST call "lookup_sdk_symbol" with the REPLACEMENT method name BEFORE writing any fix.
- Example: if "withOpacity" is deprecated, call lookup_sdk_symbol(query="withValues") to learn the correct signature.
- Do NOT guess the replacement API's parameters. The SDK signature is the only source of truth.
- Common trap: Color.withValues() uses NAMED parameter `alpha`, e.g. `Colors.white.withValues(alpha: 0.6)`.

CRITICAL — Dart constructor patterns:
- Modern Dart uses `super.key` in the parameter list. Do NOT also add `: super(key: key)` in the initializer — that causes a duplicate argument error.
- Correct: `const MyWidget({super.key, required this.child});`
- Wrong:  `const MyWidget({super.key, required this.child}) : super(key: key);`
- A const constructor must NOT have a body `{}`. End with `;`.

- If compile errors OR verifier feedback relate to an unknown API, wrong arguments, or deprecated methods, use the "lookup_sdk_symbol" tool to check the correct signature BEFORE attempting a fix. Do NOT guess.
"""

def _format_skills_block(skills: list[dict[str, str]] | None) -> str:
    if not skills:
        return ""
    lines = ["Agent skills:"]
    for skill in skills:
        name = skill.get("name") or skill.get("path") or "skill"
        content = skill.get("content") or ""
        lines.append(f"\n### {name}\n{content}")
    return "\n".join(lines) + "\n\n"


_RUN_COMMAND_STDOUT_TAIL = 2000
_RUN_COMMAND_STDERR_TAIL = 1000
_FIX_MODE_FILE_CAP = 8_000
_FIX_MODE_MAX_FILES = 4


def _norm_rel(path: str) -> str:
    return os.path.normpath(str(path).strip().lstrip("/")).replace("\\", "/")


def _plan_path_set(plan: dict[str, Any]) -> set[str]:
    """Relative paths the dev agent is allowed to create or modify."""
    paths: set[str] = set()
    for key in ("files_to_create", "files_to_modify"):
        for item in plan.get(key) or []:
            rel = item.get("path") if isinstance(item, dict) else str(item)
            if rel:
                paths.add(_norm_rel(rel))
    return paths


def _truncate_tail(text: str, limit: int) -> str:
    if not text or len(text) <= limit:
        return text or ""
    return f"...[truncated {len(text) - limit} chars]...\n{text[-limit:]}"


def _collect_required_fixes(
    verifier_report: dict[str, Any] | None,
) -> tuple[list[str], list[str]]:
    """Flatten a failed verifier report into (required_fixes, analyze_error_lines)."""
    if not verifier_report or verifier_report.get("passed", True):
        return [], []
    gate = verifier_report.get("deterministic_gate") or verifier_report
    analyze = gate.get("analyze") or {}
    error_lines = [str(line) for line in (analyze.get("error_lines") or [])]
    # Analyzer lines are surfaced separately in the brief; never repeat them as
    # "required fixes" so the checklist stays one-entry-per-distinct-problem.
    seen: set[str] = {line.strip() for line in error_lines}
    fixes: list[str] = []
    sources = [
        verifier_report.get("required_fixes") or [],
        gate.get("required_fixes") or [],
        [
            issue.get("reason") if isinstance(issue, dict) else issue
            for issue in (verifier_report.get("issues") or [])
        ],
    ]
    for source in sources:
        for item in source:
            text = str(item).strip() if item else ""
            if text and text not in seen:
                seen.add(text)
                fixes.append(text)
    return fixes, error_lines


def _build_iteration_brief(
    worktree_path: str,
    allowed_paths: set[str],
    required_fixes: list[str],
    analyze_errors: list[str],
    continuing: bool = False,
) -> str:
    """Task brief for the dev loop. First iteration: implement. Retry iterations:
    FIX MODE — an explicit fix checklist so the agent patches the existing
    implementation instead of rewriting from scratch.

    When *continuing* (the prior iteration's conversation is being carried forward),
    the agent already has its earlier reads in context, so we skip re-injecting the
    planned-file bodies and just hand it the checklist — it can read_file_tool any
    file it needs to re-check current on-disk state."""
    if not required_fixes and not analyze_errors:
        return (
            "Begin implementation. Analyze your options, call tools, and verify "
            "your changes with analyze_changed_files iteratively."
        )

    lines = [
        "FIX MODE — the implementation from the previous iteration ALREADY EXISTS in the worktree.",
        "Do NOT rewrite files from scratch. Apply ONLY the fixes below (batch them with apply_edits).",
        "",
        "Required fixes:",
    ]
    lines.extend(f"{idx}. {fix}" for idx, fix in enumerate(required_fixes, 1))
    if analyze_errors:
        lines.append("")
        lines.append("Analyzer errors (verbatim):")
        lines.extend(f"- {line}" for line in analyze_errors[:25])

    if continuing:
        lines.append("")
        lines.append(
            "You already have the earlier context from this session. Re-read a file "
            "with read_file_tool only if you need to confirm its current on-disk state, "
            "then apply the fixes and run analyze_changed_files. When every fix is "
            "applied and analyze passes for your files, respond with a plain-text summary."
        )
        return "\n".join(lines)

    existing = [
        rel
        for rel in sorted(allowed_paths)
        if os.path.isfile(os.path.join(worktree_path, rel))
    ]
    # Only inject files the feedback actually references — the agent can
    # read_file_tool anything else on demand. Fall back to the first few
    # planned files when nothing matches (never provide zero context).
    feedback_text = "\n".join([*required_fixes, *analyze_errors]).lower()
    referenced = [
        rel
        for rel in existing
        if rel.lower() in feedback_text
        or os.path.basename(rel).lower() in feedback_text
    ]
    to_inject = (referenced or existing)[:_FIX_MODE_MAX_FILES]
    if to_inject:
        lines.append("")
        lines.append("Current contents of your planned files:")
        for rel in to_inject:
            try:
                content = read_file(worktree_path, rel)
            except Exception:
                continue
            if len(content) > _FIX_MODE_FILE_CAP:
                content = content[:_FIX_MODE_FILE_CAP] + "\n...[file truncated]"
            lines.append(f"\n### {rel}\n```dart\n{content}\n```")
        skipped = len(existing) - len(to_inject)
        if skipped > 0:
            lines.append(
                f"\n({skipped} other planned file(s) exist; use read_file_tool if needed)"
            )

    lines.append("")
    lines.append(
        "Apply each fix with edit_file_tool, then run analyze_changed_files_tool. "
        "When every fix is applied and analyze passes for your files, respond with a plain-text summary."
    )
    return "\n".join(lines)


def _changed_lib_dart(worktree_path: str) -> list[str]:
    """lib/*.dart files with uncommitted changes in the worktree."""
    try:
        changed = list_changed_files(worktree_path)
    except Exception:
        return []
    return [
        _norm_rel(path)
        for path in changed
        if path.endswith(".dart") and _norm_rel(path).startswith("lib/")
    ]


def _scoped_analyze(worktree_path: str, analyze_targets: list[str]) -> str:
    """flutter/dart analyze over the planned lib files UNION any lib/*.dart the dev
    has actually changed. Matching the verifier's target set here stops the
    "dev passes on a narrow scope, wide verifier rejects" ping-pong. Compact JSON."""
    target_set = set(analyze_targets) | set(_changed_lib_dart(worktree_path))
    targets = sorted(
        rel for rel in target_set if os.path.isfile(os.path.join(worktree_path, rel))
    )
    if not targets:
        return "No planned lib/*.dart files exist yet — create them before analyzing."
    result = run_dart_analyze(worktree_path, targets)
    payload: dict[str, Any] = {
        "passed": result.get("passed", False),
        "error_count": result.get("error_count", 0),
        "error_lines": result.get("error_lines", [])[:25],
        "targets": targets,
    }
    if not payload["passed"] and not payload["error_lines"]:
        payload["output_tail"] = _truncate_tail(
            result.get("stdout") or result.get("stderr") or "", 1500
        )
    return json.dumps(payload, indent=2)


def _guard_run_command(
    command: str,
    worktree_path: str,
    analyze_targets: list[str],
    allow_flutter_test: bool,
    planned_test_files: list[str],
) -> tuple[str | None, str]:
    """Inspect a run_command request. Returns (result, command): when result is not
    None it is the final tool output (redirect or block) and nothing is executed;
    otherwise the (possibly rewritten) command should be run."""
    try:
        tokens = shlex.split(command.strip())
    except ValueError:
        return None, command
    if len(tokens) >= 2 and tokens[0] in ("flutter", "dart"):
        if tokens[1] == "analyze":
            return (
                "Redirected to analyze_changed_files (full-project analyze is disabled "
                "— pre-existing issues outside your plan are not your responsibility):\n"
                + _scoped_analyze(worktree_path, analyze_targets),
                command,
            )
        if tokens[1] == "test":
            if not allow_flutter_test:
                return (
                    "BLOCKED: the approved plan contains no test files, so flutter test "
                    "is disabled for this task. Use analyze_changed_files to verify "
                    "compilation instead.",
                    command,
                )
            requested = [tok for tok in tokens[2:] if not tok.startswith("-")]
            invalid = [
                rel for rel in (_norm_rel(tok) for tok in requested)
                if rel not in planned_test_files
            ]
            if invalid:
                return (
                    "BLOCKED: flutter test may only target planned test files. "
                    f"Not in plan: {', '.join(invalid)}",
                    command,
                )
            if not requested and planned_test_files:
                return None, " ".join(tokens[:2] + planned_test_files)
    return None, command


def _execute_dev_tool(
    action: str,
    args: dict[str, Any],
    worktree_path: str,
    *,
    allowed_paths: set[str] | None = None,
    analyze_targets: list[str] | None = None,
    allow_flutter_test: bool = True,
    planned_test_files: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Run one dev-agent tool call. Pure blocking IO/subprocess — invoked via
    asyncio.to_thread so the event loop (API, SSE, other runs) stays responsive.
    Write/edit/rollback actions are restricted to allowed_paths (the approved plan).
    Returns (tool_result, affected_files)."""
    analyze_targets = analyze_targets or []
    planned_test_files = planned_test_files or []

    def _scope_error(path: str) -> str | None:
        if allowed_paths is None or _norm_rel(path) in allowed_paths:
            return None
        return (
            f"BLOCKED: {path} is not in the approved plan "
            f"(allowed: {', '.join(sorted(allowed_paths)) or 'none'}). "
            "Do not modify out-of-scope files; pre-existing failures in them are "
            "not your responsibility."
        )

    try:
        if action == "read_file":
            path = args.get("path", "")
            return read_file(worktree_path, path), [path]
        elif action == "write_file":
            path = args.get("path", "")
            blocked = _scope_error(path)
            if blocked:
                return blocked, []
            abs_target = os.path.join(worktree_path, _norm_rel(path))
            if os.path.isfile(abs_target) and not args.get("overwrite"):
                return (
                    f"BLOCKED: {path} already exists. Use edit_file to change it; "
                    "only pass overwrite=true if the file is corrupted beyond repair.",
                    [],
                )
            write_file(worktree_path, path, args.get("content", ""))
            return f"Successfully wrote file to {path}.", [path]
        elif action == "edit_file":
            path = args.get("path", "")
            blocked = _scope_error(path)
            if blocked:
                return blocked, []
            edit_file(
                worktree_path,
                path,
                args.get("target_content", ""),
                args.get("replacement_content", ""),
                allow_multiple=args.get("allow_multiple", False)
            )
            return f"Successfully replaced content in {path}.", [path]
        elif action == "roll_back_file":
            path = args.get("path", "")
            blocked = _scope_error(path)
            if blocked:
                return blocked, []
            roll_back_file(worktree_path, path)
            return f"Successfully rolled back uncommitted changes to {path}.", [path]
        elif action == "list_files":
            directory = args.get("directory", "lib")
            abs_dir = os.path.join(worktree_path, directory)
            if os.path.exists(abs_dir):
                return json.dumps(os.listdir(abs_dir)), []
            return f"Directory not found: {directory}", []
        elif action == "search_codebase":
            results = grep_codebase(worktree_path, args.get("query", ""))
            return json.dumps(results[:15]), []
        elif action == "lookup_sdk_symbol":
            result = lookup_sdk_symbol(worktree_path, args.get("query", ""))
            return result, []
        elif action == "analyze_changed_files":
            return _scoped_analyze(worktree_path, analyze_targets), []
        elif action == "run_command":
            guard_result, command = _guard_run_command(
                args.get("command", ""),
                worktree_path,
                analyze_targets,
                allow_flutter_test,
                planned_test_files,
            )
            if guard_result is not None:
                return guard_result, []
            res = run_safe_command(worktree_path, command)
            return (
                f"Return Code: {res.returncode}\n"
                f"STDOUT:\n{_truncate_tail(res.stdout, _RUN_COMMAND_STDOUT_TAIL)}\n"
                f"STDERR:\n{_truncate_tail(res.stderr, _RUN_COMMAND_STDERR_TAIL)}",
                [],
            )
        return f"Unknown action: {action}", []
    except EditFileError as exc:
        return f"Edit failed: {exc}", []
    except Exception as exc:  # surface tool errors back to the agent loop
        return f"Error executing tool {action}: {exc}", []


_DEV_MSG_KEEP_RECENT_TOOLS = 12
_DEV_MSG_STUB_LEN = 400
# The stale/full cutoff advances in batches of this many tool results instead
# of shifting by one on every single call. Recomputing "last N full" as a pure
# sliding window means almost every turn stubs exactly one message for the
# first time — a byte-level mutation partway through the conversation, which
# breaks prefix-based prompt caching (both Anthropic's cache_control and
# OpenAI's automatic longest-prefix match need that prefix to stay
# byte-identical call to call). Freezing the cutoff for a whole batch lets the
# cache actually hold across that stretch instead of invalidating every step.
#
# Must stay comfortably above how many tool calls a single step can add: the
# "batch independent tool calls into one turn" prompt instruction (see
# DEV_SYSTEM_LOOP) routinely produces 5-11 tool results in ONE step now, so a
# batch size anywhere near that (6 was measured live to be too small) gets
# blown past within a single step almost every time, and the cutoff ends up
# advancing every step anyway — the exact bug this constant exists to avoid.
_DEV_MSG_TRIM_BATCH = 24


def _trim_dev_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dev-loop-tuned wrapper around the shared tool-message trimmer — see
    app.agents.message_trim.trim_tool_messages for the rationale."""
    return trim_tool_messages(
        messages,
        keep_recent=_DEV_MSG_KEEP_RECENT_TOOLS,
        stub_len=_DEV_MSG_STUB_LEN,
        batch_size=_DEV_MSG_TRIM_BATCH,
    )


def _finalize_dev_changes(
    worktree_path: str, plan: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Stage changed files and optionally run build_runner. Blocking git/subprocess
    work — invoked via asyncio.to_thread. Returns (file_changes, build_runner). The
    build_runner dict carries a `signature` over its Dart source inputs so the verifier
    can skip re-running it when nothing changed since."""
    changed_files = list_changed_files(worktree_path)
    file_changes: list[dict[str, Any]] = []

    if not changed_files:
        return file_changes, {"passed": True, "skipped": True, "targets": [], "signature": ""}

    stage_files(worktree_path, changed_files)
    dart_paths = [path for path in changed_files if path.endswith(".dart")]
    build_runner = maybe_run_build_runner(worktree_path, dart_paths)
    if build_runner.get("passed") and not build_runner.get("skipped"):
        generated = [
            path for path in list_changed_files(worktree_path) if is_generated_dart_path(path)
        ]
        if generated:
            stage_files(worktree_path, generated)
    # Signature over the (non-generated) Dart inputs so the verifier can reuse this
    # build_runner result instead of running it a second time.
    build_runner["signature"] = dart_source_signature(worktree_path, dart_paths)

    create_paths = [f.get("path") for f in plan.get("files_to_create", [])]
    for path in changed_files:
        action_type = "create" if path in create_paths else "modify"
        file_changes.append(
            {
                "path": path,
                "action": action_type,
                "summary": "Updated iteratively by agentic dev loop",
            }
        )
    return file_changes, build_runner

async def run_dev(
    *,
    plan: dict[str, Any],
    context_bundle: dict[str, Any],
    worktree_path: str,
    project_path: str,
    verifier_report: dict[str, Any] | None = None,
    run_id: str | None = None,
    prior_messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # When prior_messages is supplied (a retry / step-limit continuation), the dev
    # loop resumes that conversation instead of rebuilding the system prompt and
    # re-running discovery — the earlier reads and reasoning are already in context.
    continuing = bool(prior_messages)
    logger.info("Dev starting worktree=%s using tiered model=%s", worktree_path, get_dev_model())
    if run_id:
        append_activity(
            run_id,
            type="status",
            phase="dev",
            title="Development started (native tool calling)",
        )

    # Sync planned files from the project to start with
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

    guide = context_bundle.get("project_guide") or {}
    project_guide_text = f"Project guide ({guide.get('path', 'AGENTS.md')}):\n{guide.get('content', '')}" if guide.get("found") else ""
    project_context_text = f"Project context:\n{context_bundle.get('project_context', '')}\n\n" if context_bundle.get("project_context") else ""
    dev_skills_text = _format_skills_block(context_bundle.get("dev_skills"))

    prior_work = context_bundle.get("prior_work") or {}
    prior_work_text = (
        "Prior implementation attempts exist for this ticket — see the plan's discovery "
        "notes for what's already covered. Read the relevant existing files before writing "
        "new ones to avoid duplicating completed work.\n\n"
        if prior_work.get("found") else ""
    )

    # --- Plan-scoped guard context (enforced in _execute_dev_tool) ---
    allowed_paths = _plan_path_set(plan)
    analyze_targets = sorted(
        p for p in allowed_paths if p.endswith(".dart") and p.startswith("lib/")
    )
    planned_test_files = sorted(
        p for p in allowed_paths if p.startswith("test/") or p.endswith("_test.dart")
    )
    allow_flutter_test = bool(planned_test_files)

    # --- Iteration brief: first pass implements; retries enter FIX MODE with the
    # current file contents + an explicit fix checklist (no rewrite-from-scratch) ---
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

    # --- Proactive deprecated-API pre-lookup ---
    # Always inject common SDK migration hints so the agent never writes deprecated APIs.
    # On retry iterations, also scan analyze output for additional deprecation warnings.
    sdk_hints = ""
    try:
        _DEPRECATION_MIGRATIONS = {
            "withOpacity": "withValues",
        }
        looked_up: set[str] = set()

        # Always pre-lookup the most common migrations
        for deprecated, replacement in _DEPRECATION_MIGRATIONS.items():
            if replacement not in looked_up:
                looked_up.add(replacement)
                hint = await asyncio.to_thread(lookup_sdk_symbol, worktree_path, replacement)
                if hint and "Could not find" not in hint:
                    sdk_hints += (
                        f"\n\nSDK reference for '{replacement}' "
                        f"(replacement for deprecated '{deprecated}'):\n{hint[:2000]}\n"
                    )
                    logger.info("Pre-looked up SDK symbol '%s' for deprecated '%s'", replacement, deprecated)
    except Exception:
        logger.debug("Proactive SDK lookup failed (non-fatal)", exc_info=True)

    dev_model = get_dev_model()
    logger.info("Dev starting native tool-calling loop model=%s", dev_model)

    # Per-run baseline store so edits can be undone to the run's start state
    # (not HEAD), preserving pre-existing uncommitted work in in-place runs.
    baseline_dir = (
        os.path.join(settings.CODE_AGENT_DATA_DIR, "baselines", run_id) if run_id else None
    )

    # Build tools pre-bound to this run's worktree + plan scope
    tool_functions, tool_schemas = make_dev_tools(
        worktree_path=worktree_path,
        allowed_paths=allowed_paths,
        analyze_targets=analyze_targets,
        allow_flutter_test=allow_flutter_test,
        planned_test_files=planned_test_files,
        baseline_dir=baseline_dir,
    )

    if continuing:
        # Resume the carried conversation: keep it bounded, then append only the new
        # fix brief as a fresh user turn. The plan/guide/skills already live in the
        # first user message of prior_messages, so we don't repeat them.
        messages = _trim_dev_messages(list(prior_messages))
        messages.append(
            {
                "role": "user",
                "content": (
                    (f"SDK references (auto-looked-up for deprecated APIs in feedback):\n{sdk_hints}\n\n" if sdk_hints else "")
                    + brief
                ),
            }
        )
    else:
        # plan_markdown is a prose restatement of fields already present
        # elsewhere in this same dict (feature_summary, architecture,
        # acceptance_criteria, discovery_evidence, ...) — it exists for the
        # human-facing plan review UI, not for the dev loop. Dropping it here
        # saves ~2k tokens on every single turn of the run without losing any
        # information the model doesn't already have in structured form.
        dev_plan = {k: v for k, v in plan.items() if k != "plan_markdown"}
        messages = [
            {"role": "system", "content": DEV_SYSTEM_LOOP},
            {
                "role": "user",
                "content": (
                    f"Approved plan:\n{json.dumps(dev_plan, indent=2)}\n\n"
                    + (f"{project_guide_text}\n\n" if project_guide_text else "")
                    + project_context_text
                    + dev_skills_text
                    + prior_work_text
                    + (f"SDK references (auto-looked-up for deprecated APIs in feedback):\n{sdk_hints}\n\n" if sdk_hints else "")
                    + brief
                ),
            },
        ]

    dev_summary = "Development finished."
    truncated = False
    step = 0
    max_steps = 40  # safety net only — model stops naturally when it makes no tool calls

    while step < max_steps:
        step += 1
        logger.info("Dev loop step %d/%d model=%s", step, max_steps, dev_model)
        if run_id:
            append_activity(
                run_id,
                type="llm",
                phase="dev",
                title=f"Thinking (step {step})",
                meta={"model": dev_model, "state": "started"},
            )

        response = await acompletion(
            model=dev_model,
            messages=messages,
            tools=tool_schemas,
            tool_choice="auto",
            # Nudges repeated calls in this run toward the same cache-holding
            # backend shard (OpenAI-only; Anthropic client drops unknown kwargs).
            **({"prompt_cache_key": run_id} if run_id else {}),
        )

        usage = getattr(response, "usage", None)
        if run_id and usage:
            total = int(getattr(usage, "total_tokens", 0) or 0)
            if total > 0:
                append_activity(
                    run_id,
                    type="token",
                    phase="dev",
                    title=f"Token usage (dev step {step})",
                    detail=f"{total} tokens",
                    meta={
                        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                        "total_tokens": total,
                        "cached_tokens": extract_cached_tokens(usage),
                    },
                )

        msg = response.choices[0].message
        # Append the full assistant message (preserves tool_calls for the API)
        messages.append(msg.model_dump(exclude_unset=False))

        # No tool calls → model is done; its content is the completion summary
        if not msg.tool_calls:
            dev_summary = (msg.content or "Implementation completed.").strip()
            logger.info("Dev agent completed naturally step=%d summary=%s", step, dev_summary[:80])
            break

        # Execute each tool call the model requested
        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}

            logger.info("Dev tool call: %s args=%s", fn_name, str(fn_args)[:200])
            if run_id:
                append_activity(
                    run_id,
                    type="tool",
                    phase="dev",
                    title=f"Called tool: {fn_name}",
                    detail=f"Arguments: {json.dumps(fn_args, default=str)[:500]}",
                    meta={"tool": fn_name},
                )

            fn = tool_functions.get(fn_name)
            if fn is None:
                tool_result = f"Unknown tool: {fn_name}"
            else:
                tool_result = await asyncio.to_thread(fn, **fn_args)

            logger.info("Tool %s result length=%d", fn_name, len(str(tool_result)))
            if run_id:
                append_activity(
                    run_id,
                    type="tool",
                    phase="dev",
                    title=f"Tool result: {fn_name}",
                    detail=str(tool_result)[:1000],
                    meta={"tool": fn_name},
                )

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(tool_result),
            })

        # Previously only applied at run boundaries (continuation start / final
        # persistence), so a single long run's tool results kept accumulating
        # in full for its entire lifetime. Applying it every step keeps a
        # long-running loop bounded instead of growing unchecked until it hits
        # a rate limit — safe to call every step since it's a no-op on
        # messages that are already stubbed.
        messages = _trim_dev_messages(messages)

    else:
        # Exited via step limit, not natural completion
        truncated = True
        dev_summary = f"Dev agent hit step limit ({max_steps}) — partial implementation."
        logger.error("Dev agent exhausted step limit run_id=%s steps=%d", run_id, step)
        if run_id:
            append_activity(
                run_id,
                type="status",
                phase="dev",
                title=f"Step limit hit ({max_steps}) — partial implementation",
                meta={"truncated": True, "steps": step},
            )

    logger.info("Dev loop finished run_id=%s steps=%d truncated=%s", run_id, step, truncated)

    # Post-processing: stage changed files, run build_runner if needed
    file_changes, build_runner = await asyncio.to_thread(
        _finalize_dev_changes, worktree_path, plan
    )

    return {
        "file_changes": file_changes,
        "summary": dev_summary,
        "truncated": truncated,
        "build_runner": build_runner,
        "synced_from_project": synced,
        "messages": [{"role": "dev", "content": dev_summary}],
        # Full tool-calling conversation (trimmed) so the next iteration can resume
        # from here instead of restarting discovery.
        "dev_messages": _trim_dev_messages(messages),
        # {passed, signature} so the verifier can skip a redundant build_runner run.
        "dev_build_runner": {
            "passed": bool(build_runner.get("passed", True)),
            "skipped": bool(build_runner.get("skipped", False)),
            "signature": build_runner.get("signature", ""),
        },
    }
