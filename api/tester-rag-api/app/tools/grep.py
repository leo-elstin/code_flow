import os
import re
import subprocess
from dataclasses import dataclass

from app.tools.path_guard import PathGuardError, resolve_read_path


@dataclass
class GrepMatch:
    file_path: str
    line_number: int
    line_text: str


DEFAULT_EXCLUDES = [
    ".git",
    ".venv",
    "build",
    "node_modules",
    ".dart_tool",
    "__pycache__",
    "ios/build",
    "ios/Pods",
    "ios/.symlinks",
    "android/app/build",
    "android/.gradle",
    "macos/Build",
    ".idea",
]


def normalize_search_terms(text: str) -> list[str]:
    stop_words = {
        "module",
        "write",
        "the",
        "test",
        "cases",
        "for",
        "code",
        "related",
        "add",
        "new",
        "feature",
        "with",
        "all",
        "context",
        "required",
        "a",
        "an",
        "and",
        "or",
        "in",
        "to",
        "package",
        "flutter",
        "dart",
        "src",
        "widgets",
        "framework",
        "rendering",
        "material",
        "cupertino",
        "foundation",
        "services",
        "object",
        "element",
        "state",
        "widget",
        "context",
        "build",
        "layout",
        "render",
        "class",
        "void",
        "final",
        "const",
        "var",
        "import",
        "return",
        "super",
        "this",
        "extends",
        "with",
        "implements",
        "async",
        "await",
        "future",
        "stream",
        "normal",
        "mounting",
        "frames",
        "inflateWidget",
        "updateChild",
        "createChild",
        "performLayout",
        "layout",
        "drawFrame",
    }
    terms: set[str] = set()
    for raw in re.findall(r"[A-Za-z0-9_-]+", text):
        word = raw.lower()
        if len(word) <= 2 or word in stop_words:
            continue
        terms.add(word)
        terms.add(word.replace("-", "_"))
        terms.add(word.replace("_", "-"))
        if "_" in word or "-" in word:
            parts = re.split(r"[_-]+", word)
            terms.update(p for p in parts if len(p) > 2)
        if word.isalpha():
            terms.add(word)
    return sorted(terms)


def grep_codebase(
    project_path: str,
    pattern: str,
    *,
    max_results: int = 100,
) -> list[GrepMatch]:
    if not os.path.isdir(project_path):
        raise FileNotFoundError(f"Project path not found: {project_path}")

    args = [
        "rg",
        "--line-number",
        "--no-heading",
        "--color=never",
        "--max-count",
        str(max_results),
    ]
    for item in DEFAULT_EXCLUDES:
        args.extend(["--glob", f"!**/{item}/**"])

    args.extend([pattern, project_path])

    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("ripgrep (rg) is required but not installed") from exc

    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or "grep failed")

    matches: list[GrepMatch] = []
    root = os.path.realpath(project_path)
    for line in proc.stdout.splitlines():
        if ":" not in line:
            continue
        file_part, line_no, content = line.split(":", 2)
        abs_path = os.path.realpath(file_part)
        if not abs_path.startswith(root + os.sep):
            continue
        rel = os.path.relpath(abs_path, root)
        matches.append(
            GrepMatch(
                file_path=rel,
                line_number=int(line_no),
                line_text=content.strip(),
            )
        )
    return matches


def grep_terms(project_path: str, terms: list[str], *, max_results: int = 100) -> list[GrepMatch]:
    all_matches: list[GrepMatch] = []
    seen: set[tuple[str, int]] = set()
    sorted_terms = sorted(terms, key=len, reverse=True)[:30]
    for term in sorted_terms:
        try:
            matches = grep_codebase(project_path, re.escape(term), max_results=max_results)
        except RuntimeError:
            matches = _python_fallback_grep(project_path, term, max_results=max_results)
        for match in matches:
            key = (match.file_path, match.line_number)
            if key in seen:
                continue
            seen.add(key)
            all_matches.append(match)
    return all_matches[:max_results]


def _python_fallback_grep(project_path: str, term: str, *, max_results: int) -> list[GrepMatch]:
    matches: list[GrepMatch] = []
    term_lower = term.lower()
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".")
            and not any(
                os.path.relpath(os.path.join(root, d), project_path).replace("\\", "/").startswith(exclude)
                for exclude in DEFAULT_EXCLUDES
            )
        ]
        for name in files:
            if not name.endswith((".dart", ".py", ".yaml", ".json", ".md")):
                continue
            abs_path = os.path.join(root, name)
            rel = os.path.relpath(abs_path, project_path)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as handle:
                    for idx, line in enumerate(handle, start=1):
                        if term_lower in line.lower():
                            matches.append(
                                GrepMatch(file_path=rel, line_number=idx, line_text=line.strip())
                            )
                            if len(matches) >= max_results:
                                return matches
            except OSError:
                continue
    return matches


def read_file(project_path: str, relative_path: str, *, max_chars: int = 120_000) -> str:
    abs_path = resolve_read_path(project_path, relative_path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(relative_path)
    with open(abs_path, "r", encoding="utf-8", errors="replace") as handle:
        content = handle.read(max_chars + 1)
    if len(content) > max_chars:
        return content[:max_chars] + "\n... [TRUNCATED]"
    return content
