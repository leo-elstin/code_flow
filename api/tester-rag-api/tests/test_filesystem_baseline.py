"""Baseline-snapshot rollback: in-place runs must undo the agent's edits to the
state the run started from (preserving uncommitted work), not to HEAD."""

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.tools.filesystem import (
    restore_all_baselines,
    roll_back_file,
    snapshot_baseline,
)


def _write(root: str, rel: str, content: str) -> None:
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _read(root: str, rel: str) -> str:
    return (Path(root) / rel).read_text(encoding="utf-8")


def test_rollback_restores_uncommitted_content_not_head():
    with tempfile.TemporaryDirectory() as wt, tempfile.TemporaryDirectory() as base:
        # Pre-existing uncommitted work on disk when the run starts.
        _write(wt, "lib/foo.dart", "WIP from the user")
        snapshot_baseline(base, wt, "lib/foo.dart")
        # Agent edits it.
        _write(wt, "lib/foo.dart", "agent rewrite")

        roll_back_file(wt, "lib/foo.dart", baseline_dir=base)

        # Restored to the user's WIP, not deleted and not reverted to HEAD.
        assert _read(wt, "lib/foo.dart") == "WIP from the user"


def test_rollback_deletes_agent_created_file():
    with tempfile.TemporaryDirectory() as wt, tempfile.TemporaryDirectory() as base:
        # File does not exist at run start.
        snapshot_baseline(base, wt, "lib/new_service.dart")
        _write(wt, "lib/new_service.dart", "created by agent")

        roll_back_file(wt, "lib/new_service.dart", baseline_dir=base)

        assert not (Path(wt) / "lib/new_service.dart").exists()


def test_snapshot_is_idempotent_keeps_original_baseline():
    with tempfile.TemporaryDirectory() as wt, tempfile.TemporaryDirectory() as base:
        _write(wt, "a.dart", "original")
        snapshot_baseline(base, wt, "a.dart")
        _write(wt, "a.dart", "edit 1")
        snapshot_baseline(base, wt, "a.dart")  # must NOT overwrite the baseline
        _write(wt, "a.dart", "edit 2")

        roll_back_file(wt, "a.dart", baseline_dir=base)

        assert _read(wt, "a.dart") == "original"


def test_restore_all_baselines_preserves_untouched_wip():
    with tempfile.TemporaryDirectory() as wt, tempfile.TemporaryDirectory() as base:
        # File the agent touches (snapshotted), plus unrelated user WIP it never
        # touches (no snapshot).
        _write(wt, "edited.dart", "user version")
        snapshot_baseline(base, wt, "edited.dart")
        _write(wt, "edited.dart", "agent version")
        _write(wt, "untouched_wip.dart", "user's other uncommitted work")

        restored = restore_all_baselines(base, wt)

        assert "edited.dart" in restored
        assert _read(wt, "edited.dart") == "user version"          # undone
        assert _read(wt, "untouched_wip.dart") == "user's other uncommitted work"  # preserved


def test_restore_all_baselines_deletes_created_and_keeps_rest():
    with tempfile.TemporaryDirectory() as wt, tempfile.TemporaryDirectory() as base:
        snapshot_baseline(base, wt, "created.dart")   # absent at start
        _write(wt, "created.dart", "agent made this")
        _write(wt, "keep.dart", "unrelated WIP")

        restore_all_baselines(base, wt)

        assert not (Path(wt) / "created.dart").exists()
        assert _read(wt, "keep.dart") == "unrelated WIP"


def test_in_place_workspace_allows_dirty_tree():
    from app.services.worktree import _prepare_in_place_workspace

    with tempfile.TemporaryDirectory() as repo:
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "t@t.io"], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "t"], check=True)
        _write(repo, "tracked.txt", "v1")
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "init"], check=True)
        # Make the tree dirty.
        _write(repo, "tracked.txt", "uncommitted edit")

        with patch(
            "app.services.worktree.ensure_pub_dependencies", return_value={"passed": True}
        ):
            info = _prepare_in_place_workspace(repo, run_id="r1")

        # No longer raises on a dirty tree.
        assert info["workspace_mode"] == "in_place"
        assert os.path.realpath(info["worktree_path"]) == os.path.realpath(repo)
        # The user's uncommitted change is left intact.
        assert _read(repo, "tracked.txt") == "uncommitted edit"
