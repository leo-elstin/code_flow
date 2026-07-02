"""Pure-function tests for the token-usage optimizations: discovery-evidence
dedup, verifier LLM payload slimming, and FIX-MODE brief file selection."""

from app.agents.roles import dev as dev_mod
from app.agents.roles.verifier import _build_llm_payload
from app.services.feature_discovery import _dedupe_evidence


# -- _dedupe_evidence --------------------------------------------------------

def test_dedupe_grep_collapses_duplicate_lines():
    grep = [
        {"file_path": "lib/a.dart", "line_number": 1, "line_text": "x"},
        {"file_path": "lib/a.dart", "line_number": 1, "line_text": "x (dup)"},
        {"file_path": "lib/a.dart", "line_number": 2, "line_text": "y"},
    ]
    grep_out, _, _ = _dedupe_evidence(grep, [], [])
    assert [(m["file_path"], m["line_number"]) for m in grep_out] == [
        ("lib/a.dart", 1),
        ("lib/a.dart", 2),
    ]
    assert grep_out[0]["line_text"] == "x"  # first occurrence wins


def test_dedupe_drops_lsp_hits_into_grep_covered_files():
    grep = [{"file_path": "lib/a.dart", "line_number": 1, "line_text": "x"}]
    lsp = [
        {"kind": "reference", "file_path": "lib/a.dart"},   # covered by grep
        {"kind": "definition", "file_path": "lib/a.dart"},  # covered by grep
        {"kind": "reference", "file_path": "lib/b.dart"},   # new file — kept
        {"kind": "reference", "file_path": "lib/b.dart"},   # duplicate — dropped
        {"kind": "workspace_symbol", "file_path": "lib/a.dart"},  # kept: not nav
    ]
    _, lsp_out, _ = _dedupe_evidence(grep, lsp, [])
    assert lsp_out == [
        {"kind": "reference", "file_path": "lib/b.dart"},
        {"kind": "workspace_symbol", "file_path": "lib/a.dart"},
    ]


def test_dedupe_caps_symbols_per_file():
    symbols = [{"file_path": "lib/a.dart", "name": f"s{i}"} for i in range(8)]
    symbols += [{"file_path": "lib/b.dart", "name": "other"}]
    _, _, out = _dedupe_evidence([], [], symbols, max_symbols_per_file=5)
    assert sum(1 for s in out if s["file_path"] == "lib/a.dart") == 5
    assert sum(1 for s in out if s["file_path"] == "lib/b.dart") == 1


# -- _build_llm_payload ------------------------------------------------------

def test_llm_payload_strips_passing_subprocess_output():
    gate = {
        "passed": True,
        "pub_get": {"passed": True, "stdout": "x" * 8000, "stderr": "y" * 500},
        "build_runner": {"passed": True, "stdout": "z" * 8000, "stderr": ""},
        "test_results": {"passed": True, "stdout": "t" * 8000, "stderr": ""},
        "analyze": {"passed": True, "error_lines": ["keep me"]},
    }
    payload = _build_llm_payload({}, [], [], [], gate)
    g = payload["deterministic_gate"]
    assert g["pub_get"]["stdout"] == ""
    assert g["build_runner"]["stdout"] == ""
    assert g["test_results"]["stdout"] == ""
    assert g["analyze"]["error_lines"] == ["keep me"]
    # Original gate untouched (report still carries full output).
    assert len(gate["pub_get"]["stdout"]) == 8000


def test_llm_payload_keeps_failed_test_tail():
    gate = {
        "test_results": {"passed": False, "stdout": "a" * 5000, "stderr": "b" * 5000},
    }
    payload = _build_llm_payload({}, [], [], [], gate)
    section = payload["deterministic_gate"]["test_results"]
    assert len(section["stdout"]) == 1500
    assert len(section["stderr"]) == 1500


def test_llm_payload_caps_diffs():
    diffs = [{"path": f"lib/f{i}.dart", "diff": "d" * 10_000} for i in range(30)]
    payload = _build_llm_payload({}, [], [], diffs, {})
    assert len(payload["diffs"]) == 20
    assert all(len(d["diff"]) == 6_000 for d in payload["diffs"])


# -- _build_iteration_brief --------------------------------------------------

def _write(tmp_path, rel, content):
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def test_fix_mode_injects_only_referenced_files(tmp_path):
    _write(tmp_path, "lib/a.dart", "class A {}")
    _write(tmp_path, "lib/b.dart", "class B {}")
    _write(tmp_path, "lib/c.dart", "class C {}")
    brief = dev_mod._build_iteration_brief(
        str(tmp_path),
        {"lib/a.dart", "lib/b.dart", "lib/c.dart"},
        ["Fix the null check in lib/b.dart"],
        ["lib/b.dart:3:1 - error - something"],
    )
    assert "### lib/b.dart" in brief
    assert "### lib/a.dart" not in brief
    assert "### lib/c.dart" not in brief
    assert "other planned file(s) exist" in brief


def test_fix_mode_matches_by_basename(tmp_path):
    _write(tmp_path, "lib/widgets/fancy_button.dart", "class FancyButton {}")
    _write(tmp_path, "lib/other.dart", "class Other {}")
    brief = dev_mod._build_iteration_brief(
        str(tmp_path),
        {"lib/widgets/fancy_button.dart", "lib/other.dart"},
        ["fancy_button.dart has a broken constructor"],
        [],
    )
    assert "### lib/widgets/fancy_button.dart" in brief
    assert "### lib/other.dart" not in brief


def test_fix_mode_falls_back_when_nothing_referenced(tmp_path):
    _write(tmp_path, "lib/a.dart", "class A {}")
    _write(tmp_path, "lib/b.dart", "class B {}")
    brief = dev_mod._build_iteration_brief(
        str(tmp_path),
        {"lib/a.dart", "lib/b.dart"},
        ["Improve error handling"],  # no file named
        [],
    )
    # Fallback: still injects (never zero context).
    assert "### lib/a.dart" in brief
    assert "### lib/b.dart" in brief


def test_fix_mode_respects_file_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_mod, "_FIX_MODE_MAX_FILES", 2)
    monkeypatch.setattr(dev_mod, "_FIX_MODE_FILE_CAP", 50)
    for name in ("a", "b", "c"):
        _write(tmp_path, f"lib/{name}.dart", "x" * 200)
    brief = dev_mod._build_iteration_brief(
        str(tmp_path),
        {"lib/a.dart", "lib/b.dart", "lib/c.dart"},
        ["fix lib/a.dart, lib/b.dart and lib/c.dart"],
        [],
    )
    assert brief.count("### lib/") == 2
    assert "...[file truncated]" in brief


def test_first_iteration_brief_unchanged(tmp_path):
    brief = dev_mod._build_iteration_brief(str(tmp_path), set(), [], [])
    assert brief.startswith("Begin implementation")
