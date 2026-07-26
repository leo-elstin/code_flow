import asyncio
import json
import os
from typing import Any

from app.core.logging_config import get_logger
from app.services.generation import chat_completion_json
from app.services.run_activity import append_activity
from app.core.config import settings
from app.tools.dart_tools import (
    check_di_registrations,
    dart_source_signature,
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
    prior_build_runner: dict[str, Any] | None = None,
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
            # Reuse the dev node's build_runner result when its Dart source inputs are
            # unchanged since — build_runner then runs at most once per dev→verify
            # cycle (it costs 2-3 min). The dev run already staged the generated files.
            reuse_signature = ""
            if prior_build_runner and prior_build_runner.get("passed"):
                reuse_signature = prior_build_runner.get("signature") or ""
            current_signature = (
                dart_source_signature(
                    worktree_path, [p for p in changed if p.endswith(".dart")]
                )
                if reuse_signature
                else ""
            )
            if reuse_signature and reuse_signature == current_signature:
                build_runner = {
                    "passed": True,
                    "skipped": True,
                    "reused": True,
                    "targets": dart_targets,
                }
                if run_id:
                    append_activity(
                        run_id,
                        type="tool",
                        phase="verifier",
                        title="build_runner reused (unchanged since dev)",
                        meta={"tool": "build_runner", "passed": True, "reused": True},
                    )
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

    # DI registration evidence. injectable/get_it registrations land in generated
    # *.config.dart files that are usually gitignored, so they never show in the
    # diff the LLM reviewer reads — which made it repeatedly hallucinate "DI
    # integration is missing" even though build_runner had wired the service.
    # Read the generated config from disk and surface hard evidence instead.
    di_registrations = check_di_registrations(
        worktree_path, _planned_paths(plan.get("files_to_create", []))
    )
    if di_registrations.get("checked") and build_runner.get("passed", True):
        if run_id:
            append_activity(
                run_id,
                type="tool",
                phase="verifier",
                title=(
                    "DI registration verified"
                    if not di_registrations.get("missing")
                    else "DI registration missing"
                ),
                files=sorted(di_registrations.get("registered", {}).values()),
                meta={
                    "tool": "di_check",
                    "registered": list(di_registrations.get("registered", {})),
                    "missing": di_registrations.get("missing", []),
                },
            )
        for cls in di_registrations.get("missing", []):
            msg = (
                f"{cls} is annotated injectable but is not registered in any generated "
                "*.config.dart — run build_runner so it joins the runtime DI graph"
            )
            issues.append({"severity": "blocker", "file": None, "reason": msg})
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
        "di_registrations": di_registrations,
        "changed_files": sorted(changed),
        "synced_from_project": synced_from_project,
        "dev_targets": sorted(dev_targets),
    }


_MAX_DIFF_FILES = 20
_MAX_DIFF_CHARS = 6_000
_FAILED_OUTPUT_TAIL = 1_500


def _build_llm_payload(
    plan: dict[str, Any],
    acceptance_criteria: list[str],
    file_changes: list[dict[str, Any]],
    diffs: list[dict[str, Any]],
    gate: dict[str, Any],
) -> dict[str, Any]:
    """Trim the LLM-review payload: subprocess stdout/stderr adds no review
    signal once the gate passed (error_lines already carry analyzer findings),
    and only failed test output is worth showing. Diffs are capped per file.
    plan_markdown is dropped too — it's a prose restatement of fields already
    present elsewhere in this same plan dict (feature_summary, architecture,
    acceptance_criteria, ...), so it adds no review signal, only tokens."""
    plan = {k: v for k, v in plan.items() if k != "plan_markdown"}
    gate_summary = dict(gate)
    for key in ("pub_get", "build_runner", "test_results"):
        section = gate_summary.get(key)
        if not isinstance(section, dict):
            continue
        section = dict(section)
        keep_tail = key == "test_results" and not section.get("passed", True)
        for stream in ("stdout", "stderr"):
            value = section.get(stream)
            if not value:
                continue
            section[stream] = value[-_FAILED_OUTPUT_TAIL:] if keep_tail else ""
        gate_summary[key] = section
    return {
        "plan": plan,
        "acceptance_criteria": acceptance_criteria,
        "file_changes": file_changes,
        "diffs": [
            {"path": d.get("path"), "diff": (d.get("diff") or "")[:_MAX_DIFF_CHARS]}
            for d in diffs[:_MAX_DIFF_FILES]
        ],
        "deterministic_gate": gate_summary,
    }


async def run_verifier(
    *,
    plan: dict[str, Any],
    acceptance_criteria: list[str],
    worktree_path: str,
    file_changes: list[dict[str, Any]],
    project_path: str | None = None,
    run_id: str | None = None,
    prior_build_runner: dict[str, Any] | None = None,
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
            prior_build_runner=prior_build_runner,
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

        threshold = settings.CODE_AGENT_VERIFIER_SKIP_LLM_TRIVIAL_CHARS
        if (
            threshold > 0
            and not (plan.get("files_to_create") or [])
            and sum(len(d.get("diff") or "") for d in diffs) <= threshold
        ):
            # Trivial modify-only change with all deterministic checks green —
            # the LLM review adds little; opt-in fast path.
            logger.info("Verifier passed (gate only; LLM review skipped, trivial diff)")
            report = {
                "passed": True,
                "issues": [],
                "required_fixes": [],
                "deterministic_gate": gate,
                "llm_review": "skipped_trivial",
            }
            return {
                "verifier_report": report,
                "diffs": diffs,
                "messages": [{"role": "verifier", "content": "Verification passed."}],
            }

        llm_report, _usage = await chat_completion_json(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You verify a Flutter feature implementation against a plan. "
                        "Deterministic checks already passed. "
                        "Return JSON: {passed: bool, issues: [], required_fixes: [], reasoning: string optional}. "
                        "Set passed=true unless a planned acceptance criterion is clearly unmet.\n"
                        "IMPORTANT — generated code is invisible in diffs. Files matching "
                        "*.config.dart, *.g.dart, *.freezed.dart, *.mocks.dart are build_runner "
                        "output and are gitignored, so they will NOT appear in the diffs you are "
                        "shown. Dependency-injection registrations (injectable/get_it) live in "
                        "*.config.dart. Do NOT flag a service as 'not registered', 'missing DI "
                        "wiring', or 'not resolvable through the app DI path' based on its absence "
                        "from the diff. The field deterministic_gate.di_registrations is the source "
                        "of truth: every class listed under its 'registered' map is confirmed wired "
                        "into the runtime DI graph. Only raise a DI blocker for a class that appears "
                        "in di_registrations.missing. A class carrying @injectable/@lazySingleton/"
                        "@singleton needs no manual registration call — build_runner generates it."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        _build_llm_payload(
                            plan, acceptance_criteria, file_changes, diffs, gate
                        ),
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
