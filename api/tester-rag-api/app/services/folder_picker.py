"""Native folder picker for local single-user deployments."""

from __future__ import annotations

import os
import subprocess
import sys


def pick_folder(*, initial_dir: str | None = None) -> str | None:
    """Open a native directory picker on the host machine.

    Returns an absolute path, or ``None`` when the user cancels.
    """
    start_dir = os.path.abspath(initial_dir) if initial_dir and os.path.isdir(initial_dir) else None

    if sys.platform == "darwin":
        return _pick_folder_macos(start_dir)
    if sys.platform == "win32":
        return _pick_folder_tkinter(start_dir)
    return _pick_folder_tkinter(start_dir)


def _pick_folder_macos(initial_dir: str | None) -> str | None:
    script = 'set chosenFolder to choose folder with prompt "Select Flutter or Dart project folder"'
    if initial_dir:
        escaped = initial_dir.replace("\\", "\\\\").replace('"', '\\"')
        script += f' default location alias POSIX file "{escaped}"'
    script += "\nreturn POSIX path of chosenFolder"

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return _pick_folder_tkinter(initial_dir)

    if result.returncode != 0:
        return None

    path = result.stdout.strip()
    return os.path.abspath(path) if path else None


def _pick_folder_tkinter(initial_dir: str | None) -> str | None:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        path = filedialog.askdirectory(
            initialdir=initial_dir,
            title="Select Flutter or Dart project folder",
            mustexist=True,
        )
    finally:
        root.destroy()

    if not path:
        return None
    return os.path.abspath(path)
