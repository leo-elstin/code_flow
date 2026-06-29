import os
from datetime import datetime, timezone
from typing import Any

from app.core.logging_config import get_logger
from app.services.worktree import resolve_worktree_path
from app.tools.git_tools import (
    GitMergeError,
    abort_merge_if_in_progress,
    commit_all,
    get_checkout_branch,
    is_repo_clean,
    merge_branch,
)

logger = get_logger("merge_worktree")


class MergeValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "validation_failed") -> None:
        super().__init__(message)
        self.code = code


def validate_merge_request(state: dict[str, Any]) -> None:
    if state.get("status") != "completed":
        raise MergeValidationError(
            "Merge is only available for completed runs",
            code="invalid_status",
        )
    if state.get("workspace_mode") == "in_place":
        raise MergeValidationError(
            "Merge is not available for in-place runs — changes are already in the project checkout",
            code="in_place_workspace",
        )
    if not state.get("worktree_path"):
        raise MergeValidationError("Worktree not available for merge", code="no_worktree")
    merge_report = state.get("merge_report") or {}
    if merge_report.get("applied"):
        raise MergeValidationError(
            f"Already merged to {merge_report.get('target_branch', 'base branch')}",
            code="already_merged",
        )


def apply_merge_to_base(
    *,
    run_id: str,
    project_path: str,
    worktree_path: str,
    target_branch: str | None = None,
    commit_message: str | None = None,
) -> dict[str, Any]:
    if not os.path.isdir(os.path.join(project_path, ".git")):
        raise MergeValidationError(f"Not a git repository: {project_path}", code="not_git_repo")

    resolved_worktree = resolve_worktree_path(
        project_path,
        run_id=run_id,
        worktree_path=worktree_path,
    )
    if not os.path.isdir(resolved_worktree):
        raise MergeValidationError(
            f"Worktree not found: {resolved_worktree}",
            code="worktree_missing",
        )

    if not is_repo_clean(project_path):
        raise MergeValidationError(
            "Main project checkout has uncommitted changes — commit or stash before merging",
            code="dirty_main_repo",
        )

    agent_branch = f"agent/{run_id}"
    message = commit_message or f"Code agent run {run_id}"

    try:
        commit_sha = commit_all(resolved_worktree, message)
        abort_merge_if_in_progress(project_path)
        resolved_target = target_branch or get_checkout_branch(project_path)
        merge_branch(project_path, agent_branch)
    except GitMergeError as exc:
        logger.warning(
            "Merge failed run_id=%s code=%s conflicts=%s",
            run_id,
            exc.code,
            exc.conflict_files,
        )
        return {
            "applied": False,
            "target_branch": target_branch or _safe_checkout_branch(project_path),
            "agent_branch": agent_branch,
            "conflict_files": exc.conflict_files,
            "error": str(exc),
            "code": exc.code,
        }

    logger.info(
        "Merge applied run_id=%s target=%s agent=%s commit=%s",
        run_id,
        resolved_target,
        agent_branch,
        commit_sha,
    )
    return {
        "applied": True,
        "target_branch": resolved_target,
        "agent_branch": agent_branch,
        "commit_sha": commit_sha,
        "conflict_files": [],
        "error": None,
        "merged_at": datetime.now(timezone.utc).isoformat(),
    }


def _safe_checkout_branch(project_path: str) -> str | None:
    try:
        return get_checkout_branch(project_path)
    except GitMergeError:
        return None
