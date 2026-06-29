import os
import subprocess
import uuid
import shutil
from typing import Literal

from app.core.config import settings
from app.core.logging_config import get_logger
from app.tools.dart_tools import ensure_pub_dependencies
from app.tools.git_tools import is_repo_clean

logger = get_logger("worktree")

WorkspaceMode = Literal["worktree", "in_place"]


class WorktreeError(RuntimeError):
    pass


def resolve_worktree_path(
    project_path: str,
    *,
    run_id: str | None = None,
    worktree_path: str | None = None,
) -> str:
    """Resolve a worktree directory anchored to the target git project, not process cwd."""
    if worktree_path and os.path.isabs(worktree_path):
        return os.path.realpath(worktree_path)

    rel = worktree_path or os.path.join(settings.CODE_AGENT_WORKTREES_DIR, run_id or "")
    if os.path.isabs(rel):
        return os.path.realpath(rel)

    return os.path.realpath(os.path.join(project_path, rel))


def prepare_workspace(
    project_path: str,
    run_id: str | None = None,
    *,
    workspace_mode: WorkspaceMode = "worktree",
) -> dict:
    """Create an isolated worktree or use the main project checkout for editing."""
    if workspace_mode == "in_place":
        return _prepare_in_place_workspace(project_path, run_id=run_id)
    info = create_worktree(project_path, run_id=run_id)
    info["workspace_mode"] = "worktree"
    return info


def _prepare_in_place_workspace(project_path: str, run_id: str | None = None) -> dict:
    if not os.path.isdir(os.path.join(project_path, ".git")):
        raise WorktreeError(f"Not a git repository: {project_path}")

    # In-place mode edits the real checkout and the dev agent's roll_back_file
    # runs `git checkout HEAD -- <path>`, which would silently discard any
    # uncommitted changes to files it touches. Refuse on a dirty tracked tree.
    # (Untracked files are not destroyed by checkout, so they don't block.)
    if not is_repo_clean(project_path):
        raise WorktreeError(
            "In-place mode requires a clean working tree. The agent edits your "
            "real checkout and can roll back tracked files, which would discard "
            "uncommitted changes. Commit or stash your work and retry, or use "
            "worktree mode."
        )

    run_id = run_id or str(uuid.uuid4())
    worktree_path = os.path.realpath(project_path)
    pub_get = ensure_pub_dependencies(worktree_path)
    if not pub_get.get("passed", True):
        detail = pub_get.get("stderr") or pub_get.get("stdout") or "pub get failed"
        raise WorktreeError(f"Failed to prepare Dart workspace: {detail.strip()}")

    return {
        "run_id": run_id,
        "worktree_path": worktree_path,
        "branch": None,
        "workspace_mode": "in_place",
        "pub_get": pub_get,
    }


def sync_dirty_files(project_path: str, worktree_path: str) -> None:
    """Sync uncommitted changes (modified, added, deleted, untracked) from project_path to worktree_path."""
    logger.info("Syncing dirty files from %s to %s", project_path, worktree_path)
    proc = subprocess.run(
        ["git", "-C", project_path, "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        logger.warning("git status --porcelain failed in %s: %s", project_path, proc.stderr)
        return

    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        status_code = line[:2]
        rel_path = line[3:].strip()
        
        # Handle renames
        if " -> " in rel_path:
            parts = rel_path.split(" -> ")
            if len(parts) == 2:
                old_rel, new_rel = parts[0].strip(), parts[1].strip()
                old_wt_path = os.path.join(worktree_path, old_rel)
                if os.path.exists(old_wt_path):
                    if os.path.isdir(old_wt_path):
                        shutil.rmtree(old_wt_path)
                    else:
                        os.remove(old_wt_path)
                rel_path = new_rel

        src_file = os.path.join(project_path, rel_path)
        dest_file = os.path.join(worktree_path, rel_path)

        if os.path.isdir(src_file) and not os.path.islink(src_file):
            continue

        if "D" in status_code:
            if os.path.exists(dest_file):
                try:
                    if os.path.isdir(dest_file):
                        shutil.rmtree(dest_file)
                    else:
                        os.remove(dest_file)
                    logger.info("Deleted %s in worktree", rel_path)
                except Exception as exc:
                    logger.warning("Failed to delete %s in worktree: %s", rel_path, exc)
        else:
            if os.path.exists(src_file):
                try:
                    os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                    if os.path.islink(src_file):
                        if os.path.exists(dest_file):
                            os.remove(dest_file)
                        os.symlink(os.readlink(src_file), dest_file)
                    else:
                        shutil.copy2(src_file, dest_file)
                    logger.info("Synced dirty file %s to worktree", rel_path)
                except Exception as exc:
                    logger.warning("Failed to copy %s to worktree: %s", rel_path, exc)


def create_worktree(project_path: str, run_id: str | None = None) -> dict:
    if not os.path.isdir(os.path.join(project_path, ".git")):
        raise WorktreeError(f"Not a git repository: {project_path}")

    run_id = run_id or str(uuid.uuid4())
    worktree_path = resolve_worktree_path(project_path, run_id=run_id)
    os.makedirs(os.path.dirname(worktree_path), exist_ok=True)
    branch = f"agent/{run_id}"

    if os.path.exists(worktree_path):
        raise WorktreeError(f"Worktree already exists: {worktree_path}")

    proc = subprocess.run(
        ["git", "-C", project_path, "worktree", "add", "-b", branch, worktree_path],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise WorktreeError(proc.stderr.strip() or "git worktree add failed")

    # Sync dirty changes to the newly created worktree before running pub get
    try:
        sync_dirty_files(project_path, worktree_path)
    except Exception as exc:
        logger.exception("Failed to sync dirty files to worktree: %s", exc)

    pub_get = ensure_pub_dependencies(worktree_path)
    if not pub_get.get("passed", True):
        remove_worktree(project_path, worktree_path)
        detail = pub_get.get("stderr") or pub_get.get("stdout") or "pub get failed"
        raise WorktreeError(f"Failed to prepare Dart workspace: {detail.strip()}")

    return {
        "run_id": run_id,
        "worktree_path": worktree_path,
        "branch": branch,
        "pub_get": pub_get,
    }


def remove_worktree(project_path: str, worktree_path: str) -> None:
    subprocess.run(
        ["git", "-C", project_path, "worktree", "remove", "--force", worktree_path],
        capture_output=True,
        text=True,
        check=False,
    )
