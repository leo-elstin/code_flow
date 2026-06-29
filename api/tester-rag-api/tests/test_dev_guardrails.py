"""Guardrail tests for the dev agent tool layer (scope enforcement, FIX MODE,
output truncation, scoped analyze). See app/agents/roles/dev.py."""

import os

import pytest

try:  # the dart tree-sitter grammar is a native lib; absent/mismatched on CI runners
    import app.agents.dart_parser  # noqa: F401
except OSError:
    import sys
    import types

    _stub = types.ModuleType("app.agents.dart_parser")

    class _DartStructuralParserStub:  # not exercised by these tests
        def __init__(self, *args, **kwargs):
            raise RuntimeError("dart_parser unavailable on this platform")

    _stub.DartStructuralParser = _DartStructuralParserStub
    _stub.DART_LANGUAGE = None
    _stub.load_custom_language = lambda *a, **k: None
    sys.modules["app.agents.dart_parser"] = _stub

from app.agents.roles import dev as dev_mod
from app.agents.roles.dev import (
    _build_iteration_brief,
    _collect_required_fixes,
    _execute_dev_tool,
    _plan_path_set,
    _truncate_tail,
)

WIDGET = "lib/ui/widgets/glowing_ring.dart"
PLAN = {
    "files_to_create": [{"path": WIDGET}],
    "files_to_modify": [],
}
ALLOWED = _plan_path_set(PLAN)


@pytest.fixture
def worktree(tmp_path):
    (tmp_path / "lib" / "ui" / "widgets").mkdir(parents=True)
    return str(tmp_path)


def _call(action, args, worktree, **overrides):
    kwargs = dict(
        allowed_paths=ALLOWED,
        analyze_targets=[WIDGET],
        allow_flutter_test=False,
        planned_test_files=[],
    )
    kwargs.update(overrides)
    return _execute_dev_tool(action, args, worktree, **kwargs)


class _FakeShellResult:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


# --- Scope enforcement -------------------------------------------------------

def test_write_file_outside_plan_blocked(worktree):
    result, files = _call(
        "write_file", {"path": "test/widget_test.dart", "content": "x"}, worktree
    )
    assert result.startswith("BLOCKED")
    assert files == []
    assert not os.path.exists(os.path.join(worktree, "test/widget_test.dart"))


def test_edit_file_outside_plan_blocked(worktree):
    result, files = _call(
        "edit_file",
        {"path": "lib/main.dart", "target_content": "a", "replacement_content": "b"},
        worktree,
    )
    assert result.startswith("BLOCKED")
    assert files == []


def test_read_file_is_not_scope_restricted(worktree):
    other = os.path.join(worktree, "lib", "main.dart")
    with open(other, "w", encoding="utf-8") as fh:
        fh.write("void main() {}")
    result, _ = _call("read_file", {"path": "lib/main.dart"}, worktree)
    assert "void main() {}" in result


# --- write_file overwrite protection ----------------------------------------

def test_write_file_in_plan_then_requires_overwrite(worktree):
    result, files = _call("write_file", {"path": WIDGET, "content": "class A {}"}, worktree)
    assert "Successfully" in result
    assert files == [WIDGET]

    result, files = _call("write_file", {"path": WIDGET, "content": "class B {}"}, worktree)
    assert result.startswith("BLOCKED")
    assert files == []
    with open(os.path.join(worktree, WIDGET), encoding="utf-8") as fh:
        assert fh.read() == "class A {}"  # original untouched

    result, _ = _call(
        "write_file",
        {"path": WIDGET, "content": "class B {}", "overwrite": True},
        worktree,
    )
    assert "Successfully" in result


# --- flutter test gating ------------------------------------------------------

def test_flutter_test_blocked_without_planned_tests(worktree):
    result, _ = _call("run_command", {"command": "flutter test"}, worktree)
    assert result.startswith("BLOCKED")


def test_bare_flutter_test_scoped_to_planned_tests(worktree, monkeypatch):
    captured = {}

    def fake_run(path, command, timeout_seconds=180):
        captured["command"] = command
        return _FakeShellResult(stdout="ok")

    monkeypatch.setattr(dev_mod, "run_safe_command", fake_run)
    _call(
        "run_command",
        {"command": "flutter test"},
        worktree,
        allow_flutter_test=True,
        planned_test_files=["test/glowing_ring_test.dart"],
    )
    assert captured["command"] == "flutter test test/glowing_ring_test.dart"


def test_flutter_test_on_unplanned_file_blocked(worktree):
    result, _ = _call(
        "run_command",
        {"command": "flutter test test/widget_test.dart"},
        worktree,
        allow_flutter_test=True,
        planned_test_files=["test/glowing_ring_test.dart"],
    )
    assert result.startswith("BLOCKED")
    assert "test/widget_test.dart" in result


# --- Scoped analyze ------------------------------------------------------------

def test_raw_flutter_analyze_redirected_to_scoped(worktree, monkeypatch):
    captured = {}

    def fake_analyze(path, targets):
        captured["targets"] = targets
        return {"passed": True, "error_count": 0, "error_lines": []}

    monkeypatch.setattr(dev_mod, "run_dart_analyze", fake_analyze)
    with open(os.path.join(worktree, WIDGET), "w", encoding="utf-8") as fh:
        fh.write("class A {}")

    result, _ = _call("run_command", {"command": "flutter analyze"}, worktree)
    assert "Redirected to analyze_changed_files" in result
    assert captured["targets"] == [WIDGET]


def test_analyze_changed_files_before_files_exist(worktree):
    result, _ = _call("analyze_changed_files", {}, worktree)
    assert "No planned lib/*.dart files exist yet" in result


# --- Output truncation ----------------------------------------------------------

def test_run_command_output_truncated(worktree, monkeypatch):
    monkeypatch.setattr(
        dev_mod,
        "run_safe_command",
        lambda *a, **k: _FakeShellResult(stdout="x" * 10_000),
    )
    result, _ = _call("run_command", {"command": "git status"}, worktree)
    assert len(result) < 4_000
    assert "truncated" in result


def test_truncate_tail_keeps_tail():
    out = _truncate_tail("a" * 100 + "TAIL", 10)
    assert out.endswith("TAIL")
    assert "truncated" in out
    assert _truncate_tail("short", 10) == "short"


# --- FIX MODE brief ---------------------------------------------------------------

def test_collect_required_fixes_flattens_report_and_gate():
    report = {
        "passed": False,
        "required_fixes": ["Wrap the CustomPaint child in a Center"],
        "issues": [{"severity": "blocker", "reason": "Clamp progress before painting"}],
        "deterministic_gate": {
            "required_fixes": ["dart analyze failed on changed lib files"],
            "analyze": {"error_lines": ["error - withValues has no parameter 'opacity'"]},
        },
    }
    fixes, errors = _collect_required_fixes(report)
    assert "Wrap the CustomPaint child in a Center" in fixes
    assert "Clamp progress before painting" in fixes
    assert "dart analyze failed on changed lib files" in fixes
    assert errors == ["error - withValues has no parameter 'opacity'"]


def test_collect_required_fixes_empty_on_pass():
    assert _collect_required_fixes(None) == ([], [])
    assert _collect_required_fixes({"passed": True}) == ([], [])


def test_first_iteration_brief_is_implementation(worktree):
    brief = _build_iteration_brief(worktree, ALLOWED, [], [])
    assert "Begin implementation" in brief
    assert "FIX MODE" not in brief


def test_fix_mode_brief_has_checklist_and_current_content(worktree):
    with open(os.path.join(worktree, WIDGET), "w", encoding="utf-8") as fh:
        fh.write("class GlowingRing {}")
    brief = _build_iteration_brief(
        worktree,
        ALLOWED,
        ["Clamp progress before painting", "Center the optional child"],
        ["error - line 62"],
    )
    assert brief.startswith("FIX MODE")
    assert "1. Clamp progress before painting" in brief
    assert "2. Center the optional child" in brief
    assert "error - line 62" in brief
    assert "class GlowingRing {}" in brief
    assert "Do NOT rewrite files" in brief
