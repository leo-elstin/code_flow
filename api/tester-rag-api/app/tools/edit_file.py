import os
from app.tools.path_guard import resolve_write_path

class EditFileError(ValueError):
    pass

def edit_file(worktree_path: str, relative_path: str, target_content: str, replacement_content: str, allow_multiple: bool = False) -> None:
    """
    Surgically replace a contiguous block of text (target_content) in a file with replacement_content.
    Ensures target_content matches exactly once to prevent incorrect/ambiguous edits, unless allow_multiple is True.
    """
    abs_path = resolve_write_path(worktree_path, relative_path)
    if not os.path.isfile(abs_path):
        raise EditFileError(f"Target file does not exist: {relative_path}")
        
    with open(abs_path, "r", encoding="utf-8") as handle:
        content = handle.read()
        
    count = content.count(target_content)
    if count == 0:
        raise EditFileError(f"Target content not found in file: {relative_path}")
    elif count > 1 and not allow_multiple:
        raise EditFileError(
            f"Target content found {count} times in file: {relative_path}. "
            "Please provide a more unique context matching block, or pass allow_multiple=true if you intend to replace all occurrences."
        )
        
    new_content = content.replace(target_content, replacement_content) if allow_multiple else content.replace(target_content, replacement_content, 1)
    
    with open(abs_path, "w", encoding="utf-8") as handle:
        handle.write(new_content)
