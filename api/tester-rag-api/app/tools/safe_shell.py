import subprocess
import shlex
import os
from typing import NamedTuple

class SafeShellResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str

ALLOWED_COMMAND_PREFIXES = [
    ("flutter", "pub", "get"),
    ("flutter", "pub", "run"),
    ("flutter", "test"),
    ("flutter", "analyze"),
    ("dart", "pub", "get"),
    ("dart", "analyze"),
    ("dart", "run", "build_runner"),
    ("git", "status"),
    ("git", "diff"),
    ("git", "add"),
    ("git", "checkout"),
]

def is_safe_command(cmd_str: str) -> bool:
    """
    Check if a command matches the allowed prefixes and does not contain dangerous characters.
    """
    cmd_str = cmd_str.strip()
    if not cmd_str:
        return False
    
    # Block shell execution features that combine or redirect commands
    blocked_chars = [";", "&&", "||", "|", "`", "$", "(", ")", "<", ">", "\n", "\r"]
    for char in blocked_chars:
        if char in cmd_str:
            return False
            
    try:
        tokens = shlex.split(cmd_str)
    except ValueError:
        return False
        
    if not tokens:
        return False
        
    # Check if the command prefix matches one of our whitelisted structures
    for prefix in ALLOWED_COMMAND_PREFIXES:
        length = len(prefix)
        if len(tokens) >= length and tuple(tokens[:length]) == prefix:
            return True
            
    return False

def run_safe_command(worktree_path: str, command: str, timeout_seconds: int = 180) -> SafeShellResult:
    """
    Execute a whitelisted shell command within the specified worktree path.
    """
    if not is_safe_command(command):
        return SafeShellResult(
            returncode=-1,
            stdout="",
            stderr=f"Command execution blocked by safety policy: '{command}'"
        )
        
    try:
        proc = subprocess.run(
            shlex.split(command),
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=timeout_seconds
        )
        return SafeShellResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr
        )
    except subprocess.TimeoutExpired:
        return SafeShellResult(
            returncode=-2,
            stdout="",
            stderr=f"Command timed out after {timeout_seconds} seconds."
        )
    except Exception as e:
        return SafeShellResult(
            returncode=-3,
            stdout="",
            stderr=f"Failed to run command due to internal error: {str(e)}"
        )
