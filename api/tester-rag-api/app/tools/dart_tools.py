import os
import re
import subprocess

from app.core.config import settings

_DART_METHOD_RE = re.compile(
    r"^\s+(?:@\w+(?:\([^)]*\))?\s+)*"
    r"(?:async\s+)?(Future<[^>]+>|Future|void|int|bool|String|double|List<[\w<>, ]+>|\w+)\s+"
    r"(\w+)\s*\(",
    re.MULTILINE,
)

# Tokens that, when they appear in the "type" slot, mean the line is an
# expression/statement (e.g. `return Center(`, `await load(`, `const Icon(`),
# NOT a method declaration. Matching these caused the verifier's
# "removed existing methods" guardrail to fire on removed widget usages.
_DART_EXPR_LEAD_TOKENS = frozenset(
    {
        "return", "await", "yield", "throw", "const", "new", "final", "var",
        "else", "case", "if", "for", "while", "switch", "catch", "assert",
        "super", "this",
    }
)

_GENERATED_SUFFIXES = (".g.dart", ".freezed.dart", ".gr.dart", ".mocks.dart")


def _read_pubspec(worktree_path: str) -> str | None:
    pubspec = os.path.join(worktree_path, "pubspec.yaml")
    if not os.path.isfile(pubspec):
        return None
    try:
        return open(pubspec, encoding="utf-8").read()
    except OSError:
        return None


def is_flutter_project(worktree_path: str) -> bool:
    content = _read_pubspec(worktree_path)
    if not content:
        return False
    return "flutter:" in content


def project_has_build_runner(worktree_path: str) -> bool:
    content = _read_pubspec(worktree_path)
    if not content:
        return False
    return "build_runner" in content


def needs_pub_get(worktree_path: str) -> bool:
    pubspec = os.path.join(worktree_path, "pubspec.yaml")
    package_config = os.path.join(worktree_path, ".dart_tool", "package_config.json")
    if not os.path.isfile(pubspec):
        return False
    if not os.path.isfile(package_config):
        return True
    return os.path.getmtime(pubspec) > os.path.getmtime(package_config)


def ensure_pub_dependencies(worktree_path: str) -> dict:
    """Install Dart/Flutter package dependencies in the worktree when needed."""
    if not needs_pub_get(worktree_path):
        return {"passed": True, "skipped": True}

    timeout = settings.CODE_AGENT_PUB_GET_TIMEOUT
    if is_flutter_project(worktree_path):
        cmd = [settings.FLUTTER_BIN, "pub", "get"]
    else:
        cmd = [settings.DART_BIN, "pub", "get"]

    try:
        proc = subprocess.run(
            cmd,
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"").decode("utf-8", errors="replace") if exc.stdout else ""
        err = (exc.stderr or b"").decode("utf-8", errors="replace") if exc.stderr else ""
        return {
            "passed": False,
            "exit_code": -1,
            "stdout": output[-8000:],
            "stderr": f"pub get timed out after {timeout}s\n{err}"[-8000:],
            "timed_out": True,
            "skipped": False,
            "command": " ".join(cmd),
        }

    return {
        "passed": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-8000:] if proc.stdout else "",
        "stderr": proc.stderr[-8000:] if proc.stderr else "",
        "timed_out": False,
        "skipped": False,
        "command": " ".join(cmd),
    }


def should_run_build_runner(worktree_path: str, dart_paths: list[str]) -> bool:
    if not settings.CODE_AGENT_RUN_BUILD_RUNNER:
        return False
    if not dart_paths:
        return False
    return project_has_build_runner(worktree_path)


def run_build_runner(worktree_path: str) -> dict:
    """Run `dart run build_runner build --delete-conflicting-outputs` in the worktree."""
    pub_get = ensure_pub_dependencies(worktree_path)
    if not pub_get.get("passed", True):
        pub_get["skipped"] = False
        return pub_get

    timeout = settings.CODE_AGENT_BUILD_RUNNER_TIMEOUT
    cmd = [
        settings.DART_BIN,
        "run",
        "build_runner",
        "build",
        "--delete-conflicting-outputs",
    ]

    try:
        proc = subprocess.run(
            cmd,
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"").decode("utf-8", errors="replace") if exc.stdout else ""
        err = (exc.stderr or b"").decode("utf-8", errors="replace") if exc.stderr else ""
        return {
            "passed": False,
            "exit_code": -1,
            "stdout": output[-8000:],
            "stderr": f"build_runner timed out after {timeout}s\n{err}"[-8000:],
            "timed_out": True,
            "skipped": False,
        }

    return {
        "passed": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-8000:] if proc.stdout else "",
        "stderr": proc.stderr[-8000:] if proc.stderr else "",
        "timed_out": False,
        "skipped": False,
    }


def maybe_run_build_runner(worktree_path: str, dart_paths: list[str]) -> dict:
    """Run build_runner when the project uses it and Dart files were changed."""
    if not should_run_build_runner(worktree_path, dart_paths):
        return {"passed": True, "skipped": True, "targets": dart_paths}
    result = run_build_runner(worktree_path)
    result["targets"] = dart_paths
    return result


def is_generated_dart_path(path: str) -> bool:
    return path.endswith(_GENERATED_SUFFIXES)


# Matches an injectable/get_it annotation immediately preceding a class decl,
# tolerating stacked annotations between them. Captures the class name.
_INJECTABLE_CLASS_RE = re.compile(
    r"@(?:lazySingleton|singleton|injectable|Injectable|LazySingleton|Singleton)\b"
    r"[^\n]*\n(?:\s*@[^\n]*\n)*"
    r"\s*(?:abstract\s+)?class\s+(\w+)",
)


def injectable_classes(content: str) -> set[str]:
    """Return class names in *content* that carry an injectable/get_it annotation
    (@injectable, @lazySingleton, @singleton, and their PascalCase forms)."""
    return set(_INJECTABLE_CLASS_RE.findall(content))


def check_di_registrations(worktree_path: str, created_paths: list[str]) -> dict:
    """Confirm newly-created injectable classes are wired into the generated DI graph.

    injectable/get_it registrations live in generated ``*.config.dart`` files, which
    are commonly gitignored (so they never appear in a git diff). build_runner emits
    them on disk, so this reads those files directly rather than trusting the diff.

    Returns a dict with:
      - checked: whether any annotated classes were found to verify
      - registered: {class_name: config_file} for classes confirmed in the DI graph
      - missing: class names annotated injectable but absent from every config file
    """
    annotated: dict[str, str] = {}
    for rel in created_paths:
        if not rel.endswith(".dart") or is_generated_dart_path(rel):
            continue
        abs_path = os.path.join(worktree_path, rel)
        try:
            with open(abs_path, encoding="utf-8") as handle:
                content = handle.read()
        except OSError:
            continue
        for cls in injectable_classes(content):
            annotated[cls] = rel

    if not annotated:
        return {"checked": False, "registered": {}, "missing": []}

    config_blobs: list[tuple[str, str]] = []
    lib_root = os.path.join(worktree_path, "lib")
    for root, dirs, files in os.walk(lib_root):
        dirs[:] = [d for d in dirs if d != ".dart_tool"]
        for name in files:
            if name.endswith(".config.dart"):
                abs_cfg = os.path.join(root, name)
                try:
                    with open(abs_cfg, encoding="utf-8") as handle:
                        config_blobs.append((os.path.relpath(abs_cfg, worktree_path), handle.read()))
                except OSError:
                    pass

    registered: dict[str, str] = {}
    for cls in annotated:
        pattern = re.compile(rf"\b{re.escape(cls)}\b")
        for rel_cfg, blob in config_blobs:
            if pattern.search(blob):
                registered[cls] = rel_cfg
                break

    missing = sorted(cls for cls in annotated if cls not in registered)
    return {
        "checked": True,
        "annotated": annotated,
        "registered": registered,
        "missing": missing,
    }


def run_dart_analyze(worktree_path: str, files: list[str] | None = None) -> dict:
    """Analyze only the provided relative paths when possible (avoids full-project hangs)."""
    pub_get = ensure_pub_dependencies(worktree_path)
    if not pub_get.get("passed", True):
        pub_get["skipped"] = False
        return pub_get

    targets = files or []
    timeout = settings.CODE_AGENT_DART_ANALYZE_TIMEOUT
    if is_flutter_project(worktree_path):
        cmd = [settings.FLUTTER_BIN, "analyze"]
    else:
        cmd = [settings.DART_BIN, "analyze"]
    if targets:
        cmd.extend(targets)

    try:
        proc = subprocess.run(
            cmd,
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"").decode("utf-8", errors="replace") if exc.stdout else ""
        err = (exc.stderr or b"").decode("utf-8", errors="replace") if exc.stderr else ""
        label = cmd[0]
        return {
            "passed": False,
            "blocking_passed": False,
            "exit_code": -1,
            "stdout": output[-8000:],
            "stderr": f"{label} analyze timed out after {timeout}s\n{err}"[-8000:],
            "timed_out": True,
            "error_count": -1,
        }

    combined = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
    error_count, error_lines = _parse_analyze_errors(combined, targets)
    clean_pass = error_count == 0
    blocking_passed = clean_pass or not settings.CODE_AGENT_ANALYZE_BLOCKING

    return {
        "passed": clean_pass,
        "blocking_passed": blocking_passed,
        "advisory_only": not settings.CODE_AGENT_ANALYZE_BLOCKING and not clean_pass,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-8000:] if proc.stdout else "",
        "stderr": proc.stderr[-8000:] if proc.stderr else "",
        "timed_out": False,
        "error_count": error_count,
        "error_lines": error_lines[:25],
    }


def _parse_analyze_errors(output: str, targets: list[str]) -> tuple[int, list[str]]:
    if not output.strip():
        return 0, []
    if not targets:
        errors = [line.strip() for line in output.splitlines() if " error " in line]
        return len(errors), errors[:25]

    errors: list[str] = []
    for line in output.splitlines():
        if " error " not in line:
            continue
        if any(target in line for target in targets):
            errors.append(line.strip())
    return len(errors), errors[:25]


def dart_public_method_names(content: str) -> set[str]:
    """Return method names declared in a Dart class body.

    The underlying regex is loose (`<Type> <name>(`), so it also matches
    constructor/widget invocations and expression statements. We filter those
    out, because misreading a removed widget usage (e.g. `const Center(...)`,
    `CircularProgressIndicator(...)`, `const Icon(...)`) as a removed public
    method made the verifier's method-removal guardrail raise false blockers
    and exhaust retries. Dart methods are lowerCamelCase; constructors/types
    are PascalCase — so a leading uppercase callee is never a method here."""
    names: set[str] = set()
    for type_tok, name in _DART_METHOD_RE.findall(content):
        if name[:1].isupper():  # Center(, Icon(, CircularProgressIndicator(
            continue
        if type_tok in _DART_EXPR_LEAD_TOKENS:  # return foo(, await bar(, const baz(
            continue
        names.add(name)
    return names


def run_flutter_test(worktree_path: str, targets: list[str] | None = None) -> dict:
    """Run unit/widget tests in the worktree."""
    pub_get = ensure_pub_dependencies(worktree_path)
    if not pub_get.get("passed", True):
        pub_get["skipped"] = False
        return pub_get

    timeout = 180
    cmd = []
    if is_flutter_project(worktree_path):
        cmd = [settings.FLUTTER_BIN, "test"]
    else:
        cmd = [settings.DART_BIN, "test"]

    if targets:
        cmd.extend(targets)

    try:
        proc = subprocess.run(
            cmd,
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"").decode("utf-8", errors="replace") if exc.stdout else ""
        err = (exc.stderr or b"").decode("utf-8", errors="replace") if exc.stderr else ""
        return {
            "passed": False,
            "exit_code": -1,
            "stdout": output[-8000:],
            "stderr": f"test run timed out after {timeout}s\n{err}"[-8000:],
            "timed_out": True,
            "skipped": False,
        }

    # Extract test failures (e.g. check for "Some tests failed" or non-zero exit code)
    passed = proc.returncode == 0
    return {
        "passed": passed,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-8000:] if proc.stdout else "",
        "stderr": proc.stderr[-8000:] if proc.stderr else "",
        "timed_out": False,
        "skipped": False,
    }

