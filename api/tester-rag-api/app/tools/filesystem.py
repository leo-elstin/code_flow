import os
import subprocess

from app.tools.path_guard import resolve_write_path


def write_file(worktree_path: str, relative_path: str, content: str) -> None:
    abs_path = resolve_write_path(worktree_path, relative_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as handle:
        handle.write(content)


def apply_unified_diff(worktree_path: str, relative_path: str, new_content: str) -> None:
    write_file(worktree_path, relative_path, new_content)


def roll_back_file(worktree_path: str, relative_path: str) -> None:
    """
    Discard any uncommitted edits in the worktree for the given file path.
    """
    abs_path = resolve_write_path(worktree_path, relative_path)
    if not os.path.exists(abs_path):
        return
        
    # Run git checkout HEAD -- path to revert the file to the last check-in state
    subprocess.run(
        ["git", "checkout", "HEAD", "--", relative_path],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False
    )

