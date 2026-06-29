import os
import subprocess

from app.core.config import settings
from app.services.project_explorer import ProjectExplorer


class GitMergeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "merge_failed",
        conflict_files: list[str] | None = None,
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.conflict_files = conflict_files or []
        self.stderr = stderr


def _run_git(args: list[str], *, cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def get_checkout_branch(repo_path: str) -> str:
    proc = _run_git(["branch", "--show-current"], cwd=repo_path)
    if proc.returncode != 0:
        raise GitMergeError(
            proc.stderr.strip() or "Could not read checkout branch",
            code="branch_read_failed",
            stderr=proc.stderr,
        )
    branch = proc.stdout.strip()
    if not branch:
        raise GitMergeError(
            "Detached HEAD — checkout a branch before merging",
            code="detached_head",
        )
    return branch


def is_repo_clean(repo_path: str) -> bool:
    proc = _run_git(
        ["status", "--porcelain", "--untracked-files=no"],
        cwd=repo_path,
    )
    if proc.returncode != 0:
        return False
    return not proc.stdout.strip()


def merge_in_progress(repo_path: str) -> bool:
    proc = _run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=repo_path)
    return proc.returncode == 0


def abort_merge_if_in_progress(repo_path: str) -> None:
    if merge_in_progress(repo_path):
        proc = _run_git(["merge", "--abort"], cwd=repo_path)
        if proc.returncode != 0:
            raise GitMergeError(
                proc.stderr.strip() or "Failed to abort in-progress merge",
                code="merge_abort_failed",
                stderr=proc.stderr,
            )


def commit_all(repo_path: str, message: str) -> str | None:
    """Stage and commit all changes. Returns commit SHA or None if nothing to commit."""
    status = _run_git(["status", "--porcelain"], cwd=repo_path)
    if status.returncode != 0:
        raise GitMergeError(
            status.stderr.strip() or "Could not read git status",
            code="status_failed",
            stderr=status.stderr,
        )
    if not status.stdout.strip():
        head = _run_git(["rev-parse", "HEAD"], cwd=repo_path)
        if head.returncode == 0:
            return head.stdout.strip()
        return None

    add = _run_git(["add", "-A"], cwd=repo_path)
    if add.returncode != 0:
        raise GitMergeError(
            add.stderr.strip() or "git add failed",
            code="commit_failed",
            stderr=add.stderr,
        )

    commit = _run_git(["commit", "-m", message], cwd=repo_path)
    if commit.returncode != 0:
        raise GitMergeError(
            commit.stderr.strip() or "git commit failed",
            code="commit_failed",
            stderr=commit.stderr,
        )

    head = _run_git(["rev-parse", "HEAD"], cwd=repo_path)
    if head.returncode != 0:
        return None
    return head.stdout.strip()


def list_unmerged_files(repo_path: str) -> list[str]:
    proc = _run_git(
        ["diff", "--name-only", "--diff-filter=U"],
        cwd=repo_path,
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def merge_branch(repo_path: str, source_branch: str) -> None:
    proc = _run_git(["merge", source_branch, "--no-edit"], cwd=repo_path)
    if proc.returncode == 0:
        return

    conflict_files = list_unmerged_files(repo_path)
    abort = _run_git(["merge", "--abort"], cwd=repo_path)
    stderr = proc.stderr or proc.stdout or ""
    if abort.returncode != 0:
        stderr = f"{stderr}\n{abort.stderr}".strip()

    raise GitMergeError(
        f"Merge conflict merging {source_branch}",
        code="merge_conflict",
        conflict_files=conflict_files,
        stderr=stderr,
    )


def explore_project(project_path: str, sub_dir: str = "lib") -> dict:
    return ProjectExplorer(project_path).explore(sub_dir)


def list_changed_files(worktree_path: str) -> list[str]:
    """Return tracked diffs, staged changes, and untracked files."""
    names: set[str] = set()

    for args in (
        ["git", "-C", worktree_path, "diff", "--name-only", "HEAD"],
        ["git", "-C", worktree_path, "diff", "--name-only", "--cached"],
    ):
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            names.update(line.strip() for line in proc.stdout.splitlines() if line.strip())

    untracked = subprocess.run(
        ["git", "-C", worktree_path, "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=False,
    )
    if untracked.returncode == 0:
        names.update(line.strip() for line in untracked.stdout.splitlines() if line.strip())

    return sorted(names)


def stage_files(worktree_path: str, paths: list[str]) -> None:
    if not paths:
        return
    subprocess.run(
        ["git", "-C", worktree_path, "add", "--", *paths],
        capture_output=True,
        text=True,
        check=False,
    )


def read_file_at_head(worktree_path: str, relative_path: str) -> str | None:
    """Read a tracked file as it existed at HEAD (before uncommitted edits)."""
    proc = subprocess.run(
        ["git", "-C", worktree_path, "show", f"HEAD:{relative_path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    return proc.stdout


def get_file_diff(worktree_path: str, relative_path: str) -> str:
    proc = subprocess.run(
        ["git", "-C", worktree_path, "diff", "HEAD", "--", relative_path],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.stdout.strip():
        return proc.stdout

    cached = subprocess.run(
        ["git", "-C", worktree_path, "diff", "--cached", "--", relative_path],
        capture_output=True,
        text=True,
        check=False,
    )
    if cached.stdout.strip():
        return cached.stdout

    abs_path = os.path.join(worktree_path, relative_path)
    if os.path.isfile(abs_path):
        return "[new or modified file]"
    return proc.stdout


def get_all_diffs(worktree_path: str) -> list[dict]:
    diffs: list[dict] = []
    for rel in list_changed_files(worktree_path):
        diffs.append({"path": rel, "diff": get_file_diff(worktree_path, rel)})
    return diffs
