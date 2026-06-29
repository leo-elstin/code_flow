import asyncio
import json
import os
from typing import Any

from app.core.logging_config import get_logger
from app.services.generation import chat_completion_json
from app.services.run_activity import append_activity
from app.core.config import settings
from app.tools.dart_tools import (
    ensure_pub_dependencies,
    is_generated_dart_path,
    maybe_run_build_runner,
    run_dart_analyze,
    run_flutter_test,
)
from app.tools.dart_tools import dart_public_method_names
from app.tools.git_tools import get_all_diffs, list_changed_files, read_file_at_head, stage_files
from app.tools.worktree_sync import sync_planned_files_from_project

logger = get_logger("verifier")

_ANALYZE_SKIP_PREFIXES = ("test/", "integration_test/")


def _planned_paths(items: list[Any]) -> list[str]:
    paths: list[str] = []
    for item in items:
        if isinstance(item, dict):
            path = item.get("path")
        else:
            path = str(item)
        if path:
            paths.append(path.lstrip("/"))
    return paths


def _dev_target_paths(file_changes: list[dict[str, Any]] | None) -> set[str]:
    return {path.lstrip("/") for path in (_planned_paths(file_changes or []))}


def _analyze_targets(
    changed: set[str],
    worktree_path: str,
    dev_targets: set[str] | None = None,
) -> list[str]:
    pool = dev_targets if dev_targets else changed
    targets = sorted(
        path
        for path in pool
        if path.endswith(".dart")
        and path.startswith("lib/")
        and not path.startswith(_ANALYZE_SKIP_PREFIXES)
        and os.path.isfile(os.path.join(worktree_path, path))
    )
    if targets:
        return targets
    return sorted(
        path
        for path in changed
        if path.endswith(".dart")
        and path.startswith("lib/")
        and os.path.isfile(os.path.join(worktree_path, path))
    )


def compare_to_plan(
    plan: dict[str, Any],
    worktree_path: str,
    file_changes: list[dict[str, Any]] | None = None,
    project_path: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    required_fixes: list[str] = []
    synced_from_project: list[str] = []

    if project_path:
        synced_from_project = sync_planned_files_from_project(project_path, worktree_path, plan)
        if synced_from_project:
            logger.info(
                "Synced %d planned file(s) from project into worktree: %s",
                len(synced_from_project),
                ", ".join(synced_from_project[:8]),
            )

    changed_git = set(list_changed_files(worktree_path))
    changed_dev = _dev_target_paths(file_changes)
    changed = changed_git | changed_dev
    dev_targets = changed_dev

    for rel in _planned_paths(plan.get("files_to_create", [])):
        abs_path = os.path.join(worktree_path, rel)
        if not os.path.isfile(abs_path):
            msg = f"Missing planned new file: {rel}"
            issues.append({"severity": "blocker", "file": rel, "reason": msg})
            required_fixes.append(msg)

    for rel in _planned_paths(plan.get("files_to_modify", [])):
        if rel not in dev_targets:
            continue

        abs_path = os.path.join(worktree_path, rel)
        if not os.path.isfile(abs_path):
            msg = f"Dev target missing in worktree: {rel}"
            issues.append({"severity": "blocker", "file": rel, "reason": msg})
            required_fixes.append(msg)
        elif rel not in changed:
            msg = f"Dev did not modify target file: {rel}"
            issues.append({"severity": "blocker", "file": rel, "reason": msg})
            required_fixes.append(msg)
        elif rel.endswith(".dart"):
            original = read_file_at_head(worktree_path, rel)
            if original:
                try:
                    with open(abs_path, encoding="utf-8") as handle:
                        current = handle.read()
                except OSError:
                    current = ""
                removed = dart_public_method_names(original) - dart_public_method_names(current)
                if removed:
                    names = ", ".join(sorted(removed))
                    msg = f"Removed existing methods from {rel}: {names}"
                    issues.append({"severity": "blocker", "file": rel, "reason": msg})
                    required_fixes.append(msg)

    dart_targets = _analyze_targets(changed, worktree_path, dev_targets)
    pub_get: dict[str, Any] = {"passed": True, "skipped": True}
    build_runner: dict[str, Any] = {"passed": True, "skipped": True, "targets": dart_targets}
    if dart_targets:
        pub_get = ensure_pub_dependencies(worktree_path)
        if run_id:
            append_activity(
                run_id,
                type="tool",
                phase="verifier",
                title="pub get"
                if pub_get.get("passed", True)
                else "pub get failed",
                meta={"tool": "pub_get", "passed": pub_get.get("passed", True)},
            )
        if not pub_get.get("passed", True):
            msg = "pub get failed"
            issues.append(
                {
                    "severity": "blocker",
                    "file": None,
                    "reason": msg,
                    "details": pub_get.get("stdout") or pub_get.get("stderr"),
                }
            )
            required_fixes.append(msg)
        else:
            build_runner = maybe_run_build_runner(worktree_path, dart_targets)
            if run_id and not build_runner.get("skipped"):
                append_activity(
                    run_id,
                    type="tool",
                    phase="verifier",
                    title="build_runner"
                    if build_runner.get("passed")
                    else "build_runner failed",
                    files=dart_targets,
                    meta={"tool": "build_runner", "passed": build_runner.get("passed")},
                )
            if build_runner.get("passed") and not build_runner.get("skipped"):
                generated = [
                    path
                    for path in list_changed_files(worktree_path)
                    if is_generated_dart_path(path)
                ]
                if generated:
                    stage_files(worktree_path, generated)
                    changed = set(list_changed_files(worktree_path)) | changed_dev
                    dart_targets = _analyze_targets(changed, worktree_path, dev_targets)
            if not build_runner.get("passed"):
                msg = "build_runner failed"
                issues.append(
                    {
                        "severity": "blocker",
                        "file": None,
                        "reason": msg,
                        "details": build_runner.get("stdout") or build_runner.get("stderr"),
                    }
                )
                required_fixes.append(msg)

    analyze: dict[str, Any] = {"passed": True, "skipped": True, "targets": dart_targets}
    if dart_targets and pub_get.get("passed", True) and build_runner.get("passed", True):
        analyze = run_dart_analyze(worktree_path, dart_targets)
        analyze["targets"] = dart_targets
        if run_id:
            append_activity(
                run_id,
                type="tool",
                phase="verifier",
                title="dart analyze"
                if analyze.get("passed", True)
                else "dart analyze failed",
                files=dart_targets,
                meta={
                    "tool": "dart_analyze",
                    "passed": analyze.get("passed", True),
                    "error_count": analyze.get("error_count"),
                },
            )
        blocking_passed = analyze.get("blocking_passed", analyze.get("passed", True))
        if not blocking_passed:
            msg = "dart analyze failed on changed lib files"
            details = "\n".join(analyze.get("error_lines") or []) or analyze.get("stdout") or analyze.get("stderr")
            issues.append(
                {
                    "severity": "blocker",
                    "file": None,
                    "reason": msg,
                    "details": details,
                }
            )
            required_fixes.append(msg)
        elif not analyze.get("passed", True):
            logger.warning(
                "Analyze found %s error(s) in dev lib files (advisory; not blocking)",
                analyze.get("error_count", "?"),
            )
            issues.append(
                {
                    "severity": "warning",
                    "file": None,
                    "reason": "dart analyze reported errors in changed lib files",
                    "details": "\n".join(analyze.get("error_lines") or [])[:4000],
                }
            )

    # Automated test execution gate — only run tests that are part of the plan
    test_results: dict[str, Any] = {"passed": True, "skipped": True}
    planned_paths = set(_planned_paths(plan.get("files_to_create", []))) | set(
        _planned_paths(plan.get("files_to_modify", []))
    )
    test_files = [
        path for path in changed
        if (path.endswith("_test.dart") or path.startswith("test/"))
        and path in planned_paths  # only run tests the plan explicitly includes
        and os.path.isfile(os.path.join(worktree_path, path))
    ]
    if test_files and pub_get.get("passed", True) and build_runner.get("passed", True):
        if run_id:
            append_activity(
                run_id,
                type="status",
                phase="verifier",
                title=f"Running {len(test_files)} changed test(s)",
            )
        test_results = run_flutter_test(worktree_path, test_files)
        if run_id:
            append_activity(
                run_id,
                type="tool",
                phase="verifier",
                title="flutter test" if test_results.get("passed", True) else "flutter test failed",
                files=test_files,
                meta={"tool": "flutter_test", "passed": test_results.get("passed", True)},
            )
        if not test_results.get("passed", True):
            msg = "Automated unit tests failed"
            details = test_results.get("stdout", "") + "\n" + test_results.get("stderr", "")
            issues.append(
                {
                    "severity": "blocker",
                    "file": None,
                    "reason": msg,
                    "details": details[-4000:],
                }
            )
            required_fixes.append(msg)

    passed = len(required_fixes) == 0
    return {
        "passed": passed,
        "issues": issues,
        "required_fixes": required_fixes,
        "pub_get": pub_get,
        "build_runner": build_runner,
        "analyze": analyze,
        "test_results": test_results,
        "changed_files": sorted(changed),
        "synced_from_project": synced_from_project,
        "dev_targets": sorted(dev_targets),
    }


async def run_verifier(
    *,
    plan: dict[str, Any],
    acceptance_criteria: list[str],
    worktree_path: str,
    file_changes: list[dict[str, Any]],
    project_path: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    logger.info(
        "Verifier starting worktree=%s dev_targets=%s",
        worktree_path,
        sorted(_dev_target_paths(file_changes)),
    )
    try:
        if run_id:
            append_activity(
                run_id,
                type="status",
                phase="verifier",
                title="Verification started",
            )
        # compare_to_plan shells out to pub get / build_runner / dart analyze /
        # flutter test (up to several minutes); run it off the event loop.
        gate = await asyncio.to_thread(
            compare_to_plan,
            plan,
            worktree_path,
            file_changes,
            project_path=project_path,
            run_id=run_id,
        )
        diffs = await asyncio.to_thread(get_all_diffs, worktree_path)

        if not gate["passed"]:
            blockers = gate.get("required_fixes") or [
                issue.get("reason", "")
                for issue in gate.get("issues", [])
                if issue.get("severity") == "blocker"
            ]
            logger.warning(
                "Verifier deterministic gate failed: %s",
                "; ".join(blockers[:5]) or "unknown",
            )
            return {
                "verifier_report": gate,
                "diffs": diffs,
                "messages": [{"role": "verifier", "content": "Deterministic gates failed."}],
            }

        llm_report, _usage = await chat_completion_json(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You verify a Flutter feature implementation against a plan. "
                        "Deterministic checks already passed. "
                        "Return JSON: {passed: bool, issues: [], required_fixes: [], reasoning: string optional}. "
                        "Set passed=true unless a planned acceptance criterion is clearly unmet."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "plan": plan,
                            "acceptance_criteria": acceptance_criteria,
                            "file_changes": file_changes,
                            "diffs": diffs[:20],
                            "deterministic_gate": gate,
                        },
                        indent=2,
                    )[:120000],
                },
            ],
            run_id=run_id,
            phase="verifier",
            label="verifier",
        )
        if run_id and llm_report.get("reasoning"):
            append_activity(
                run_id,
                type="thinking",
                phase="verifier",
                title="Verifier reasoning",
                detail=str(llm_report.get("reasoning"))[:4000],
            )
        passed = bool(llm_report.get("passed", True))
        report = {
            "passed": passed,
            "issues": llm_report.get("issues") or [],
            "required_fixes": llm_report.get("required_fixes") or [],
            "deterministic_gate": gate,
        }
        if passed:
            logger.info("Verifier passed (LLM review)")
        else:
            logger.warning("Verifier failed LLM review")
        return {
            "verifier_report": report,
            "diffs": diffs,
            "messages": [
                {
                    "role": "verifier",
                    "content": "Verification passed." if passed else "Verification failed.",
                }
            ],
        }
    except Exception:
        logger.exception("Verifier error worktree=%s", worktree_path)
        raise
