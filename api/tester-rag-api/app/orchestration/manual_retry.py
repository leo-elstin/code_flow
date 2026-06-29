import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.agents.roles.dev import run_dev
from app.agents.roles.fix_planner import plan_analyze_fixes
from app.agents.roles.qa import run_qa
from app.agents.roles.verifier import run_verifier
from app.core.config import settings
from app.core.logging_config import get_logger
from app.orchestration.state import FeatureRunState
from app.services.run_activity import append_activity
from app.tools.dart_tools import run_dart_analyze
from app.tools.git_tools import list_changed_files

logger = get_logger("manual_retry")

PatchStateFn = Callable[[dict[str, Any]], Awaitable[None]]
GetStateFn = Callable[[], Awaitable[FeatureRunState | None]]


def validate_manual_retry_eligibility(state: FeatureRunState) -> None:
    if state.get("status") != "failed":
        raise ValueError("Manual retry is only available for failed runs")
    if not state.get("worktree_path"):
        raise ValueError("Worktree not available for manual retry")


def analyze_targets(worktree_path: str, plan: dict[str, Any]) -> list[str]:
    changed = [
        path
        for path in list_changed_files(worktree_path)
        if path.startswith("lib/") and path.endswith(".dart")
    ]
    if changed:
        return sorted(changed)

    planned: list[str] = []
    for key in ("files_to_create", "files_to_modify"):
        for item in plan.get(key, []) or []:
            rel = item.get("path") if isinstance(item, dict) else str(item)
            rel = str(rel).lstrip("/")
            if rel.startswith("lib/") and rel.endswith(".dart"):
                planned.append(rel)
    return sorted(set(planned))


def build_analyze_verifier_report(
    analyze: dict[str, Any],
    fix_plan: dict[str, Any],
) -> dict[str, Any]:
    error_lines = list(analyze.get("error_lines") or [])
    # Do NOT seed required_fixes with the raw analyzer lines: they are already
    # surfaced verbatim to the dev agent (via the analyze gate -> "Analyzer errors"
    # block in the iteration brief). Seeding them here as well made one analyzer
    # error appear three times — raw line, rephrased fix, and step — bloating the
    # fix checklist (e.g. 25 errors -> 43 "required fixes"). Keep only the
    # planner's distinct, deduplicated guidance.
    required_fixes: list[str] = []
    _seen = {line.strip() for line in error_lines}
    for source in (fix_plan.get("required_fixes") or [], fix_plan.get("steps") or []):
        for item in source:
            text = str(item).strip()
            if text and text not in _seen:
                _seen.add(text)
                required_fixes.append(text)

    issues = [
        {
            "severity": "blocker",
            "file": _file_from_analyze_line(line),
            "reason": line,
        }
        for line in error_lines
    ]
    return {
        "passed": False,
        "required_fixes": required_fixes,
        "issues": issues,
        "deterministic_gate": {"analyze": analyze, "passed": False},
        "fix_plan": fix_plan,
        "headline": fix_plan.get("summary") or "Fix flutter analyze errors",
    }


def _file_from_analyze_line(line: str) -> str | None:
    if " • " not in line:
        return None
    parts = line.split(" • ")
    if len(parts) >= 3:
        return parts[2].split(":")[0].strip()
    return None


async def execute_manual_retry(
    *,
    get_state: GetStateFn,
    patch_state: PatchStateFn,
) -> None:
    state = await get_state()
    if not state:
        raise ValueError("Run not found")

    run_id = state.get("run_id", "")
    worktree_path = state.get("worktree_path")
    if not worktree_path:
        raise ValueError("Worktree not available for manual retry")

    max_loops = settings.CODE_AGENT_MAX_MANUAL_RETRY_ITERATIONS
    plan = state.get("plan") or {}
    project_path = state["project_path"]
    user_request = state.get("user_request", "")
    targets = analyze_targets(worktree_path, plan)
    last_analyze: dict[str, Any] = {}
    analyze_clean = False
    prev_error_count: int | None = None

    for loop in range(1, max_loops + 1):
        logger.info("Manual retry run_id=%s loop=%d/%d", run_id, loop, max_loops)
        await patch_state(
            {
                "status": "developing",
                "iteration": loop,
                "error": None,
                "messages": [
                    {
                        "role": "system",
                        "content": f"Manual retry: running flutter analyze (loop {loop}/{max_loops})",
                    }
                ],
            }
        )

        if run_id:
            append_activity(
                run_id,
                type="tool",
                phase="dev",
                title=f"flutter analyze (manual retry {loop}/{max_loops})",
                files=targets,
                meta={"tool": "dart_analyze", "loop": loop},
            )
        analyze = await asyncio.to_thread(run_dart_analyze, worktree_path, targets or None)
        last_analyze = analyze

        if analyze.get("timed_out"):
            await patch_state(
                {
                    "status": "failed",
                    "error": "flutter analyze timed out during manual retry",
                    "verifier_report": build_analyze_verifier_report(
                        analyze,
                        {"summary": "Analyze timed out", "required_fixes": [], "steps": []},
                    ),
                }
            )
            return

        if analyze.get("error_count", 0) == 0 and analyze.get("passed", False):
            analyze_clean = True
            logger.info("Manual retry run_id=%s analyze clean at loop=%d", run_id, loop)
            break

        # Convergence guard: if a previous fix pass did not reduce the analyzer
        # error count, the loop is not making progress. Bail immediately instead
        # of burning the remaining (model-call-heavy) fix/dev iterations.
        error_count = analyze.get("error_count", 0)
        if prev_error_count is not None and error_count >= prev_error_count:
            logger.warning(
                "Manual retry run_id=%s not converging (errors %s -> %s at loop=%d); stopping early",
                run_id,
                prev_error_count,
                error_count,
                loop,
            )
            if run_id:
                append_activity(
                    run_id,
                    type="status",
                    phase="dev",
                    title=(
                        f"Stopping early — analyze errors not decreasing "
                        f"({prev_error_count} → {error_count})"
                    ),
                    meta={"tool": "dart_analyze", "loop": loop, "converged": False},
                )
            break
        prev_error_count = error_count

        state = await get_state() or state
        fix_plan = await plan_analyze_fixes(
            analyze_result=analyze,
            plan=plan,
            user_request=user_request,
            previous_verifier_report=state.get("verifier_report"),
            loop=loop,
            max_loops=max_loops,
            run_id=run_id or None,
        )
        verifier_report = build_analyze_verifier_report(analyze, fix_plan)
        await patch_state(
            {
                "verifier_report": verifier_report,
                "messages": [
                    {
                        "role": "fix_planner",
                        "content": fix_plan.get("summary") or "Analyze fix plan created.",
                    }
                ],
            }
        )

        dev_result = await run_dev(
            plan=plan,
            context_bundle=state.get("context_bundle", {}),
            worktree_path=worktree_path,
            project_path=project_path,
            verifier_report=verifier_report,
            run_id=run_id or None,
        )
        await patch_state(
            {
                "file_changes": dev_result.get("file_changes", []),
                "messages": dev_result.get("messages", []),
            }
        )

    if not analyze_clean:
        await patch_state(
            {
                "status": "failed",
                "iteration": max_loops,
                "error": f"Manual retry exhausted after {max_loops} analyze/fix loops",
                "verifier_report": build_analyze_verifier_report(
                    last_analyze,
                    {
                        "summary": "Analyze errors remain after manual retry loops",
                        "required_fixes": last_analyze.get("error_lines") or [],
                        "steps": [],
                    },
                ),
            }
        )
        return

    state = await get_state() or state

    # --- Post-analyze-clean: verifier ⇄ dev loop (mirrors main graph) ---
    verifier_dev_max = min(max_loops, 3)  # cap verifier↔dev retries
    for v_loop in range(1, verifier_dev_max + 1):
        await patch_state({"status": "verifying", "error": None})

        if run_id:
            append_activity(
                run_id,
                type="status",
                phase="verifier",
                title=f"Verification started",
            )

        verifier_result = await run_verifier(
            plan=plan,
            acceptance_criteria=state.get("acceptance_criteria", []),
            worktree_path=worktree_path,
            file_changes=state.get("file_changes", []),
            project_path=project_path,
            run_id=run_id or None,
        )
        report = verifier_result["verifier_report"]
        base_patch: dict[str, Any] = {
            "verifier_report": report,
            "diffs": verifier_result.get("diffs", []),
            "messages": verifier_result.get("messages", []),
        }

        if report.get("passed"):
            await patch_state({**base_patch, "status": "qa"})
            state = await get_state() or state
            qa_result = await run_qa(
                plan=plan,
                acceptance_criteria=state.get("acceptance_criteria", []),
                diffs=state.get("diffs", []),
                project_path=project_path,
                worktree_path=worktree_path,
                run_id=run_id or None,
            )
            await patch_state(
                {
                    "status": "completed",
                    "qa_report": qa_result.get("qa_report", {}),
                    "messages": qa_result.get("messages", []),
                    "error": None,
                }
            )
            logger.info("Manual retry run_id=%s completed", run_id)
            return

        # Verifier failed — route back to dev with the feedback
        if v_loop >= verifier_dev_max:
            break  # exhausted retries

        logger.warning(
            "Manual retry run_id=%s verifier rejected (v_loop=%d/%d), routing back to dev",
            run_id,
            v_loop,
            verifier_dev_max,
        )
        await patch_state(
            {
                **base_patch,
                "status": "developing",
                "iteration": v_loop,
            }
        )

        if run_id:
            append_activity(
                run_id,
                type="status",
                phase="dev",
                title=f"Entering development (verifier retry {v_loop}/{verifier_dev_max})",
            )

        dev_result = await run_dev(
            plan=plan,
            context_bundle=state.get("context_bundle", {}),
            worktree_path=worktree_path,
            project_path=project_path,
            verifier_report=report,
            run_id=run_id or None,
        )
        await patch_state(
            {
                "file_changes": dev_result.get("file_changes", []),
                "messages": dev_result.get("messages", []),
            }
        )
        state = await get_state() or state

    await patch_state(
        {
            **base_patch,
            "status": "failed",
            "error": "Verifier failed after manual retry",
        }
    )
    logger.warning("Manual retry run_id=%s verifier failed after analyze clean", run_id)

